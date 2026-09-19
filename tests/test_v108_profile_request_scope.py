import asyncio
import json
import tempfile
import unittest
from pathlib import Path


async def _asgi_request(app, path, headers=None):
    raw_headers = [(b"host", b"testserver")]
    raw_headers.extend(
        (str(key).lower().encode("latin-1"), str(value).encode("latin-1"))
        for key, value in (headers or {}).items()
    )
    sent = False
    messages = []

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "scheme": "http", "path": path,
        "raw_path": path.encode("ascii"), "query_string": b"",
        "headers": raw_headers, "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    await app(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"")
        for message in messages if message["type"] == "http.response.body"
    )
    return start["status"], json.loads(body or b"{}")


def asgi_request(*args, **kwargs):
    return asyncio.run(_asgi_request(*args, **kwargs))


class ProfileRequestScopeV108Tests(unittest.TestCase):
    def setUp(self):
        from helper.auth import AuthManager, COOKIE_NAME
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ScopedStore
        from helper.store import Store
        from helper.web import create_app

        self.temp = tempfile.TemporaryDirectory()
        self.base = Store(Path(self.temp.name))
        registry = ProfileRegistry(self.base)
        registry.create(
            name="Friend",
            kind="shared",
            profile_id="friend-a",
            token="friend-secret",
        )
        ScopedStore(self.base, "default").set("daily_notice", "owner-marker")
        ScopedStore(self.base, "friend-a").set("daily_notice", "friend-marker")

        auth = AuthManager(self.base)
        auth.create_account("admin", "correct-horse")
        token, _ = auth.create_session("admin")

        self.app = create_app(store=self.base, start_scheduler=False)
        self.registry = self.app.state.profiles
        app = self.app

        @app.get("/api/test/profile-marker")
        async def profile_marker():
            return {"daily_notice": app.state.store.get("daily_notice", "")}

        self.headers = {"cookie": f"{COOKIE_NAME}={token}"}

    def tearDown(self):
        self.temp.cleanup()

    def test_profile_header_reads_requested_scope_without_changing_global_active(self):
        status, response = asgi_request(
            self.app, "/api/test/profile-marker",
            headers={**self.headers, "X-Plex-Profile": "friend-a"},
        )

        self.assertEqual(200, status)
        self.assertEqual("friend-marker", response["daily_notice"])
        self.assertEqual("default", self.registry._saved_active_id())

    def test_two_tabs_can_read_different_profiles(self):
        _, owner = asgi_request(
            self.app, "/api/test/profile-marker",
            headers={**self.headers, "X-Plex-Profile": "default"},
        )
        _, friend = asgi_request(
            self.app, "/api/test/profile-marker",
            headers={**self.headers, "X-Plex-Profile": "friend-a"},
        )

        self.assertEqual("owner-marker", owner["daily_notice"])
        self.assertEqual("friend-marker", friend["daily_notice"])

    def test_disabled_explicit_profile_is_rejected(self):
        self.registry.archive("friend-a")

        status, response = asgi_request(
            self.app, "/api/test/profile-marker",
            headers={**self.headers, "X-Plex-Profile": "friend-a"},
        )

        self.assertEqual(400, status)
        self.assertEqual("profile_unavailable", response["code"])

    def test_unscoped_profile_list_allows_stale_tab_recovery(self):
        self.registry.archive("friend-a")

        status, response = asgi_request(
            self.app, "/api/plex/profiles",
            headers={**self.headers, "X-Plex-Profile": "friend-a"},
        )

        self.assertEqual(200, status)
        self.assertEqual(["default"], [row["id"] for row in response["items"]])

    def test_auth_asset_exports_per_tab_profile_contract(self):
        script = (Path(__file__).parents[1] / "src/helper/static/auth.js").read_text()

        self.assertIn("X-Plex-Profile", script)
        self.assertIn("profile", script)
        self.assertIn("setProfile", script)
        self.assertIn("sessionStorage", script)
        self.assertIn("profile_unavailable", script)
        self.assertIn("syncProfile", script)


if __name__ == "__main__":
    unittest.main()
