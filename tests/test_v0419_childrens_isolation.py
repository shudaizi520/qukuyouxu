import unittest
from contextlib import nullcontext
import sys
import types


NOW = 2_000_000_000
DAY = 86400


def track(track_id, **values):
    row = {
        "id": str(track_id),
        "title": f"Song {track_id}",
        "artist": f"Artist {track_id}",
        "album": f"Album {track_id}",
        "duration": 180,
        "available": True,
        "view_count": 1,
        "last_viewed_at": NOW - 200 * DAY,
        "added_at": NOW - 5 * DAY,
        "user_rating": 0,
        "genres": ["流行"],
        "styles": [],
        "moods": [],
        "paths": [f"/music/pop/song-{track_id}.flac"],
    }
    row.update(values)
    return row


class ChildrensAudiencePolicyV0419Tests(unittest.TestCase):
    def test_explicit_metadata_and_classification_mark_childrens_tracks(self):
        from helper.audience import is_childrens_track

        self.assertTrue(is_childrens_track(track(1, genres=["Children's Music"])))
        self.assertTrue(is_childrens_track(track(2, album="宝宝巴士儿歌大全")))
        self.assertTrue(is_childrens_track(track(3), ["分类:儿童音乐"]))
        self.assertTrue(is_childrens_track(track(4, paths=["/music/儿歌/小星星.flac"])))

    def test_ordinary_song_with_child_word_is_not_guessed_as_childrens_music(self):
        from helper.audience import is_childrens_track

        self.assertFalse(is_childrens_track(track(1, title="像个孩子")))
        self.assertFalse(is_childrens_track(track(2, artist="小孩乐团")))
        self.assertFalse(is_childrens_track(track(3, album="童年的回忆")))

    def test_context_filter_removes_child_tracks_events_features_and_seeds_together(self):
        from helper.audience import filter_childrens_context

        tracks = [track(1), track(2)]
        features = {"1": ["分类:流行"], "2": ["分类:儿歌"]}
        events = [
            {"track_id": "1", "value": 1, "at": NOW},
            {"track_id": "2", "value": 1, "at": NOW},
        ]
        result = filter_childrens_context(tracks, features, events, ["1", "2"])

        self.assertEqual(["1"], [row["id"] for row in result["tracks"]])
        self.assertEqual({"1": ["分类:流行"]}, result["features"])
        self.assertEqual(["1"], [row["track_id"] for row in result["events"]])
        self.assertEqual(["1"], result["seed_ids"])
        self.assertEqual({"2"}, result["excluded_ids"])


class ChildrensLearningAndRecommendationV0419Tests(unittest.TestCase):
    def test_behavior_profile_ignores_excluded_child_track_history(self):
        from helper.behavior import behavior_profile

        events = [
            {"track_id": "1", "value": 1, "kind": "completed", "at": NOW - DAY},
            {"track_id": "2", "value": 1, "kind": "completed", "at": NOW - DAY},
        ]
        profile = behavior_profile(events, NOW, excluded_ids={"2"})

        self.assertEqual({"1"}, set(profile))

    def test_history_seed_reader_rejects_events_for_tracks_removed_by_audience_filter(self):
        if "fastapi" not in sys.modules:
            fastapi = types.ModuleType("fastapi")
            fastapi.Request = type("Request", (), {})
            sys.modules["fastapi"] = fastapi
        from helper.daily_mix_v035 import _recent_seed_ids

        adult_tracks = [track(1)]
        events = [{"id": "2", "viewed_at": NOW - DAY}]

        self.assertEqual([], _recent_seed_ids(adult_tracks, events, NOW, 7, 20))

    def test_daily_recommendation_neither_learns_from_nor_selects_child_track(self):
        from helper.recommend import recommend

        tracks = [
            track(1, artist="Parent Artist", view_count=0),
            track(2, artist="Parent Artist", genres=["儿歌"], user_rating=10, view_count=50),
        ]
        result = recommend(
            tracks, {"2": ["分类:儿歌"]}, {}, {"size": 10}, [], ["2"], NOW, "daily",
            behavior={"2": {"score": 20, "positive": 20}},
        )

        self.assertNotIn("2", [row["id"] for row in result["items"]])
        self.assertEqual(0, result["stats"]["positive_seed_count"])
        self.assertEqual(1, result["stats"]["childrens_excluded_count"])

    def test_all_smart_playlist_kinds_exclude_childrens_tracks(self):
        from helper.smart_mixes import select_smart_mix

        tracks = [track(index) for index in range(1, 15)]
        tracks[0] = track(
            1, genres=["儿童音乐"], view_count=99,
            last_viewed_at=NOW - 500 * DAY, added_at=NOW - DAY,
        )
        cases = {
            "weekly": {"size": 10},
            "time_capsule": {"size": 10, "stale_days": 180},
            "recent_additions": {"size": 10, "added_days": 90},
            "custom": {"size": 10},
        }
        for kind, options in cases.items():
            with self.subTest(kind=kind):
                result = select_smart_mix(kind, tracks, [], options, NOW, kind)
                self.assertNotIn("1", [row["id"] for row in result["items"]])
                self.assertEqual(1, result["stats"]["childrens_excluded_count"])

    def test_pre_upgrade_smart_mix_preview_cannot_publish_after_policy_change(self):
        if "fastapi" not in sys.modules:
            fastapi = types.ModuleType("fastapi")
            fastapi.Request = type("Request", (), {})
            sys.modules["fastapi"] = fastapi
        from helper.engine import SafetyError
        from helper.smart_mix_web import publish_smart_mix

        class Store:
            def get(self, key, default=None):
                if key == "smart_mix_plans":
                    return {"weekly": {"id": "old-plan", "created_at": NOW, "applied": False}}
                return default

        class Engine:
            store = Store()

            @staticmethod
            def exclusive():
                return nullcontext()

        with self.assertRaisesRegex(SafetyError, "版本"):
            publish_smart_mix(Engine(), "old-plan", now=NOW)


if __name__ == "__main__":
    unittest.main()
