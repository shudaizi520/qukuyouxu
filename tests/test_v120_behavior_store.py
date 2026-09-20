import tempfile
import unittest
from pathlib import Path

from helper.store import Store


NOW = 2_000_000_000.0


class BehaviorStoreV120Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_append_is_idempotent_and_profile_scoped(self):
        from helper.behavior_store import BehaviorRepository

        repo = BehaviorRepository(self.store)
        row = {
            "event_key": "one",
            "track_id": "7",
            "kind": "completed",
            "value": 1.0,
            "at": NOW,
        }

        self.assertTrue(repo.append_event("default", row))
        self.assertFalse(repo.append_event("default", row))
        self.assertTrue(repo.append_event("friend", row))
        self.assertEqual(["7"], [x["track_id"] for x in repo.list_events("default", NOW)])
        self.assertEqual(["7"], [x["track_id"] for x in repo.list_events("friend", NOW)])

    def test_prune_keeps_180_days_and_latest_limit_per_profile(self):
        from helper.behavior_store import EVENT_MAX_AGE, BehaviorRepository

        repo = BehaviorRepository(self.store)
        repo._event_limit = 3
        for index in range(5):
            repo.append_event(
                "default",
                {
                    "event_key": str(index),
                    "track_id": str(index),
                    "kind": "completed",
                    "value": 1.0,
                    "at": NOW - index,
                },
            )
        repo.append_event(
            "default",
            {
                "event_key": "expired",
                "track_id": "expired",
                "kind": "completed",
                "value": 1.0,
                "at": NOW - EVENT_MAX_AGE - 1,
            },
        )
        repo.append_event(
            "friend",
            {
                "event_key": "friend",
                "track_id": "friend",
                "kind": "completed",
                "value": 1.0,
                "at": NOW - 20,
            },
        )

        repo.prune("default", NOW)

        self.assertEqual(["0", "1", "2"], [x["track_id"] for x in repo.list_events("default", NOW)])
        self.assertEqual(["friend"], [x["track_id"] for x in repo.list_events("friend", NOW)])

    def test_track_and_user_aggregates_round_trip_per_profile(self):
        from helper.behavior_store import BehaviorRepository

        repo = BehaviorRepository(self.store)
        repo.save_track_state("default", "9", {"positive_evidence": 1.25})
        repo.save_track_state("friend", "9", {"positive_evidence": 0.25})
        repo.save_user_state("default", {"valid_outcomes": 4})

        self.assertEqual(1.25, repo.load_track_states("default")["9"]["positive_evidence"])
        self.assertEqual(0.25, repo.load_track_state("friend", "9")["positive_evidence"])
        self.assertEqual({"valid_outcomes": 4}, repo.load_user_state("default"))
        self.assertEqual({}, repo.load_user_state("friend"))

    def test_record_evidence_updates_event_and_aggregates_exactly_once(self):
        from helper.behavior_store import BehaviorRepository

        repo = BehaviorRepository(self.store)
        row = {
            "event_key": "atomic-one",
            "track_id": "9",
            "kind": "completed",
            "value": 1.0,
            "at": NOW,
        }

        self.assertTrue(repo.record_evidence("default", row, NOW))
        self.assertFalse(repo.record_evidence("default", row, NOW))

        state = repo.load_track_state("default", "9")
        user = repo.load_user_state("default")
        self.assertEqual(1.0, state["positive_evidence"])
        self.assertEqual(1, user["valid_outcomes"])

    def test_legacy_migration_is_idempotent_and_conservative(self):
        from helper.behavior_store import BehaviorRepository

        repo = BehaviorRepository(self.store)
        legacy = [
            {"track_id": "1", "kind": "completed", "value": 1.0, "at": NOW},
            {"track_id": "2", "kind": "observed_skip", "value": -0.5, "at": NOW},
            {"track_id": "3", "kind": "explicit_avoid", "value": -1.0, "at": NOW},
        ]

        first = repo.migrate_profile("default", legacy, NOW)
        second = repo.migrate_profile("default", legacy, NOW)

        self.assertEqual(3, first["inserted"])
        self.assertEqual(0, second["inserted"])
        rows = {row["track_id"]: row for row in repo.list_events("default", NOW)}
        self.assertEqual(-0.30, rows["2"]["value"])
        self.assertEqual(-1.0, rows["3"]["value"])
        self.assertTrue(self.store.get("profile:default:behavior_v2_migration")["complete"])

        repo.rebuild_aggregates("default", NOW)
        states = repo.load_track_states("default")
        self.assertAlmostEqual(0.30, states["2"]["skip_evidence"], places=6)
        self.assertTrue(states["3"]["hard_avoid"])

    def test_event_stats_count_without_materializing_event_payloads(self):
        from helper.behavior_store import BehaviorRepository

        repo = BehaviorRepository(self.store)
        repo.append_event("default", {
            "event_key": "first", "track_id": "1", "kind": "completed",
            "value": 1.0, "at": NOW - 10,
        })
        repo.append_event("default", {
            "event_key": "second", "track_id": "2", "kind": "confirmed_skip",
            "value": 0.45, "at": NOW,
        })

        self.assertEqual({"count": 2, "last_at": NOW}, repo.event_stats("default", NOW))

    def test_invalid_profile_id_is_rejected_before_sql(self):
        from helper.behavior_store import BehaviorRepository

        repo = BehaviorRepository(self.store)
        with self.assertRaises(ValueError):
            repo.list_events("../other", NOW)


if __name__ == "__main__":
    unittest.main()
