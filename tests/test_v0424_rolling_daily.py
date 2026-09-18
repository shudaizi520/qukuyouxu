import sys
import types
import fastapi
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if "fastapi" not in sys.modules:
    fastapi = types.ModuleType("fastapi")
    fastapi.Request = object
    sys.modules["fastapi"] = fastapi


NOW = 2_000_000_000
DAY = 86400


def track(index, *, artist=None):
    return {
        "id": str(index), "title": f"Song {index}",
        "artist": artist or f"Artist {index}", "album": f"Album {index}",
        "duration": 180, "available": True, "view_count": 0,
        "last_viewed_at": 0, "user_rating": 0,
        "added_at": NOW - 300 * DAY, "genres": [],
    }


class RollingDailyPolicyTests(unittest.TestCase):
    def test_consumed_members_are_removed_and_unplayed_members_are_preserved(self):
        from helper.daily import rolling_preserve_ids

        before = {"items": [{"id": str(i)} for i in range(1, 6)]}
        rows = [track(i) for i in range(1, 8)]
        rows[0]["last_viewed_at"] = NOW - 10
        events = [{"track_id": "2", "kind": "observed_skip", "at": NOW - 5}]

        self.assertEqual(
            ["3", "4", "5"],
            rolling_preserve_ids(before, rows, events, NOW - 100),
        )

    def test_no_listening_preserves_the_entire_current_playlist(self):
        from helper.daily import rolling_preserve_ids

        before = {"items": [{"id": str(i)} for i in range(1, 6)]}

        self.assertEqual(
            ["1", "2", "3", "4", "5"],
            rolling_preserve_ids(before, [track(i) for i in range(1, 8)], [], NOW - 100),
        )

    def test_recent_seeds_follow_last_twenty_active_plays_not_seven_calendar_days(self):
        from helper.daily_mix_v035 import recent_positive_play_times

        rows = [track(1), track(2)]
        events = [
            {"id": "1", "viewed_at": NOW - 15 * DAY},
            {"id": "2", "viewed_at": NOW - 181 * DAY},
        ]

        self.assertEqual(
            {"1": NOW - 15 * DAY},
            recent_positive_play_times(rows, events, NOW),
        )

    def test_unplayed_playlist_members_keep_their_order(self):
        from helper.daily_mix_v035 import select_daily_mix

        rows = [track(i) for i in range(1, 41)]
        result = select_daily_mix(
            rows, {}, {"tracks": {}, "artists": {}},
            {"size": 30, "artist_cap": 6, "favorite_percent": 20,
             "rediscovery_days": 90, "daily_avoid_days": 21},
            [], [], NOW, "rolling-order", play_events=[], behavior={},
            preserve_ids=["7", "3", "9"],
        )

        self.assertEqual(["7", "3", "9"], [row["id"] for row in result["items"][:3]])
        self.assertEqual(3, result["stats"]["rolling_retained"])
        self.assertEqual(27, result["stats"]["rolling_replenished"])

    def test_unplayed_member_is_kept_even_if_it_was_heard_before_this_playlist(self):
        from helper.daily_mix_v035 import select_daily_mix

        rows = [track(i) for i in range(1, 21)]
        rows[6]["last_viewed_at"] = NOW - 15 * DAY
        result = select_daily_mix(
            rows, {}, {"tracks": {}, "artists": {}},
            {"size": 10, "artist_cap": 6, "favorite_percent": 20,
             "rediscovery_days": 90, "daily_avoid_days": 21},
            [], [], NOW, "keep-prior-play", play_events=None, behavior={},
            preserve_ids=["7", "3"],
        )

        self.assertEqual(["7", "3"], [row["id"] for row in result["items"][:2]])

    def test_preserved_tracks_still_obey_artist_cap(self):
        from helper.daily_mix_v035 import select_daily_mix

        rows = [track(i, artist="One Artist" if i <= 4 else None) for i in range(1, 21)]
        result = select_daily_mix(
            rows, {}, {"tracks": {}, "artists": {}},
            {"size": 10, "artist_cap": 2, "favorite_percent": 20,
             "rediscovery_days": 90, "daily_avoid_days": 21},
            [], [], NOW, "rolling-cap", play_events=[], behavior={},
            preserve_ids=["1", "2", "3", "5"],
        )

        self.assertEqual(["1", "2", "5"], [row["id"] for row in result["items"][:3]])
        self.assertNotIn("3", {row["id"] for row in result["items"]})


if __name__ == "__main__":
    unittest.main()
