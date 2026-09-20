import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StatusUiV120Tests(unittest.TestCase):
    def test_status_uses_cooled_tracks_instead_of_negative_tracks(self):
        page = (ROOT / "src/helper/static/status.html").read_text()
        script = (ROOT / "src/helper/static/status.js").read_text()

        for label in ("已学习", "偏好歌曲", "冷却中", "当前播放"):
            self.assertIn(label, page)
        self.assertNotIn("行为事件", page)
        self.assertNotIn("正向歌曲", page)
        self.assertNotIn("负向歌曲", page)
        self.assertIn("b.learned_tracks", script)
        self.assertIn("b.preferred_tracks", script)
        self.assertIn("b.cooled_tracks", script)
        self.assertNotIn("b.positive_tracks", script)
        self.assertNotIn("b.negative_tracks", script)

    def test_status_api_reports_v2_learning_counts_without_changing_connection_state(self):
        from helper.behavior_store import BehaviorRepository
        from helper.extra_web import extensions_status
        from helper.scoped_store import ScopedStore
        from helper.store import Store

        now = time.time()
        with tempfile.TemporaryDirectory() as directory:
            base = Store(Path(directory))
            store = ScopedStore(base, "default")
            repository = BehaviorRepository(base)
            repository.save_track_state("default", "1", {
                "positive_evidence": 2.0, "affinity": 0.7,
                "confidence": 0.8, "updated_at": now,
            })
            repository.save_track_state("default", "2", {
                "skip_evidence": 0.7, "cooldown_until": now + 86400,
                "updated_at": now,
            })
            repository.append_event("default", {
                "event_key": "event-1", "track_id": "1", "kind": "completed",
                "value": 1.0, "at": now,
            })

            result = extensions_status(store)

        behavior = result["behavior"]
        self.assertEqual(1, behavior["event_count"])
        self.assertEqual(2, behavior["learned_tracks"])
        self.assertEqual(1, behavior["preferred_tracks"])
        self.assertEqual(1, behavior["cooled_tracks"])
        self.assertEqual(0, behavior["active_sessions"])
        self.assertIn("state", result["webhook"])


if __name__ == "__main__":
    unittest.main()
