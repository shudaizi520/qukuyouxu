import asyncio
import contextlib
import inspect
import sys
import unittest
import types
import fastapi
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if "fastapi" not in sys.modules:
    fastapi = types.ModuleType("fastapi")
    fastapi.Request = object
    sys.modules["fastapi"] = fastapi

from helper.profile_web import attach_profile_routes


class _Routes:
    def __init__(self):
        self.handlers = {}

    def _register(self, method, path):
        def decorator(handler):
            self.handlers[(method, path)] = handler
            return handler
        return decorator

    def get(self, path):
        return self._register("GET", path)

    def post(self, path):
        return self._register("POST", path)


class ProfileHttpContractTests(unittest.TestCase):
    def test_profile_post_routes_declare_framework_request_injection(self):
        routes = _Routes()
        attach_profile_routes(routes, object(), object(), lambda request: {}, lambda: None)

        post_paths = (
            "/api/plex/profiles/select",
            "/api/plex/profiles/create",
            "/api/plex/profiles/remove",
            "/api/plex/profiles/restore",
            "/api/plex/recipients/home/import",
            "/api/plex/recipients/shared/import",
        )
        for path in post_paths:
            with self.subTest(path=path):
                parameter = inspect.signature(routes.handlers[("POST", path)]).parameters["request"]
                self.assertIsNot(
                    inspect.Signature.empty,
                    parameter.annotation,
                    "FastAPI would treat untyped 'request' as a required query parameter and return 422",
                )
                self.assertEqual("Request", parameter.annotation)

    def test_profile_selection_uses_the_shared_operation_lock(self):
        routes = _Routes()
        calls = []
        class Registry:
            def select(self, profile_id):
                calls.append(("select", profile_id))
                return {"id": profile_id}
        class Engine:
            def exclusive(self):
                @contextlib.contextmanager
                def locked():
                    calls.append("lock")
                    yield
                return locked()
        async def body(_request):
            return {"profile_id": "friend"}
        def ensure_idle():
            calls.append("idle")

        attach_profile_routes(routes, object(), Registry(), body, ensure_idle, engine=Engine())
        asyncio.run(routes.handlers[("POST", "/api/plex/profiles/select")](object()))

        self.assertEqual(["idle", "lock", ("select", "friend")], calls)


if __name__ == "__main__":
    unittest.main()
