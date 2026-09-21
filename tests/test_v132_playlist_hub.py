import tempfile
import unittest
import sys
from html.parser import HTMLParser
from contextlib import contextmanager
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
        from helper.playlist_hub import assistant_playlist_rows

        rows = assistant_playlist_rows(self.store)
        self.assertEqual(
            ["daily", "smart", "favorite", "category", "external"],
            [row["kind"] for row in rows],
        )
        self.assertEqual(
            ["每日推荐", "每周常听", "我的最爱", "开车精选", "百万收藏"],
            [row["title"] for row in rows],
        )
        self.assertEqual([30, 2, None, 3, 1], [row["count"] for row in rows])
        physical = [row for row in rows if row["kind"] != "favorite"]
        self.assertTrue(all(row["playlist_id"] for row in physical))
        self.assertTrue(all(row["manage_url"].startswith("/") for row in physical))

    def test_daily_entry_remains_available_before_its_first_publish(self):
        from helper.playlist_hub import assistant_playlist_rows

        self.store.set("daily_managed", None)
        self.store.set("daily_published_view", None)

        rows = assistant_playlist_rows(self.store)
        daily = rows[0]
        self.assertEqual("daily", daily["kind"])
        self.assertEqual("每日推荐", daily["title"])
        self.assertEqual("", daily["playlist_id"])
        self.assertEqual("未建立", daily["status"])
        self.assertEqual("/mixes", daily["manage_url"])

    def test_every_automatic_playlist_writer_applies_manual_track_choices(self):
        for name in ("daily.py", "smart_mix_web.py", "engine.py", "external_service.py"):
            source = (ROOT / "src/helper" / name).read_text(encoding="utf-8")
            self.assertIn("apply_manual_edits", source, name)


class _PlaylistPlex:
    def __init__(self, state, audio=None):
        self.state = state
        self.audio = audio
        self.deleted = []
        self.summary_updates = []

    def identity(self):
        return {"machine": "machine-a"}

    def playlist_state(self, playlist_id):
        if str(playlist_id) != str(self.state["id"]):
            raise ValueError("missing")
        return {**self.state, "items": [dict(row) for row in self.state["items"]]}

    def open_audio_part(self, track_id, range_header=""):
        self.last_audio = (str(track_id), range_header)
        return self.audio

    def open_browser_audio(self, track_id, range_header="", offset_seconds=0):
        self.last_audio_offset = offset_seconds
        return self.open_audio_part(track_id, range_header)

    def track_section(self, track_id):
        return "1" if str(track_id) == "10" else "22"

    def track_metadata(self, track_id):
        return {"id": str(track_id), "thumb": "/library/metadata/10/thumb", "library_section_id": self.track_section(track_id)}

    def open_artwork(self, path):
        self.last_artwork = str(path)
        return self.artwork

    def delete_playlist(self, playlist_id):
        self.deleted.append(str(playlist_id))

    def update_playlist_summary(self, playlist_id, summary):
        self.summary_updates.append((str(playlist_id), str(summary)))
        self.state["summary"] = str(summary)

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
        self.exclusive_entries = 0

    def plex_factory(self, _settings):
        return self.plex

    def marker(self, category_id):
        return "[owned:" + str(category_id) + "]"

    def daily_scope(self):
        return "scope-a"

    @contextmanager
    def exclusive(self):
        self.exclusive_entries += 1
        yield

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
        self.plex.artwork = None
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

    def test_detail_migrates_an_unchanged_legacy_daily_marker_after_restore(self):
        from helper.engine import fingerprint
        from helper.playlist_hub import playlist_detail

        self.state["summary"] = "[PCH:11111111111111111111111111111111:daily]\n旧说明"
        record = dict(self.store.get("daily_managed"))
        record["fingerprint"] = fingerprint(self.state)
        self.store.set("daily_managed", record)

        result = playlist_detail(self.engine, "daily", "daily")

        self.assertEqual(2, result["count"])
        self.assertIn("[owned:daily]", self.state["summary"])
        self.assertEqual("旧说明", self.state["summary"].split("\n", 1)[1])
        self.assertEqual(fingerprint(self.state), self.store.get("daily_managed")["fingerprint"])
        self.assertEqual(1, len(self.plex.summary_updates))

    def test_detail_never_adopts_an_unmarked_same_name_playlist(self):
        from helper.engine import fingerprint
        from helper.playlist_hub import playlist_detail

        self.state["summary"] = "用户自己创建的歌单"
        record = dict(self.store.get("daily_managed"))
        record["fingerprint"] = fingerprint(self.state)
        self.store.set("daily_managed", record)

        with self.assertRaisesRegex(Exception, "管理标记"):
            playlist_detail(self.engine, "daily", "daily")

    def test_detail_migrates_an_unchanged_legacy_external_marker_after_restore(self):
        from helper.engine import fingerprint
        from helper.external_playlist_sync import external_marker
        from helper.external_store import ExternalRepository
        from helper.playlist_hub import playlist_detail

        repository = ExternalRepository(self.store)
        source = repository.upsert_source("default", {
            "provider": "qq", "external_id": "100", "url": "https://y.qq.com/100",
            "title": "外部热门", "revision": "r1", "tracks": [
                {"source_track_key": "a", "position": 0, "title": "第一首", "artists": ["甲"]},
            ],
        }, 4_000)
        self.state.update({
            "title": "外部热门",
            "summary": "[QKYX:external:old-install:" + source["id"] + "]\n旧说明",
        })
        repository.save_managed("default", source["id"], {
            "id": "900", "title": "外部热门", "fingerprint": fingerprint(self.state),
            "marker": "[QKYX:external:old-install:" + source["id"] + "]",
        })

        result = playlist_detail(self.engine, "external", source["id"])

        expected = external_marker(self.store.get("installation_id"), source["id"])
        self.assertEqual("外部热门", result["title"])
        self.assertIn(expected, self.state["summary"])
        self.assertEqual(fingerprint(self.state), repository.get_managed("default", source["id"])["fingerprint"])

    def test_audio_rejects_tracks_outside_the_selected_owned_playlist(self):
        from helper.playlist_hub import stream_playlist_audio

        with self.assertRaisesRegex(ValueError, "当前歌单"):
            stream_playlist_audio(self.engine, "daily", "daily", "999", "", "session")

    def test_library_audio_uses_live_plex_membership_even_if_catalog_is_stale(self):
        import asyncio
        from helper.playlist_hub import stream_library_audio
        from tests.test_v130_external_audio import FakeAudioResponse, close_response

        self.plex.audio = FakeAudioResponse(status=200, headers={
            "Content-Type": "audio/mpeg", "Content-Length": "1024",
        })
        response = stream_library_audio(self.engine, "10", "", "session", 75.5)
        self.assertEqual(200, response.status_code)
        self.assertEqual(("10", ""), self.plex.last_audio)
        self.assertEqual(75.5, self.plex.last_audio_offset)
        asyncio.run(close_response(response))
        with self.assertRaisesRegex(ValueError, "当前曲库"):
            stream_library_audio(self.engine, "999", "", "session-other")
        catalog = self.store.get("catalog")
        catalog[0]["available"] = False
        self.store.set("catalog", catalog)
        self.plex.audio = FakeAudioResponse(status=200, headers={
            "Content-Type": "audio/mpeg", "Content-Length": "1024",
        })
        response = stream_library_audio(self.engine, "10", "", "session-disabled")
        asyncio.run(close_response(response))

    def test_library_artwork_uses_the_validated_catalog_row(self):
        from helper.playlist_hub import stream_library_artwork
        from tests.test_v130_external_audio import FakeAudioResponse

        self.plex.artwork = FakeAudioResponse(status=200, headers={
            "Content-Type": "image/jpeg", "Content-Length": "4",
        }, chunks=[b"jpeg"])
        response = stream_library_artwork(self.engine, "10")
        self.assertEqual("/library/metadata/10/thumb/1", self.plex.last_artwork)
        self.assertEqual("image/jpeg", response.media_type)
        with self.assertRaisesRegex(ValueError, "当前曲库"):
            stream_library_artwork(self.engine, "999")
        with self.assertRaisesRegex(ValueError, "没有可用封面"):
            stream_library_artwork(self.engine, "20")

    def test_daily_delete_validates_ownership_and_never_deletes_music_files(self):
        from helper.playlist_hub import remove_playlist

        result = remove_playlist(self.engine, "daily", "daily", "每日推荐")
        self.assertEqual(["900"], self.plex.deleted)
        self.assertEqual(1, self.engine.exclusive_entries)
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
    def test_workspace_shows_each_real_panel_once_for_tool_and_system_pages(self):
        workspace = (STATIC / "playlist-workspace.js").read_text(encoding="utf-8")

        self.assertNotIn("const panels=", workspace)
        show = workspace.split("function show(next)", 1)[1].split(
            "function openPage", 1
        )[0]
        self.assertIn("playlistSectionView.hidden=current.panel!=='section'", show)
        self.assertIn("playlistView.hidden=current.panel!=='playlist'", show)
        self.assertIn("playlistSearchView.hidden=current.panel!=='search'", show)
        self.assertIn(
            "playlistToolView.hidden=!['tool','system'].includes(current.panel)",
            show,
        )

    def test_playlist_sidebar_navigation_never_adds_button_pending_content(self):
        script = (STATIC / "playlists.js").read_text(encoding="utf-8")
        sections = (STATIC / "playlist-sections.js").read_text(encoding="utf-8")
        styles = (STATIC / "product.css").read_text(encoding="utf-8")

        navigate = script.split("async function navigate(fn)", 1)[1].split(
            "async function json", 1
        )[0]
        self.assertNotIn("PCHUI.run", navigate)
        self.assertIn("onOpenPlaylist:item=>", script)
        self.assertIn("navigate(()=>openPlaylist(item))", script)
        self.assertIn("button.onclick=()=>onOpenPlaylist(item)", sections)
        loading = script.split("async function openPlaylist", 1)[1].split(
            "function openSection", 1
        )[0]
        self.assertIn("setPlaylistLoading(true)", loading)
        self.assertIn("setPlaylistLoading(false)", loading)
        self.assertIn(
            ".playlist-side-list button.pch-pending::before{content:none}",
            styles,
        )

    def test_player_uses_a_top_progress_rail_and_svg_control_icons(self):
        page = (STATIC / "playlists.html").read_text(encoding="utf-8")
        player_script = (STATIC / "playlist-player.js").read_text(encoding="utf-8")
        player = page.split('<footer id="playlistPlayer"', 1)[1].split(
            "</footer>", 1
        )[0]

        self.assertLess(
            player.index('id="playlistProgressRail"'),
            player.index('class="playlist-player-body"'),
        )
        for control_id in ("playerPrevious", "playerToggle", "playerNext", "playerMute"):
            control = player.split(f'id="{control_id}"', 1)[1].split("</button>", 1)[0]
            self.assertIn("<svg", control, control_id)
        for glyph in ("‹", "›", "▶", "❚❚"):
            self.assertNotIn(glyph, player)
            self.assertNotIn(f"textContent='{glyph}'", player_script)
        self.assertIn("playerToggle.dataset.state='playing'", player_script)
        self.assertIn("playerToggle.dataset.state='paused'", player_script)

    def test_volume_control_sits_with_center_playback_buttons(self):
        class Parents(HTMLParser):
            def __init__(self):
                super().__init__()
                self.stack = []
                self.ancestors = []

            def handle_starttag(self, tag, attrs):
                attributes = dict(attrs)
                if attributes.get("id") == "playerMute":
                    self.ancestors = [node.get("class", "") for node in self.stack]
                if tag not in {"input", "img", "br", "hr", "meta", "link"}:
                    self.stack.append(attributes)

            def handle_endtag(self, tag):
                if self.stack:
                    self.stack.pop()

        parser = Parents()
        parser.feed((STATIC / "playlists.html").read_text(encoding="utf-8"))
        self.assertIn("playlist-player-center", parser.ancestors)
        self.assertNotIn("playlist-player-tools", parser.ancestors)

    def test_dragging_seek_does_not_outline_the_whole_rail(self):
        styles = (STATIC / "product.css").read_text(encoding="utf-8")
        selector = ".playlist-player .playlist-progress-rail input[type=range]:focus{"
        self.assertIn("outline:none", styles.split(selector, 1)[1].split("}", 1)[0])
        self.assertIn(
            ".playlist-progress-rail input[type=range]:focus-visible::-webkit-slider-thumb",
            styles,
        )

    def test_volume_panel_opens_for_pointer_hover_and_keyboard_focus(self):
        styles = (STATIC / "product.css").read_text(encoding="utf-8")

        self.assertIn(
            ".playlist-volume-control:hover .playlist-volume-panel,"
            ".playlist-volume-control:focus-within .playlist-volume-panel",
            styles,
        )
        bridge = styles.split(".playlist-volume-panel::after{", 1)[1].split(
            "}", 1
        )[0]
        self.assertIn('content:""', bridge)
        self.assertIn("position:absolute", bridge)
        self.assertIn("top:100%", bridge)
        self.assertIn("right:0", bridge)
        self.assertIn("width:100%", bridge)
        self.assertIn("height:9px", bridge)

    def test_player_ranges_reset_the_global_text_input_box_model(self):
        styles = (STATIC / "product.css").read_text(encoding="utf-8")

        selector = (
            ".playlist-player .playlist-progress-rail input[type=range],"
            ".playlist-player .playlist-volume-panel input[type=range]{"
        )
        reset = styles.split(selector, 1)[1].split("}", 1)[0]
        for declaration in (
            "min-height:0",
            "padding:0",
            "border:0",
            "background:transparent",
            "box-shadow:none",
        ):
            self.assertIn(declaration, reset)

    def test_zero_volume_mute_click_restores_the_last_audible_volume(self):
        player = (STATIC / "playlist-player.js").read_text(encoding="utf-8")

        self.assertIn("let lastAudibleVolume=audio.volume||1", player)
        volume_input = player.split("playerVolume.oninput=", 1)[1].split(";\n", 1)[0]
        self.assertIn("if(audio.volume>0)lastAudibleVolume=audio.volume", volume_input)
        self.assertIn("audio.muted=audio.volume===0", volume_input)
        self.assertIn("syncVolumeState()", volume_input)
        mute_click = player.split("playerMute.onclick=", 1)[1].split(";\n", 1)[0]
        self.assertIn("if(audio.muted||audio.volume===0)", mute_click)
        self.assertIn("audio.volume=lastAudibleVolume", mute_click)
        self.assertIn("playerVolume.value=String(lastAudibleVolume)", mute_click)
        self.assertIn("audio.muted=false", mute_click)
        self.assertIn("else audio.muted=true", mute_click)
        self.assertIn("syncVolumeState()", mute_click)
        sync = player.split("function syncVolumeState()", 1)[1].split(
            "function showFeedback", 1
        )[0]
        self.assertIn("const muted=audio.muted||audio.volume===0", sync)
        self.assertIn("setMuted(muted)", sync)

    def test_mute_icon_has_cross_and_synced_accessible_state(self):
        page = (STATIC / "playlists.html").read_text(encoding="utf-8")
        player = (STATIC / "playlist-player.js").read_text(encoding="utf-8")
        styles = (STATIC / "product.css").read_text(encoding="utf-8")
        mute = page.split('id="playerMute"', 1)[1].split("</button>", 1)[0]
        self.assertIn('class="player-icon-mute-waves"', mute)
        self.assertIn('class="player-icon-mute-cross"', mute)
        set_muted = player.split("function setMuted(value)", 1)[1].split(
            "function syncVolumeState", 1
        )[0]
        self.assertIn("player.dataset.muted=String(value)", set_muted)
        self.assertIn("playerMute.setAttribute('aria-label',label)", set_muted)
        self.assertIn("playerMute.title=label", set_muted)
        self.assertIn(".player-icon-mute-cross{display:none}", styles)
        self.assertIn("[data-muted=true] .player-icon-mute-waves{display:none}", styles)
        self.assertIn("[data-muted=true] .player-icon-mute-cross{display:block}", styles)

    def test_global_search_replaces_the_redundant_playlist_add_button(self):
        page = (STATIC / "playlists.html").read_text(encoding="utf-8")
        search = (STATIC / "playlist-search.js").read_text(encoding="utf-8")
        self.assertNotIn('id="playlistAddTrack"', page)
        self.assertIn('id="librarySearchForm"', page)
        self.assertIn("function openAddDialog(track)", search)
        self.assertNotIn("function focus()", search)

    def test_track_header_and_rows_keep_song_artist_and_album_in_separate_columns(self):
        page = (STATIC / "playlists.html").read_text(encoding="utf-8")
        script = (STATIC / "playlists.js").read_text(encoding="utf-8")

        header = page.split('class="playlist-track-head"', 1)[1].split("</div>", 1)[0]
        labels = ["歌曲", "歌手", "专辑", "时长"]
        positions = [header.index(f">{label}<") for label in labels]
        self.assertEqual(sorted(positions), positions)
        render_tracks = script.split("function renderNextTrackBatch()", 1)[1].split(
            "function renderTracks()", 1
        )[0]
        self.assertIn("artist.className='playlist-track-artist'", render_tracks)
        append = render_tracks.split("row.append(", 1)[1].split(")", 1)[0]
        self.assertEqual("number,identity,artist,album,duration,actions", append)
        self.assertIn("identity.append(heart,title)", render_tracks)
        self.assertIn("current?.can_remove_tracks", render_tracks)

    def test_track_columns_compact_before_the_sidebar_can_clip_the_action_column(self):
        styles = (STATIC / "product.css").read_text(encoding="utf-8")

        compact = styles.split("@media(max-width:1020px){", 1)[1].split(
            "\n}", 1
        )[0]
        self.assertIn(
            "grid-template-columns:34px minmax(0,1fr) minmax(100px,.7fr) 58px 100px",
            compact,
        )
        self.assertIn(".playlist-track-head span:nth-child(4)", compact)
        self.assertIn(".playlist-track-album{display:none}", compact)

    def test_home_uses_one_workspace_state_for_global_search_and_navigation(self):
        page = (STATIC / "playlists.html").read_text(encoding="utf-8")
        script = (STATIC / "playlists.js").read_text(encoding="utf-8")
        workspace = (STATIC / "playlist-workspace.js").read_text(encoding="utf-8")
        search = (STATIC / "playlist-search.js").read_text(encoding="utf-8")
        web = (ROOT / "src/helper/web.py").read_text(encoding="utf-8")

        topbar = page.split('<header class="topbar">', 1)[1].split("</header>", 1)[0]
        self.assertIn('id="librarySearchInput"', topbar)
        self.assertIn('id="librarySearchForm"', topbar)
        self.assertNotIn('id="playlistSearch"', page)
        self.assertIn('id="playlistSearchView"', page)
        self.assertIn('id="librarySearchResults"', page)
        self.assertIn('data-workspace-url="/status"', topbar)
        self.assertIn('id="openSettings"', topbar)
        self.assertIn("openWorkspacePage('/settings','设置','system')", script)
        self.assertNotIn('href="/status"', topbar)
        self.assertNotIn('href="/settings"', topbar)

        self.assertIn("import {createPlaylistWorkspace}", script)
        self.assertIn("import {createLibrarySearch}", script)
        self.assertIn("export function createPlaylistWorkspace", workspace)
        self.assertIn("export function createLibrarySearch", search)
        self.assertIn("let requestGeneration=0", search)
        self.assertIn("requestId!==requestGeneration", search)
        self.assertIn("profileId!==getProfileId()", search)
        self.assertIn("'playlist-workspace.js'", web)
        self.assertIn("'playlist-search.js'", web)
        self.assertIn("function openSearchWorkspace(query)", script)
        open_search = script.split("function openSearchWorkspace", 1)[1].split(
            "const librarySearch", 1
        )[0]
        self.assertIn("++playlistRequest", open_search)
        self.assertIn("setPlaylistLoading(false)", open_search)
        self.assertIn("panel:'search'", open_search)
        self.assertIn("onSearchStart:openSearchWorkspace", script)

        navigation = workspace.split("function renderNavigation", 1)[1].split(
            "function show", 1
        )[0]
        self.assertIn("#smartHubButton,#libraryHubButton,#customPlaylistList button", navigation)
        self.assertIn("classList.toggle('active'", navigation)

    def test_workspace_switches_do_not_destroy_the_persistent_audio_element(self):
        page = (STATIC / "playlists.html").read_text(encoding="utf-8")
        workspace = (STATIC / "playlist-workspace.js").read_text(encoding="utf-8")
        web = (ROOT / "src/helper/web.py").read_text(encoding="utf-8")

        self.assertEqual(1, page.count('id="playerAudio"'))
        open_workspace = workspace.split("function openPage", 1)[1].split(
            "return {", 1
        )[0]
        self.assertNotIn("location.href", open_workspace)
        self.assertNotIn("stopPlayback", open_workspace)
        self.assertIn("embedded", open_workspace)
        embedded_routes = web.split("embedded =", 1)[1].split("r.headers['X-Frame-Options']", 1)[0]
        self.assertIn("'/status'", embedded_routes)
        self.assertIn("'/settings'", embedded_routes)

    def test_home_is_a_single_management_and_playback_surface(self):
        page = (STATIC / "playlists.html").read_text(encoding="utf-8")
        self.assertIn('id="playlistProfile"', page)
        self.assertIn('id="smartHubButton"', page)
        self.assertIn('id="libraryHubButton"', page)
        self.assertIn('id="customPlaylistList"', page)
        self.assertIn('id="playlistTracks"', page)
        self.assertIn('id="playlistPlayer"', page)
        self.assertIn('id="playerToggle"', page)
        self.assertIn('id="playerPrevious"', page)
        self.assertIn('id="playerNext"', page)
        self.assertIn('id="playerSeek"', page)
        self.assertIn('id="playerArtwork"', page)
        self.assertNotIn('data-tool-url="/daily"', page)
        self.assertIn('data-tool-url="/external"', page)
        self.assertIn('id="playlistSectionView"', page)
        self.assertIn('id="playlistToolFrame"', page)
        self.assertIn('id="librarySearchDialog"', page)
        self.assertIn('id="librarySearchResults"', page)
        self.assertNotIn("不会删除音乐文件", page)
        self.assertLessEqual(page.count('class="muted"'), 3)

    def test_every_main_page_links_to_the_playlist_home_once(self):
        for name in (
            "daily.html", "home.html", "mixes.html", "external.html",
            "status.html", "settings.html",
        ):
            page = (STATIC / name).read_text(encoding="utf-8")
            expected = 'href="/" target="_top">我的歌单</a>' if name == "settings.html" else 'href="/">我的歌单</a>'
            self.assertEqual(1, page.count(expected), name)
        playlist_home = (STATIC / "playlists.html").read_text(encoding="utf-8")
        self.assertEqual(1, playlist_home.count('id="workspaceHome"'))

    def test_server_serves_playlist_home_and_moves_daily_workflow_to_daily_route(self):
        source = (ROOT / "src/helper/web.py").read_text(encoding="utf-8")
        self.assertIn("@app.get('/daily')", source)
        self.assertIn("STATIC / 'playlists.html'", source)
        self.assertIn("'playlists.js'", source)
        hub = (ROOT / "src/helper/playlist_hub.py").read_text(encoding="utf-8")
        self.assertIn('@app.get("/api/playlists")', hub)
        self.assertIn('@app.get("/api/playlists/{kind}/{key}")', hub)
        self.assertIn('@app.post("/api/playlists/remove")', hub)

    def test_remove_route_dispatches_every_managed_playlist_kind_under_the_lock(self):
        hub = (ROOT / "src/helper/playlist_hub.py").read_text(encoding="utf-8")
        remove_route = hub.split('@app.post("/api/playlists/remove")', 1)[1].split(
            '@app.post("/api/playlists/rename")', 1
        )[0]
        self.assertNotIn('if kind in ("smart", "category")', remove_route)
        self.assertNotIn("with target.exclusive():", remove_route)
        self.assertIn("return remove_playlist(", remove_route)

    def test_home_player_keeps_one_queue_and_advances_when_a_track_ends(self):
        script = (STATIC / "playlists.js").read_text(encoding="utf-8")
        search = (STATIC / "playlist-search.js").read_text(encoding="utf-8")
        player = (STATIC / "playlist-player.js").read_text(encoding="utf-8")
        styles = (STATIC / "product.css").read_text(encoding="utf-8")
        self.assertIn("audio.onended", player)
        self.assertIn("audio.ontimeupdate", player)
        self.assertIn("function playNext()", player)
        self.assertIn("function playPrevious()", player)
        self.assertIn("function playAt(items,index", player)
        self.assertIn("function openTool(", script)
        self.assertIn("playerArtwork", player)
        self.assertIn("/api/playlists/search", search)
        self.assertIn("/tracks/edit", script + search)
        self.assertIn("let queue=[]", player)
        self.assertIn("startQueueTrack", player)
        self.assertIn("let context=null", player)
        self.assertIn("/api/playlists/library/tracks/", script)
        self.assertNotIn("/api/playlists/'+encoded(context?.kind)", script)
        self.assertNotIn("if(stop)stopPlayback();current=item;const detail", script)
        self.assertIn(
            "grid-template-columns:34px minmax(0,1fr) minmax(100px,.7fr) 58px 100px",
            styles,
        )

    def test_playlist_home_has_one_daily_entry_and_a_stable_sticky_shell(self):
        page = (STATIC / "playlists.html").read_text(encoding="utf-8")
        script = (STATIC / "playlists.js").read_text(encoding="utf-8")
        styles = (STATIC / "product.css").read_text(encoding="utf-8")
        self.assertNotIn('data-tool-url="/daily"', page)
        self.assertIn('id="playlistStickyHead"', page)
        self.assertIn('class="playlist-track-scroll"', page)
        self.assertIn(".playlist-sticky-head{", styles)
        sidebar = styles.split(".playlist-sidebar{", 1)[1].split("}", 1)[0]
        self.assertNotIn("position:fixed", sidebar)
        self.assertIn(".playlist-list-scroll{", styles)

    def test_initial_load_is_lazy_and_profile_events_reload_loaded_data(self):
        script = (STATIC / "playlists.js").read_text(encoding="utf-8")
        self.assertIn("loadedProfileId", script)
        self.assertIn("async function switchProfile", script)
        self.assertNotIn("for(const candidate of candidates)", script)
        self.assertNotIn("else await json('/api/playlists/'", script)
        self.assertIn("unavailablePlaylists", script)
        open_playlist = script.split("async function openPlaylist", 1)[1].split("async function openFirstAvailable", 1)[0]
        self.assertNotIn("stopPlayback()", open_playlist)
        self.assertIn("setPlaylistLoading", open_playlist)

    def test_profile_switch_discards_stale_account_requests(self):
        script = (STATIC / "playlists.js").read_text(encoding="utf-8")
        self.assertIn("let profileRequest=0", script)
        self.assertIn("requestId!==profileRequest", script)
        self.assertIn("++playlistRequest", script)
        switch_profile = script.split("async function switchProfile", 1)[1].split(
            "$('playlistProfile').onchange", 1
        )[0]
        self.assertIn("setPlaylistLoading(false)", switch_profile)
        self.assertIn("resetPlaylistView()", switch_profile)
        reset_view = script.split("function resetPlaylistView", 1)[1].split(
            "async function switchProfile", 1
        )[0]
        self.assertIn("workspace.reset()", reset_view)
        self.assertIn("正在载入歌单", reset_view)

    def test_playlist_deletion_only_stops_audio_from_that_playlist(self):
        script = (STATIC / "playlists.js").read_text(encoding="utf-8")
        self.assertIn("function playingFrom(item)", script)
        remove_track = script.split("async function removeTrack", 1)[1].split(
            "async function switchProfile", 1
        )[0]
        remove_playlist = script.split("$('playlistRemove').onclick", 1)[1].split(
            "$('playlistToolBack').onclick", 1
        )[0]
        self.assertIn("playingFrom(selected)", remove_track)
        self.assertIn("playingFrom(selected)", remove_playlist)

    def test_playing_highlight_and_unbuilt_daily_keep_unambiguous_context(self):
        script = (STATIC / "playlists.js").read_text(encoding="utf-8")
        render_tracks = script.split("function renderNextTrackBatch", 1)[1].split(
            "function renderTracks", 1
        )[0]
        self.assertIn("(isPlaying?' playing':'')", render_tracks)
        unbuilt = script.split("if(!item.can_play)", 1)[1].split("throw Error", 1)[0]
        self.assertIn("navigation:{type:'section',section:item.section}", unbuilt)
        self.assertIn("setPlaylistLoading(false)", unbuilt)
        self.assertNotIn("current=null", unbuilt)
        open_workspace = script.split("function openWorkspacePage", 1)[1].split(
            "function openTool", 1
        )[0]
        self.assertIn("++playlistRequest", open_workspace)
        self.assertIn("setPlaylistLoading(false)", open_workspace)

    def test_playlist_shell_owns_the_viewport_and_only_content_regions_scroll(self):
        page = (STATIC / "playlists.html").read_text(encoding="utf-8")
        styles = (STATIC / "product.css").read_text(encoding="utf-8")
        external = (STATIC / "external.js").read_text(encoding="utf-8")
        body_rule = styles.split("body[data-view=playlists]{", 1)[1].split("}", 1)[0]
        workspace_rule = styles.split("body[data-view=playlists] #workspace{", 1)[1].split("}", 1)[0]
        hub_rule = styles.split(".playlist-hub{", 1)[1].split("}", 1)[0]
        player_rule = styles.split(".playlist-player{", 1)[1].split("}", 1)[0]
        feedback_rule = styles.split(".playlist-player-feedback{", 1)[1].split("}", 1)[0]
        self.assertIn("overflow:hidden", body_rule)
        self.assertIn("height:100dvh", workspace_rule)
        self.assertIn("grid-template-rows:64px minmax(0,1fr) 72px", workspace_rule)
        self.assertIn("min-height:0", hub_rule)
        self.assertIn(".playlist-track-scroll{min-height:0;overflow:auto", styles)
        self.assertIn("height:72px", player_rule)
        self.assertNotIn("position:fixed", player_rule)
        self.assertNotIn("position:absolute", feedback_rule)
        self.assertIn('class="playlist-track-scroll"', page)
        self.assertIn("#playlistView.is-loading .playlist-tracks", styles)
        self.assertNotIn("pch-tool-height", external)
        self.assertNotIn("is-auto-height", styles)

    def test_player_is_compact_theme_ready_and_keeps_errors_beside_controls(self):
        page = (STATIC / "playlists.html").read_text(encoding="utf-8")
        script = (STATIC / "playlists.js").read_text(encoding="utf-8")
        player_script = (STATIC / "playlist-player.js").read_text(encoding="utf-8")
        web = (ROOT / "src/helper/web.py").read_text(encoding="utf-8")
        styles = (STATIC / "product.css").read_text(encoding="utf-8")
        player = page.split('<footer id="playlistPlayer"', 1)[1].split("</footer>", 1)[0]
        self.assertIn('id="playerFeedback"', player)
        self.assertIn('id="playerRetry"', player)
        self.assertIn('id="playerErrorNext"', player)
        self.assertIn('id="playerVolume"', player)
        self.assertNotIn('id="playlistPlayer" class="playlist-player" hidden', page)
        self.assertIn('<audio id="playerAudio" preload="none"></audio>', page)
        self.assertIn("import {createPlaylistPlayer}", script)
        self.assertIn("export function createPlaylistPlayer", player_script)
        self.assertIn("let playbackGeneration=0", player_script)
        self.assertIn("generation!==playbackGeneration", player_script)
        self.assertIn("retryCount<1", player_script)
        self.assertIn("setTimeout", player_script)
        self.assertIn("NotAllowedError", player_script)
        self.assertIn("playerRetry", player_script)
        self.assertIn("function retryNow()", player_script)
        retry_handler = player_script.split("byId(document,'playerRetry').onclick", 1)[1].split(";", 1)[0]
        self.assertIn("retryNow", retry_handler)
        self.assertNotIn("handleFailure", retry_handler)
        self.assertIn("playerVolume", player_script)
        self.assertIn("'playlist-player.js'", web)
        player_styles = styles.split(".playlist-player{", 1)[1].split(".playlist-search-dialog", 1)[0]
        self.assertNotIn("min-height:88px", player_styles)
        self.assertIn("height:72px", player_styles)
        self.assertIn(".playlist-player-feedback{", styles)
        self.assertIn("--playlist-wallpaper:", styles)
        self.assertIn("--playlist-accent:", styles)

    def test_tool_switch_does_not_stop_the_persistent_player(self):
        script = (STATIC / "playlists.js").read_text(encoding="utf-8")
        open_workspace = script.split("function openWorkspacePage", 1)[1].split("function openTool", 1)[0]
        self.assertNotIn("stopPlayback", open_workspace)

    def test_returning_to_current_playlist_cancels_an_inflight_selection(self):
        script = (STATIC / "playlists.js").read_text(encoding="utf-8")
        restore = script.split("function restorePlaylistView", 1)[1].split(
            "function openWorkspacePage", 1
        )[0]
        self.assertIn("++playlistRequest", restore)
        self.assertIn("setPlaylistLoading(false)", restore)
        self.assertIn("setSidebarOpen(false)", restore)

    def test_playlist_loading_locks_actions_until_the_selected_detail_arrives(self):
        script = (STATIC / "playlists.js").read_text(encoding="utf-8")
        loading = script.split("function setPlaylistLoading", 1)[1].split(
            "async function openPlaylist", 1
        )[0]
        for element_id in (
            "playlistManage", "playlistRemove", "playlistPlayAll",
        ):
            self.assertIn(element_id, loading)
        self.assertIn(".disabled", loading)
        self.assertIn("$('playlistTracks').inert=playlistLoading", loading)
        open_playlist = script.split("async function openPlaylist", 1)[1].split(
            "function openSection", 1
        )[0]
        self.assertIn("return false", open_playlist)
        self.assertIn("return true", open_playlist)
        self.assertIn("catch(error)", open_playlist)
        self.assertIn("requestId!==playlistRequest", open_playlist)
        self.assertIn("restoreCurrentPlaylistSelection(item.section||'smart')", open_playlist)

    def test_logout_stops_player_and_invalidates_account_requests(self):
        script = (STATIC / "playlists.js").read_text(encoding="utf-8")
        auth = (STATIC / "auth.js").read_text(encoding="utf-8")
        search = (STATIC / "playlist-search.js").read_text(encoding="utf-8")
        self.assertIn("function resetSession()", script)
        reset = script.split("function resetSession()", 1)[1].split(
            "async function switchProfile", 1
        )[0]
        self.assertIn("playlistPlayer.stop()", reset)
        self.assertIn("librarySearch.reset()", reset)
        self.assertIn("loadedProfileId=''", reset)
        self.assertIn("++profileRequest", reset)
        self.assertIn("window.addEventListener('pch-auth-logout',resetSession)", script)
        self.assertIn("function announceLogout()", auth)
        self.assertIn("function expireSession()", auth)
        request = auth.split("async function request", 1)[1].split("async function post", 1)[0]
        logout = auth.split("async function logout", 1)[1].split("window.PCHAuth", 1)[0]
        self.assertIn("expireSession()", request)
        self.assertIn("expireSession()", logout)
        search_reset = search.split("function reset()", 1)[1].split(
            "function focus", 1
        )[0]
        self.assertIn("if(dialog.open)dialog.close()", search_reset)

    def test_manual_edits_update_sidebar_counts_for_current_or_other_playlist(self):
        script = (STATIC / "playlists.js").read_text(encoding="utf-8")
        search = (STATIC / "playlist-search.js").read_text(encoding="utf-8")
        self.assertIn("function syncPlaylistCount(kind,key,count)", script)
        sync = script.split("function syncPlaylistCount", 1)[1].split(
            "function restorePlaylistView", 1
        )[0]
        self.assertIn("item.count", sync)
        self.assertIn("renderPlaylistList()", sync)
        self.assertIn("const result=await requestJson", search)
        self.assertIn("onPlaylistChanged(kind,key,result.count)", search)
        self.assertIn("syncPlaylistCount(selected.kind,selected.key,result.count)", script)

    def test_initial_inventory_opens_a_section_without_eager_playlist_reads(self):
        script = (STATIC / "playlists.js").read_text(encoding="utf-8")
        load = script.split("async function loadPlaylists", 1)[1].split(
            "async function loadProfiles", 1
        )[0]
        self.assertNotIn("openFirstAvailable", script)
        self.assertIn("openSection(preferred?.section||'smart')", load)
        self.assertIn("if(selected&&selected.can_play)", load)

    def test_small_screen_sidebar_is_a_closed_drawer_with_one_toggle(self):
        page = (STATIC / "playlists.html").read_text(encoding="utf-8")
        script = (STATIC / "playlists.js").read_text(encoding="utf-8")
        styles = (STATIC / "product.css").read_text(encoding="utf-8")
        self.assertEqual(1, page.count('id="playlistSidebarToggle"'))
        self.assertEqual(1, page.count('id="playlistSidebarBackdrop"'))
        self.assertIn('id="playlistSidebar"', page)
        self.assertIn("function setSidebarOpen(value)", script)
        self.assertIn("playlist-sidebar-open", script)
        self.assertIn("compactSidebar.matches", script)
        self.assertIn("sidebar.inert=compact&&!open", script)
        self.assertIn("backdrop.hidden=!open", script)
        self.assertIn("compactSidebar.addEventListener('change'", script)
        mobile = styles.split("@media(max-width:700px){\n", 1)[1].split("\n}", 1)[0]
        self.assertIn("grid-template-columns:minmax(0,1fr)", mobile)
        self.assertIn("transform:translateX(-100%)", mobile)
        self.assertIn(".playlist-sidebar-open .playlist-sidebar", mobile)

    def test_stopping_player_clears_every_visible_track_state(self):
        player = (STATIC / "playlist-player.js").read_text(encoding="utf-8")
        stop = player.split("function stop()", 1)[1].split("function mount()", 1)[0]

        self.assertIn("updateArtwork(null)", stop)
        self.assertIn("playerCurrent", stop)
        self.assertIn("playerDuration", stop)
        self.assertIn("playerSeek", stop)
        self.assertIn("0:00", stop)

    def test_metadata_duration_drives_unknown_transcode_timeline(self):
        player = (STATIC / "playlist-player.js").read_text(encoding="utf-8")
        self.assertIn("let sourceOffset=0", player)
        self.assertIn("let trackDuration=0", player)
        self.assertIn("function logicalPosition()", player)
        timeline = player.split("function updateTimeline()", 1)[1].split(
            "function bindSourceHandlers", 1
        )[0]
        self.assertIn("logicalPosition()", timeline)
        self.assertIn("trackDuration", timeline)
        self.assertIn("playerDuration", timeline)

    def test_local_media_uses_catalog_routes_and_unknown_streams_do_not_seek_by_offset(self):
        player = (STATIC / "playlist-player.js").read_text(encoding="utf-8")
        script = (STATIC / "playlists.js").read_text(encoding="utf-8")
        seek = player.split("function seekTo(seconds)", 1)[1].split(
            "function playNext", 1
        )[0]
        self.assertIn("Number.isFinite(audio.duration)", seek)
        self.assertNotIn("replaceSource(target,true", seek)
        self.assertNotIn("offsetSeconds", player)
        media = script.split("function mediaUrl", 1)[1].split(
            "function playlistContext", 1
        )[0]
        self.assertIn("/api/playlists/library/tracks/", media)
        self.assertNotIn("context?.kind", media)
        self.assertNotIn("params.set('offset'", media)

    def test_stale_source_media_events_are_generation_and_source_guarded(self):
        player = (STATIC / "playlist-player.js").read_text(encoding="utf-8")
        handlers = player.split("function bindSourceHandlers", 1)[1].split(
            "function replaceSource", 1
        )[0]
        for event in ("onerror", "ontimeupdate", "onplaying", "onpause", "onended"):
            self.assertIn("audio." + event, handlers)
        self.assertGreaterEqual(handlers.count("generation!==playbackGeneration"), 5)
        self.assertGreaterEqual(handlers.count("sourceMatches(source)"), 5)

    def test_player_reports_profile_scoped_playback_without_blocking_audio(self):
        player = (STATIC / "playlist-player.js").read_text(encoding="utf-8")
        script = (STATIC / "playlists.js").read_text(encoding="utf-8")
        web = (ROOT / "src/helper/web.py").read_text(encoding="utf-8")
        self.assertIn("reportPlayback=()=>Promise.resolve()", player)
        self.assertIn("const PROGRESS_INTERVAL=15", player)
        self.assertIn("function report(event", player)
        for name in ("play", "resume", "pause", "progress", "stop", "scrobble"):
            self.assertIn("'" + name + "'", player)
        self.assertIn("reportPlayback(event,payload", player)
        self.assertIn(".catch(()=>{})", player)
        self.assertIn("function reportWebPlayback", script)
        self.assertIn("function newWebPlayerId()", script)
        self.assertIn("typeof crypto.randomUUID==='function'", script)
        self.assertIn("Math.random()", script)
        self.assertIn("'/api/playback/events'", script)
        self.assertIn("'X-Plex-Profile':profileId", script)
        self.assertIn("keepalive", script)
        self.assertIn("reportPlayback:reportWebPlayback", script)
        self.assertIn("attach_web_playback_route", web)

    def test_page_hide_and_visibility_flush_current_profile_playback(self):
        player = (STATIC / "playlist-player.js").read_text(encoding="utf-8")
        script = (STATIC / "playlists.js").read_text(encoding="utf-8")
        self.assertIn("flush", player.split("return {", 1)[1])
        self.assertIn("document.addEventListener('visibilitychange'", script)
        self.assertIn("playlistPlayer.paused()?'pause':'progress'", script)
        self.assertIn("playlistPlayer.flush(event,true)", script)
        self.assertIn("window.addEventListener('pagehide'", script)
        self.assertIn("playlistPlayer.flush('stop',true)", script)

    def test_workspace_view_switch_keeps_player_instance_and_logical_timeline(self):
        page = (STATIC / "playlists.html").read_text(encoding="utf-8")
        script = (STATIC / "playlists.js").read_text(encoding="utf-8")
        self.assertEqual(1, page.count('id="playerAudio"'))
        open_workspace = script.split("function openWorkspacePage", 1)[1].split(
            "function openTool", 1
        )[0]
        self.assertNotIn("playlistPlayer.stop()", open_workspace)
        self.assertNotIn("playerAudio", open_workspace)

    def test_embedded_external_preview_uses_the_single_bottom_player(self):
        external = (STATIC / "external.js").read_text(encoding="utf-8")
        script = (STATIC / "playlists.js").read_text(encoding="utf-8")
        player = (STATIC / "playlist-player.js").read_text(encoding="utf-8")
        external_web = (ROOT / "src/helper/external_web.py").read_text(encoding="utf-8")

        self.assertIn("pch-player-preview", external)
        self.assertIn("window.parent.postMessage", external)
        self.assertIn("pch-player-preview", script)
        self.assertIn("playlistToolFrame", script)
        self.assertIn("event.origin!==location.origin", script)
        self.assertIn("playPreview", player)
        self.assertIn("context.kind!=='preview'||!track.source", player)
        self.assertIn("return track.source", player)
        audio_route = external_web.split(
            '@app.get("/api/external/sources/{source_id}/tracks/{track_key}/audio")', 1
        )[1]
        self.assertIn('offset: str = "0"', audio_route)
        self.assertIn("offset_seconds=offset", audio_route)

    def test_returning_from_a_tool_refreshes_the_playlist_inventory(self):
        script = (STATIC / "playlists.js").read_text(encoding="utf-8")

        self.assertIn("async function returnFromWorkspace()", script)
        refresh = script.split("async function returnFromWorkspace()", 1)[1].split(
            "function openWorkspacePage", 1
        )[0]
        self.assertIn("workspace.current()", refresh)
        self.assertIn("view.type==='playlist'", refresh)
        self.assertIn("await loadPlaylists(preferred,requestId)", refresh)
        self.assertIn("playlistToolBack", script)
        self.assertIn("returnFromWorkspace", script.split("playlistToolBack", 1)[1])

    def test_library_search_handles_failures_stale_errors_and_keyboard_playback(self):
        search = (STATIC / "playlist-search.js").read_text(encoding="utf-8")

        run = search.split("async function run(query)", 1)[1].split(
            "async function confirmAdd", 1
        )[0]
        self.assertIn("catch(error)", run)
        self.assertGreaterEqual(run.count("requestId!==requestGeneration"), 2)
        self.assertIn("搜索失败，请重试", run)
        self.assertIn("row.onkeydown", search)
        self.assertIn("event.key==='Enter'||event.key===' '", search)

    def test_workspace_navigation_cancels_an_inflight_library_search(self):
        script = (STATIC / "playlists.js").read_text(encoding="utf-8")
        navigation = script.split("function openWorkspacePage", 1)[1].split(
            "function openTool", 1
        )[0]

        self.assertIn("librarySearch.reset()", navigation)

    def test_embedded_auth_loss_resets_the_parent_workspace(self):
        auth = (STATIC / "auth.js").read_text(encoding="utf-8")
        script = (STATIC / "playlists.js").read_text(encoding="utf-8")

        self.assertIn("const EMBEDDED=", auth)
        self.assertIn("window.parent.postMessage({type:'pch-auth-logout'}", auth)
        self.assertIn("expire:expireSession", auth)
        boot = auth.split("async function boot()", 1)[1].split("async function login", 1)[0]
        self.assertIn("else if(EMBEDDED)expireSession()", boot)
        listener = script.split("window.addEventListener('message'", 1)[1].split(
            "window.addEventListener('keydown'", 1
        )[0]
        self.assertIn("pch-auth-logout", listener)
        self.assertIn("PCHAuth.expire()", listener)


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

    def test_embedded_external_workspace_is_compact_and_has_no_nested_scroll(self):
        page = (STATIC / "external.html").read_text(encoding="utf-8")
        script = (STATIC / "external.js").read_text(encoding="utf-8")
        hub_script = (STATIC / "playlists.js").read_text(encoding="utf-8")
        styles = (STATIC / "product.css").read_text(encoding="utf-8")
        self.assertIn("external-page-title", page)
        self.assertIn("external-source-strip", page)
        self.assertIn("function showPreviewError", script)
        self.assertNotIn("ResizeObserver", script)
        self.assertNotIn("pch-tool-height", script)
        self.assertNotIn("pch-tool-height", hub_script)
        self.assertNotIn("player.play().catch(()=>notify('浏览器暂时无法播放", script)
        self.assertIn(".pch-embedded body[data-view=external] .external-shell", styles)
        self.assertIn(".external-source-list{display:flex", styles)
        self.assertNotIn("is-auto-height", styles)


if __name__ == "__main__":
    unittest.main()
