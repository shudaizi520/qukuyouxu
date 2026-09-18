import asyncio
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path


async def _asgi_request(app, path, method="GET", headers=None, body=None):
    payload = b"" if body is None else json.dumps(body).encode("utf-8")
    supplied_headers = {str(key).lower(): value for key, value in (headers or {}).items()}
    host = supplied_headers.pop("host", "testserver")
    raw_headers = [(b"host", str(host).encode("latin-1"))]
    for key, value in supplied_headers.items():
        raw_headers.append((str(key).lower().encode("latin-1"), str(value).encode("latin-1")))
    if payload:
        raw_headers.extend([
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode("ascii")),
        ])
    sent = False
    messages = []

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": payload, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path,
        "raw_path": path.encode("ascii"), "query_string": b"",
        "headers": raw_headers, "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    await app(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"") for message in messages if message["type"] == "http.response.body"
    )
    return start["status"], dict(start.get("headers", [])), response_body


def asgi_request(*args, **kwargs):
    return asyncio.run(_asgi_request(*args, **kwargs))


class ReleaseBlockerTests(unittest.TestCase):
    def test_starting_a_new_preview_invalidates_the_old_confirmation(self):
        from helper.engine import Engine
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            store.set("plan", {"id": "old-plan", "applied": False, "groups": []})
            engine = Engine(store)
            release = threading.Event()
            engine.preview = lambda *_args, **_kwargs: release.wait(2)

            engine.start_job("preview")
            try:
                self.assertIsNone(store.get("plan"))
            finally:
                release.set()
                deadline = time.time() + 2
                while engine.job["running"] and time.time() < deadline:
                    time.sleep(0.01)

    def _app(self, root, **kwargs):
        from helper.store import Store
        from helper.web import create_app

        return create_app(store=Store(Path(root)), start_scheduler=False, **kwargs)

    def test_malformed_host_cannot_turn_protected_route_into_public_route(self):
        with tempfile.TemporaryDirectory() as root:
            app = self._app(root)
            status, _, _ = asgi_request(
                app, "/api/status", headers={"host": "testserver/api/auth/status?"}
            )
            self.assertEqual(401, status)

    def test_fresh_setup_creates_the_first_admin_without_an_extra_code(self):
        with tempfile.TemporaryDirectory() as root:
            app = self._app(root)
            payload = {"username": "admin", "password": "safe-password", "confirm_password": "safe-password"}
            status, _, _ = asgi_request(app, "/api/auth/setup", method="POST", body=payload)
            self.assertEqual(200, status)

    def test_fresh_setup_never_creates_a_setup_code(self):
        from helper.store import Store
        from helper.web import create_app

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            create_app(store=store, start_scheduler=False)
            self.assertIsNone(store.get("auth_bootstrap_token"))

    def test_public_origin_supports_https_reverse_proxy_without_trusting_other_origins(self):
        from helper.auth import AuthManager, COOKIE_NAME
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            auth = AuthManager(store)
            username = auth.create_account("admin", "safe-password")
            session, _ = auth.create_session(username)
            app = self._app(root, public_origin="https://music.example")
            headers = {"cookie": f"{COOKIE_NAME}={session}", "origin": "https://music.example"}
            status, _, _ = asgi_request(app, "/api/schedule", method="POST", headers=headers, body={"enabled": False})
            self.assertEqual(200, status)
            headers["origin"] = "https://attacker.example"
            status, _, _ = asgi_request(app, "/api/schedule", method="POST", headers=headers, body={"enabled": False})
            self.assertEqual(403, status)

    def test_authenticated_malformed_host_is_rejected_without_server_error(self):
        from helper.auth import AuthManager, COOKIE_NAME
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            auth = AuthManager(store)
            username = auth.create_account("admin", "safe-password")
            session, _ = auth.create_session(username)
            app = self._app(root)
            status, _, _ = asgi_request(
                app, "/api/plex/profiles",
                headers={"cookie": f"{COOKIE_NAME}={session}", "host": "testserver/bad"},
            )
            self.assertEqual(400, status)

    def test_manual_connection_cannot_switch_when_only_smart_playlist_is_managed(self):
        from helper.auth import AuthManager, COOKIE_NAME
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            auth = AuthManager(store)
            username = auth.create_account("admin", "safe-password")
            session, _ = auth.create_session(username)
            settings = store.get("settings")
            settings.update(plex_url="http://old-plex:32400", plex_token="old-token", section="11")
            store.set_many({"settings": settings, "smart_mix_managed": {"weekly": {"id": "playlist"}}})
            app = self._app(root)
            status, _, _ = asgi_request(
                app, "/api/settings", method="POST",
                headers={"cookie": f"{COOKIE_NAME}={session}"},
                body={"plex_url": "http://new-plex:32400", "section": "12"},
            )
            self.assertEqual(400, status)

    def test_qq_authorization_manager_follows_active_profile(self):
        with tempfile.TemporaryDirectory() as root:
            app = self._app(root)
            registry = app.state.profiles
            registry.create(name="朋友", kind="shared", profile_id="friend")
            registry.select("friend")
            self.assertIs(app.state.qq_auth.qq_client, app.state.profile_runtime.engine("friend").qq)

    def test_every_accepted_background_job_dispatches_to_an_operation(self):
        from helper.library_engine import LibraryEngine
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            engine = LibraryEngine(Store(Path(root)))
            cases = {
                "daily_repair": ("repair_daily", {"snapshot_id": "snapshot"}),
                "base_preview": ("preview_base", {}),
                "base_apply": ("apply_base", {"plan_id": "plan"}),
                "single_check": ("check_single_connection", {}),
                "single_enrich": ("enrich_singles", {}),
            }
            for kind, (method_name, arguments) in cases.items():
                with self.subTest(kind=kind):
                    calls = []
                    setattr(engine, method_name, lambda *args, _kind=kind, **kwargs: calls.append((_kind, args, kwargs)))
                    engine.start_job(kind, **arguments)
                    deadline = time.time() + 2
                    while engine.job["running"] and time.time() < deadline:
                        time.sleep(0.01)
                    self.assertFalse(engine.job["running"])
                    self.assertEqual("", engine.job["error"])
                    self.assertEqual(kind, calls[0][0])


if __name__ == "__main__":
    unittest.main()
