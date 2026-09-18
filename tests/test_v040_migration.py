import tempfile
import unittest
from pathlib import Path


class ProfileMigrationV040Tests(unittest.TestCase):
    def test_legacy_state_is_copied_once_without_changing_originals(self):
        from helper.profiles import migrate_default_profile
        from helper.scoped_store import ScopedStore
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            legacy_settings = {
                **store.get("settings"),
                "plex_url": "http://127.0.0.1:32400",
                "plex_token": "owner-secret",
                "section": "15",
            }
            legacy_managed = {"theme-1": {"id": "77", "fingerprint": "abc"}}
            store.set_many({"settings": legacy_settings, "managed": legacy_managed})

            first = migrate_default_profile(store, now=100)
            scoped = ScopedStore(store, "default")
            self.assertTrue(first["migrated"])
            self.assertEqual(legacy_settings, scoped.get("settings"))
            self.assertEqual(legacy_managed, scoped.get("managed"))
            self.assertEqual(legacy_settings, store.get("settings"))
            self.assertEqual(legacy_managed, store.get("managed"))

            scoped.set("managed", {"new": {"id": "88"}})
            second = migrate_default_profile(store, now=200)
            self.assertFalse(second["migrated"])
            self.assertEqual({"new": {"id": "88"}}, scoped.get("managed"))
            self.assertEqual(100, store.get("profiles_migration_v1")["at"])

    def test_global_keys_are_not_duplicated_into_profile_namespace(self):
        from helper.profiles import migrate_default_profile
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            migrate_default_profile(store)
            scoped_rows = store.get_prefix("profile:default:")
            self.assertNotIn("profile:default:installation_id", scoped_rows)
            self.assertNotIn("profile:default:auth_sessions", scoped_rows)
            self.assertNotIn("profile:default:plex_profiles_v1", scoped_rows)


if __name__ == "__main__":
    unittest.main()
