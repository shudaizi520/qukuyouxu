import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/helper/static"
sys.path.insert(0, str(ROOT / "src"))


class PlaylistSectionUiTests(unittest.TestCase):
    def test_sidebar_has_two_fixed_hubs_and_one_custom_list(self):
        page = (STATIC / "playlists.html").read_text(encoding="utf-8")
        self.assertEqual(1, page.count('id="smartHubButton"'))
        self.assertEqual(1, page.count('id="libraryHubButton"'))
        self.assertEqual(1, page.count('id="customPlaylistList"'))
        self.assertEqual(1, page.count('id="customPlaylistRefresh"'))
        self.assertNotIn('id="playlistAddTrack"', page)
        self.assertEqual(1, page.count('id="playlistSectionView"'))

    def test_sections_module_groups_without_kind_badges(self):
        script = (STATIC / "playlist-sections.js").read_text(encoding="utf-8")
        self.assertIn("row.section==='smart'", script)
        self.assertIn("row.section==='library'", script)
        self.assertIn("row.section==='custom'", script)
        self.assertIn("export function createPlaylistSections", script)
        self.assertNotIn("item.kind==='daily'?'日'", script)

    def test_workspace_supports_section_view_and_one_active_navigation_item(self):
        workspace = (STATIC / "playlist-workspace.js").read_text(encoding="utf-8")
        self.assertIn("view.type==='section'", workspace)
        self.assertIn("playlistSectionView.hidden=current.panel!=='section'", workspace)
        self.assertIn("#smartHubButton,#libraryHubButton,#customPlaylistList button", workspace)
        self.assertIn("classList.toggle('active'", workspace)

    def test_close_button_is_visible_before_hover_and_uses_theme_variables(self):
        styles = (STATIC / "product.css").read_text(encoding="utf-8")
        rule = styles.split(".playlist-dialog-head button{", 1)[1].split("}", 1)[0]
        self.assertIn("color:var(--playlist-muted)", rule)
        self.assertIn(".playlist-dialog-head button:hover", styles)
        self.assertIn(".playlist-dialog-head button:focus-visible", styles)

    def test_new_module_is_served_by_the_static_allowlist(self):
        web = (ROOT / "src/helper/web.py").read_text(encoding="utf-8")
        self.assertIn("'playlist-sections.js'", web)


if __name__ == "__main__":
    unittest.main()
