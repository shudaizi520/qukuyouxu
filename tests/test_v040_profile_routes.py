import tempfile
import unittest
from pathlib import Path


class ActiveProfileStoreV040Tests(unittest.TestCase):
    def test_active_store_follows_selected_profile_without_exposing_token(self):
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ActiveProfileStore, ScopedStore
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            base = Store(Path(root))
            registry = ProfileRegistry(base)
            registry.create(name="朋友甲", kind="shared", profile_id="friend-a", token="friend-secret")
            ScopedStore(base, "default").set("daily_history", ["owner"])
            ScopedStore(base, "friend-a").set("daily_history", ["friend"])
            active = ActiveProfileStore(base, registry)

            self.assertEqual(["owner"], active.get("daily_history"))
            registry.select("friend-a")
            self.assertEqual(["friend"], active.get("daily_history"))
            self.assertNotIn("friend-secret", repr(registry.list_public()))

    def test_connection_sync_updates_registry_and_scoped_settings(self):
        from helper.profile_web import sync_profile_connection
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ActiveProfileStore
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            base = Store(Path(root))
            registry = ProfileRegistry(base)
            active = ActiveProfileStore(base, registry)
            sync_profile_connection(
                active,
                account={"id": "10", "username": "owner"},
                server={"machine": "machine-a", "name": "Main", "url": "http://plex:32400"},
                library={"id": "15", "name": "Music"},
                token="new-secret-token",
            )
            profile = registry.list_public()[0]
            self.assertEqual("machine-a", profile["server"]["machine"])
            self.assertEqual("15", profile["library"]["id"])
            self.assertTrue(profile["token_present"])
            self.assertEqual("new-secret-token", active.get("settings")["plex_token"])
            self.assertNotIn("new-secret-token", repr(profile))

    def test_disconnect_clears_authorization_and_schedules_but_preserves_local_work(self):
        from helper.profile_web import disconnect_profile_connection
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ActiveProfileStore
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            base = Store(Path(root))
            registry = ProfileRegistry(base)
            active = ActiveProfileStore(base, registry)
            registry.update(
                "default",
                account={"id": "10", "username": "owner"},
                server={"machine": "machine-a", "name": "Main", "url": "http://plex:32400"},
                library={"id": "15", "name": "Music"},
                token="new-secret-token",
            )
            settings = active.get("settings")
            settings.update(
                plex_url="http://plex:32400",
                plex_token="new-secret-token",
                section="15",
                account_label="owner",
                auto_enabled=True,
            )
            active.set_many({
                "settings": settings,
                "plex_saved": {"account": {"id": "10"}},
                "plex_connection": {"result": {"server": "Main"}},
                "daily_settings": {**active.get("daily_settings"), "enabled": True},
                "smart_mix_settings": {"auto_enabled": True, "weekly_auto_enabled": True},
                "managed": {"folk": {"id": "playlist-1"}},
                "single_cache": {"song-1": {"title": "保留的缓存"}},
            })

            result = disconnect_profile_connection(active)

            self.assertEqual("Plex 已断开", result["message"])
            disconnected = active.get("settings")
            for key in ("plex_url", "plex_token", "section", "account_label"):
                self.assertEqual("", disconnected[key])
            self.assertFalse(disconnected["auto_enabled"])
            self.assertFalse(active.get("daily_settings")["enabled"])
            self.assertFalse(active.get("smart_mix_settings")["auto_enabled"])
            self.assertFalse(active.get("smart_mix_settings")["weekly_auto_enabled"])
            self.assertIsNone(active.get("plex_connection"))
            self.assertEqual({}, active.get("plex_saved"))
            profile = registry.list_public()[0]
            self.assertFalse(profile["token_present"])
            self.assertEqual({}, profile["account"])
            self.assertEqual("playlist-1", active.get("managed")["folk"]["id"])
            self.assertEqual("保留的缓存", active.get("single_cache")["song-1"]["title"])
            self.assertEqual(
                ("10", "machine-a", "15"),
                (
                    active.get("plex_reconnect_identity")["account"]["id"],
                    active.get("plex_reconnect_identity")["server"]["machine"],
                    active.get("plex_reconnect_identity")["library"]["id"],
                ),
            )

    def test_managed_profile_can_reconnect_only_to_the_same_identity_after_disconnect(self):
        from helper.profile_web import disconnect_profile_connection, guard_connection_change
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ActiveProfileStore
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            base = Store(Path(root))
            registry = ProfileRegistry(base)
            active = ActiveProfileStore(base, registry)
            registry.update(
                "default",
                account={"id": "10", "username": "owner"},
                server={"machine": "machine-a", "name": "Main", "url": "http://plex:32400"},
                library={"id": "15", "name": "Music"},
                token="new-secret-token",
            )
            settings = active.get("settings")
            settings.update(
                plex_url="http://plex:32400",
                plex_token="new-secret-token",
                section="15",
                account_label="owner",
            )
            active.set_many({
                "settings": settings,
                "plex_saved": {
                    "account": {"id": "10", "username": "owner"},
                    "server": {"machine": "machine-a"},
                    "library": {"id": "15"},
                },
                "managed": {"folk": {"id": "playlist-1"}},
            })

            disconnect_profile_connection(active)

            guard_connection_change(active, "10", "machine-a", "15")
            for account, machine, library in (
                ("11", "machine-a", "15"),
                ("10", "machine-b", "15"),
                ("10", "machine-a", "16"),
            ):
                with self.subTest(account=account, machine=machine, library=library):
                    with self.assertRaises(ValueError):
                        guard_connection_change(active, account, machine, library)

    def test_smart_playlist_alone_preserves_and_guards_reconnect_identity(self):
        from helper.profile_web import disconnect_profile_connection, guard_connection_change
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ActiveProfileStore
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            base = Store(Path(root))
            registry = ProfileRegistry(base)
            active = ActiveProfileStore(base, registry)
            registry.update(
                "default", account={"id": "10", "username": "owner"},
                server={"machine": "machine-a", "url": "http://plex:32400"},
                library={"id": "15", "name": "Music"}, token="secret",
            )
            settings = active.get("settings")
            settings.update(plex_url="http://plex:32400", plex_token="secret", section="15")
            active.set_many({
                "settings": settings,
                "plex_saved": {
                    "account": {"id": "10", "username": "owner"},
                    "server": {"machine": "machine-a"}, "library": {"id": "15"},
                },
                "smart_mix_managed": {"weekly": {"id": "playlist-2"}},
            })

            disconnect_profile_connection(active)

            self.assertEqual("machine-a", active.get("plex_reconnect_identity")["server"]["machine"])
            guard_connection_change(active, "10", "machine-a", "15")
            with self.assertRaises(ValueError):
                guard_connection_change(active, "10", "machine-b", "15")

    def test_removed_smart_playlist_snapshot_also_preserves_identity(self):
        from helper.profile_web import disconnect_profile_connection, guard_connection_change
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ActiveProfileStore
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            base = Store(Path(root))
            registry = ProfileRegistry(base)
            active = ActiveProfileStore(base, registry)
            registry.update(
                "default", account={"id": "10", "username": "owner"},
                server={"machine": "machine-a", "url": "http://plex:32400"},
                library={"id": "15", "name": "Music"}, token="secret",
            )
            settings = active.get("settings")
            settings.update(plex_url="http://plex:32400", plex_token="secret", section="15")
            active.set_many({
                "settings": settings,
                "plex_saved": {
                    "account": {"id": "10", "username": "owner"},
                    "server": {"machine": "machine-a", "url": "http://plex:32400"},
                    "library": {"id": "15"},
                },
                "smart_mix_removed": {"weekly": {"snapshot_id": "removed-1"}},
            })

            disconnect_profile_connection(active)

            self.assertEqual("10", active.get("plex_reconnect_identity")["account"]["id"])
            with self.assertRaises(ValueError):
                guard_connection_change(active, "11", "machine-a", "15")

    def test_scope_migration_only_updates_snapshots_still_referenced_by_managed_state(self):
        from helper.connection_scope import migrate_managed_scopes, stable_library_scope
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ActiveProfileStore
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            base = Store(Path(root))
            registry = ProfileRegistry(base)
            active = ActiveProfileStore(base, registry)
            registry.update(
                "default", account={"id": "10", "username": "owner"},
                server={"machine": "machine-a", "url": "http://plex:32400"},
                library={"id": "15", "name": "Music"}, token="secret",
            )
            settings = active.get("settings")
            settings.update(plex_url="http://plex:32400", plex_token="secret", section="15")
            active.set_many({
                "settings": settings,
                "plex_saved": {
                    "account": {"id": "10", "username": "owner"},
                    "server": {"machine": "machine-a", "url": "http://plex:32400"},
                    "library": {"id": "15"},
                },
                "smart_mix_removed": {"weekly": {"snapshot_id": "referenced"}},
                "snapshots": [
                    {"id": "referenced", "machine": "machine-a", "scope": "old-scope"},
                    {"id": "unrelated", "machine": "machine-a", "scope": "other-account-scope"},
                ],
            })

            migrate_managed_scopes(active)

            snapshots = {row["id"]: row for row in active.get("snapshots")}
            self.assertEqual(stable_library_scope(active), snapshots["referenced"]["scope"])
            self.assertEqual("other-account-scope", snapshots["unrelated"]["scope"])

    def test_scope_migration_does_not_adopt_unverifiable_legacy_removed_category(self):
        from helper.connection_scope import migrate_managed_scopes
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ActiveProfileStore
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            base = Store(Path(root))
            registry = ProfileRegistry(base)
            active = ActiveProfileStore(base, registry)
            registry.update(
                "default", account={"id": "10", "username": "owner"},
                server={"machine": "machine-a", "url": "http://plex:32400"},
                library={"id": "15", "name": "Music"}, token="secret",
            )
            settings = active.get("settings")
            settings.update(plex_url="http://plex:32400", plex_token="secret", section="15")
            active.set_many({
                "settings": settings,
                "plex_saved": {
                    "account": {"id": "10", "username": "owner"},
                    "server": {"machine": "machine-a", "url": "http://plex:32400"},
                    "library": {"id": "15"},
                },
                "retired_managed": {"hot": {"snapshot_id": "removed-hot", "title": "网络热歌"}},
                "snapshots": [{
                    "id": "removed-hot", "kind": "managed_remove", "category_id": "hot",
                    "machine": "machine-a", "scope": "", "status": "applied",
                }],
            })

            migrate_managed_scopes(active)

            self.assertNotIn("scope", active.get("retired_managed")["hot"])
            snapshot = next(row for row in active.get("snapshots") if row["id"] == "removed-hot")
            self.assertEqual("", snapshot["scope"])


if __name__ == "__main__":
    unittest.main()
