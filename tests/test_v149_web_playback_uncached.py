import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class _Store:
    def get(self, key, default=None):
        return {"catalog": [], "settings": {"section": "11"}}.get(key, default)


class UncachedWebPlaybackTests(unittest.TestCase):
    def test_verified_plex_track_can_generate_learning_event(self):
        from helper.web_playback import _parse_payload

        class Plex:
            def track_section(self, track_id):
                self.track_id = track_id
                return "11"

        plex = Plex()
        payload = {"event": "play", "track_id": "7", "player_id": "web-player", "event_id": "one", "position": 0, "duration": 180}
        profile = {"id": "default", "account": {"id": "owner"}, "server": {"machine": "plex"}, "library": {"id": "11"}}
        event = _parse_payload(_Store(), profile, payload, plex_factory=lambda _settings: plex)
        self.assertEqual("7", event["track_id"])
        self.assertEqual("7", plex.track_id)

    def test_other_library_track_is_rejected(self):
        from helper.web_playback import _parse_payload

        class Plex:
            def track_section(self, _track_id):
                return "22"

        payload = {"event": "play", "track_id": "7", "player_id": "web-player", "event_id": "one", "position": 0, "duration": 180}
        profile = {"id": "default", "library": {"id": "11"}}
        with self.assertRaisesRegex(ValueError, "当前曲库"):
            _parse_payload(_Store(), profile, payload, plex_factory=lambda _settings: Plex())
