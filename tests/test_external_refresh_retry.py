"""Exercise source refresh recovery through the real profile scheduler."""
import copy
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_v130_external_service import FakeProviders
from tests.test_v149_library_sharing import FakePlex, shared_library


def chart(revision="old", order=(1, 2)):
    from helper.external_sources import make_track

    names = {1: ("歌曲一", "歌手甲"), 2: ("歌曲二", "歌手乙")}
    return {
        "provider": "qq", "external_id": "62",
        "url": "https://y.qq.com/n/ryqq/toplist/62",
        "title": "飙升榜", "revision": revision,
        "tracks": [make_track(i, *names[n], duration_ms=180000, source_id=f"song-{n}")
                   for i, n in enumerate(order)],
    }


@pytest.fixture
def retry_runtime(shared_library):
    from helper.automation import automation_settings, ensure_profile_schedule
    from helper.external_service import ExternalPlaylistService
    from helper.profile_controls import write_control
    from helper.scoped_store import ScopedStore

    base, registry, runtime, data = shared_library
    registry.create(
        name="经典音乐", kind="owner", profile_id="classic", account={"id": "owner"},
        server={"machine": "server-a", "url": "http://plex"},
        library={"id": "12", "name": "经典音乐"}, token="owner-token",
    )

    class CatalogPlex(FakePlex):
        def __init__(self, cfg):
            super().__init__(cfg["plex_token"], data)
            self.section = str(cfg["section"])

        def sections(self):
            return [{"id": "11"}, {"id": "12"}]

        def tracks(self, section):
            assert str(section) == self.section
            offset = 100 if self.section == "12" else 0
            return [{"id": str(offset + n), "title": title, "artist": artist,
                     "album": "", "duration": 180, "available": True}
                    for n, title, artist in [(1, "歌曲一", "歌手甲"),
                                             (2, "歌曲二", "歌手乙"),
                                             (3, "歌曲三", "歌手丙")]]

    clock = [datetime(2027, 1, 10, 12, tzinfo=timezone(timedelta(hours=8))).timestamp()]
    provider = FakeProviders([chart()])
    for profile in registry.list_public():
        store = ScopedStore(base, profile["id"], registry=registry)
        cfg = store.get("settings")
        cfg.update(plex_url="http://plex", plex_token=registry.get(profile["id"])["token"],
                   section=profile["library"]["id"])
        store.set("settings", cfg)
        write_control(store, "daily", False)
        write_control(store, "smart", False)
        engine = runtime.engine(profile["id"])
        engine.plex_factory = CatalogPlex
        engine.external = ExternalPlaylistService(
            store, CatalogPlex, provider, clock=lambda: clock[0],
        )
    settings = automation_settings(base, registry, runtime, now=clock[0])
    for profile in registry.list_public():
        store = runtime.engine(profile["id"]).store
        state = ensure_profile_schedule(store, settings, clock[0])
        for task in state["tasks"].values():
            if task.get("next_at"):
                task.update(next_at=clock[0] + 30 * 86400, slot=clock[0] + 30 * 86400)
        store.set("automation_schedule_v1", state)
    service = runtime.engine("default").external
    source = service.import_source(value=chart()["url"])
    published = service.publish(source["id"], "飙升榜", source["revision"])
    service.set_follow_updates(source["id"], True)
    runtime.sync_library_shares_due(clock[0])
    return base, runtime, provider, clock, service, source, published, data


def fail_refresh(fixture):
    _base, _runtime, provider, _clock, service, source, _published, _data = fixture
    bad = chart("bad")
    bad["tracks"][0]["artists"] = []
    provider.results.append(bad)
    with pytest.raises(ValueError, match="来源歌曲缺少歌手"):
        service.refresh(source["id"])


def test_due_refresh_runs_between_daily_slots_and_converges_all_chart_copies(retry_runtime):
    from helper.external_store import ExternalRepository

    base, runtime, provider, clock, service, source, published, data = retry_runtime
    fail_refresh(retry_runtime)
    assert [r["id"] for r in data["owner-token"]["playlists"][published["playlist_id"]]["items"]] == ["1", "2"]
    clock[0] += 900
    provider.results.append(chart("recovered", (2, 1)))

    runtime.run_due(clock[0])

    record = service.repository.get_source("default", source["id"])
    assert record["revision"] == "recovered"
    assert record["failure_count"] == 0
    assert record["next_retry_at"] is None
    assert [r["id"] for r in data["owner-token"]["playlists"][published["playlist_id"]]["items"]] == ["2", "1"]
    child = runtime.engine("friend").store.get("managed")["external:" + source["id"]]
    assert [r["id"] for r in data["friend-token"]["playlists"][child["id"]]["items"]] == ["2", "1"]
    classic = runtime.engine("classic").store
    repository = ExternalRepository(classic)
    mirrored = repository.list_sources("classic")[0]
    assert mirrored["follow_updates"] is False
    copy_record = repository.get_managed("classic", mirrored["id"])
    assert [r["id"] for r in data["owner-token"]["playlists"][copy_record["id"]]["items"]] == ["102", "101"]
    calls = provider.calls
    runtime.run_due(clock[0] + 60)
    assert provider.calls == calls  # recovered/healthy sources are not polled every minute


def test_failed_refresh_retries_follow_persistent_backoff_after_restart(retry_runtime):
    from helper.external_sources import ExternalSourceError
    from helper.profile_runtime import ProfileRuntime

    base, runtime, provider, clock, service, source, _published, _data = retry_runtime
    fail_refresh(retry_runtime)
    calls = provider.calls
    runtime.run_due(clock[0] + 899)
    assert provider.calls == calls
    # Recreate the runtime, keeping the durable store and external transport doubles.
    restarted = ProfileRuntime(base, runtime.registry, engine_factory=lambda store: runtime.engine(store.profile_id))
    for delay in (900, 3600, 21600, 86400, 86400):
        clock[0] += delay
        provider.results.append(ExternalSourceError("临时不可用", retryable=True))
        restarted.run_due(clock[0])
        calls += 1
        assert provider.calls == calls
        record = service.repository.get_source("default", source["id"])
        expected_delay = {2: 3600, 3: 21600}.get(record["failure_count"], 86400)
        assert record["next_retry_at"] == clock[0] + expected_delay
        restarted.run_due(clock[0] + 60)
        assert provider.calls == calls


@pytest.mark.parametrize("disabled", ["global", "source", "profile", "removal", "confirmation", "file"])
def test_refresh_retry_respects_switches_and_source_scope(retry_runtime, disabled):
    from helper.automation import AUTOMATION_KEY

    base, runtime, provider, clock, service, source, _published, _data = retry_runtime
    fail_refresh(retry_runtime)
    if disabled == "global":
        cfg = base.get(AUTOMATION_KEY)
        cfg["library"]["enabled"] = False
        cfg["revision"] += 1
        base.set(AUTOMATION_KEY, cfg)
    elif disabled == "source":
        service.set_follow_updates(source["id"], False)
    elif disabled == "profile":
        runtime.registry.update("default", enabled=False)
    elif disabled == "removal":
        service.store.set("profile_removal_v1", {"next_retry_at": clock[0] + 86400})
    elif disabled == "confirmation":
        service.repository.set_needs_confirmation("default", source["id"], True)
    else:
        with base._db() as db:
            db.execute("UPDATE external_source SET provider='txt' WHERE profile_id='default' AND id=?", (source["id"],))
    calls = provider.calls
    clock[0] += 900

    runtime.run_due(clock[0])

    assert provider.calls == calls
    assert service.repository.get_source("default", source["id"])["failure_count"] == 1


def test_retry_does_not_refetch_sources_owned_by_pending_order_recovery(retry_runtime):
    _base, runtime, provider, clock, service, source, _published, _data = retry_runtime
    fail_refresh(retry_runtime)
    record = service.repository.get_managed("default", source["id"])
    service.repository.save_managed("default", source["id"], {**record, "order_pending": True})
    calls = provider.calls
    clock[0] += 900

    runtime.run_due(clock[0])

    assert provider.calls == calls


def test_unavailable_retry_keeps_old_playlist_and_does_not_block_other_sources(retry_runtime):
    _base, runtime, provider, clock, service, source, published, data = retry_runtime
    fail_refresh(retry_runtime)
    other = chart("other")
    other.update(external_id="26", url="https://y.qq.com/n/ryqq/toplist/26", title="热歌榜")
    provider.results.append(other)
    imported = service.import_source(value=other["url"])
    service.publish(imported["id"], "热歌榜", imported["revision"])
    service.set_follow_updates(imported["id"], True)
    service.repository.record_failure("default", imported["id"], "临时失败", clock[0])
    old = copy.deepcopy(data["owner-token"]["playlists"][published["playlist_id"]])
    clock[0] += 900
    recovered = {**other, "revision": "other-recovered"}

    class RecoveryProviders:
        def fetch(self, recognized):
            if recognized["external_id"] == "26":
                return copy.deepcopy(recovered)
            raise ValueError("来源歌曲缺少歌手")

    service.providers = RecoveryProviders()

    runtime.run_due(clock[0])

    assert service.repository.get_source("default", imported["id"])["revision"] == "other-recovered"
    assert service.repository.get_source("default", source["id"])["failure_count"] == 2
    assert data["owner-token"]["playlists"][published["playlist_id"]] == old


@pytest.mark.parametrize("raised", [False, True])
def test_failed_cross_library_sync_keeps_a_durable_retry(retry_runtime, monkeypatch, raised):
    import helper.library_sharing as sharing

    _base, runtime, provider, clock, service, source, _published, _data = retry_runtime
    fail_refresh(retry_runtime)
    clock[0] += 900
    provider.results.append(chart("recovered", (2, 1)))
    real_sync = sharing.sync_qq_toplists_across_libraries

    def unavailable(*args):
        if raised:
            raise TimeoutError("临时离线")
        return {"updated": 0, "errors": [{"source_id": source["id"], "error": "TimeoutError"}]}

    monkeypatch.setattr(sharing, "sync_qq_toplists_across_libraries", unavailable)
    runtime.run_due(clock[0])

    failed = service.repository.get_source("default", source["id"])
    assert failed["failure_count"] == 2
    assert failed["next_retry_at"] == clock[0] + 3600
    monkeypatch.setattr(sharing, "sync_qq_toplists_across_libraries", real_sync)
    clock[0] += 3600
    provider.results.append(chart("recovered", (2, 1)))
    runtime.run_due(clock[0])
    assert service.repository.get_source("default", source["id"])["failure_count"] == 0
    assert runtime.engine("classic").external.repository.list_sources("classic")[0]["revision"] == "recovered"


def test_valid_source_with_offline_plex_preserves_long_term_backoff(retry_runtime):
    from helper.clients import PlexError

    _base, runtime, provider, clock, service, source, _published, _data = retry_runtime
    fail_refresh(retry_runtime)

    class OfflinePlex(service.plex_factory):
        def playlist_state(self, playlist_id):
            raise PlexError("Plex 临时离线")

    service.plex_factory = OfflinePlex
    for failures, delay in enumerate((900, 3600, 21600, 86400), start=2):
        clock[0] += delay
        provider.results.append(chart("valid"))
        runtime.run_due(clock[0])
        record = service.repository.get_source("default", source["id"])
        assert record["failure_count"] == failures
        assert record["next_retry_at"] == clock[0] + {2: 3600, 3: 21600}.get(failures, 86400)


def test_daily_slot_catchup_does_not_erase_failed_recovery_checkpoint(retry_runtime, monkeypatch):
    import helper.library_sharing as sharing

    _base, runtime, provider, clock, service, source, _published, _data = retry_runtime
    fail_refresh(retry_runtime)
    clock[0] += 900
    owner = runtime.engine("default")
    state = owner.store.get("automation_schedule_v1")
    state["tasks"]["library"].update(next_at=clock[0], slot=clock[0])
    owner.store.set("automation_schedule_v1", state)
    # Limit the unrelated full-library classification; exercise its real refresh.
    monkeypatch.setattr(owner, "refresh_new_tracks", lambda: {"external": {"refresh": service.auto_refresh()}})
    monkeypatch.setattr(sharing, "sync_qq_toplists_across_libraries", lambda *args: {
        "updated": 0, "errors": [{"source_id": source["id"], "error": "TimeoutError"}]})
    provider.results.append(chart("recovered", (2, 1)))

    runtime.run_due(clock[0])

    record = service.repository.get_source("default", source["id"])
    assert record["failure_count"] == 2
    assert record["next_retry_at"] == clock[0] + 3600
