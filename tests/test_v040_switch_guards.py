import asyncio
import contextlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class ProfileSwitchGuardV040Tests(unittest.TestCase):
    def setUp(self):
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ScopedStore
        from helper.store import Store

        self.temp = tempfile.TemporaryDirectory()
        self.base = Store(Path(self.temp.name))
        self.registry = ProfileRegistry(self.base)
        self.store = ScopedStore(self.base, "default")
        self.store.set("plex_saved", {
            "account": {"id": "10", "username": "owner"},
            "server": {"machine": "machine-a"},
            "library": {"id": "15"},
        })

    def tearDown(self):
        self.temp.cleanup()

    def test_managed_profile_rejects_account_server_or_library_change(self):
        from helper.profile_web import guard_connection_change

        self.store.set("daily_managed", {"id": "88"})
        for account, machine, library in (
            ("11", "machine-a", "15"),
            ("10", "machine-b", "15"),
            ("10", "machine-a", "16"),
        ):
            with self.subTest(account=account, machine=machine, library=library):
                with self.assertRaises(ValueError):
                    guard_connection_change(self.store, account, machine, library)

    def test_managed_profile_allows_same_identity_token_refresh(self):
        from helper.profile_web import guard_connection_change

        self.store.set("managed", {"theme": {"id": "77"}})
        guard_connection_change(self.store, "10", "machine-a", "15")

    def test_unmanaged_profile_can_select_another_connection(self):
        from helper.profile_web import guard_connection_change

        guard_connection_change(self.store, "11", "machine-b", "16")

    def test_recently_removed_managed_playlist_still_protects_connection_identity(self):
        from helper.profile_web import guard_connection_change

        self.store.set("retired_managed", {"theme": {"snapshot_id": "removed-1"}})
        with self.assertRaises(ValueError):
            guard_connection_change(self.store, "10", "machine-a", "16")

    def test_library_save_uses_the_same_managed_switch_guard(self):
        self.store.set("managed", {"theme": {"id": "77"}})
        with patch.dict("sys.modules", {"fastapi": SimpleNamespace(Request=object)}):
            from helper.plex_state_v0316 import save_library

            with self.assertRaises(ValueError):
                save_library(self.store, "16", "Another Library")
        self.assertEqual("15", self.store.get("plex_saved")["library"]["id"])

    def test_library_select_route_waits_for_idle_and_uses_the_operation_lock(self):
        from helper.plex_state_v0316 import attach_routes

        settings = self.store.get("settings")
        settings.update(plex_url="http://plex:32400", plex_token="secret", section="15")
        self.store.set("settings", settings)

        handlers = {}
        class App:
            def get(self, path):
                return lambda fn: handlers.setdefault(("GET", path), fn) or fn
            def post(self, path):
                return lambda fn: handlers.setdefault(("POST", path), fn) or fn
        calls = []
        class Engine:
            def exclusive(self):
                @contextlib.contextmanager
                def locked():
                    calls.append("lock")
                    yield
                return locked()
        async def body(_request):
            return {"section": "15", "name": "Music"}
        def ensure_idle():
            calls.append("idle")

        attach_routes(App(), self.store, Engine(), body, ensure_idle)
        asyncio.run(handlers[("POST", "/api/plex/library/select")](object()))

        self.assertEqual(["idle", "lock"], calls)


if __name__ == "__main__":
    unittest.main()
