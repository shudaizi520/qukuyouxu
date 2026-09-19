import asyncio
import contextlib
import sys
import tempfile
import types
import fastapi
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if "fastapi" not in sys.modules:
    fastapi = types.ModuleType("fastapi")
    fastapi.Request = object
    sys.modules["fastapi"] = fastapi

if "fastapi.responses" not in sys.modules:
    responses = types.ModuleType("fastapi.responses")
    responses.Response = object
    sys.modules["fastapi.responses"] = responses

from helper.daily_mix_v036 import attach_v036_routes
from helper.profiles import ProfileRegistry
from helper.scoped_store import ActiveProfileStore
from helper.store import Store


class _Routes:
    def __init__(self):
        self.handlers = {}
        self.state = types.SimpleNamespace(v0317_routes_attached=True)

    def _add(self, method, path):
        def decorator(handler):
            self.handlers[(method, path)] = handler
            return handler
        return decorator

    def get(self, path):
        return self._add("GET", path)

    def post(self, path):
        return self._add("POST", path)


class _Engine:
    def __init__(self):
        self.plex_calls = 0

    def plex_factory(self, _settings):
        self.plex_calls += 1
        raise RuntimeError("shared token would receive Plex HTTP 403 on /accounts")

    def exclusive(self):
        return contextlib.nullcontext()


class _ReconnectPlex:
    def identity(self):
        return {"machine": "machine-a", "server": "Main", "version": "1.0"}

    def sections(self):
        return [
            {"id": "15", "title": "Music"},
            {"id": "16", "title": "Other Music"},
        ]


class _ReconnectEngine:
    def __init__(self):
        self.settings_seen = []

    def plex_factory(self, settings):
        self.settings_seen.append(dict(settings))
        if not settings.get("plex_token"):
            raise RuntimeError("disconnected credentials must not be queried")
        return _ReconnectPlex()

    def exclusive(self):
        return contextlib.nullcontext()


class SharedBehaviorIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Store(Path(self.temp.name))
        self.registry = ProfileRegistry(self.base)
        self.registry.create(
            name="shudai6",
            kind="shared",
            profile_id="shared-248098626",
            token="friend-secret",
            account={"id": "248098626", "username": "shudai6"},
            server={"machine": "machine-a", "name": "Plex", "url": "http://plex:32400"},
            library={"id": "11", "name": "音乐"},
        )
        self.registry.select("shared-248098626")
        self.store = ActiveProfileStore(self.base, self.registry)
        self.routes = _Routes()
        self.engine = _Engine()
        self.payload = {}

        async def body(_request):
            return dict(self.payload)

        attach_v036_routes(self.routes, self.store, self.engine, body, lambda: None)

    def tearDown(self):
        self.temp.cleanup()

    def test_shared_profile_learning_uses_profile_identity_not_a_legacy_picker(self):
        from helper.behavior import profile_behavior_identity

        self.store.set("product_settings", {
            "behavior_enabled": True,
            "behavior_account_id": "wrong-old-selection",
            "behavior_user": "wrong-old-user",
        })

        account_id, username = profile_behavior_identity(self.store)

        self.assertEqual("248098626", account_id)
        self.assertEqual("shudai6", username)
        self.assertNotIn(("GET", "/api/product/settings/verified"), self.routes.handlers)
        self.assertNotIn(("POST", "/api/product/settings/verified"), self.routes.handlers)
        self.assertEqual(0, self.engine.plex_calls)

    def test_runtime_engine_store_keeps_profile_identity(self):
        from helper.behavior import profile_behavior_identity
        from helper.profile_runtime import ProfileRuntime

        class Engine:
            def __init__(self, store):
                self.store = store

        runtime = ProfileRuntime(self.base, self.registry, engine_factory=Engine)

        account_id, username = profile_behavior_identity(
            runtime.engine("shared-248098626").store
        )

        self.assertEqual(("248098626", "shudai6"), (account_id, username))


class ManagedReconnectTests(unittest.TestCase):
    def test_authorized_single_server_can_resume_after_page_refresh(self):
        with tempfile.TemporaryDirectory() as root:
            base = Store(Path(root))
            registry = ProfileRegistry(base)
            store = ActiveProfileStore(base, registry)
            store.set("plex_login_pending", {
                "id": "pin-1",
                "status": "authorized",
                "expires_at": 1,
                "user": {"id": "10", "username": "owner"},
                "resources": [{
                    "machine": "machine-a",
                    "name": "Main",
                    "token": "replacement-token",
                    "connections": [{"uri": "http://plex:32400"}],
                }],
            })
            routes = _Routes()
            engine = _ReconnectEngine()

            async def body(_request):
                return {}

            attach_v036_routes(routes, store, engine, body, lambda: None)
            result = routes.handlers[("POST", "/api/plex/login/resume")]()

            self.assertEqual("connected", result["status"])
            self.assertEqual("machine-a", result["machine"])
            self.assertEqual("replacement-token", store.get("settings")["plex_token"])
            self.assertEqual("connected", store.get("plex_login_pending")["status"])

    def test_authorized_multiple_servers_waits_for_an_explicit_choice(self):
        with tempfile.TemporaryDirectory() as root:
            base = Store(Path(root))
            registry = ProfileRegistry(base)
            store = ActiveProfileStore(base, registry)
            store.set("plex_login_pending", {
                "id": "pin-2",
                "status": "authorized",
                "user": {"id": "10", "username": "owner"},
                "resources": [
                    {"machine": "machine-a", "name": "Main", "owned": True, "connections": []},
                    {"machine": "machine-b", "name": "Backup", "owned": True, "connections": []},
                ],
            })
            routes = _Routes()

            async def body(_request):
                return {}

            attach_v036_routes(routes, store, _ReconnectEngine(), body, lambda: None)
            result = routes.handlers[("POST", "/api/plex/login/resume")]()

            self.assertEqual("selection_required", result["status"])
            self.assertEqual(["machine-a", "machine-b"], [row["machine"] for row in result["servers"]])
            self.assertEqual("", store.get("settings")["plex_token"])

    def test_official_login_reuses_preserved_identity_without_querying_revoked_credentials(self):
        with tempfile.TemporaryDirectory() as root:
            base = Store(Path(root))
            registry = ProfileRegistry(base)
            store = ActiveProfileStore(base, registry)
            store.set_many({
                "managed": {"folk": {"id": "playlist-1"}},
                "plex_reconnect_identity": {
                    "account": {"id": "10", "username": "owner"},
                    "server": {"machine": "machine-a", "name": "Main"},
                    "library": {"id": "15", "name": "Music"},
                },
                "plex_login_pending": {
                    "id": "pin-1",
                    "status": "authorized",
                    "user": {"id": "10", "username": "owner"},
                    "resources": [{
                        "machine": "machine-a",
                        "name": "Main",
                        "token": "replacement-token",
                        "connections": [{"uri": "http://plex:32400"}],
                    }],
                },
            })
            routes = _Routes()
            engine = _ReconnectEngine()

            async def body(_request):
                return {"confirm": True, "pin_id": "pin-1", "machine": "machine-a"}

            attach_v036_routes(routes, store, engine, body, lambda: None)
            result = asyncio.run(routes.handlers[("POST", "/api/plex/login/connect")](object()))

            self.assertEqual("15", result["section"])
            self.assertEqual("15", store.get("settings")["section"])
            self.assertEqual({}, store.get("plex_reconnect_identity"))
            self.assertTrue(all(row.get("plex_token") for row in engine.settings_seen))


if __name__ == "__main__":
    unittest.main()
