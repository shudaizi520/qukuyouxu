import tempfile
import unittest
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/helper/static"
sys.path.insert(0, str(ROOT / "src"))


class PlaylistHubRowsTests(unittest.TestCase):
    def setUp(self):
        from helper.external_store import ExternalRepository
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ScopedStore
        from helper.store import Store

        self.temp = tempfile.TemporaryDirectory()
        self.base = Store(Path(self.temp.name))
        self.registry = ProfileRegistry(self.base)
        self.store = ScopedStore(self.base, "default", registry=self.registry)
        self.store.set("daily_managed", {
            "id": "daily-1", "title": "每日推荐", "published_at": 2_000,
        })
        self.store.set("daily_published_view", {
            "count": 30, "published_at": 2_000,
        })
        self.store.set("managed", {
            "theme:drive": {"id": "theme-1", "title": "开车精选"},
        })
        self.store.set("plan", {
            "groups": [{"id": "theme:drive", "desired": ["1", "2", "3"]}],
        })
        self.store.set("smart_mix_managed", {
            "weekly": {"id": "smart-1", "title": "每周常听", "updated_at": 3_000},
        })
        self.store.set("smart_mix_plans", {
            "weekly": {"items": [{"id": "1"}, {"id": "2"}], "applied": True},
        })

        repository = ExternalRepository(self.store)
        self.external = repository.upsert_source("default", {
            "provider": "qq", "external_id": "100", "url": "https://y.qq.com/n/ryqq/playlist/100",
            "title": "百万收藏", "revision": "r1", "tracks": [
                {"source_track_key": "a", "position": 0, "title": "歌一", "artists": ["歌手甲"]},
                {"source_track_key": "b", "position": 1, "title": "歌二", "artists": ["歌手乙"]},
            ],
        }, 4_000)
        repository.replace_matches("default", self.external["id"], [
            {"source_track_key": "a", "status": "matched", "plex_track_id": "10"},
            {"source_track_key": "b", "status": "missing", "plex_track_id": ""},
        ], "catalog-r1")
        repository.save_managed("default", self.external["id"], {
            "id": "external-1", "title": "百万收藏", "fingerprint": "fp",
        })

    def tearDown(self):
        self.temp.cleanup()

    def test_collects_every_assistant_owned_playlist_in_one_consistent_shape(self):
        from helper.playlist_hub import playlist_rows

        rows = playlist_rows(self.store)
        self.assertEqual(
            ["daily", "smart", "category", "external"],
            [row["kind"] for row in rows],
        )
        self.assertEqual(
            ["每日推荐", "每周常听", "开车精选", "百万收藏"],
            [row["title"] for row in rows],
        )
        self.assertEqual([30, 2, 3, 1], [row["count"] for row in rows])
        self.assertTrue(all(row["playlist_id"] for row in rows))
        self.assertTrue(all(row["manage_url"].startswith("/") for row in rows))

    def test_every_automatic_playlist_writer_applies_manual_track_choices(self):
        for name in ("daily.py", "smart_mix_web.py", "engine.py", "external_service.py"):
            source = (ROOT / "src/helper" / name).read_text(encoding="utf-8")
            self.assertIn("apply_manual_edits", source, name)


class _PlaylistPlex:
    def __init__(self, state, audio=None):
        self.state = state
        self.audio = audio
        self.deleted = []

    def identity(self):
        return {"machine": "machine-a"}

    def playlist_state(self, playlist_id):
        if str(playlist_id) != str(self.state["id"]):
            raise ValueError("missing")
        return {**self.state, "items": [dict(row) for row in self.state["items"]]}

    def open_audio_part(self, track_id, range_header=""):
        self.last_audio = (str(track_id), range_header)
        return self.audio

    def delete_playlist(self, playlist_id):
        self.deleted.append(str(playlist_id))

    def append(self, playlist_id, ids):
        for track_id in ids:
            self.state["items"].append({"id": str(track_id), "item_id": str(100 + len(self.state["items"]))})

    def remove_items(self, playlist_id, item_ids):
        wanted = {str(value) for value in item_ids}
        self.state["items"] = [row for row in self.state["items"] if str(row.get("item_id")) not in wanted]

    def read_playlist_until(self, playlist_id, predicate, attempts=8, delay=0.25):
        state = self.playlist_state(playlist_id)
        return state if predicate(state) else state


class _PlaylistEngine:
    def __init__(self, store, plex):
        self.store = store
        self.plex = plex

    def plex_factory(self, _settings):
        return self.plex

    def marker(self, category_id):
        return "[owned:" + str(category_id) + "]"

    def daily_scope(self):
        return "scope-a"

    def _save_snapshot(self, snapshot):
        rows = list(self.store.get("snapshots", []) or [])
        existing = next((index for index, row in enumerate(rows) if row.get("id") == snapshot["id"]), None)
        if existing is None:
            rows.append(dict(snapshot))
        else:
            rows[existing] = dict(snapshot)
        self.store.set("snapshots", rows)


class PlaylistHubPlaybackTests(unittest.TestCase):
    def setUp(self):
        from helper.engine import fingerprint
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ScopedStore
        from helper.store import Store

        self.temp = tempfile.TemporaryDirectory()
        self.base = Store(Path(self.temp.name))
        registry = ProfileRegistry(self.base)
        self.store = ScopedStore(self.base, "default", registry=registry)
        settings = self.store.get("settings")
        settings.update(plex_url="http://plex", plex_token="secret-token", section="1")
        self.store.set("settings", settings)
        self.store.set("catalog", [
            {"id": "10", "title": "第一首", "artist": "甲", "album": "专辑甲", "duration": 180, "thumb": "/library/metadata/10/thumb/1", "available": True},
            {"id": "20", "title": "第二首", "artist": "乙", "album": "专辑乙", "duration": 200, "available": True},
        ])
        self.state = {
            "id": "900", "title": "每日推荐", "summary": "[owned:daily]",
            "items": [{"id": "10", "item_id": "1"}, {"id": "20", "item_id": "2"}],
        }
        self.store.set("daily_managed", {
            "id": "900", "title": "每日推荐", "machine": "machine-a", "scope": "scope-a",
            "fingerprint": fingerprint(self.state),
        })
        self.plex = _PlaylistPlex(self.state)
        self.engine = _PlaylistEngine(self.store, self.plex)

    def tearDown(self):
        self.temp.cleanup()

    def test_detail_preserves_playlist_order_and_supplies_track_metadata(self):
        from helper.playlist_hub import playlist_detail

        result = playlist_detail(self.engine, "daily", "daily")
        self.assertEqual("每日推荐", result["title"])
        self.assertEqual(["10", "20"], [row["id"] for row in result["tracks"]])
        self.assertEqual(["第一首", "第二首"], [row["title"] for row in result["tracks"]])
        self.assertEqual([180, 200], [row["duration"] for row in result["tracks"]])
        self.assertEqual("/library/metadata/10/thumb/1", result["tracks"][0]["thumb"])

    def test_audio_rejects_tracks_outside_the_selected_owned_playlist(self):
        from helper.playlist_hub import stream_playlist_audio

        with self.assertRaisesRegex(ValueError, "当前歌单"):
            stream_playlist_audio(self.engine, "daily", "daily", "999", "", "session")

    def test_daily_delete_validates_ownership_and_never_deletes_music_files(self):
        from helper.playlist_hub import remove_playlist

        result = remove_playlist(self.engine, "daily", "daily", "每日推荐")
        self.assertEqual(["900"], self.plex.deleted)
        self.assertIsNone(self.store.get("daily_managed"))
        self.assertEqual("applied", self.store.get("snapshots")[-1]["status"])
        self.assertIn("音乐文件未删除", result["message"])

    def test_manual_add_and_remove_update_fingerprint_and_persistent_overrides(self):
        from helper.playlist_hub import edit_playlist_track

        added = edit_playlist_track(self.engine, "daily", "daily", "20", "remove")
        self.assertEqual(["10"], [row["id"] for row in self.state["items"]])
        self.assertEqual(["20"], self.store.get("playlist_manual_edits")["daily:daily"]["exclude"])
        self.assertTrue(added["message"])
        edit_playlist_track(self.engine, "daily", "daily", "20", "add")
        self.assertEqual(["10", "20"], [row["id"] for row in self.state["items"]])
        edits = self.store.get("playlist_manual_edits")["daily:daily"]
        self.assertEqual(["20"], edits["include"])
        self.assertEqual([], edits["exclude"])

    def test_manual_overrides_keep_additions_and_exclusions_during_regeneration(self):
        from helper.playlist_hub import apply_manual_edits

        self.store.set("playlist_manual_edits", {
            "daily:daily": {"include": ["20"], "exclude": ["10"]},
        })
        self.assertEqual(["20"], apply_manual_edits(self.store, "daily", "daily", ["10"]))


class PlaylistHubPageTests(unittest.TestCase):
    def test_home_is_a_single_management_and_playback_surface(self):
        page = (STATIC / "playlists.html").read_text(encoding="utf-8")
        self.assertIn('id="playlistProfile"', page)
        self.assertIn('id="playlistList"', page)
        self.assertIn('id="playlistTracks"', page)
        self.assertIn('id="playlistPlayer"', page)
        self.assertIn('id="playerToggle"', page)
        self.assertIn('id="playerPrevious"', page)
        self.assertIn('id="playerNext"', page)
        self.assertIn('id="playerSeek"', page)
        self.assertIn('id="playerArtwork"', page)
        self.assertIn('id="playlistTools"', page)
        self.assertIn('data-tool-url="/daily"', page)
        self.assertIn('data-tool-url="/mixes"', page)
        self.assertIn('data-tool-url="/external"', page)
        self.assertIn('data-tool-url="/library"', page)
        self.assertIn('id="playlistToolFrame"', page)
        self.assertIn('id="librarySearchDialog"', page)
        self.assertIn('id="librarySearchResults"', page)
        self.assertNotIn("不会删除音乐文件", page)
        self.assertLessEqual(page.count('class="muted"'), 1)

    def test_every_main_page_links_to_the_playlist_home_once(self):
        for name in (
            "daily.html", "home.html", "mixes.html", "external.html",
            "status.html", "settings.html", "playlists.html",
        ):
            page = (STATIC / name).read_text(encoding="utf-8")
            self.assertEqual(1, page.count('href="/">我的歌单</a>'), name)

    def test_server_serves_playlist_home_and_moves_daily_workflow_to_daily_route(self):
        source = (ROOT / "src/helper/web.py").read_text(encoding="utf-8")
        self.assertIn("@app.get('/daily')", source)
        self.assertIn("STATIC / 'playlists.html'", source)
        self.assertIn("'playlists.js'", source)
        hub = (ROOT / "src/helper/playlist_hub.py").read_text(encoding="utf-8")
        self.assertIn('@app.get("/api/playlists")', hub)
        self.assertIn('@app.get("/api/playlists/{kind}/{key}")', hub)
        self.assertIn('@app.post("/api/playlists/remove")', hub)

    def test_home_player_keeps_one_queue_and_advances_when_a_track_ends(self):
        script = (STATIC / "playlists.js").read_text(encoding="utf-8")
        self.assertIn("player.addEventListener('ended',playNext)", script)
        self.assertIn("player.addEventListener('timeupdate'", script)
        self.assertIn("function playNext()", script)
        self.assertIn("function playPrevious()", script)
        self.assertIn("function playAt(index", script)
        self.assertIn("function openTool(", script)
        self.assertIn("playerArtwork", script)
        self.assertIn("/api/playlists/search", script)
        self.assertIn("/tracks/edit", script)


class ExternalPlaylistPreviewUiTests(unittest.TestCase):
    def test_audio_player_is_hidden_and_playback_controls_stay_in_the_track_row(self):
        page = (STATIC / "external.html").read_text(encoding="utf-8")
        script = (STATIC / "external.js").read_text(encoding="utf-8")
        self.assertIn('<audio id="auditionPlayer" preload="none" hidden>', page)
        self.assertNotIn("auditionBar", page + script)
        self.assertNotIn("auditionLabel", page + script)
        self.assertIn("external-preview-button", script)
        self.assertIn("external-preview-progress", script)
        self.assertIn("player.addEventListener('timeupdate'", script)
        self.assertIn("button.textContent='暂停'", script)
        self.assertIn("if(current)stopAudition()", script)

    def test_external_page_uses_clear_primary_sections_without_instruction_blocks(self):
        page = (STATIC / "external.html").read_text(encoding="utf-8")
        self.assertIn("导入歌单", page)
        self.assertIn("创建 Plex 歌单", page)
        self.assertIn("缺失歌曲", page)
        self.assertNotIn("external-trust-note", page)
        self.assertNotIn("publishExplanation", page)
        self.assertNotIn("这只是查看链接", page)


if __name__ == "__main__":
    unittest.main()
