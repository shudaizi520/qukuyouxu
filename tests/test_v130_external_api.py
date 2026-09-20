import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from tests.test_v130_external_service import snapshot


async def _request(app, url, method="GET", headers=None, body=None):
    parsed = urlsplit(url)
    payload = b"" if body is None else json.dumps(body).encode()
    raw_headers = [(b"host", b"testserver")]
    raw_headers.extend(
        (str(key).lower().encode("latin-1"), str(value).encode("latin-1"))
        for key, value in (headers or {}).items()
    )
    if payload:
        raw_headers.extend([(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())])
    sent = False
    messages = []
    response_complete = asyncio.Event()

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": payload, "more_body": False}
        await response_complete.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            response_complete.set()

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": parsed.path, "raw_path": parsed.path.encode(),
        "query_string": parsed.query.encode(), "headers": raw_headers,
        "client": ("127.0.0.1", 12345), "server": ("testserver", 80),
    }
    await app(scope, receive, send)
    start = next(item for item in messages if item["type"] == "http.response.start")
    data = b"".join(item.get("body", b"") for item in messages if item["type"] == "http.response.body")
    return start["status"], dict(start.get("headers") or []), data


def request(*args, **kwargs):
    return asyncio.run(_request(*args, **kwargs))


class ExternalApiV130Tests(unittest.TestCase):
    def setUp(self):
        from helper.auth import AuthManager, COOKIE_NAME
        from helper.external_store import ExternalRepository
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ScopedStore
        from helper.store import Store
        from helper.web import create_app

        self.temp = tempfile.TemporaryDirectory()
        self.base = Store(Path(self.temp.name))
        registry = ProfileRegistry(self.base)
        registry.create("长辈", "home", profile_id="parent")
        default = ScopedStore(self.base, "default", registry=registry)
        settings = default.get("settings")
        settings.update(plex_url="http://plex", plex_token="secret-token", section="11")
        default.set("settings", settings)
        default.set("catalog", [
            {"id": "40", "title": "同名歌", "artist": "歌手丁", "album": "版本 A", "available": True, "paths": ["/private/a.flac"]},
            {"id": "41", "title": "同名歌", "artist": "歌手丁", "album": "版本 B", "available": True, "paths": ["/private/b.flac"]},
            {"id": "42", "title": "同名歌", "artist": "另一位", "album": "", "available": True, "paths": []},
        ])
        repository = ExternalRepository(default)
        self.source = repository.upsert_source("default", snapshot(), 2_000_000_000)
        repository.replace_matches("default", self.source["id"], [
            {"source_track_key": "a", "status": "matched", "plex_track_id": "10", "candidate_ids": [], "reason": "matched", "manual": False},
            {"source_track_key": "b", "status": "missing", "plex_track_id": "", "candidate_ids": [], "reason": "missing", "manual": False},
            {"source_track_key": "c", "status": "matched", "plex_track_id": "30", "candidate_ids": [], "reason": "matched", "manual": False},
            {"source_track_key": "d", "status": "review", "plex_track_id": "", "candidate_ids": ["40", "41", "42", "43"], "reason": "ambiguous", "manual": False},
        ], "catalog-r1")
        repository.append_run("default", self.source["id"], {
            "kind": "import", "status": "completed", "started_at": 10,
            "finished_at": 12, "message": "导入完成", "private_detail": "must-not-leak",
        })
        parent_source = snapshot(revision="parent")
        self.parent_source = repository.upsert_source("parent", parent_source, 2_000_000_000)
        auth = AuthManager(self.base)
        auth.create_account("admin", "safe-password")
        token, _ = auth.create_session("admin")
        self.headers = {"cookie": f"{COOKIE_NAME}={token}", "X-Plex-Profile": "default"}
        self.app = create_app(store=self.base, start_scheduler=False)

    def tearDown(self):
        self.app.state.profile_runtime.close()
        self.temp.cleanup()

    def json(self, url, method="GET", body=None, headers=None):
        status, response_headers, payload = request(
            self.app, url, method=method, headers=headers if headers is not None else self.headers, body=body
        )
        return status, json.loads(payload or b"{}"), response_headers

    def test_routes_require_login_and_pin_sources_to_requested_profile(self):
        status, _, _ = self.json("/api/external/sources", headers={})
        self.assertEqual(401, status)

        status, payload, _ = self.json(
            f"/api/external/sources/{self.source['id']}",
            headers={**self.headers, "X-Plex-Profile": "parent"},
        )
        self.assertEqual(400, status)
        self.assertNotIn("secret-token", json.dumps(payload))

    def test_list_and_detail_are_compact_and_never_expose_secrets_or_all_candidates(self):
        status, listing, _ = self.json("/api/external/sources")
        self.assertEqual(200, status)
        self.assertEqual(self.source["id"], listing["items"][0]["id"])
        self.assertEqual("completed", listing["items"][0]["last_run"]["status"])
        self.assertNotIn("private_detail", listing["items"][0]["last_run"])
        status, detail, _ = self.json(f"/api/external/sources/{self.source['id']}?status=review&page=1&limit=20")
        self.assertEqual(200, status)
        self.assertEqual(1, detail["total"])
        self.assertEqual(3, len(detail["tracks"][0]["candidate_ids"]))
        self.assertEqual("版本 A", detail["tracks"][0]["candidates"][0]["album"])
        self.assertNotIn("paths", detail["tracks"][0]["candidates"][0])
        rendered = json.dumps(detail, ensure_ascii=False)
        self.assertNotIn("secret-token", rendered)
        self.assertNotIn("QKYX:external", rendered)

    def test_publish_requires_confirm_and_current_revision_before_background_work(self):
        url = f"/api/external/sources/{self.source['id']}/publish"
        status, _, _ = self.json(url, method="POST", body={"title": "百万收藏", "revision": self.source["revision"]})
        self.assertEqual(400, status)
        status, _, _ = self.json(url, method="POST", body={"confirm": True, "title": "百万收藏", "revision": "stale"})
        self.assertEqual(400, status)

    def test_follow_setting_is_immediate_and_remove_requires_exact_confirmation(self):
        source_id = self.source["id"]
        status, payload, _ = self.json(
            f"/api/external/sources/{source_id}/settings", method="POST", body={"follow_updates": True}
        )
        self.assertEqual(200, status)
        self.assertTrue(payload["follow_updates"])

        status, _, _ = self.json(
            f"/api/external/sources/{source_id}/remove", method="POST",
            body={"confirm": False, "title": "百万收藏"},
        )
        self.assertEqual(400, status)
        status, _, _ = self.json(
            f"/api/external/sources/{source_id}/remove", method="POST",
            body={"confirm": True, "title": "错误名称"},
        )
        self.assertEqual(400, status)

    def test_exports_and_unknown_format(self):
        source_id = self.source["id"]
        status, headers, body = request(
            self.app, f"/api/external/sources/{source_id}/export?format=text", headers=self.headers
        )
        self.assertEqual(200, status)
        self.assertIn("缺失歌", body.decode())
        self.assertIn(b"attachment", headers[b"content-disposition"])
        status, _, _ = self.json(f"/api/external/sources/{source_id}/export?format=pdf")
        self.assertEqual(400, status)

    def test_oversized_import_is_rejected_before_dispatch(self):
        status, payload, _ = self.json(
            "/api/external/import", method="POST",
            body={"filename": "big.txt", "content_base64": "A" * (2 * 1024 * 1024 + 1)},
        )
        self.assertEqual(413, status)
        self.assertIn("2MB", payload["error"])

    def test_audio_route_is_attached_inside_the_authenticated_api(self):
        paths = {getattr(route, "path", "") for route in self.app.routes}
        self.assertIn("/api/external/sources/{source_id}/tracks/{track_key}/audio", paths)


if __name__ == "__main__":
    unittest.main()
