import asyncio
import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class _Plex:
    def __init__(self, token):
        self.token = token

    def identity(self):
        return {"machine": "m1", "server": "Plex"}

    def sections(self):
        return [
            {"id": "11", "title": "音乐", "type": "artist"},
            {"id": "15", "title": "经典音乐", "type": "artist"},
            {"id": "99", "title": "电影", "type": "movie"},
        ]

    def playlists(self):
        return []


class _ClientFactory:
    def __init__(self):
        self.tokens = []

    def __call__(self, _url, token):
        self.tokens.append(token)
        return _Plex(token)


class ProfileLibrariesV108Tests(unittest.TestCase):
    def setUp(self):
        from helper.plex_recipients import PlexRecipientService
        from helper.profiles import ProfileRegistry
        from helper.store import Store

        self.temp = tempfile.TemporaryDirectory()
        self.base = Store(Path(self.temp.name))
        self.registry = ProfileRegistry(self.base)
        self.registry.update(
            "default",
            name="shudaizi",
            kind="owner",
            account={"id": "10", "username": "shudaizi"},
            server={"machine": "m1", "name": "Plex", "url": "http://plex"},
            library={"id": "11", "name": "音乐"},
            token="owner-token",
        )
        self.registry.create(
            name="shudai6",
            kind="shared",
            profile_id="shared-248098626",
            account={"id": "248098626", "username": "shudai6"},
            server={"machine": "m1", "name": "Plex", "url": "http://plex"},
            library={"id": "11", "name": "音乐"},
            token="friend-token",
        )
        self.clients = _ClientFactory()
        self.service = PlexRecipientService(
            self.base, self.registry, client_factory=self.clients
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_shared_profile_lists_music_libraries_using_its_own_token(self):
        rows = self.service.list_profile_libraries("shared-248098626")

        self.assertEqual(["11", "15"], [row["id"] for row in rows])
        self.assertEqual(["friend-token"], self.clients.tokens)

    def test_non_music_sections_are_not_returned(self):
        rows = self.service.list_profile_libraries("default")

        self.assertEqual(["11", "15"], [row["id"] for row in rows])

    def test_recipient_library_discovery_does_not_expose_token(self):
        self.service._shared_records = lambda _owner: [{
            "id": "248098626",
            "username": "shudai6",
            "title": "shudai6",
            "token": "friend-token",
        }]

        result = self.service.list_recipient_libraries(
            "default", "shared", "248098626"
        )

        self.assertEqual("shudai6", result["account"]["username"])
        self.assertEqual(["11", "15"], [row["id"] for row in result["libraries"]])
        self.assertNotIn("token", repr(result).lower())

    def test_protected_library_selection_creates_profile_and_preserves_source(self):
        from helper.scoped_store import ScopedStore

        ScopedStore(self.base, "default").set("daily_managed", {"id": "playlist-1"})

        result = self.service.select_profile_library("default", "15")

        self.assertEqual("created", result["mode"])
        self.assertEqual("11", self.registry.get("default")["library"]["id"])
        self.assertEqual("15", result["profile"]["library"]["id"])

    def test_unresolved_playlist_snapshot_also_creates_a_new_profile(self):
        from helper.scoped_store import ScopedStore

        scoped = ScopedStore(self.base, "default")
        scoped.set("snapshots", [{"id": "pending", "kind": "daily", "status": "uncertain"}])

        result = self.service.select_profile_library("default", "15")

        self.assertEqual("created", result["mode"])
        self.assertEqual("11", self.registry.get("default")["library"]["id"])
        self.assertEqual("uncertain", scoped.get("snapshots")[0]["status"])

    def test_unmanaged_library_selection_switches_existing_profile(self):
        result = self.service.select_profile_library("shared-248098626", "15")

        self.assertEqual("switched", result["mode"])
        self.assertEqual("shared-248098626", result["profile"]["id"])

    def test_selecting_existing_sibling_refreshes_registry_and_runtime_token(self):
        from helper.scoped_store import ScopedStore

        sibling = self.registry.create_for_library(
            "default", {"id": "15", "name": "经典音乐"}
        )
        self.registry.update(sibling["id"], token="old-token")
        sibling_store = ScopedStore(self.base, sibling["id"])
        settings = dict(sibling_store.get("settings"))
        settings["plex_token"] = "old-token"
        sibling_store.set("settings", settings)
        self.registry.update("default", token="renewed-owner-token")

        result = self.service.select_profile_library("default", "15")

        self.assertEqual(sibling["id"], result["profile"]["id"])
        self.assertEqual("renewed-owner-token", self.registry.get(sibling["id"])["token"])
        self.assertEqual("renewed-owner-token", sibling_store.get("settings")["plex_token"])

    def test_library_not_returned_by_target_token_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "无权访问"):
            self.service.select_profile_library("shared-248098626", "99")


class ProfileLibraryRoutesV108Tests(unittest.TestCase):
    def setUp(self):
        from helper.profiles import ProfileRegistry
        from helper.store import Store

        self.temp = tempfile.TemporaryDirectory()
        self.base = Store(Path(self.temp.name))
        self.registry = ProfileRegistry(self.base)
        self.handlers = {}
        self.locked = False

        class App:
            def get(inner, path):
                return lambda fn: self.handlers.setdefault(("GET", path), fn) or fn

            def post(inner, path):
                return lambda fn: self.handlers.setdefault(("POST", path), fn) or fn

        outer = self

        class Engine:
            def exclusive(inner):
                @contextlib.contextmanager
                def locked():
                    outer.locked = True
                    try:
                        yield
                    finally:
                        outer.locked = False
                return locked()

        async def body(request):
            return request

        from helper.profile_web import attach_profile_routes
        attach_profile_routes(App(), self.base, self.registry, body, lambda: None, Engine())

    def tearDown(self):
        self.temp.cleanup()

    def test_library_routes_use_the_shared_operation_lock(self):
        from helper.plex_recipients import PlexRecipientService

        def libraries(_service, profile_id):
            self.assertTrue(self.locked)
            return [{"id": "11", "name": profile_id}]

        def recipient(_service, owner_profile_id, kind, user_id):
            self.assertTrue(self.locked)
            return {"account": {"id": user_id}, "libraries": [], "owner": owner_profile_id, "kind": kind}

        def select(_service, profile_id, library_id):
            self.assertTrue(self.locked)
            return {"profile": {"id": profile_id, "library": {"id": library_id}}, "mode": "switched"}

        with patch.object(PlexRecipientService, "list_profile_libraries", libraries), \
                patch.object(PlexRecipientService, "list_recipient_libraries", recipient), \
                patch.object(PlexRecipientService, "select_profile_library", select):
            listed = self.handlers[("GET", "/api/plex/profiles/libraries")]("default")
            discovered = asyncio.run(self.handlers[("POST", "/api/plex/recipients/libraries")]({
                "owner_profile_id": "default", "kind": "shared", "user_id": "42",
            }))
            selected = asyncio.run(self.handlers[("POST", "/api/plex/profiles/library")]({
                "profile_id": "default", "library_id": "15",
            }))

        self.assertEqual("default", listed["items"][0]["name"])
        self.assertEqual("42", discovered["account"]["id"])
        self.assertEqual("15", selected["profile"]["library"]["id"])

    def test_profile_learning_switch_updates_only_the_selected_profile_and_keeps_history(self):
        from helper.scoped_store import ScopedStore

        self.registry.update(
            "default",
            account={"id": "10", "username": "owner"},
            server={"machine": "m1"},
            library={"id": "11", "name": "音乐"},
        )
        second = self.registry.create(
            name="经典音乐", kind="owner", profile_id="owner-classics",
            account={"id": "10", "username": "owner"},
            server={"machine": "m1"}, library={"id": "15", "name": "经典音乐"},
        )
        target = ScopedStore(self.base, second["id"])
        target.set_many({
            "behavior_events": [{"track_id": "7", "at": 10}],
            "daily_plan": {"id": "old-daily-preview"},
            "smart_mix_plans": {"old-smart-preview": {"id": "old-smart-preview"}},
        })

        result = asyncio.run(self.handlers[("POST", "/api/plex/profiles/learning")](
            {"profile_id": second["id"], "enabled": False}
        ))
        listed = asyncio.run(self.handlers[("GET", "/api/plex/profiles")]())
        rows = {row["id"]: row for row in listed["items"]}

        self.assertFalse(result["behavior_enabled"])
        self.assertFalse(rows[second["id"]]["behavior_enabled"])
        self.assertTrue(rows["default"]["behavior_enabled"])
        self.assertEqual([{"track_id": "7", "at": 10}], target.get("behavior_events"))
        self.assertIsNone(target.get("daily_plan"))
        self.assertEqual({}, target.get("smart_mix_plans"))


if __name__ == "__main__":
    unittest.main()
