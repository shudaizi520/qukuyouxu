import sys
import types
import fastapi
import unittest


if "fastapi" not in sys.modules:
    fastapi = types.ModuleType("fastapi")
    fastapi.Request = object
    sys.modules["fastapi"] = fastapi


NOW = 2_000_000_000
DAY = 86400


def track(index, *, count=0, last=0, artist=None, album=None):
    return {
        "id": str(index),
        "title": f"Song {index}",
        "artist": artist or f"Artist {index}",
        "album": f"Album {index}" if album is None else album,
        "duration": 180,
        "available": True,
        "view_count": count,
        "last_viewed_at": last,
        "user_rating": 0,
        "added_at": NOW - 500 * DAY,
        "genres": [],
    }


SETTINGS = {
    "size": 50,
    "recent_days": 7,
    "rediscovery_days": 90,
    "daily_avoid_days": 21,
    "artist_cap": 2,
    "favorite_cap": 4,
}


class AdaptiveDailyPolicyTests(unittest.TestCase):
    def test_fifty_song_mix_uses_40_30_20_10_structure(self):
        from helper.daily_mix_v035 import select_daily_mix

        stable = [track(i, count=3, last=NOW - 30 * DAY) for i in range(1, 21)]
        rediscovery = [track(i, count=2, last=NOW - 200 * DAY) for i in range(21, 36)]
        exploration = [track(i) for i in range(36, 46)]
        recent_taste = [track(i) for i in range(46, 51)]
        behavior = {
            str(i): {"long_term_score": 2.0, "score": 2.0, "cooldown_until": 0}
            for i in range(1, 21)
        }

        result = select_daily_mix(
            stable + rediscovery + exploration + recent_taste,
            {}, {"tracks": {}, "artists": {}}, SETTINGS, [], [], NOW, "adaptive-mix",
            play_events=[], similar_ids={"999": [str(i) for i in range(46, 51)]},
            behavior=behavior,
        )

        self.assertEqual(50, len(result["items"]))
        self.assertEqual("daily-mix-v0.4.24", result["stats"]["algorithm_version"])
        self.assertEqual({
            "稳定喜好": 20,
            "久未重听": 15,
            "曲库探索": 10,
            "近期口味": 5,
        }, result["stats"]["bucket_counts"])

    def test_track_in_adaptive_fatigue_cooldown_is_not_selected(self):
        from helper.daily_mix_v035 import select_daily_mix

        tracks = [track(i) for i in range(1, 13)]
        behavior = {
            "1": {
                "long_term_score": 10.0,
                "score": 10.0,
                "cooldown_until": NOW + 7 * DAY,
            }
        }
        settings = {**SETTINGS, "size": 10}

        result = select_daily_mix(
            tracks, {}, {"tracks": {}, "artists": {}}, settings,
            [], [], NOW, "fatigue", play_events=[], behavior=behavior,
        )

        self.assertNotIn("1", {row["id"] for row in result["items"]})
        self.assertEqual(1, result["stats"]["excluded_by_fatigue"])

    def test_same_album_contributes_at_most_one_song(self):
        from helper.daily_mix_v035 import select_daily_mix

        tracks = [
            track(1, artist="Same Artist", album="Same Album"),
            track(2, artist="Same Artist", album="Same Album"),
        ] + [track(i) for i in range(3, 13)]
        settings = {**SETTINGS, "size": 10}

        result = select_daily_mix(
            tracks, {}, {"tracks": {}, "artists": {}}, settings,
            [], ["1", "2"], NOW, "album-cap", play_events=[], behavior={},
        )

        chosen = {row["id"] for row in result["items"]}
        self.assertLessEqual(len(chosen & {"1", "2"}), 1)

    def test_twenty_one_day_repeat_window_is_never_relaxed_to_fill_size(self):
        from helper.daily_mix_v035 import select_daily_mix

        tracks = [track(i) for i in range(1, 11)]
        settings = {**SETTINGS, "size": 10}
        history = [{"created_at": NOW - 10 * DAY, "ids": ["1"], "song_keys": []}]

        result = select_daily_mix(
            tracks, {}, {"tracks": {}, "artists": {}}, settings,
            history, [], NOW, "strict-repeat", play_events=[], behavior={},
        )

        self.assertEqual(9, len(result["items"]))
        self.assertNotIn("1", {row["id"] for row in result["items"]})
        self.assertEqual(21, result["stats"]["daily_avoid_window_days"])

    def test_old_profile_cannot_reduce_the_fixed_twenty_one_day_window(self):
        from helper.daily_mix_v035 import select_daily_mix

        tracks = [track(i) for i in range(1, 11)]
        history = [{"created_at": NOW - 10 * DAY, "ids": ["1"], "song_keys": []}]

        result = select_daily_mix(
            tracks, {}, {"tracks": {}, "artists": {}},
            {**SETTINGS, "size": 10, "daily_avoid_days": 7},
            history, [], NOW, "fixed-repeat", play_events=[], behavior={},
        )

        self.assertEqual(9, len(result["items"]))
        self.assertNotIn("1", {row["id"] for row in result["items"]})
        self.assertEqual(21, result["stats"]["daily_avoid_window_days"])

    def test_repeat_window_blocks_same_song_with_a_new_plex_rating_key(self):
        from helper.daily_mix_v035 import _song_key, select_daily_mix

        duplicate = track(1, artist="Same Artist")
        duplicate["title"] = "Same Song"
        tracks = [duplicate] + [track(i) for i in range(2, 11)]
        history = [{
            "created_at": NOW - 10 * DAY,
            "ids": ["999"],
            "song_keys": [_song_key(duplicate)],
        }]

        result = select_daily_mix(
            tracks, {}, {"tracks": {}, "artists": {}}, {**SETTINGS, "size": 10},
            history, [], NOW, "song-repeat", play_events=[], behavior={},
        )

        self.assertEqual(9, len(result["items"]))
        self.assertNotIn("1", {row["id"] for row in result["items"]})

    def test_artist_cap_two_is_never_relaxed_to_fill_size(self):
        from helper.daily_mix_v035 import select_daily_mix

        tracks = [
            track(index, artist=f"Artist {(index - 1) // 3}")
            for index in range(1, 13)
        ]
        result = select_daily_mix(
            tracks, {}, {"tracks": {}, "artists": {}}, {**SETTINGS, "size": 10},
            [], [], NOW, "strict-artist", play_events=[], behavior={},
        )

        counts = {}
        for row in result["items"]:
            counts[row["artist"]] = counts.get(row["artist"], 0) + 1
        self.assertEqual(8, len(result["items"]))
        self.assertLessEqual(max(counts.values()), 2)
        self.assertEqual(2, result["stats"]["artist_cap_used"])

    def test_stable_taste_can_be_filled_by_tracks_related_to_a_favorite(self):
        from helper.daily_mix_v035 import select_daily_mix

        tracks = [track(i) for i in range(1, 31)]
        features = {str(i): ["分类:共同偏好"] for i in range(1, 13)}
        result = select_daily_mix(
            tracks, features, {"tracks": {}, "artists": {}}, {**SETTINGS, "size": 30},
            [], ["1"], NOW, "stable-related", play_events=[], behavior={},
        )

        self.assertEqual(12, result["stats"]["bucket_counts"]["稳定喜好"])
        self.assertLessEqual(result["stats"]["favorite_selected_count"], 4)

    def test_avoided_song_duplicate_cannot_seed_stable_taste(self):
        from helper.daily_mix_v035 import select_daily_mix

        avoided = track(1, artist="Same Artist")
        avoided["title"] = "Same Song"
        duplicate = dict(avoided, id="2")
        related = track(3)
        tracks = [avoided, duplicate, related] + [track(i) for i in range(4, 13)]
        features = {"2": ["分类:shared"], "3": ["分类:shared"]}
        feedback = {"tracks": {"1": {"value": "avoid"}}, "artists": {}}

        result = select_daily_mix(
            tracks, features, feedback, {**SETTINGS, "size": 10},
            [], ["2"], NOW, "avoid-seed", play_events=[], behavior={},
        )

        chosen = {row["id"]: row for row in result["items"]}
        self.assertNotIn("1", chosen)
        self.assertNotIn("2", chosen)
        self.assertNotEqual("稳定喜好", chosen["3"]["source_bucket"])

    def test_albumless_tracks_are_limited_by_artist_not_an_invented_album(self):
        from helper.daily_mix_v035 import select_daily_mix

        tracks = [track(1, artist="Same Artist", album=""), track(2, artist="Same Artist", album="")]
        tracks.extend(track(i) for i in range(3, 11))
        result = select_daily_mix(
            tracks, {}, {"tracks": {}, "artists": {}}, {**SETTINGS, "size": 10},
            [], [], NOW, "missing-album", play_events=[], behavior={},
        )

        self.assertEqual(10, len(result["items"]))
        self.assertTrue({"1", "2"}.issubset({row["id"] for row in result["items"]}))

    def test_new_behavior_event_invalidates_an_unpublished_preview_signature(self):
        import helper.engine  # imports DailyMixin through the production order
        from helper.daily import DailyMixin

        class Store:
            def __init__(self):
                self.values = {
                    "settings": {}, "daily_settings": {}, "feedback": {},
                    "metadata_overrides": {}, "daily_playlist_target": None,
                    "product_settings": {}, "behavior_events": [],
                }

            def get(self, key, default=None):
                return self.values.get(key, default)

        engine = object.__new__(DailyMixin)
        engine.store = Store()
        before = engine.daily_signature()
        engine.store.values["behavior_events"] = [
            {"track_id": "1", "kind": "observed_skip", "value": -0.4, "at": NOW}
        ]

        self.assertNotEqual(before, engine.daily_signature())


if __name__ == "__main__":
    unittest.main()
