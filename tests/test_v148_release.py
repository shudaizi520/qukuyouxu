"""Regression contracts for the playlist workspace and proxy deployment."""
import sys
import tempfile
import unittest
import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.append(dict(attrs))


class _Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("src"):
            self.assets.append(attrs["src"])
        if tag == "link" and attrs.get("rel") == "stylesheet":
            self.assets.append(attrs.get("href", ""))
        if tag == "img" and attrs.get("src"):
            self.assets.append(attrs["src"])


class PlaylistNavigationTests(unittest.TestCase):
    def test_every_required_page_asset_is_self_hosted(self):
        static = ROOT / "src/helper/static"
        for path in static.glob("*.html"):
            assets = _Assets()
            assets.feed(path.read_text(encoding="utf-8"))
            for url in assets.assets:
                self.assertTrue(url.startswith("/static/") or url.startswith("data:"),
                                f"{path.name}: remote page asset {url}")

    def test_settings_management_links_escape_the_embedded_frame(self):
        page = (ROOT / "src/helper/static/settings.html").read_text(encoding="utf-8")
        parser = _Links()
        parser.feed(page)
        for path in ("/daily", "/mixes", "/library"):
            links = [row for row in parser.links if row.get("href") == path]
            self.assertTrue(links, path)
            self.assertTrue(all(row.get("target") == "_top" for row in links), path)

    def test_sidebar_create_and_import_are_distinct_actions(self):
        page = (ROOT / "src/helper/static/playlists.html").read_text(encoding="utf-8")
        for token in ('id="customPlaylistCreate"', 'id="customPlaylistImport"',
                      'aria-label="新建歌单"', 'aria-label="导入外部歌单"',
                      'id="playlistCreateDialog"'):
            self.assertTrue(token in page, token)

    def test_settings_opens_as_a_standalone_management_page(self):
        page = (ROOT / "src/helper/static/playlists.html").read_text(encoding="utf-8")
        script = (ROOT / "src/helper/static/playlists.js").read_text(encoding="utf-8")
        self.assertTrue('id="openSettings"' in page)
        self.assertTrue("location.assign('/settings')" in script)

    def test_mobile_sidebar_keeps_settings_accessible(self):
        page = (ROOT / "src/helper/static/playlists.html").read_text(encoding="utf-8")
        script = (ROOT / "src/helper/static/playlists.js").read_text(encoding="utf-8")
        style = (ROOT / "src/helper/static/product.css").read_text(encoding="utf-8")
        self.assertIn('id="mobileSettings"', page)
        self.assertIn("$('mobileSettings').onclick=", script)
        self.assertIn('.playlist-sidebar-footer', style)

    def test_global_rules_and_contextual_playlist_actions_are_named_differently(self):
        settings = (ROOT / "src/helper/static/settings.html").read_text(encoding="utf-8")
        script = (ROOT / "src/helper/static/playlists.js").read_text(encoding="utf-8")
        self.assertTrue("集中管理生成规则" in settings)
        self.assertTrue("调整此歌单" in script)

    def test_empty_search_targets_point_to_the_new_create_action(self):
        search = (ROOT / "src/helper/static/playlist-search.js").read_text(encoding="utf-8")
        self.assertTrue('侧栏“＋”新建歌单' in search)

    def test_create_dialog_submits_a_confirmed_plex_playlist_and_selects_it(self):
        script = (ROOT / "src/helper/static/playlists.js").read_text(encoding="utf-8")
        self.assertTrue("'/api/playlists/create'" in script)
        self.assertTrue("$('customPlaylistCreate').onclick" in script)
        self.assertTrue("loadPlaylists({kind:result.kind,key:result.key}" in script)

    def test_large_playlist_has_incremental_rows_without_rebuilding_on_player_events(self):
        page = (ROOT / "src/helper/static/playlists.html").read_text(encoding="utf-8")
        script = (ROOT / "src/helper/static/playlists.js").read_text(encoding="utf-8")
        self.assertTrue('id="playlistLoadMore"' in page)
        self.assertTrue('TRACK_BATCH_SIZE' in script)
        self.assertTrue('onStateChange:refreshPlayingRows' in script)

    def test_optional_plex_help_does_not_require_foreign_website(self):
        page = (ROOT / "src/helper/static/settings.html").read_text(encoding="utf-8")
        self.assertFalse('href="https://app.plex.tv/' in page)

    def test_manual_lan_plex_connection_is_available_without_foreign_login(self):
        page = (ROOT / "src/helper/static/settings.html").read_text(encoding="utf-8")
        self.assertIn('id="showManualFallback"', page)
        self.assertIn('>手动连接 Plex</button>', page)
        self.assertNotIn('id="showManualFallback" class="secondary" type="button" hidden', page)

    def test_enter_cannot_submit_manual_playlist_twice_while_pending(self):
        script = (ROOT / "src/helper/static/playlists.js").read_text(encoding="utf-8")
        self.assertIn("if(button.disabled)return;", script)


class ManualPlaylistTests(unittest.TestCase):
    def test_manual_playlist_is_not_shown_from_a_sibling_music_library(self):
        from helper.playlist_hub import sibling_owned_playlist_ids

        profiles = [
            {"id": "first", "kind": "owner", "account": {"id": "account"},
             "server": {"machine": "machine"}, "library": {"id": "11"}},
            {"id": "second", "kind": "owner", "account": {"id": "account"},
             "server": {"machine": "machine"}, "library": {"id": "15"}},
        ]

        class Registry:
            def get(self, profile_id):
                return next(row for row in profiles if row["id"] == profile_id)

            def list_public(self, enabled_only=False):
                return profiles

        class Store:
            def get(self, key, default=None):
                return ["721"] if key == "manual_plex_playlist_ids" else default

        runtime = SimpleNamespace(engine=lambda _profile_id: SimpleNamespace(store=Store()))
        with patch("helper.playlist_hub.assistant_playlist_rows", return_value=[]):
            self.assertEqual({"721"}, sibling_owned_playlist_ids(Registry(), runtime, "first"))

    def test_deleting_a_manually_created_playlist_clears_its_profile_scope(self):
        from helper.playlist_hub import remove_playlist
        from tests.test_v146_playlist_inventory import PlaylistInventoryTests

        engine, _plex = PlaylistInventoryTests().writable_engine()
        engine.store.set("manual_plex_playlist_ids", ["10", "99"])
        remove_playlist(engine, "plex", "10", "工作")
        self.assertEqual(["99"], engine.store.get("manual_plex_playlist_ids"))

    def test_create_api_requires_login_and_confirmation_before_plex_mutation(self):
        from helper.auth import AuthManager, COOKIE_NAME
        from helper.scoped_store import ScopedStore
        from helper.store import Store
        from helper.web import create_app
        from tests.test_release_blockers import asgi_request

        class Plex:
            def __init__(self):
                self.created = []

            def create_blank(self, title):
                self.created.append(title)
                return {"id": "721", "title": title, "smart": False, "items": []}

            def read_playlists_until(self, predicate):
                rows = [{"ratingKey": "721", "playlistType": "audio", "smart": "0"}]
                return rows if predicate(rows) else []

        class Engine:
            job = {"running": False}

            def __init__(self, store, plex):
                self.store, self.plex = store, plex

            def plex_factory(self, _settings):
                return self.plex

            def exclusive(self):
                return nullcontext()

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            app = create_app(store=store, start_scheduler=False)
            plex = Plex()
            engine = Engine(ScopedStore(store, "default", registry=app.state.profiles), plex)
            app.state.profile_runtime.engine = lambda _profile_id: engine
            path = "/api/playlists/create"
            status, _, _ = asgi_request(app, path, method="POST", body={"title": "周末听歌", "confirm": True})
            self.assertEqual(401, status)
            username = AuthManager(store).create_account("admin", "safe-password")
            session, _ = AuthManager(store).create_session(username)
            headers = {"cookie": f"{COOKIE_NAME}={session}"}
            status, _, _ = asgi_request(app, path, method="POST", headers=headers,
                                        body={"title": "周末听歌"})
            self.assertEqual(400, status)
            self.assertEqual([], plex.created)
            status, _, payload = asgi_request(app, path, method="POST", headers=headers,
                                              body={"title": "周末听歌", "confirm": True})
            self.assertEqual(200, status)
            self.assertEqual({"kind": "plex", "key": "721", "title": "周末听歌"}, json.loads(payload))
            self.assertEqual(["周末听歌"], plex.created)

    def test_plex_blank_creation_does_not_attach_an_arbitrary_song(self):
        from xml.etree import ElementTree as ET
        from helper.clients import PlexClient

        create_blank = getattr(PlexClient, "create_blank", None)
        self.assertTrue(callable(create_blank), "missing blank Plex playlist creation")
        client = object.__new__(PlexClient)
        calls = []

        def request(path, method="GET", params=None):
            calls.append((path, method, params))
            return ET.fromstring('<MediaContainer><Playlist ratingKey="721"/></MediaContainer>')

        client._xml = request
        client.playlist_state = lambda _playlist_id: self.fail("must retain the returned ID without an immediate read")
        result = create_blank(client, "周末听歌")
        self.assertEqual("721", result["id"])
        self.assertEqual([("/playlists", "POST", {"title": "周末听歌", "type": "audio", "smart": 0})], calls)

    def test_blank_plex_playlist_is_created_and_scoped_to_its_profile(self):
        from helper import playlist_hub
        from helper.store import Store

        create = getattr(playlist_hub, "create_manual_playlist", None)
        self.assertTrue(callable(create), "missing manual playlist creation")

        class Plex:
            def __init__(self):
                self.titles = []

            def create_blank(self, title):
                self.titles.append(title)
                return {"id": "721", "title": title, "smart": False, "items": []}

            def read_playlists_until(self, predicate):
                rows = [{"ratingKey": "721", "title": self.titles[-1],
                         "playlistType": "audio", "smart": "0", "leafCount": "0"}]
                return rows if predicate(rows) else []

        class Engine:
            def __init__(self, store, plex):
                self.store, self.plex = store, plex

            def plex_factory(self, _settings):
                return self.plex

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            store.profile_id = "default"
            plex = Plex()
            result = create(Engine(store, plex), "周末听歌")
            self.assertEqual({"kind": "plex", "key": "721", "title": "周末听歌"}, result)
            self.assertEqual(["周末听歌"], plex.titles)
            self.assertEqual(["721"], store.get("manual_plex_playlist_ids"))

    def test_uncertain_post_create_read_never_retries_or_loses_the_new_id(self):
        from helper.engine import SafetyError
        from helper.playlist_hub import create_manual_playlist
        from helper.store import Store

        class Plex:
            creates = 0

            def create_blank(self, title):
                self.creates += 1
                return {"id": "721", "title": title, "smart": False, "items": []}

            def read_playlists_until(self, _predicate):
                raise RuntimeError("Plex read timed out")

        class Engine:
            def __init__(self, store, plex):
                self.store, self.plex = store, plex

            def plex_factory(self, _settings):
                return self.plex

        with tempfile.TemporaryDirectory() as root:
            store, plex = Store(Path(root)), Plex()
            try:
                create_manual_playlist(Engine(store, plex), "周末听歌")
            except Exception as exc:
                self.assertIsInstance(exc, SafetyError)
                self.assertIn("不要重复创建", str(exc))
            else:
                self.fail("uncertain create read was reported as success")
            self.assertEqual(1, plex.creates)
            self.assertEqual(["721"], store.get("manual_plex_playlist_ids"))

    def test_invalid_manual_title_never_contacts_plex(self):
        from helper import playlist_hub

        create = getattr(playlist_hub, "create_manual_playlist", None)
        self.assertTrue(callable(create), "missing manual playlist creation")

        class Engine:
            store = None

            def plex_factory(self, _settings):
                raise AssertionError("invalid title contacted Plex")

        for title in ("", "  ", "x" * 81, "bad\nname"):
            with self.subTest(title=title), self.assertRaises(ValueError):
                create(Engine(), title)


class ReverseProxyTests(unittest.TestCase):
    def test_https_proxy_and_private_lan_origins_work_without_accepting_public_foreign_origins(self):
        from helper.web import create_app
        from helper.store import Store
        from tests.test_release_blockers import asgi_request

        with tempfile.TemporaryDirectory() as root:
            app = create_app(store=Store(Path(root)), start_scheduler=False,
                             public_origin="https://music.example")
            body = {"username": "admin", "password": "safe-password",
                    "confirm_password": "safe-password"}
            status, headers, _ = asgi_request(app, "/api/auth/setup", method="POST",
                headers={"host": "music.example", "origin": "https://music.example"}, body=body)
            self.assertEqual(200, status)
            self.assertIn(b"secure", headers[b"set-cookie"].lower())
            status, headers, _ = asgi_request(app, "/api/auth/login", method="POST",
                headers={"host": "192.168.50.99:9512", "origin": "http://192.168.50.99:9512"},
                body={"username": "admin", "password": "safe-password"})
            self.assertEqual(200, status)
            self.assertNotIn(b"secure", headers[b"set-cookie"].lower())
            status, _, _ = asgi_request(app, "/api/auth/login", method="POST",
                headers={"host": "music.example", "origin": "https://attacker.example"},
                body={"username": "admin", "password": "safe-password"})
            self.assertEqual(403, status)


if __name__ == "__main__":
    unittest.main()
