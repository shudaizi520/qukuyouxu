import sys
import unittest
import re
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

    def test_ui_has_one_binary_heart_and_no_rating_selector(self):
        page = (STATIC / "playlists.html").read_text(encoding="utf-8")
        script = (STATIC / "playlists.js").read_text(encoding="utf-8")
        self.assertEqual(1, page.count('id="playerLiked"'))
        self.assertIn("track.liked?'♥':'♡'", script)
        self.assertIsNone(re.search(r"rating-slider|rating-select|playerStars", page + script))

    def test_search_targets_only_accept_capable_playlists(self):
        script = (STATIC / "playlist-search.js").read_text(encoding="utf-8")
        self.assertIn("row=>row.playlist_id&&row.can_add_tracks", script)
        self.assertIn("当前没有可添加歌曲的普通歌单", script)
        self.assertIn("侧栏“＋”新建歌单", script)

    def test_controls_follow_backend_capabilities_and_warn_before_delete(self):
        page = (STATIC / "playlists.html").read_text(encoding="utf-8")
        script = (STATIC / "playlists.js").read_text(encoding="utf-8")
        self.assertEqual(1, page.count('id="playlistRename"'))
        self.assertEqual(1, page.count('id="playlistRenameDialog"'))
        self.assertIn("item.can_rename", script)
        self.assertIn("item.can_delete", script)
        self.assertIn("current?.can_remove_tracks", script)
        self.assertIn("只删除歌单，不删除音乐文件", script)
        self.assertIn("/api/playlists/rename", script)

    def test_liked_writes_are_serialized_and_profile_guarded_with_rollback(self):
        script = (STATIC / "playlists.js").read_text(encoding="utf-8")
        liked = script.split("async function setLiked(track,liked)", 1)[1].split(
            "function renderTracks", 1
        )[0]
        self.assertIn("while(pending.confirmed!==pending.desired)", liked)
        self.assertIn("likedRequests.get(trackId)===pending", liked)
        self.assertIn("profileGeneration===profileRequest", liked)
        self.assertIn("paintLiked(pending.track,pending.confirmed,pending.rating)", liked)
        self.assertIn("previous!==pending.confirmed", liked)

    def test_failed_playlist_open_returns_to_its_section(self):
        script = (STATIC / "playlists.js").read_text(encoding="utf-8")
        self.assertIn("function restoreCurrentPlaylistSelection(fallbackSection='smart')", script)
        self.assertIn("restoreCurrentPlaylistSelection(item.section||'smart')", script)
        self.assertIn("type:'section',section:fallbackSection,panel:'section'", script)

    def test_profile_switch_closes_stale_rename_dialog(self):
        script = (STATIC / "playlists.js").read_text(encoding="utf-8")
        switch_profile = script.split("async function switchProfile", 1)[1].split(
            "async function removeTrack", 1
        )[0]
        self.assertIn("playlistRenameDialog", switch_profile)
        self.assertIn(".close()", switch_profile)


if __name__ == "__main__":
    unittest.main()
