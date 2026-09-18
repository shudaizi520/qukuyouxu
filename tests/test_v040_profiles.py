import tempfile
import unittest
from pathlib import Path


class ProfileRegistryV040Tests(unittest.TestCase):
    def setUp(self):
        from helper.profiles import ProfileRegistry
        from helper.store import Store

        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.registry = ProfileRegistry(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_default_profile_is_stable_and_public_data_redacts_token(self):
        self.store.set("settings", {
            **self.store.get("settings"),
            "plex_url": "http://127.0.0.1:32400",
            "plex_token": "top-secret-token",
            "section": "15",
        })
        self.registry.refresh_default_from_legacy()

        public = self.registry.list_public()
        self.assertEqual("default", self.registry.active_id())
        self.assertEqual(1, len(public))
        self.assertNotIn("token", public[0])
        self.assertNotIn("top-secret-token", repr(public))
        self.assertTrue(public[0]["token_present"])

    def test_profiles_have_isolated_state_and_validated_ids(self):
        from helper.scoped_store import ScopedStore

        friend = self.registry.create(
            name="朋友甲",
            kind="shared",
            profile_id="friend-a",
            token="friend-secret",
            account={"id": "42", "username": "friend"},
            server={"machine": "machine-1", "name": "Plex", "url": "http://plex:32400"},
            library={"id": "15", "name": "音乐"},
        )
        self.assertEqual("friend-a", friend["id"])
        default = ScopedStore(self.store, "default")
        other = ScopedStore(self.store, "friend-a")
        default.set("daily_history", [{"id": "owner-song"}])
        other.set("daily_history", [{"id": "friend-song"}])
        self.assertEqual([{"id": "owner-song"}], default.get("daily_history"))
        self.assertEqual([{"id": "friend-song"}], other.get("daily_history"))
        with self.assertRaises(ValueError):
            self.registry.create(name="bad", kind="owner", profile_id="../escape")

    def test_selection_and_removal_are_guarded(self):
        from helper.scoped_store import ScopedStore

        self.registry.create(name="朋友甲", kind="shared", profile_id="friend-a", token="12345678")
        selected = self.registry.select("friend-a")
        self.assertEqual("friend-a", selected["id"])
        with self.assertRaises(ValueError):
            self.registry.select("missing")
        with self.assertRaises(ValueError):
            self.registry.remove("default")

        ScopedStore(self.store, "friend-a").set("daily_managed", {"id": "99"})
        with self.assertRaises(ValueError):
            self.registry.remove("friend-a")


if __name__ == "__main__":
    unittest.main()
