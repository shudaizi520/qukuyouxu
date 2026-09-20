import unittest


NOW = 2_000_000_000
DAY = 86400


def track(index, *, count=2, last=None, added=None, artist=None, album=None):
    return {
        "id": str(index),
        "title": f"Song {index}",
        "artist": artist or f"Artist {index}",
        "album": f"Album {index}" if album is None else album,
        "duration": 180,
        "available": True,
        "view_count": count,
        "last_viewed_at": NOW - 30 * DAY if last is None else last,
        "user_rating": 0,
        "added_at": NOW - 400 * DAY if added is None else added,
        "genres": [],
    }


def quota_fixture():
    rows = []
    rows.extend(track(i, count=0, last=0, added=NOW - 10 * DAY) for i in range(1, 15))
    rows.extend(track(i) for i in range(15, 17))
    rows.extend(track(i) for i in range(17, 31))
    rows.extend(track(i) for i in range(31, 39))
    rows.extend(track(i, last=NOW - 200 * DAY) for i in range(39, 47))
    rows.extend(track(i) for i in range(47, 51))
    behavior = {
        **{
            str(i): {
                "skip_evidence": 0.4,
                "affinity": -0.1,
                "confidence": 0.4,
                "cooldown_until": NOW - DAY,
            }
            for i in range(15, 17)
        },
        **{
            str(i): {
                "affinity": 0.8,
                "confidence": 0.8,
                "cooldown_until": 0,
            }
            for i in range(17, 31)
        },
        **{
            str(i): {"affinity": 0.1, "confidence": 0.1, "cooldown_until": 0}
            for i in range(39, 47)
        },
        # New/unplayed takes precedence over an apparently stable state.
        "1": {"affinity": 0.9, "confidence": 0.9, "cooldown_until": 0},
    }
    similar = {"999": [str(i) for i in range(31, 39)]}
    return rows, behavior, similar


class DailyMixV2Tests(unittest.TestCase):
    def test_rotation_adapter_exposes_v2_as_the_active_policy(self):
        import helper.engine  # Load production mixins in their normal order.
        from helper.rotation import POLICY_VERSION

        self.assertEqual("daily-mix-v2.0.0", POLICY_VERSION)

    def test_fifty_song_mix_uses_v2_targets_without_bucket_overlap(self):
        from helper.daily_mix_v2 import select_daily_mix_v2

        rows, behavior, similar = quota_fixture()
        result = select_daily_mix_v2(
            rows, {}, {"tracks": {}, "artists": {}}, {"size": 50},
            [], [], NOW, "v2-quota", play_events=[], similar_ids=similar,
            behavior=behavior, user_state={},
        )

        self.assertEqual(50, len(result["items"]))
        self.assertEqual("daily-mix-v2.0.0", result["stats"]["algorithm_version"])
        self.assertEqual({
            "稳定偏好": 14,
            "近期口味": 8,
            "新鲜发现": 14,
            "久未重听": 8,
            "跨口味探索": 4,
            "恢复观察": 2,
        }, result["stats"]["bucket_counts"])
        self.assertEqual(50, len({row["id"] for row in result["items"]}))
        self.assertEqual("新鲜发现", next(row for row in result["items"] if row["id"] == "1")["bucket"])

    def test_hard_avoid_and_cooldown_are_never_relaxed(self):
        from helper.daily_mix_v2 import select_daily_mix_v2

        rows = [track(i, count=0, last=0) for i in range(1, 61)]
        result = select_daily_mix_v2(
            rows, {}, {"tracks": {"1": {"value": "avoid"}}, "artists": {}},
            {"size": 50}, [], [], NOW, "hard-excludes", play_events=[],
            behavior={
                "2": {"hard_avoid": True},
                "3": {"cooldown_until": NOW + 30 * DAY},
            }, user_state={},
        )

        ids = {row["id"] for row in result["items"]}
        self.assertNotIn("1", ids)
        self.assertNotIn("2", ids)
        self.assertNotIn("3", ids)

    def test_discovery_target_stays_between_fourteen_and_twenty_four(self):
        from helper.daily_mix_v2 import discovery_target

        self.assertEqual(24, discovery_target({
            "discovery_valid": 40,
            "discovery_completed": 32,
            "discovery_early_skips": 3,
        }))
        self.assertEqual(14, discovery_target({
            "discovery_valid": 40,
            "discovery_completed": 4,
            "discovery_early_skips": 30,
        }))
        self.assertEqual(18, discovery_target({"discovery_valid": 29}))

    def test_stable_repeat_is_fourteen_days_and_ordinary_repeat_is_twenty_one(self):
        from helper.daily_mix_v2 import select_daily_mix_v2

        rows = [track(i) for i in range(1, 16)]
        behavior = {"1": {"affinity": 0.8, "confidence": 0.8}}
        history = [{
            "created_at": NOW - 15 * DAY,
            "ids": ["1", "2"],
            "song_keys": [],
        }]
        result = select_daily_mix_v2(
            rows, {}, {"tracks": {}, "artists": {}}, {"size": 15},
            history, [], NOW, "repeat-windows", play_events=[],
            behavior=behavior, user_state={},
        )

        ids = {row["id"] for row in result["items"]}
        self.assertIn("1", ids)
        self.assertNotIn("2", ids)
        self.assertEqual(14, result["stats"]["stable_repeat_days"])
        self.assertEqual(21, result["stats"]["daily_avoid_window_days"])

    def test_recovery_never_exceeds_two_and_caps_relax_in_order(self):
        from helper.daily_mix_v2 import select_daily_mix_v2

        rows = [
            track(i, artist="One Artist", album="One Album" if i <= 2 else f"Album {i}")
            for i in range(1, 13)
        ]
        behavior = {
            str(i): {"skip_evidence": 0.4, "cooldown_until": NOW - DAY}
            for i in range(1, 7)
        }
        result = select_daily_mix_v2(
            rows, {}, {"tracks": {}, "artists": {}}, {"size": 10},
            [], [], NOW, "relax-caps", play_events=[], behavior=behavior,
            user_state={},
        )

        self.assertLessEqual(result["stats"]["bucket_counts"]["恢复观察"], 2)
        self.assertEqual(3, len(result["items"]))
        self.assertEqual(3, result["stats"]["artist_cap_used"])
        self.assertEqual(2, result["stats"]["album_cap_used"])
        self.assertTrue(result["stats"]["rule_relaxed"])


if __name__ == "__main__":
    unittest.main()
