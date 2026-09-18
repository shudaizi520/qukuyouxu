import tempfile
import time
import unittest
from pathlib import Path
import sys
import types
import fastapi


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if "fastapi" not in sys.modules:
    fastapi = types.ModuleType("fastapi")
    fastapi.Request = object
    responses = types.ModuleType("fastapi.responses")
    responses.Response = object
    fastapi.responses = responses
    sys.modules["fastapi"] = fastapi
    sys.modules["fastapi.responses"] = responses


class StatusRefreshV0425Tests(unittest.TestCase):
    def setUp(self):
        from helper.scoped_store import ScopedStore
        from helper.store import Store

        self.temp = tempfile.TemporaryDirectory()
        self.base = Store(Path(self.temp.name))
        self.store = ScopedStore(self.base, "default")

    def tearDown(self):
        self.temp.cleanup()

    def test_status_refreshes_every_five_seconds_during_playback(self):
        from helper.extra_web import extensions_status

        self.store.set("behavior_status", {"active": 0})
        self.store.set("behavior_sessions", {
            "10:phone": {
                "state": "media.play", "terminal_event": "", "updated_at": time.time(),
                "duration_seconds": 240, "offset_seconds": 20,
            }
        })

        status = extensions_status(self.store)
        self.assertEqual(1, status["behavior"]["active_sessions"])
        self.assertEqual(5000, status["status_refresh_ms"])

    def test_status_expires_playback_when_stop_webhook_is_missing(self):
        from helper.extra_web import extensions_status

        self.store.set("behavior_status", {"active": 1})
        self.store.set("behavior_sessions", {
            "10:phone": {
                "state": "media.play", "terminal_event": "", "updated_at": time.time() - 400,
                "duration_seconds": 180, "offset_seconds": 0,
            }
        })

        status = extensions_status(self.store)
        self.assertEqual(0, status["behavior"]["active_sessions"])
        self.assertEqual(45000, status["status_refresh_ms"])

    def test_paused_session_is_not_reported_as_currently_playing(self):
        from helper.extra_web import extensions_status

        self.store.set("behavior_status", {"active": 1})
        self.store.set("behavior_sessions", {
            "10:phone": {
                "state": "media.pause", "terminal_event": "", "updated_at": time.time(),
                "duration_seconds": 240, "offset_seconds": 30,
            }
        })

        self.assertEqual(0, extensions_status(self.store)["behavior"]["active_sessions"])


if __name__ == "__main__":
    unittest.main()
