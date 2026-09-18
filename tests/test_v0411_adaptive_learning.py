import unittest


DAY = 86400
NOW = 2_000_000_000


def event(track_id, value, kind, days_ago):
    return {
        "track_id": str(track_id),
        "value": value,
        "kind": kind,
        "at": NOW - days_ago * DAY,
    }


class AdaptiveBehaviorProfileTests(unittest.TestCase):
    def test_recent_behavior_events_excludes_expired_and_future_rows(self):
        from helper.behavior import MAX_EVENT_AGE, recent_behavior_events

        rows = [
            {"track_id": "1", "at": NOW - MAX_EVENT_AGE},
            {"track_id": "2", "at": NOW - MAX_EVENT_AGE - 1},
            {"track_id": "3", "at": NOW + 1},
            {"track_id": "4", "at": NOW - 10},
        ]

        self.assertEqual(["1", "4"], [row["track_id"] for row in recent_behavior_events(rows, NOW)])

    def test_one_recent_skip_does_not_erase_established_taste(self):
        from helper.behavior import behavior_profile

        profile = behavior_profile([
            event(1, 1.0, "completed", 90),
            event(1, 1.0, "completed", 45),
            event(1, 1.0, "completed", 15),
            event(1, -0.45, "observed_skip", 1),
        ], NOW)["1"]

        self.assertGreater(profile["long_term_score"], 0)
        self.assertEqual(1, profile["recent_skip_days"])
        self.assertEqual(7, profile["fatigue_days"])
        self.assertGreater(profile["cooldown_until"], NOW)

    def test_repeated_skips_extend_temporary_fatigue_without_flipping_taste(self):
        from helper.behavior import behavior_profile

        profile = behavior_profile([
            event(1, 1.0, "completed", 120),
            event(1, 1.0, "completed", 60),
            event(1, 1.0, "completed", 20),
            event(1, -0.4, "observed_skip", 5),
            event(1, -0.4, "observed_skip", 3),
            event(1, -0.4, "observed_skip", 1),
        ], NOW)["1"]

        self.assertGreater(profile["long_term_score"], 0)
        self.assertEqual(3, profile["recent_skip_days"])
        self.assertEqual(30, profile["fatigue_days"])
        self.assertEqual(NOW - DAY + 30 * DAY, profile["cooldown_until"])

    def test_single_skip_of_unknown_track_is_only_weak_short_term_evidence(self):
        from helper.behavior import behavior_profile

        profile = behavior_profile([
            event(9, -0.5, "observed_skip", 1),
        ], NOW)["9"]

        self.assertEqual(0, profile["long_term_score"])
        self.assertEqual(1, profile["recent_skip_days"])
        self.assertLess(profile["short_term_score"], 0)
        self.assertEqual(0, profile["cooldown_until"])

    def test_later_completion_recovers_from_earlier_skip(self):
        from helper.behavior import behavior_profile

        profile = behavior_profile([
            event(5, -0.5, "observed_skip", 10),
            event(5, 1.0, "completed", 1),
        ], NOW)["5"]

        self.assertGreater(profile["long_term_score"], 0)
        self.assertGreater(profile["short_term_score"], 0)
        self.assertEqual(0, profile["cooldown_until"])

    def test_repeated_unknown_skips_create_temporary_cooldown(self):
        from helper.behavior import behavior_profile

        profile = behavior_profile([
            event(8, -0.4, "observed_skip", 5),
            event(8, -0.4, "observed_skip", 3),
            event(8, -0.4, "observed_skip", 1),
        ], NOW)["8"]

        self.assertEqual(3, profile["recent_skip_days"])
        self.assertEqual(14, profile["fatigue_days"])
        self.assertGreater(profile["cooldown_until"], NOW)

    def test_completion_resets_the_skip_streak_before_a_new_skip(self):
        from helper.behavior import behavior_profile

        profile = behavior_profile([
            event(6, -0.4, "observed_skip", 10),
            event(6, 1.0, "completed", 5),
            event(6, -0.4, "observed_skip", 1),
        ], NOW)["6"]

        self.assertEqual(1, profile["recent_skip_days"])
        self.assertEqual(7, profile["fatigue_days"])


if __name__ == "__main__":
    unittest.main()
