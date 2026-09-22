import tempfile
import unittest
from pathlib import Path


class ProfileIdentityV108Tests(unittest.TestCase):
    def setUp(self):
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ScopedStore
        from helper.store import Store

        self.temp = tempfile.TemporaryDirectory()
        self.base = Store(Path(self.temp.name))
        self.registry = ProfileRegistry(self.base)
        self.source = ScopedStore(self.base, "default")
        self.registry.update(
            "default",
            name="shudaizi",
            kind="owner",
            account={"id": "10", "username": "shudaizi"},
            server={"machine": "m1", "name": "Plex", "url": "http://plex"},
            library={"id": "11", "name": "音乐"},
            token="owner-token",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_existing_profile_identity_is_found_without_renaming_legacy_id(self):
        found = self.registry.find_identity("owner", "10", "m1", "11")
        self.assertEqual("default", found["id"])

    def test_creating_second_library_copies_preferences_but_not_library_state(self):
        from helper.scoped_store import ScopedStore

        self.source.set_many({
            "daily_settings": {**self.source.get("daily_settings"), "size": 50, "hour": 7, "enabled": True},
            "catalog": [{"id": "song-1"}],
            "cache": {"qq": {"data": [1]}},
            "feedback": {"tracks": {"song-1": {"value": "avoid"}}, "artists": {}},
            "daily_history": [{"plan_id": "old"}],
            "daily_managed": {"id": "playlist-1"},
        })

        created = self.registry.create_for_library(
            "default", {"id": "15", "name": "经典音乐"}
        )
        target = ScopedStore(self.base, created["id"])

        self.assertEqual(50, target.get("daily_settings")["size"])
        self.assertEqual(7, target.get("daily_settings")["hour"])
        self.assertFalse(target.get("daily_settings")["enabled"])
        self.assertEqual([], target.get("catalog", []))
        self.assertEqual({}, target.get("cache"))
        self.assertEqual({"tracks": {}, "artists": {}}, target.get("feedback"))
        self.assertEqual([], target.get("daily_history"))
        self.assertIsNone(target.get("daily_managed"))

    def test_repeating_same_identity_returns_existing_profile(self):
        first = self.registry.create_for_library(
            "default", {"id": "15", "name": "经典音乐"}
        )
        second = self.registry.create_for_library(
            "default", {"id": "15", "name": "经典音乐"}
        )

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(2, len(self.registry.list_public()))

    def test_unmanaged_switch_is_atomic_and_clears_only_library_derived_state(self):
        self.source.set_many({
            "catalog": [{"id": "song-1"}],
            "daily_history": [{"plan_id": "old"}],
        })

        result = self.registry.switch_unmanaged_library(
            "default", {"id": "15", "name": "经典音乐"}
        )

        self.assertEqual("15", result["library"]["id"])
        self.assertEqual("15", self.source.get("settings")["section"])
        self.assertEqual([], self.source.get("catalog"))
        self.assertEqual([], self.source.get("daily_history"))

    def test_unmanaged_switch_removes_unlisted_library_state_and_prefix_caches(self):
        self.source.set_many({
            "single_result:old-scope:1": {"id": "1", "status": "matched"},
            "name_plan": {"id": "old-name-plan"},
            "incremental_status": {"status": "completed"},
            "daily_similarity_cache": {"old": {"ids": ["1"]}},
            "webhook_seen": {"old": True},
        })

        self.registry.switch_unmanaged_library(
            "default", {"id": "15", "name": "经典音乐"}
        )

        self.assertEqual({}, self.source.get_prefix("single_result:"))
        for key in ("name_plan", "incremental_status", "daily_similarity_cache", "webhook_seen"):
            self.assertIsNone(self.source.get(key))

    def test_protected_profile_cannot_switch_in_place(self):
        self.source.set("daily_managed", {"id": "playlist-1"})

        with self.assertRaisesRegex(ValueError, "已有托管歌单"):
            self.registry.switch_unmanaged_library(
                "default", {"id": "15", "name": "经典音乐"}
            )


if __name__ == "__main__":
    unittest.main()
