import tempfile
import time
import unittest
from pathlib import Path

from helper.behavior_store import BehaviorRepository
from helper.daily_mix_v2 import select_daily_mix_v2
from helper.store import Store


NOW = 2_000_000_000


def track(index):
    return {
        "id": str(index), "title": f"Song {index}", "artist": f"Artist {index}",
        "album": f"Album {index}", "duration": 180, "available": True,
        "view_count": 0, "last_viewed_at": 0, "user_rating": 0,
        "added_at": NOW - index, "genres": [],
    }


class RecommendationPerformanceV120Tests(unittest.TestCase):
    def _generate(self, count):
        started = time.perf_counter()
        result = select_daily_mix_v2(
            [track(index) for index in range(1, count + 1)], {},
            {"tracks": {}, "artists": {}}, {"size": 50}, [], [], NOW,
            f"performance-{count}", play_events=[], behavior={}, user_state={},
        )
        return result, time.perf_counter() - started

    def test_three_thousand_track_generation_remains_bounded(self):
        result, elapsed = self._generate(3000)
        self.assertEqual(50, len(result["items"]))
        self.assertLess(elapsed, 5.0)

    def test_ten_thousand_track_generation_is_linear_enough_for_nas(self):
        result, elapsed = self._generate(10000)
        self.assertEqual(50, len(result["items"]))
        self.assertLess(elapsed, 5.0)

    def test_one_event_and_aggregate_update_do_not_rewrite_legacy_json_history(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            repository = BehaviorRepository(store)
            sentinel = [{"track_id": "legacy", "kind": "completed", "at": 1}]
            store.set("profile:default:behavior_events", sentinel)
            repository.append_event("default", {
                "event_key": "one", "track_id": "1", "kind": "completed",
                "value": 1.0, "at": NOW,
            })
            repository.save_track_state("default", "1", {"positive_evidence": 1.0})
            with store.lock, store._db() as db:
                event_rows = db.execute(
                    "SELECT COUNT(*) FROM behavior_event WHERE profile_id='default'"
                ).fetchone()[0]
                aggregate_rows = db.execute(
                    "SELECT COUNT(*) FROM behavior_track_state WHERE profile_id='default'"
                ).fetchone()[0]
            legacy_history = store.get("profile:default:behavior_events")

        self.assertEqual(1, event_rows)
        self.assertEqual(1, aggregate_rows)
        self.assertEqual(sentinel, legacy_history)


if __name__ == "__main__":
    unittest.main()
