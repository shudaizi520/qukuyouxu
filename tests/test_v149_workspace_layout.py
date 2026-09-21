from pathlib import Path
import re
import unittest


STATIC = Path(__file__).resolve().parents[1] / "src/helper/static"


class WorkspaceLayoutTests(unittest.TestCase):
    def test_sidebar_count_is_not_shown(self):
        html = (STATIC / "playlists.html").read_text()
        self.assertNotIn('id="customPlaylistCount"', html)
        self.assertNotIn("customPlaylistCount", (STATIC / "playlist-sections.js").read_text())

    def test_contextual_settings_are_in_hubs(self):
        html = (STATIC / "playlists.html").read_text()
        self.assertIn('id="playlistSectionSettings"', html)
        js = (STATIC / "playlists.js").read_text()
        self.assertIn("$('playlistSectionSettings').onclick", js)
        settings = (STATIC / "settings.html").read_text()
        for name in ("playlists", "recommend", "automation"):
            self.assertNotIn(f'data-settings-target="{name}"', settings)

    def test_embedded_settings_links_stay_in_music_workspace(self):
        auth = (STATIC / "auth.js").read_text()
        player = (STATIC / "playlists.js").read_text()
        self.assertIn("pch-workspace-navigate", auth)
        self.assertIn("pch-workspace-navigate", player)
        self.assertIn("event.source!==$('playlistToolFrame').contentWindow", player)

    def test_hearts_live_next_to_song_and_player_identity(self):
        html = (STATIC / "playlists.html").read_text()
        self.assertLess(html.index('id="playerLiked"'), html.index('id="playerQueue"'))
        js = (STATIC / "playlists.js").read_text()
        self.assertIn("identity.append(heart,title)", js)
        search = (STATIC / "playlist-search.js").read_text()
        self.assertIn("titleLine.append(heart,title)", search)

    def test_heart_states_keep_pressed_red_and_unpressed_hover_red(self):
        css = (STATIC / "product.css").read_text()

        def resolved_rules(selector):
            declarations = {}
            found = False
            for names, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
                if selector in [name.strip() for name in names.split(",")]:
                    found = True
                    declarations.update(dict(
                        declaration.strip().split(":", 1)
                        for declaration in body.split(";") if ":" in declaration
                    ))
            self.assertTrue(found, selector)
            return declarations

        unpressed = resolved_rules(".playlist-heart:not([aria-pressed=true]):hover")
        pressed = resolved_rules(".playlist-heart[aria-pressed=true]")
        pressed_hover = resolved_rules(".playlist-heart[aria-pressed=true]:hover")
        self.assertEqual("var(--playlist-heart)", unpressed["color"])
        self.assertEqual("transparent", unpressed["background"])
        self.assertEqual("var(--playlist-heart)", pressed["color"])
        self.assertEqual(pressed["color"], pressed_hover["color"])
        self.assertEqual("transparent", pressed_hover["background"])
        self.assertGreaterEqual(int(resolved_rules(".playlist-now-info #playerLiked")["font-size"].removesuffix("px")), 24)

    def test_player_progress_fills_elapsed_part_of_rail(self):
        player = (STATIC / "playlist-player.js").read_text()
        css = (STATIC / "product.css").read_text()
        self.assertIn("--playlist-played", player)
        self.assertIn("var(--playlist-played", css)
        self.assertIn("::-moz-range-progress", css)

    def test_search_and_volume_have_compact_aligned_controls(self):
        css = (STATIC / "product.css").read_text()
        self.assertIn(".playlist-global-search:focus-within", css)
        self.assertIn(".playlist-player-center .playlist-volume-control button", css)

    def test_external_import_has_compact_results(self):
        js = (STATIC / "external.js").read_text()
        self.assertIn("const PAGE_SIZE=25", js)
        css = (STATIC / "product.css").read_text()
        self.assertIn(".external-track-list{max-height:", css)

    def test_smart_and_library_settings_own_their_schedules(self):
        smart = (STATIC / "mixes.html").read_text()
        library = (STATIC / "home.html").read_text()
        self.assertIn('id="dailyPolicyForm"', smart)
        self.assertIn('id="smartAutomationEnabled"', smart)
        self.assertIn('id="libraryAutomationEnabled"', library)
        self.assertIn('/static/contextual-settings.js', smart)
        self.assertIn('/static/contextual-settings.js', library)

    def test_daily_and_other_schedules_are_next_to_their_own_settings(self):
        smart = (STATIC / "mixes.html").read_text()
        self.assertLess(smart.index('id="dailyAutomationHour"'), smart.index('id="dailyPolicyForm"'))
        self.assertGreater(smart.index('id="smartIntervalDays"'), smart.index('class="mix-list"'))
        self.assertIn('<option value="1">每 1 天</option>', smart)

    def test_smart_publish_refreshes_sidebar_without_leaving_settings(self):
        smart = (STATIC / "mixes.js").read_text()
        hub = (STATIC / "playlists.js").read_text()
        self.assertIn("type:'pch-playlists-changed'", smart)
        self.assertIn("event.data?.type==='pch-playlists-changed'", hub)

    def test_library_list_uses_compact_more_menu(self):
        library = (STATIC / "theme_home.js").read_text()
        self.assertIn("managed-playlist-more", library)
        self.assertIn("pch-open-playlist", library)

    def test_daily_settings_show_bounded_preview_before_publication(self):
        html = (STATIC / "mixes.html").read_text()
        script = (STATIC / "contextual-settings.js").read_text()
        self.assertIn('id="dailyPreview"', html)
        self.assertIn('id="dailyPreviewTracks"', html)
        self.assertIn('.slice(0,10)', script)


if __name__ == "__main__":
    unittest.main()
