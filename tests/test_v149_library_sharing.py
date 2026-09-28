"""Default same-library category sharing without a recipient QQ login."""
import copy
import tempfile
from pathlib import Path

import pytest


class FakePlex:
    def __init__(self, token, data):
        self.token = token
        self.data = data.setdefault(token, {"playlists": {}, "next": 100})

    def identity(self):
        return {"machine": "server-a"}

    def sections(self):
        return [{"id": "11", "title": "音乐"}]

    def tracks(self, section):
        assert str(section) == "11"
        return [{"id": value} for value in ("1", "2", "3")]

    def playlists(self):
        return [
            {"ratingKey": row["id"], "title": row["title"], "summary": row["summary"]}
            for row in self.data["playlists"].values()
        ]

    def playlist_state(self, playlist_id):
        from helper.clients import PlexNotFound

        try:
            return copy.deepcopy(self.data["playlists"][str(playlist_id)])
        except KeyError:
            raise PlexNotFound("Plex 中没有这个项目") from None

    def create(self, title, ids, marker, description=None):
        playlist_id = str(self.data["next"])
        self.data["next"] += 1
        row = {
            "id": playlist_id, "title": title,
            "summary": marker + ("\n" + description if description else ""),
            "items": [{"id": str(value), "item_id": f"{playlist_id}{index+1}"} for index, value in enumerate(ids)],
        }
        self.data["playlists"][playlist_id] = row
        return copy.deepcopy(row)

    def append(self, playlist_id, ids):
        row = self.data["playlists"][str(playlist_id)]
        start = len(row["items"])
        row["items"].extend(
            {"id": str(value), "item_id": f"{playlist_id}{start + index + 1}"}
            for index, value in enumerate(ids)
        )

    def delete_playlist(self, playlist_id):
        del self.data["playlists"][str(playlist_id)]

    def rename(self, playlist_id, title):
        self.data["playlists"][str(playlist_id)]["title"] = title

    def update_playlist_summary(self, playlist_id, summary):
        self.data["playlists"][str(playlist_id)]["summary"] = str(summary)

    def owned_playlists(self, marker):
        return [row for row in self.playlists()
                if marker in str(row.get("summary") or "").splitlines()]

    def remove_items(self, playlist_id, item_ids):
        row = self.data["playlists"][str(playlist_id)]
        removed = set(map(str, item_ids))
        row["items"] = [item for item in row["items"] if item["item_id"] not in removed]


@pytest.fixture
def shared_library():
    from helper.engine import fingerprint
    from helper.profile_runtime import ProfileRuntime
    from helper.profiles import ProfileRegistry
    from helper.scoped_store import ScopedStore
    from helper.store import Store

    with tempfile.TemporaryDirectory() as root:
        base = Store(Path(root))
        registry = ProfileRegistry(base)
        registry.update(
            "default", kind="owner", account={"id": "owner"},
            server={"machine": "server-a", "url": "http://plex"},
            library={"id": "11", "name": "音乐"}, token="owner-token",
        )
        registry.refresh_access("default", "owner-token")
        registry.create(
            name="朋友", kind="shared", profile_id="friend", account={"id": "friend"},
            server={"machine": "server-a", "url": "http://plex"},
            library={"id": "11", "name": "音乐"}, token="friend-token",
        )
        data = {}
        runtime = ProfileRuntime(base, registry)
        from helper.automation import save_automation_settings
        save_automation_settings(base, {
            "daily": {"enabled": True, "hour": 6},
            "smart": {"enabled": False, "hour": 3, "interval_days": 7},
            "library": {"enabled": True, "hour": 0},
        })
        for profile_id in ("default", "friend"):
            engine = runtime.engine(profile_id)
            engine.plex_factory = lambda cfg, data=data: FakePlex(cfg["plex_token"], data)
        owner = ScopedStore(base, "default")
        source = FakePlex("owner-token", data).create(
            "流行精选", ["1", "2"], runtime.engine("default").marker("qq:pop"),
        )
        owner.set("managed", {"qq:pop": {
            "id": source["id"], "title": source["title"],
            "fingerprint": fingerprint(source), "count": 2,
        }})
        yield base, registry, runtime, data


def test_owner_categories_default_to_recipient_without_qq_login(shared_library):
    from helper.library_sharing import sync_recipient
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    result = sync_recipient(runtime, "default", "friend")
    child = ScopedStore(base, "friend")
    record = child.get("managed")["qq:pop"]
    copied = data["friend-token"]["playlists"][record["id"]]
    assert result["created"] == 1
    assert [item["id"] for item in copied["items"]] == ["1", "2"]
    assert record["shared_from"] == "default"
    assert child.get("qq_auth_credentials") is None


def test_owner_qq_toplist_is_copied_to_every_same_library_recipient(shared_library):
    from helper.engine import fingerprint
    from helper.external_playlist_sync import external_marker
    from helper.external_sources import make_track
    from helper.external_store import ExternalRepository
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    owner = ScopedStore(base, "default")
    repository = ExternalRepository(owner)
    source = repository.upsert_source("default", {
        "provider": "qq", "external_id": "62",
        "url": "https://y.qq.com/n/ryqq/toplist/62",
        "title": "飙升榜", "revision": "chart-r1",
        "tracks": [
            make_track(0, "歌曲一", ["歌手甲"], source_id="qq-1"),
            make_track(1, "歌曲二", ["歌手乙"], source_id="qq-2"),
        ],
    }, 2_000_000_000)
    marker = external_marker(owner.get("installation_id"), source["id"])
    playlist = FakePlex("owner-token", data).create("QQ飙升榜", ["1", "2"], marker)
    repository.save_managed("default", source["id"], {
        "id": playlist["id"], "title": playlist["title"],
        "fingerprint": fingerprint(playlist), "count": 2, "marker": marker,
    })

    runtime.sync_library_shares_due(now=2_000_000_100)

    category_id = "external:" + source["id"]
    child = ScopedStore(base, "friend")
    record = child.get("managed")[category_id]
    copied = data["friend-token"]["playlists"][record["id"]]
    assert len(data["friend-token"]["playlists"]) == 2
    assert copied["title"] == "QQ飙升榜"
    assert [item["id"] for item in copied["items"]] == ["1", "2"]


def test_owner_ordinary_qq_import_stays_personal(shared_library):
    from helper.engine import fingerprint
    from helper.external_playlist_sync import external_marker
    from helper.external_sources import make_track
    from helper.external_store import ExternalRepository
    from helper.library_sharing import sync_recipient
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    owner = ScopedStore(base, "default")
    repository = ExternalRepository(owner)
    source = repository.upsert_source("default", {
        "provider": "qq", "external_id": "personal-123",
        "url": "https://y.qq.com/n/ryqq/playlist/123",
        "title": "私人导入歌单", "revision": "personal-r1",
        "tracks": [make_track(0, "歌曲一", ["歌手甲"], source_id="qq-1")],
    }, 2_000_000_000)
    marker = external_marker(owner.get("installation_id"), source["id"])
    playlist = FakePlex("owner-token", data).create("私人导入歌单", ["1"], marker)
    repository.save_managed("default", source["id"], {
        "id": playlist["id"], "title": playlist["title"],
        "fingerprint": fingerprint(playlist), "count": 1, "marker": marker,
    })

    sync_recipient(runtime, "default", "friend")

    child = ScopedStore(base, "friend")
    assert "external:" + source["id"] not in child.get("managed")


def test_disabled_owner_category_is_handed_off_without_creating_a_recipient_copy(shared_library):
    from helper.library_sharing import sync_recipient
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    ScopedStore(base, "default").set("sources", [
        {"id": "qq:pop", "name": "流行精选", "enabled": False},
    ])

    result = sync_recipient(runtime, "default", "friend")

    assert result["created"] == 0
    assert len(data["friend-token"]["playlists"]) == 0


def test_disabling_owner_category_leaves_an_existing_recipient_copy_untouched(shared_library):
    from helper.library_sharing import sync_recipient
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    sync_recipient(runtime, "default", "friend")
    child = ScopedStore(base, "friend")
    playlist_id = child.get("managed")["qq:pop"]["id"]
    data["friend-token"]["playlists"][playlist_id]["title"] = "朋友暂时改名"
    ScopedStore(base, "default").set("sources", [
        {"id": "qq:pop", "name": "流行精选", "enabled": False},
    ])

    result = sync_recipient(runtime, "default", "friend")

    assert result["updated"] == 0
    assert data["friend-token"]["playlists"][playlist_id]["title"] == "朋友暂时改名"


def test_confirmed_recipient_deletion_recreates_the_owner_copy(shared_library):
    from helper.library_sharing import sync_recipient
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    sync_recipient(runtime, "default", "friend")
    child = ScopedStore(base, "friend")
    playlist_id = child.get("managed")["qq:pop"]["id"]
    del data["friend-token"]["playlists"][playlist_id]

    first = sync_recipient(runtime, "default", "friend")
    second = sync_recipient(runtime, "default", "friend")

    assert first["created"] == 1
    assert second["created"] == 0
    assert "qq:pop" in child.get("managed")
    assert len(data["friend-token"]["playlists"]) == 1


def test_legacy_exclusion_is_not_a_recipient_switch(shared_library):
    from helper.library_sharing import sync_recipient
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    child = ScopedStore(base, "friend")
    child.set("library_share_v1", {"owner_id": "default", "excluded": ["qq:pop"]})
    assert sync_recipient(runtime, "default", "friend")["created"] == 1
    assert len(data["friend-token"]["playlists"]) == 1


def test_owner_removing_category_removes_only_unchanged_recipient_copy(shared_library):
    from helper.library_sharing import sync_recipient
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    sync_recipient(runtime, "default", "friend")
    child = ScopedStore(base, "friend")
    playlist_id = child.get("managed")["qq:pop"]["id"]
    ScopedStore(base, "default").set("managed", {})

    result = sync_recipient(runtime, "default", "friend")

    assert result["removed"] == 1
    assert playlist_id not in data["friend-token"]["playlists"]
    assert "qq:pop" not in child.get("managed")


def test_owner_removal_deletes_recipient_edited_copy_if_marker_is_intact(shared_library):
    from helper.library_sharing import sync_recipient
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    sync_recipient(runtime, "default", "friend")
    child = ScopedStore(base, "friend")
    playlist_id = child.get("managed")["qq:pop"]["id"]
    data["friend-token"]["playlists"][playlist_id]["title"] = "自己整理的歌单"
    ScopedStore(base, "default").set("managed", {})

    result = sync_recipient(runtime, "default", "friend")

    assert result["removed"] == 1
    assert playlist_id not in data["friend-token"]["playlists"]


def test_owner_delete_and_same_title_rebuild_never_touch_native_playlist(shared_library):
    from helper.engine import fingerprint
    from helper.library_sharing import confirm_owner_revision, queue_owner_revision, sync_recipient
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    sync_recipient(runtime, "default", "friend")
    native = FakePlex("friend-token", data).create("流行精选", ["3"], "")
    owner = ScopedStore(base, "default")
    old = owner.get("managed")["qq:pop"]
    queue_owner_revision(runtime, "default", "qq:pop", "delete", old)
    FakePlex("owner-token", data).delete_playlist(old["id"])
    owner.set("managed", {})
    confirm_owner_revision(runtime, "default", "qq:pop")
    result = sync_recipient(runtime, "default", "friend")
    assert result["removed"] == 1
    assert native["id"] in data["friend-token"]["playlists"]
    assert len(data["friend-token"]["playlists"]) == 1

    rebuilt = FakePlex("owner-token", data).create(
        "流行精选", ["1", "3"], runtime.engine("default").marker("qq:pop"))
    owner.set("managed", {"qq:pop": {"id": rebuilt["id"], "title": rebuilt["title"],
                                     "fingerprint": fingerprint(rebuilt), "count": 2}})
    queue_owner_revision(runtime, "default", "qq:pop", "publish", owner.get("managed")["qq:pop"])
    result = sync_recipient(runtime, "default", "friend")
    assert result["created"] == 0
    assert native["id"] in data["friend-token"]["playlists"]


def test_two_libraries_and_two_recipients_keep_independent_profile_lifecycles(shared_library):
    from helper.profile_cleanup import begin_profile_removal, resume_profile_removal
    from helper.profile_controls import read_controls
    from helper.profile_runtime import ProfileRuntime
    from helper.profiles import ProfileRegistry
    from helper.library_sharing import sync_recipient
    from helper.scoped_store import ScopedStore
    from helper.store import Store

    base, registry, runtime, data = shared_library
    registry.create(name="主账户经典", kind="owner", profile_id="owner-classic",
                    account={"id": "owner"}, server={"machine": "server-a", "url": "http://plex"},
                    library={"id": "12", "name": "经典音乐"}, token="owner-classic-token")
    for profile_id, account, section, token in (
        ("friend-two", "other", "11", "other-token"),
        ("friend-classic", "friend", "12", "classic-token"),
    ):
        registry.create(name=profile_id, kind="shared", profile_id=profile_id,
                        account={"id": account}, server={"machine": "server-a", "url": "http://plex"},
                        library={"id": section, "name": "音乐" if section == "11" else "经典音乐"},
                        token=token)
        assert read_controls(ScopedStore(base, profile_id)) == {
            "learning": True, "daily": True, "smart": True,
        }
        runtime.engine(profile_id).plex_factory = lambda cfg, data=data: FakePlex(cfg["plex_token"], data)
    assert sync_recipient(runtime, "default", "friend")["created"] == 1
    assert sync_recipient(runtime, "default", "friend-two")["created"] == 1

    classic = runtime.engine("friend-classic")
    owned = FakePlex("classic-token", data).create("每日推荐", ["1"], classic.marker("daily"))
    classic.store.set("daily_managed", {"id": owned["id"], "title": owned["title"]})
    native = FakePlex("classic-token", data).create("每日推荐", ["2"], "")
    begin_profile_removal(runtime, "friend-classic")

    # Restart against the same database before carrying out the pending remote cleanup.
    restarted_base = Store(base.root)
    restarted_registry = ProfileRegistry(restarted_base)
    restarted = ProfileRuntime(restarted_base, restarted_registry)
    restarted.engine("friend-classic").plex_factory = lambda cfg: FakePlex(cfg["plex_token"], data)
    assert resume_profile_removal(restarted, "friend-classic")["status"] == "removed"
    assert native["id"] in data["classic-token"]["playlists"]
    assert owned["id"] not in data["classic-token"]["playlists"]
    assert len(data["friend-token"]["playlists"]) == 1
    assert len(data["other-token"]["playlists"]) == 1
    assert restarted_registry.get("owner-classic")["enabled"] is True


def test_other_library_is_never_written(shared_library):
    from helper.library_sharing import sync_recipient

    _base, registry, runtime, data = shared_library
    registry.update("friend", library={"id": "22", "name": "其他曲库"})
    with pytest.raises(ValueError, match="同一曲库"):
        sync_recipient(runtime, "default", "friend")
    assert "friend-token" not in data


def test_owner_update_reconciles_recipient_edited_copy_with_marker(shared_library):
    from helper.library_sharing import sync_recipient
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    sync_recipient(runtime, "default", "friend")
    child = ScopedStore(base, "friend")
    playlist_id = child.get("managed")["qq:pop"]["id"]
    data["friend-token"]["playlists"][playlist_id]["title"] = "我自己改的"
    data["friend-token"]["playlists"][playlist_id]["summary"] = "人工说明"
    data["friend-token"]["playlists"][playlist_id]["items"] = [
        {"id": "1", "item_id": "1001"},
        {"id": "3", "item_id": "1003"},
    ]
    owner = data["owner-token"]["playlists"]["100"]
    owner["items"].append({"id": "3", "item_id": "100-2"})
    from helper.engine import fingerprint
    managed = child.get("managed")
    source = ScopedStore(base, "default").get("managed")
    source["qq:pop"]["fingerprint"] = fingerprint(owner)
    ScopedStore(base, "default").set("managed", source)

    result = sync_recipient(runtime, "default", "friend")

    assert result["updated"] == 1
    copied_ids = [row["id"] for row in data["friend-token"]["playlists"][playlist_id]["items"]]
    assert len(copied_ids) == 3
    assert set(copied_ids) == {"1", "2", "3"}
    assert data["friend-token"]["playlists"][playlist_id]["title"] == "流行精选"
    assert data["friend-token"]["playlists"][playlist_id]["summary"].splitlines()[0] == runtime.engine("friend").marker("qq:pop")
    assert managed != child.get("managed")


def test_transient_recipient_read_does_not_create_a_duplicate(shared_library):
    from helper.clients import PlexError
    from helper.library_sharing import sync_recipient
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    sync_recipient(runtime, "default", "friend")
    child = ScopedStore(base, "friend")
    playlist_id = child.get("managed")["qq:pop"]["id"]

    class ReadTimeoutPlex(FakePlex):
        def playlist_state(self, candidate):
            if str(candidate) == playlist_id:
                raise PlexError("temporary read timeout")
            return super().playlist_state(candidate)

    runtime.engine("friend").plex_factory = lambda cfg: ReadTimeoutPlex(cfg["plex_token"], data)
    result = sync_recipient(runtime, "default", "friend")

    assert result["created"] == 0
    assert result["errors"]
    assert result["retryable_errors"]
    assert result["conflicts"] == []
    assert len(data["friend-token"]["playlists"]) == 1


def test_scheduler_retries_transient_share_failure_without_consuming_revision(shared_library):
    from helper.clients import PlexError
    from helper.library_sharing import STATE_KEY, sync_recipient
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    sync_recipient(runtime, "default", "friend")
    child = ScopedStore(base, "friend")
    playlist_id = child.get("managed")["qq:pop"]["id"]
    data["friend-token"]["playlists"][playlist_id]["title"] = "等待恢复"

    class ReadTimeoutPlex(FakePlex):
        def playlist_state(self, candidate):
            if str(candidate) == playlist_id:
                raise PlexError("temporary read timeout")
            return super().playlist_state(candidate)

    runtime.engine("friend").plex_factory = lambda cfg: ReadTimeoutPlex(cfg["plex_token"], data)
    runtime.sync_library_shares_due(now=1000)
    waiting = child.get(STATE_KEY)

    assert waiting["status"] == "waiting_retry"
    assert waiting["retry_at"] == 1300
    assert "owner_digest" not in waiting

    runtime.engine("friend").plex_factory = lambda cfg: FakePlex(cfg["plex_token"], data)
    runtime.sync_library_shares_due(now=1200)
    assert data["friend-token"]["playlists"][playlist_id]["title"] == "等待恢复"
    runtime.sync_library_shares_due(now=1300)

    completed = child.get(STATE_KEY)
    assert completed["status"] == "normal"
    assert completed.get("retry_at") is None
    assert data["friend-token"]["playlists"][playlist_id]["title"] == "流行精选"


def test_scheduler_marks_ownership_conflict_for_attention_without_retry(shared_library):
    from helper.library_sharing import STATE_KEY
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    FakePlex("friend-token", data).create("流行精选", ["3"], "个人说明")

    runtime.sync_library_shares_due(now=1000)

    state = ScopedStore(base, "friend").get(STATE_KEY)
    assert state["status"] == "needs_attention"
    assert state.get("retry_at") is None
    assert state["last_result"]["conflicts"]


def test_recipient_sync_never_recalculates_owner_categories(shared_library):
    from helper.library_sharing import sync_recipient

    _base, _registry, runtime, _data = shared_library
    owner = runtime.engine("default")
    owner._preview = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("recipient sync must not run QQ/category discovery")
    )
    owner._preview_base = owner._preview

    assert sync_recipient(runtime, "default", "friend")["created"] == 1


def test_deleting_recipient_copy_from_app_does_not_opt_out(shared_library):
    from helper.library_sharing import sync_recipient
    from helper.playlist_hub import remove_playlist
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    sync_recipient(runtime, "default", "friend")
    child = ScopedStore(base, "friend")

    remove_playlist(runtime.engine("friend"), "category", "qq:pop", "流行精选")
    result = sync_recipient(runtime, "default", "friend")

    assert result["created"] == 1
    assert "qq:pop" not in (child.get("library_share_v1") or {}).get("excluded", [])
    assert len(data["friend-token"]["playlists"]) == 1


def test_scheduler_propagates_owner_categories_without_separate_automation(shared_library):
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    runtime.run_due(now=1000)

    child = ScopedStore(base, "friend")
    assert child.get("managed")["qq:pop"]["shared_from"] == "default"
    assert len(data["friend-token"]["playlists"]) == 1
    runtime.run_due(now=1060)
    assert len(data["friend-token"]["playlists"]) == 1


def test_global_library_switch_off_hands_shared_copies_back_to_plex(shared_library):
    from helper.automation import save_automation_settings

    base, _registry, runtime, data = shared_library
    save_automation_settings(base, {
        "daily": {"enabled": True, "hour": 6},
        "smart": {"enabled": False, "hour": 3, "interval_days": 7},
        "library": {"enabled": False, "hour": 0},
    })

    runtime.run_due(now=1000)

    assert data.get("friend-token", {}).get("playlists", {}) == {}


def test_scheduler_propagates_owner_category_removal(shared_library):
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    runtime.run_due(now=1000)
    ScopedStore(base, "default").set("managed", {})

    runtime.run_due(now=1060)

    assert not data["friend-token"]["playlists"]
    assert not ScopedStore(base, "friend").get("managed")


def test_shared_categories_do_not_start_a_second_qq_scan(shared_library):
    from helper.automation import PROFILE_STATE_KEY, save_automation_settings
    from helper.library_sharing import sync_recipient
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, _data = shared_library
    sync_recipient(runtime, "default", "friend")
    saved = save_automation_settings(base, {
        "daily": {"enabled": False, "hour": 6},
        "smart": {"enabled": False, "hour": 3, "interval_days": 7},
        "library": {"enabled": True, "hour": 0},
    })
    friend = ScopedStore(base, "friend")
    friend.set(PROFILE_STATE_KEY, {"revision": saved["revision"], "tasks": {
        "library": {"config": saved["library"], "next_at": 1000, "slot": 1000},
    }})
    result = runtime.run_due(now=1000)

    assert not any(row["profile_id"] == "friend" and row["kind"] == "library" for row in result)


def test_selected_recipient_has_a_simple_sync_status_route(shared_library):
    from helper.web import create_app

    base, _registry, _runtime, _data = shared_library
    app = create_app(store=base, start_scheduler=False)
    route = next(row for row in app.routes if getattr(row, "path", None) == "/api/library-share/status")
    with app.state.profiles.fixed_active("friend"):
        response = route.endpoint()

    assert response["recipient"] is True
    assert response["items"] == [{"id": "qq:pop", "title": "流行精选", "status": "等待同步"}]


def test_library_settings_has_a_dedicated_recipient_sync_panel():
    from html.parser import HTMLParser

    class ElementIds(HTMLParser):
        def __init__(self):
            super().__init__()
            self.ids = set()

        def handle_starttag(self, tag, attrs):
            for key, value in attrs:
                if key == "id":
                    self.ids.add(value)

    page = ElementIds()
    page.feed((Path(__file__).resolve().parents[1] / "src/helper/static/home.html").read_text())
    assert {"librarySharePanel", "libraryShareRows", "libraryShareMessage"} <= page.ids


def test_synced_playlist_uses_recipient_plex_rating_for_heart(shared_library):
    from helper.library_sharing import sync_recipient
    from helper.playlist_hub import playlist_detail
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    sync_recipient(runtime, "default", "friend")
    child = ScopedStore(base, "friend")
    playlist_id = child.get("managed")["qq:pop"]["id"]
    data["friend-token"]["playlists"][playlist_id]["items"][0]["user_rating"] = 10

    detail = playlist_detail(runtime.engine("friend"), "category", "qq:pop")

    assert detail["tracks"][0]["liked"] is True
    assert detail["tracks"][1]["liked"] is False


def test_forgetting_confirmed_missing_recipient_copy_allows_recreation(shared_library):
    from helper.library_sharing import sync_recipient
    from helper.managed_cleanup_v0317 import forget_missing_managed_playlist
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    sync_recipient(runtime, "default", "friend")
    child = ScopedStore(base, "friend")
    playlist_id = child.get("managed")["qq:pop"]["id"]
    del data["friend-token"]["playlists"][playlist_id]

    forget_missing_managed_playlist(runtime.engine("friend"), "qq:pop", playlist_id, "流行精选")
    assert "qq:pop" not in (child.get("library_share_v1") or {}).get("excluded", [])
    result = sync_recipient(runtime, "default", "friend")

    assert result["created"] == 1
    assert "qq:pop" not in (child.get("library_share_v1") or {}).get("excluded", [])


def test_owner_additions_append_to_recipient_without_recreating(shared_library):
    from helper.engine import fingerprint
    from helper.library_sharing import sync_recipient
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    sync_recipient(runtime, "default", "friend")
    child = ScopedStore(base, "friend")
    recipient_id = child.get("managed")["qq:pop"]["id"]
    owner = data["owner-token"]["playlists"]["100"]
    owner["items"].append({"id": "3", "item_id": "100-2"})
    source = ScopedStore(base, "default").get("managed")
    source["qq:pop"]["fingerprint"] = fingerprint(owner)
    ScopedStore(base, "default").set("managed", source)

    result = sync_recipient(runtime, "default", "friend")

    assert result["updated"] == 1
    assert child.get("managed")["qq:pop"]["id"] == recipient_id
    assert [row["id"] for row in data["friend-token"]["playlists"][recipient_id]["items"]] == ["1", "2", "3"]


def test_share_status_exposes_protected_skip_to_admin(shared_library):
    from helper.library_sharing import share_status, sync_recipient
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    sync_recipient(runtime, "default", "friend")
    child = ScopedStore(base, "friend")
    recipient_id = child.get("managed")["qq:pop"]["id"]
    data["friend-token"]["playlists"][recipient_id]["title"] = "手工修改"

    runtime.run_due(now=1000)

    assert share_status(runtime, "friend")["last_result"]["updated"] == 1


def test_owner_removal_is_reflected_without_deleting_recipient_playlist(shared_library):
    from helper.engine import fingerprint
    from helper.library_sharing import sync_recipient
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    sync_recipient(runtime, "default", "friend")
    child = ScopedStore(base, "friend")
    playlist_id = child.get("managed")["qq:pop"]["id"]
    owner = data["owner-token"]["playlists"]["100"]
    owner["items"] = owner["items"][:1]
    source = ScopedStore(base, "default").get("managed")
    source["qq:pop"]["fingerprint"] = fingerprint(owner)
    ScopedStore(base, "default").set("managed", source)

    result = sync_recipient(runtime, "default", "friend")

    assert result["updated"] == 1
    assert playlist_id in data["friend-token"]["playlists"]
    assert [row["id"] for row in data["friend-token"]["playlists"][playlist_id]["items"]] == ["1"]


def test_delete_revision_survives_source_removal_and_same_title_rebuild(shared_library):
    from helper.engine import fingerprint
    from helper.library_sharing import confirm_owner_revision, queue_owner_revision, sync_recipient
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    sync_recipient(runtime, "default", "friend")
    child = ScopedStore(base, "friend")
    old_copy = child.get("managed")["qq:pop"]["id"]
    owner = ScopedStore(base, "default")
    old_source = owner.get("managed")["qq:pop"]
    queue_owner_revision(runtime, "default", "qq:pop", "delete", old_source)
    del data["owner-token"]["playlists"][old_source["id"]]
    owner.set("managed", {})
    confirm_owner_revision(runtime, "default", "qq:pop")
    replacement = FakePlex("owner-token", data).create(
        "流行精选", ["2"], runtime.engine("default").marker("qq:pop"),
    )
    new_source = {"id": replacement["id"], "title": replacement["title"],
                  "fingerprint": fingerprint(replacement), "count": 1}
    owner.set("managed", {"qq:pop": new_source})
    queue_owner_revision(runtime, "default", "qq:pop", "publish", new_source)

    result = sync_recipient(runtime, "default", "friend")

    assert result["errors"] == []
    assert old_copy not in data["friend-token"]["playlists"]
    assert len(data["friend-token"]["playlists"]) == 1
    assert [row["id"] for row in next(iter(data["friend-token"]["playlists"].values()))["items"]] == ["2"]


def test_missing_recipient_marker_pauses_deletion_without_touching_playlist(shared_library):
    from helper.library_sharing import sync_recipient
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    sync_recipient(runtime, "default", "friend")
    child = ScopedStore(base, "friend")
    copy_id = child.get("managed")["qq:pop"]["id"]
    data["friend-token"]["playlists"][copy_id]["summary"] = ""
    owner = ScopedStore(base, "default")
    owner.set("managed", {})

    result = sync_recipient(runtime, "default", "friend")

    assert result["skipped"] == 1
    assert copy_id in data["friend-token"]["playlists"]
    assert child.get("managed")["qq:pop"]["id"] == copy_id


def test_same_library_second_owner_profile_receives_categories(shared_library):
    from helper.library_sharing import sync_recipient

    _base, registry, runtime, data = shared_library
    registry.create(
        name="其他档案", kind="owner", profile_id="second-owner", token="second-owner-token",
        account={"id": "other"}, server={"machine": "server-a", "url": "http://plex"},
        library={"id": "11", "name": "音乐"},
    )
    runtime.engine("second-owner").plex_factory = lambda cfg: FakePlex(cfg["plex_token"], data)

    result = sync_recipient(runtime, "default", "second-owner")

    assert result["created"] == 1
    assert len(data["second-owner-token"]["playlists"]) == 1


def test_prepared_owner_delete_recovers_after_restart_without_old_copy(shared_library):
    from helper.library_sharing import queue_owner_revision, sync_recipient
    from helper.scoped_store import ScopedStore

    base, registry, runtime, data = shared_library
    sync_recipient(runtime, "default", "friend")
    owner = ScopedStore(base, "default")
    old = owner.get("managed")["qq:pop"]
    queue_owner_revision(runtime, "default", "qq:pop", "delete", old)
    del data["owner-token"]["playlists"][old["id"]]
    owner.set("managed", {})
    resumed = type(runtime)(base, registry)
    for profile_id in ("default", "friend"):
        resumed.engine(profile_id).plex_factory = lambda cfg, data=data: FakePlex(cfg["plex_token"], data)

    resumed.sync_library_shares_due(now=1000)

    assert not data["friend-token"]["playlists"]
    assert not ScopedStore(base, "friend").get("managed")


def test_unconfirmed_recipient_delete_blocks_same_title_recreation(shared_library):
    from helper.engine import fingerprint
    from helper.library_sharing import confirm_owner_revision, queue_owner_revision, sync_recipient
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    sync_recipient(runtime, "default", "friend")
    owner = ScopedStore(base, "default")
    old = owner.get("managed")["qq:pop"]
    queue_owner_revision(runtime, "default", "qq:pop", "delete", old)
    del data["owner-token"]["playlists"][old["id"]]
    owner.set("managed", {})
    confirm_owner_revision(runtime, "default", "qq:pop")
    new = FakePlex("owner-token", data).create("流行精选", ["3"], runtime.engine("default").marker("qq:pop"))
    record = {"id": new["id"], "title": new["title"], "fingerprint": fingerprint(new)}
    owner.set("managed", {"qq:pop": record})
    queue_owner_revision(runtime, "default", "qq:pop", "publish", record)
    child = runtime.engine("friend").plex_factory({"plex_token": "friend-token"})
    original_delete = child.delete_playlist

    def stale_delete(_playlist_id):
        # Plex accepted the request but still returns the old ID on direct reads.
        return None

    child.delete_playlist = stale_delete
    runtime.engine("friend").plex_factory = lambda _cfg: child
    result = sync_recipient(runtime, "default", "friend")

    assert result["errors"]
    assert len(data["friend-token"]["playlists"]) == 1
    child.delete_playlist = original_delete


def test_owner_title_change_queues_immediate_same_library_reconciliation(shared_library):
    from helper.library_sharing import sync_recipient
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    sync_recipient(runtime, "default", "friend")
    child = ScopedStore(base, "friend")
    child_id = child.get("managed")["qq:pop"]["id"]
    owner = ScopedStore(base, "default")
    owner.set("sources", [{"id": "qq:pop", "name": "轻音乐夜晚", "enabled": True}])
    plan = runtime.engine("default").preview_names()
    assert plan["groups"][0]["action"] == "rename"
    runtime.wake.clear()

    runtime.engine("default").apply_names(plan["id"])

    assert runtime.wake.is_set()
    runtime.sync_library_shares_due(now=1000)
    assert child.get("managed")["qq:pop"]["id"] == child_id
    assert data["friend-token"]["playlists"][child_id]["title"] == data["owner-token"]["playlists"]["100"]["title"]


def test_owner_app_delete_queues_verified_tombstone_and_preserves_native(shared_library):
    from helper.daily_mix_v036 import remove_managed_playlist
    from helper.library_sharing import REVISIONS_KEY, sync_recipient
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    sync_recipient(runtime, "default", "friend")
    native = FakePlex("friend-token", data).create("流行精选", ["3"], "")
    owner = ScopedStore(base, "default")

    remove_managed_playlist(runtime.engine("default"), "qq:pop", "流行精选")
    revision = owner.get(REVISIONS_KEY)["qq:pop"][-1]
    assert revision["action"] == "delete"
    assert revision["confirmed"] is True
    runtime.sync_library_shares_due(now=1000)

    assert native["id"] in data["friend-token"]["playlists"]
    assert len(data["friend-token"]["playlists"]) == 1


def test_one_invalid_target_does_not_block_other_same_library_profiles(shared_library):
    from helper.library_sharing import queue_owner_revision, reconcile_owner_revision
    from helper.scoped_store import ScopedStore

    base, registry, runtime, data = shared_library
    registry.create(
        name="暂时无权", kind="shared", profile_id="a-bad", token="bad-token",
        account={"id": "bad"}, server={"machine": "server-a", "url": "http://plex"},
        library={"id": "11", "name": "音乐"},
    )
    source = ScopedStore(base, "default").get("managed")["qq:pop"]
    queue_owner_revision(runtime, "default", "qq:pop", "publish", source)
    registry.update("a-bad", library={"id": "22", "name": "其他曲库"})

    outcomes = reconcile_owner_revision(runtime, "default")

    assert outcomes["a-bad"]["errors"]
    assert outcomes["friend"]["created"] == 1
    assert len(data["friend-token"]["playlists"]) == 1
