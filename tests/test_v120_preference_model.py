import unittest


DAY = 86400
NOW = 2_000_000_000.0


def user(**values):
    return {"valid_outcomes": 0, "skip_outcomes": 0, **values}


def evidence(kind, value, at=NOW):
    return {"kind": kind, "value": value, "at": at, "track_id": "1"}


class PreferenceModelV120Tests(unittest.TestCase):
    def test_personal_skip_multiplier_is_neutral_before_thirty_events(self):
        from helper.preference_model import personal_skip_multiplier

        self.assertEqual(1.0, personal_skip_multiplier(user(valid_outcomes=29, skip_outcomes=20)))

    def test_personal_skip_multiplier_is_bounded(self):
        from helper.preference_model import personal_skip_multiplier

        self.assertEqual(1.25, personal_skip_multiplier(user(valid_outcomes=100, skip_outcomes=0)))
        self.assertEqual(0.7, personal_skip_multiplier(user(valid_outcomes=100, skip_outcomes=100)))

    def test_cooldown_boundaries_match_the_approved_ladder(self):
        from helper.preference_model import cooldown_days

        cases = ((0.24, 0), (0.25, 1), (0.59, 1), (0.60, 7),
                 (1.20, 30), (2.0, 90), (3.0, 180))
        self.assertEqual([want for _, want in cases], [cooldown_days(value) for value, want in cases])

    def test_evidence_uses_the_three_independent_half_lives(self):
        from helper.preference_model import materialize_track_state

        state = {
            "positive_evidence": 2.0,
            "skip_evidence": 2.0,
            "fatigue": 2.0,
            "updated_at": NOW,
        }
        aged = materialize_track_state(state, NOW + 180 * DAY)

        self.assertAlmostEqual(1.0, aged["positive_evidence"], places=5)
        self.assertAlmostEqual(0.25, aged["skip_evidence"], places=5)
        self.assertLess(aged["fatigue"], 0.000001)

    def test_same_day_ordinary_skips_are_capped_at_point_six(self):
        from helper.preference_model import apply_evidence

        state = {}
        current_user = user()
        for index in range(4):
            state, current_user = apply_evidence(
                state,
                current_user,
                evidence("confirmed_skip", 0.45, NOW + index),
                NOW + index,
            )

        self.assertAlmostEqual(0.60, state["skip_evidence"], places=4)
        self.assertEqual(4, current_user["skip_outcomes"])

    def test_completion_halves_skip_evidence_and_recomputes_cooldown(self):
        from helper.preference_model import apply_evidence

        state = {
            "skip_evidence": 1.4,
            "positive_evidence": 0,
            "fatigue": 0,
            "updated_at": NOW,
            "cooldown_until": NOW + 90 * DAY,
        }

        state, _ = apply_evidence(state, user(), evidence("completed", 1.0), NOW)

        self.assertLess(state["skip_evidence"], 0.71)
        self.assertLessEqual(state["cooldown_until"], NOW + 7 * DAY)

    def test_explicit_like_clears_skip_and_explicit_avoid_persists(self):
        from helper.preference_model import apply_evidence, materialize_track_state

        avoided, _ = apply_evidence({}, user(), evidence("explicit_avoid", 1.0), NOW)
        years_later = materialize_track_state(avoided, NOW + 10 * 365 * DAY)
        liked, _ = apply_evidence(years_later, user(), evidence("explicit_like", 1.0), NOW + 10 * 365 * DAY)

        self.assertTrue(years_later["hard_avoid"])
        self.assertFalse(liked["hard_avoid"])
        self.assertEqual(0.0, liked["skip_evidence"])
        self.assertEqual(0.0, liked["cooldown_until"])

    def test_late_exit_only_adds_short_term_fatigue(self):
        from helper.preference_model import apply_evidence

        state, _ = apply_evidence({}, user(), evidence("late_exit", 0.10), NOW)

        self.assertEqual(0.0, state["skip_evidence"])
        self.assertAlmostEqual(0.10, state["fatigue"])

    def test_unseen_track_materializes_as_neutral(self):
        from helper.preference_model import materialize_track_state

        state = materialize_track_state({}, NOW)

        self.assertEqual(0.0, state["affinity"])
        self.assertEqual(0.0, state["confidence"])
        self.assertEqual(0.0, state["cooldown_until"])


if __name__ == "__main__":
    unittest.main()
