"""Profile removal must never use a playlist title as proof of ownership."""

import pytest

from helper.clients import PlexNotFound
from helper.profile_runtime import ProfileRuntime
from helper.profiles import ProfileRegistry
from helper.scoped_store import ScopedStore
from helper.store import Store


class FakePlex:
    def __init__(self, machine="server-a"):
        self.machine = machine
        self.rows = {}
        self.fail_once = False
        self.deleted = []

    def identity(self):
        return {"machine": self.machine}

    def playlist_state(self, playlist_id):
        row = self.rows.get(str(playlist_id))
        if row is None:
            raise PlexNotFound("missing")
        return dict(row)

    def playlist_view(self, playlist_id):
        return self.playlist_state(playlist_id)

    def smart_playlist_info(self, playlist_id):
        row = self.playlist_state(playlist_id)
        return {"id": row["id"], "title": row["title"], "content": row.get("content", "")}

    def delete_playlist(self, playlist_id):
        if self.fail_once:
            self.fail_once = False
            raise ConnectionError("temporary failure")
        self.deleted.append(str(playlist_id))
        self.rows.pop(str(playlist_id), None)


@pytest.fixture
def setup(tmp_path):
    base = Store(tmp_path)
    registry = ProfileRegistry(base)
    for profile_id, section in (("friend-music", "11"), ("friend-classic", "12")):
        registry.create(
            name="朋友", kind="shared", profile_id=profile_id,
            account={"id": "friend"}, server={"machine": "server-a", "url": "http://plex"},
            library={"id": section, "name": section}, token="friend-token",
        )
    runtime = ProfileRuntime(base, registry)
    plex = FakePlex()
    for profile_id in ("friend-music", "friend-classic"):
        runtime.engine(profile_id).plex_factory = lambda _cfg, plex=plex: plex
    return runtime, plex


def add_managed(runtime, plex, profile_id, kind, key, playlist_id, marker=True):
    engine = runtime.engine(profile_id)
    section = runtime.registry.get(profile_id)["library"]["id"]
    label = {"daily": "daily", "smart": "smart:" + key, "category": key}[kind]
    plex.rows[playlist_id] = {
        "id": playlist_id, "title": "每日推荐", "summary": engine.marker(label) if marker else "",
        "items": [], "section": section,
    }
    state_key = {"daily": "daily_managed", "smart": "smart_mix_managed", "category": "managed"}[kind]
    if kind == "daily":
        engine.store.set(state_key, {"id": playlist_id, "title": "每日推荐"})
    else:
        records = dict(engine.store.get(state_key, {}) or {})
        records[key] = {"id": playlist_id, "title": "每日推荐"}
        engine.store.set(state_key, records)


def test_removal_deletes_only_verified_profile_playlists(setup):
    from helper.profile_cleanup import begin_profile_removal, resume_profile_removal

    runtime, plex = setup
    add_managed(runtime, plex, "friend-music", "daily", "daily", "101")
    add_managed(runtime, plex, "friend-music", "smart", "weekly", "102")
    add_managed(runtime, plex, "friend-music", "category", "work", "103")
    add_managed(runtime, plex, "friend-classic", "daily", "daily", "201")
    plex.rows["999"] = {"id": "999", "title": "每日推荐", "summary": "", "items": []}

    begin_profile_removal(runtime, "friend-music")
    assert runtime.registry.get("friend-music")["enabled"] is False
    with pytest.raises(ValueError):
        runtime.registry.select("friend-music")
    result = resume_profile_removal(runtime, "friend-music")
    assert result["status"] == "removed"
    assert set(plex.deleted) == {"101", "102", "103"}
    assert "999" in plex.rows and "201" in plex.rows
    assert runtime.registry.get("friend-classic")["enabled"] is True
    with pytest.raises(ValueError):
        runtime.registry.get("friend-music")


def test_failure_and_restart_keep_profile_for_retry(setup):
    from helper.profile_cleanup import begin_profile_removal, resume_profile_removal

    runtime, plex = setup
    add_managed(runtime, plex, "friend-music", "daily", "daily", "101")
    begin_profile_removal(runtime, "friend-music")
    plex.fail_once = True
    first = resume_profile_removal(runtime, "friend-music")
    assert first["status"] == "needs_attention"
    assert runtime.registry.get("friend-music")["id"] == "friend-music"
    retry = begin_profile_removal(runtime, "friend-music")
    assert retry["status"] == "pending" and retry["next_retry_at"] == 0
    restarted = ProfileRuntime(runtime.base_store, runtime.registry)
    restarted.engine("friend-music").plex_factory = lambda _cfg: plex
    assert resume_profile_removal(restarted, "friend-music")["status"] == "removed"


def test_missing_marker_pauses_without_deleting_native_same_title(setup):
    from helper.profile_cleanup import begin_profile_removal, resume_profile_removal

    runtime, plex = setup
    add_managed(runtime, plex, "friend-music", "daily", "daily", "101", marker=False)
    begin_profile_removal(runtime, "friend-music")
    result = resume_profile_removal(runtime, "friend-music")
    assert result["status"] == "needs_attention"
    assert "101" in plex.rows and plex.deleted == []
    assert runtime.registry.get("friend-music")["enabled"] is False


def test_already_absent_id_is_confirmed_without_delete(setup):
    from helper.profile_cleanup import begin_profile_removal, resume_profile_removal

    runtime, plex = setup
    add_managed(runtime, plex, "friend-music", "daily", "daily", "101")
    plex.rows.pop("101")
    begin_profile_removal(runtime, "friend-music")
    assert resume_profile_removal(runtime, "friend-music")["status"] == "removed"
    assert plex.deleted == []


def test_identity_cannot_be_readded_during_removal(setup):
    from helper.profile_cleanup import begin_profile_removal

    runtime, _ = setup
    begin_profile_removal(runtime, "friend-music")
    with pytest.raises(ValueError):
        runtime.registry.create_for_library("friend-classic", {"id": "11", "name": "音乐"})


def test_favorite_rule_must_match_before_deletion(setup):
    from helper.profile_cleanup import begin_profile_removal, resume_profile_removal

    runtime, plex = setup
    store = runtime.engine("friend-music").store
    store.set("favorite_smart_v2", {"playlist_id": "150", "section": "11", "status": "synced"})
    plex.rows["150"] = {"id": "150", "title": "我喜欢", "summary": "",
                        "content": "server://server-a/com.plexapp.plugins.library/library/sections/11/all?type=10&track.userRating%3E=8"}
    begin_profile_removal(runtime, "friend-music")
    assert resume_profile_removal(runtime, "friend-music")["status"] == "removed"
    assert plex.deleted == ["150"]


def test_favorite_with_unknown_rule_is_retained(setup):
    from helper.profile_cleanup import begin_profile_removal, resume_profile_removal

    runtime, plex = setup
    runtime.engine("friend-music").store.set(
        "favorite_smart_v2", {"playlist_id": "150", "section": "11", "status": "synced"})
    plex.rows["150"] = {"id": "150", "title": "我喜欢", "summary": "",
                        "content": "server://server-a/com.plexapp.plugins.library/library/sections/11/all?type=10"}
    begin_profile_removal(runtime, "friend-music")
    assert resume_profile_removal(runtime, "friend-music")["status"] == "needs_attention"
    assert plex.deleted == []


def test_restart_after_remote_checkpoint_finishes_local_bookkeeping(setup):
    from helper.profile_cleanup import STATE_KEY, begin_profile_removal, resume_profile_removal

    runtime, plex = setup
    add_managed(runtime, plex, "friend-music", "daily", "daily", "101")
    state = begin_profile_removal(runtime, "friend-music")
    plex.rows.pop("101")
    state["completed"] = ["daily:daily:101"]
    runtime.engine("friend-music").store.set(STATE_KEY, state)
    assert resume_profile_removal(runtime, "friend-music")["status"] == "removed"


def test_scheduler_resumes_removal_without_refreshing_user(setup):
    from helper.profile_cleanup import begin_profile_removal

    runtime, plex = setup
    add_managed(runtime, plex, "friend-music", "daily", "daily", "101")
    begin_profile_removal(runtime, "friend-music")
    runtime.run_due()
    with pytest.raises(ValueError):
        runtime.registry.get("friend-music")


def test_connection_factory_failure_stays_retryable(setup):
    from helper.profile_cleanup import begin_profile_removal, resume_profile_removal

    runtime, plex = setup
    add_managed(runtime, plex, "friend-music", "daily", "daily", "101")
    begin_profile_removal(runtime, "friend-music")
    runtime.engine("friend-music").plex_factory = lambda _cfg: (_ for _ in ()).throw(ConnectionError("offline"))
    result = resume_profile_removal(runtime, "friend-music")
    assert result["status"] == "needs_attention"
    assert runtime.registry.get("friend-music")["enabled"] is False


def test_external_imported_playlist_is_removed_with_its_source_record(setup):
    from helper.external_playlist_sync import external_marker
    from helper.external_store import ExternalRepository
    from helper.profile_cleanup import begin_profile_removal, resume_profile_removal

    runtime, plex = setup
    repo = ExternalRepository(runtime.base_store)
    source = repo.upsert_source("friend-music", {
        "provider": "qq", "external_id": "123", "url": "https://y.qq.com/n/ryqq/playlist/123",
        "title": "导入歌单", "revision": "one", "tracks": [{
            "source_track_key": "song-1", "position": 0, "source_track_id": "song-1",
            "title": "歌曲", "artists": ["歌手"],
        }],
    }, 1)
    repo.save_managed("friend-music", source["id"], {"id": "180", "title": "导入歌单"})
    plex.rows["180"] = {"id": "180", "title": "导入歌单", "items": [],
                        "summary": external_marker(runtime.base_store.get("installation_id"), source["id"])}
    begin_profile_removal(runtime, "friend-music")
    assert resume_profile_removal(runtime, "friend-music")["status"] == "removed"
    assert plex.deleted == ["180"]
    assert repo.list_sources("friend-music") == []
