"""Regression contracts for imported-song audition in the shared player."""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/helper/static"


class ExternalPreviewTests(unittest.TestCase):
    def test_public_matches_include_current_plex_like_state(self):
        from helper.external_web import _public_track

        catalog = {
            "10": {"user_rating": 10, "duration": 193},
            "20": {"title": "另一版本", "artist": "歌手", "duration": 205, "user_rating": 0},
        }
        matched = _public_track({
            "source_track_key": "first", "status": "matched", "plex_track_id": "10",
            "candidate_ids": [], "artists": ["歌手"],
        }, catalog)
        review = _public_track({
            "source_track_key": "second", "status": "review", "plex_track_id": "",
            "candidate_ids": ["20"], "artists": ["歌手"],
        }, catalog)
        self.assertTrue(matched["liked"])
        self.assertEqual(10, matched["user_rating"])
        self.assertEqual(193, matched["plex_duration"])
        self.assertFalse(review["candidates"][0]["liked"])

    def test_audition_button_label_is_stable_and_active_song_is_marked(self):
        script = (STATIC / "external.js").read_text(encoding="utf-8")
        styles = (STATIC / "product.css").read_text(encoding="utf-8")
        self.assertNotIn("底部播放", script)
        for label in ("暂停", "继续", "重播", "重试"):
            self.assertNotIn("button.textContent='" + label + "'", script)
        self.assertIn("pch-player-preview-state", script)
        self.assertIn("external-preview-active", script)
        self.assertIn(".external-preview-active::after", styles)

    def test_rerendered_rows_restore_the_current_audition_marker(self):
        script = (STATIC / "external.js").read_text(encoding="utf-8")
        render = script.split("function renderTracks(){", 1)[1].split("function renderReviewActions", 1)[0]
        self.assertIn("pch-player-preview-query", render)
        self.assertIn("button.dataset.previewKey", script.split("function auditionButton(", 1)[1].split("function markAuditionButton", 1)[0])

    def test_preview_like_state_remains_current_after_switching_songs(self):
        external = (STATIC / "external.js").read_text(encoding="utf-8")
        parent = (STATIC / "playlists.js").read_text(encoding="utf-8")
        self.assertIn("pch-player-like-state", external)
        self.assertIn("pch-player-like-state", parent)
        self.assertIn("profile_id:profile", external)
        self.assertIn("track.profile_id", parent)

    def test_preview_duration_uses_plex_candidate_not_external_source(self):
        external = (STATIC / "external.js").read_text(encoding="utf-8")
        self.assertIn("chosen?.duration||track.plex_duration", external)
        self.assertNotIn("chosen?.duration||track.duration_ms/1000", external)

    def test_audition_passes_plex_identity_and_duration_to_shared_player(self):
        external = (STATIC / "external.js").read_text(encoding="utf-8")
        player = (STATIC / "playlist-player.js").read_text(encoding="utf-8")
        parent = (STATIC / "playlists.js").read_text(encoding="utf-8")
        self.assertIn("plex_track_id", external)
        self.assertIn("duration_ms", external)
        self.assertIn("previewKey", external)
        self.assertIn("previewKey:()=>", player)
        self.assertIn("key:String(track.previewKey", player)
        self.assertIn("pch-player-preview-state", parent)
        self.assertNotIn("context?.kind==='preview'||", player)

    def test_player_commits_drag_once_and_uses_known_duration_for_stream_seek(self):
        player = (STATIC / "playlist-player.js").read_text(encoding="utf-8")
        self.assertIn("byId(document,'playerSeek').onchange", player)
        self.assertIn("function availableDuration()", player)
        self.assertIn("duration=availableDuration()", player)
        self.assertIn("url.searchParams.set('offset'", player)
        self.assertIn("audio.seekable", player)
        self.assertIn("replaceSource(!audio.paused,true,target)", player)
        self.assertIn("if(!trackDuration&&duration)trackDuration=duration", player)

    def test_last_preview_clears_active_marker_when_audio_ends(self):
        player = (STATIC / "playlist-player.js").read_text(encoding="utf-8")
        next_track = player.split("function playNext()", 1)[1].split("function playPrevious()", 1)[0]
        self.assertIn("setPlaying(false);onStateChange()", next_track)

    def test_progress_slider_has_a_drag_target_larger_than_its_visual_line(self):
        styles = (STATIC / "product.css").read_text(encoding="utf-8")
        self.assertIn(".playlist-progress-rail input[type=range]{height:18px", styles)
        self.assertIn(".playlist-player{height:80px;grid-template-rows:18px", styles)

    def test_auto_refresh_explains_its_schedule_dependency_without_extra_visible_copy(self):
        page = (STATIC / "external.html").read_text(encoding="utf-8")
        self.assertIn('title="新增歌曲整理运行时重新读取来源；立即刷新请点重新读取"', page)


if __name__ == "__main__":
    unittest.main()
