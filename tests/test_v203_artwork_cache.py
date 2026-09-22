"""Private artwork caching must not weaken account or audio response privacy."""
import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import Request, Response
from helper import web
from helper.store import Store


STATIC = Path(__file__).resolve().parents[1] / "src/helper/static"


class ArtworkCacheTests(unittest.TestCase):
    def test_only_successful_get_artwork_and_cover_are_privately_cached(self):
        policy = web.artwork_cache_policy
        for path in (
            "/api/playlists/library/tracks/10/artwork",
            "/api/playlists/plex/123/tracks/10/artwork",
        ):
            headers = policy(path, "GET", 200, authenticated=True, scoped=True)
            self.assertIn("private", headers["Cache-Control"])
            self.assertIn("no-cache", headers["Cache-Control"])
            self.assertNotIn("max-age", headers["Cache-Control"])
            self.assertIn("Cookie", headers["Vary"])
            self.assertIn("X-Plex-Profile", headers["Vary"])
            self.assertEqual(headers, policy(path, "GET", 304, authenticated=True, scoped=True))
        cover = policy("/api/playlists/daily/daily/cover", "GET", 200,
                       authenticated=True, scoped=True)
        self.assertEqual("private, no-cache", cover["Cache-Control"])

        for path, method, status in (
            ("/api/auth/status", "GET", 200),
            ("/api/playlists/library/tracks/10/audio", "GET", 200),
            ("/api/playlists/daily/daily/cover", "POST", 200),
            ("/api/playlists/daily/daily/cover", "GET", 401),
            ("/api/playlists/daily/daily/cover", "GET", 500),
        ):
            self.assertEqual({"Cache-Control": "no-store"},
                             policy(path, method, status, authenticated=True, scoped=True))
        self.assertEqual({"Cache-Control": "no-store"},
                         policy("/api/playlists/daily/daily/cover", "GET", 200,
                                authenticated=False, scoped=True))
        self.assertEqual({"Cache-Control": "no-store"},
                         policy("/api/playlists/daily/daily/cover", "GET", 200,
                                authenticated=True, scoped=False))

    def test_security_middleware_uses_policy(self):
        source = inspect.getsource(web.create_app)
        self.assertIn("for name, value in artwork_cache_policy(", source)
        self.assertIn("authenticated=bool(session_user), scoped=scoped", source)

    def test_etag_requires_session_and_changes_with_profile_identity(self):
        from helper.playlist_hub import artwork_etag

        class Store:
            def get(self, key):
                return "install-secret" if key == "installation_id" else None

        store = Store()
        args = (store, "session-token", "profile-a", "created-1", "/artwork/10", "/thumb/10", 300)
        tag = artwork_etag(*args, now=1000)
        self.assertTrue(tag.startswith('W/"'))
        self.assertEqual(tag, artwork_etag(*args, now=1100))
        self.assertNotEqual(tag, artwork_etag(*args, now=1300))
        self.assertNotEqual(tag, artwork_etag(store, "other-session", *args[2:], now=1000))
        self.assertNotEqual(tag, artwork_etag(store, "session-token", "profile-a", "created-2",
                                               "/artwork/10", "/thumb/10", 300, now=1000))
        self.assertNotEqual(tag, artwork_etag(store, "session-token", "profile-a", "created-1",
                                               "/artwork/10", "/thumb/changed", 300, now=1000))
        self.assertEqual("", artwork_etag(store, "", "profile-a", "created-1",
                                          "/artwork/10", "/thumb/10", 300, now=1000))

    def test_only_cover_lookup_uses_browser_cache(self):
        auth = (STATIC / "auth.js").read_text(encoding="utf-8")
        self.assertIn("cache:'no-store'", auth)
        self.assertIn("/cover", auth)
        self.assertIn("opt.cache='default'", auth)

    def test_image_loading_is_bounded_independently_of_cover_lookup(self):
        script = (STATIC / "playlist-artwork.js").read_text(encoding="utf-8")
        self.assertIn("MAX_ACTIVE_IMAGES", script)
        self.assertIn("pumpImages", script)
        self.assertIn("if(task.image.isConnected)continue", script)
        self.assertIn("setTimeout(failed,12000)", script)
        self.assertIn("sessionStorage.getItem(key)", script)
        self.assertIn("sessionStorage.setItem(key,JSON.stringify", script)
        self.assertIn("Date.now()-record.saved<3600000", script)
        self.assertIn("node.dataset.coverIds===signature", script)
        self.assertIn("node.children.length===valid.length", script)
        self.assertIn("delete node.dataset.coverIds;if(!node.children.length)", script)
        self.assertIn("pch-auth-logout", script)
        playlists = (STATIC / "playlists.js").read_text(encoding="utf-8")
        self.assertIn("cacheUser:()=>PCHAuth.status()?.username||''", playlists)
        self.assertIn("p?.account?.id&&p?.server?.machine&&p?.library?.id", playlists)

    def test_cached_artwork_revalidates_membership_and_content_before_304(self):
        def req(path, tag=""):
            headers = [(b"cookie", b"pch_session=session-token")]
            if tag:
                headers.append((b"if-none-match", tag.encode()))
            return Request({"type": "http", "method": "GET", "scheme": "http",
                            "server": ("testserver", 80), "path": path,
                            "query_string": b"", "headers": headers})

        with tempfile.TemporaryDirectory() as root:
            app = web.create_app(store=Store(Path(root)), start_scheduler=False)
            try:
                cover = next(route.endpoint for route in app.routes
                             if getattr(route, "path", "") == "/api/playlists/{kind}/{key}/cover")
                artwork = next(route.endpoint for route in app.routes
                               if getattr(route, "path", "") == "/api/playlists/library/tracks/{track_id}/artwork")
                playlist_artwork = next(route.endpoint for route in app.routes
                                        if getattr(route, "path", "") == "/api/playlists/{kind}/{key}/tracks/{track_id}/artwork")
                with patch("helper.playlist_hub.playlist_cover_candidates",
                           return_value={"track_ids": ["10"]}) as candidates:
                    response = Response()
                    self.assertEqual({"track_ids": ["10"]},
                                     cover("daily", "daily", req("/api/playlists/daily/daily/cover"), response))
                    tag = response.headers["etag"]
                    unchanged = cover("daily", "daily",
                                      req("/api/playlists/daily/daily/cover", tag), Response())
                    self.assertEqual(304, unchanged.status_code)
                    self.assertEqual(2, candidates.call_count)
                    candidates.return_value = {"track_ids": ["11"]}
                    changed = cover("daily", "daily",
                                    req("/api/playlists/daily/daily/cover", tag), Response())
                    self.assertEqual({"track_ids": ["11"]}, changed)
                    candidates.side_effect = ValueError("歌单已删除")
                    with self.assertRaisesRegex(ValueError, "歌单已删除"):
                        cover("daily", "daily", req("/api/playlists/daily/daily/cover", tag), Response())

                with patch("helper.playlist_hub.library_artwork_track",
                           return_value={"thumb": "/thumb/10"}) as lookup, patch(
                           "helper.playlist_hub._stream_artwork",
                           return_value=Response(b"jpeg", media_type="image/jpeg")) as stream:
                    path = "/api/playlists/library/tracks/10/artwork"
                    first = artwork("10", req(path), "default")
                    self.assertEqual(200, first.status_code)
                    unchanged = artwork("10", req(path, first.headers["etag"]), "default")
                    self.assertEqual(304, unchanged.status_code)
                    self.assertEqual(2, lookup.call_count)
                    self.assertEqual(1, stream.call_count)
                    lookup.return_value = {"thumb": "/thumb/changed"}
                    self.assertEqual(200, artwork("10", req(path, first.headers["etag"]), "default").status_code)
                    lookup.side_effect = ValueError("这首歌不属于当前曲库")
                    with self.assertRaisesRegex(ValueError, "不属于当前曲库"):
                        artwork("10", req(path, first.headers["etag"]), "default")
                with patch("helper.playlist_hub.playlist_artwork_track",
                           return_value={"thumb": "/thumb/10"}) as lookup, patch(
                           "helper.playlist_hub._stream_artwork",
                           return_value=Response(b"jpeg", media_type="image/jpeg")) as stream:
                    path = "/api/playlists/daily/daily/tracks/10/artwork"
                    first = playlist_artwork("daily", "daily", "10", req(path), "default")
                    self.assertEqual(200, first.status_code)
                    self.assertEqual(304, playlist_artwork(
                        "daily", "daily", "10", req(path, first.headers["etag"]), "default").status_code)
                    self.assertEqual(2, lookup.call_count)
                    self.assertEqual(1, stream.call_count)
                    lookup.side_effect = ValueError("当前歌单中没有这首歌曲")
                    with self.assertRaisesRegex(ValueError, "当前歌单中没有这首歌曲"):
                        playlist_artwork("daily", "daily", "10", req(path, first.headers["etag"]), "default")
            finally:
                app.state.profile_runtime.close()
