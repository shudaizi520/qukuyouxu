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
            "id": playlist_id, "title": title, "summary": marker,
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


def test_confirmed_recipient_deletion_opts_out_and_never_recreates(shared_library):
    from helper.library_sharing import sync_recipient
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    sync_recipient(runtime, "default", "friend")
    child = ScopedStore(base, "friend")
    playlist_id = child.get("managed")["qq:pop"]["id"]
    del data["friend-token"]["playlists"][playlist_id]

    first = sync_recipient(runtime, "default", "friend")
    second = sync_recipient(runtime, "default", "friend")

    assert first["opted_out"] == 1
    assert second["created"] == 0
    assert "qq:pop" not in child.get("managed")
    assert "qq:pop" in child.get("library_share_v1")["excluded"]
    assert not data["friend-token"]["playlists"]


def test_restore_resubscribes_only_that_category(shared_library):
    from helper.library_sharing import restore_category, sync_recipient
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    child = ScopedStore(base, "friend")
    child.set("library_share_v1", {"owner_id": "default", "excluded": ["qq:pop"]})
    assert sync_recipient(runtime, "default", "friend")["created"] == 0
    restore_category(runtime, "friend", "qq:pop")
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


def test_owner_removal_preserves_recipient_edited_copy(shared_library):
    from helper.library_sharing import sync_recipient
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    sync_recipient(runtime, "default", "friend")
    child = ScopedStore(base, "friend")
    playlist_id = child.get("managed")["qq:pop"]["id"]
    data["friend-token"]["playlists"][playlist_id]["title"] = "自己整理的歌单"
    ScopedStore(base, "default").set("managed", {})

    result = sync_recipient(runtime, "default", "friend")

    assert result["skipped"] == 1
    assert playlist_id in data["friend-token"]["playlists"]


def test_other_library_is_never_written(shared_library):
    from helper.library_sharing import sync_recipient

    _base, registry, runtime, data = shared_library
    registry.update("friend", library={"id": "22", "name": "其他曲库"})
    with pytest.raises(ValueError, match="同一曲库"):
        sync_recipient(runtime, "default", "friend")
    assert "friend-token" not in data


def test_manually_modified_recipient_playlist_is_not_overwritten(shared_library):
    from helper.library_sharing import sync_recipient
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    sync_recipient(runtime, "default", "friend")
    child = ScopedStore(base, "friend")
    playlist_id = child.get("managed")["qq:pop"]["id"]
    data["friend-token"]["playlists"][playlist_id]["title"] = "我自己改的"
    owner = data["owner-token"]["playlists"]["100"]
    owner["items"].append({"id": "3", "item_id": "100-2"})
    from helper.engine import fingerprint
    managed = child.get("managed")
    source = ScopedStore(base, "default").get("managed")
    source["qq:pop"]["fingerprint"] = fingerprint(owner)
    ScopedStore(base, "default").set("managed", source)

    result = sync_recipient(runtime, "default", "friend")

    assert result["skipped"] == 1
    assert [row["id"] for row in data["friend-token"]["playlists"][playlist_id]["items"]] == ["1", "2"]
    assert managed == child.get("managed")


def test_deleting_from_this_app_also_opts_out(shared_library):
    from helper.library_sharing import sync_recipient
    from helper.playlist_hub import remove_playlist
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    sync_recipient(runtime, "default", "friend")
    child = ScopedStore(base, "friend")

    remove_playlist(runtime.engine("friend"), "category", "qq:pop", "流行精选")
    result = sync_recipient(runtime, "default", "friend")

    assert result["created"] == 0
    assert "qq:pop" in child.get("library_share_v1")["excluded"]
    assert not data["friend-token"]["playlists"]


def test_scheduler_propagates_owner_categories_without_separate_automation(shared_library):
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    runtime.run_due(now=1000)

    child = ScopedStore(base, "friend")
    assert child.get("managed")["qq:pop"]["shared_from"] == "default"
    assert len(data["friend-token"]["playlists"]) == 1
    runtime.run_due(now=1060)
    assert len(data["friend-token"]["playlists"]) == 1


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


def test_forgetting_confirmed_missing_recipient_copy_keeps_opt_out(shared_library):
    from helper.library_sharing import sync_recipient
    from helper.managed_cleanup_v0317 import forget_missing_managed_playlist
    from helper.scoped_store import ScopedStore

    base, _registry, runtime, data = shared_library
    sync_recipient(runtime, "default", "friend")
    child = ScopedStore(base, "friend")
    playlist_id = child.get("managed")["qq:pop"]["id"]
    del data["friend-token"]["playlists"][playlist_id]

    forget_missing_managed_playlist(runtime.engine("friend"), "qq:pop", playlist_id, "流行精选")
    result = sync_recipient(runtime, "default", "friend")

    assert result["created"] == 0
    assert "qq:pop" in child.get("library_share_v1")["excluded"]


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

    assert share_status(runtime, "friend")["last_result"]["skipped"] == 1


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
