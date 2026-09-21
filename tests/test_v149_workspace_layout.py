from pathlib import Path
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

    def test_daily_settings_show_bounded_preview_before_publication(self):
        html = (STATIC / "mixes.html").read_text()
        script = (STATIC / "contextual-settings.js").read_text()
        self.assertIn('id="dailyPreview"', html)
        self.assertIn('id="dailyPreviewTracks"', html)
        self.assertIn('.slice(0,10)', script)


if __name__ == "__main__":
    unittest.main()
