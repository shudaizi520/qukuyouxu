"""Regression contracts for the simplified imported-song results."""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/helper/static"


def _rule(css: str, selector: str) -> dict[str, str]:
    declarations = {}
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if selector not in [item.strip() for item in selectors.split(",")]:
            continue
        for part in body.split(";"):
            if ":" in part:
                key, value = part.strip().split(":", 1)
                declarations[key] = value
    return declarations


class ExternalPreviewTests(unittest.TestCase):
    def test_public_matches_include_current_plex_like_state(self):
        from helper.external_web import _public_track

        catalog = {
            "10": {
                "user_rating": 10,
                "duration": 193,
                "thumb": "/library/metadata/10/thumb/1",
            },
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
        self.assertEqual("/library/metadata/10/thumb/1", matched["thumb"])
        self.assertFalse(review["candidates"][0]["liked"])

    def test_import_results_have_no_audition_controls(self):
        script = (STATIC / "external.js").read_text(encoding="utf-8")
        page = (STATIC / "external.html").read_text(encoding="utf-8")
        self.assertNotIn("auditionPlayer", page)
        self.assertNotIn("external-preview", script)
        self.assertNotIn("pch-player-preview", script)

    def test_matched_rows_render_the_same_metadata_columns_as_playlists(self):
        script = (STATIC / "external.js").read_text(encoding="utf-8")
        render = script.split("function renderTracks(){", 1)[1].split("function renderReviewActions", 1)[0]
        for class_name in ("external-track-number", "external-track-identity", "external-track-artist", "external-track-album", "external-track-duration"):
            self.assertIn(class_name, render)
        self.assertNotIn("external-track-actions", render.split("if(activeStatus==='review')", 1)[0])

    def test_import_results_do_not_exchange_player_or_like_messages(self):
        external = (STATIC / "external.js").read_text(encoding="utf-8")
        parent = (STATIC / "playlists.js").read_text(encoding="utf-8")
        self.assertNotIn("pch-player-like-state", external)
        self.assertNotIn("pch-player-like-state", parent)
        self.assertNotIn("pch-player-preview", external + parent)

    def test_import_duration_is_display_only(self):
        external = (STATIC / "external.js").read_text(encoding="utf-8")
        self.assertIn("track.duration_ms?audioTime(track.duration_ms/1000)", external)
        self.assertNotIn("/audio?", external)
        self.assertNotIn("function playTrack", external)

    def test_shared_player_has_no_import_preview_mode(self):
        external = (STATIC / "external.js").read_text(encoding="utf-8")
        player = (STATIC / "playlist-player.js").read_text(encoding="utf-8")
        parent = (STATIC / "playlists.js").read_text(encoding="utf-8")
        self.assertIn("duration_ms", external)
        self.assertNotIn("previewKey", external + player)
        self.assertNotIn("playPreview", player)
        self.assertNotIn("pch-player-preview", parent)

    def test_imported_queue_uses_one_normalizer_on_both_sides_of_the_frame_bridge(self):
        external = (STATIC / "external.js").read_text(encoding="utf-8")
        parent = (STATIC / "playlists.js").read_text(encoding="utf-8")
        page = (STATIC / "external.html").read_text(encoding="utf-8")

        self.assertIn("normalizePlaybackTrack(item,profileId)", external)
        self.assertIn("normalizePlaybackTrack(track,loadedProfileId)", parent)
        self.assertIn('type="module" src="/static/external.js', page)

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
        next_track = player.split("function advanceNext(reason)", 1)[1].split("function playNext()", 1)[0]
        self.assertIn("if(reason==='ended'){setPlaying(false);updateTimeline();emitState();}", next_track)

    def test_progress_slider_has_a_drag_target_larger_than_its_visual_line(self):
        styles = (STATIC / "design-system.css").read_text(encoding="utf-8")
        rail = _rule(styles, "body[data-view=playlists] .playlist-progress-rail")
        target = _rule(styles, "body[data-view=playlists] .playlist-progress-rail input[type=range]")
        self.assertEqual(rail["height"], "2px")
        self.assertEqual(target["height"], "20px")
        self.assertEqual(target["opacity"], "0")

    def test_auto_refresh_keeps_only_the_control_label_without_explanatory_copy(self):
        page = (STATIC / "external.html").read_text(encoding="utf-8")
        self.assertNotIn('title="新增歌曲整理运行时重新读取来源；立即刷新请点重新读取"', page)
        self.assertIn('id="followUpdatesRow"', page)


if __name__ == "__main__":
    unittest.main()
