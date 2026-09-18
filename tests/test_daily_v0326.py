import pathlib
import sys
import types
import fastapi
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if "fastapi" not in sys.modules:
    fastapi = types.ModuleType("fastapi")
    fastapi.Request = object
    responses = types.ModuleType("fastapi.responses")
    responses.Response = object
    fastapi.responses = responses
    sys.modules["fastapi"] = fastapi
    sys.modules["fastapi.responses"] = responses

from helper import daily_mix_v035 as daily_mix


NOW = 1_800_000_000
SETTINGS = {
    "size": 10,
    "recent_days": 7,
    "rediscovery_days": 90,
    "daily_avoid_days": 7,
    "artist_cap": 2,
    "favorite_cap": 4,
}


def track(index, title=None, artist=None):
    return {
        "id": str(index),
        "title": title or f"Song {index}",
        "artist": artist or f"Artist {index}",
        "album": f"Album {index}",
        "duration": 180,
        "available": True,
        "view_count": 0,
        "last_viewed_at": 0,
        "user_rating": 0,
        "genres": [],
    }


class FakeStore:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set_many(self, values):
        self.values.update(values)


class FakeEngine:
    def __init__(self, values=None):
        self.store = FakeStore(values)

    def daily_scope(self):
        return "scope-1"


def legacy_recommend(tracks, features, feedback, settings, history, seed_ids, now, seed, behavior=None):
    return {"items": []}


class DailyMixV0326Tests(unittest.TestCase):
    def test_avoided_artist_is_never_selected(self):
        result = daily_mix.select_daily_mix(
            [track(i) for i in range(1, 11)],
            {},
            {"tracks": {}, "artists": {"Artist 1": {"value": "avoid"}}},
            SETTINGS,
            [],
            [],
            NOW,
            "avoid-artist",
        )
        self.assertNotIn("1", {item["id"] for item in result["items"]})

    def test_recent_play_excludes_same_song_with_different_rating_key(self):
        tracks = [track(1, "Same Song", "Same Artist"), track(2, "Same Song", "Same Artist")]
        tracks.extend(track(i) for i in range(3, 12))
        result = daily_mix.select_daily_mix(
            tracks,
            {},
            {"tracks": {}, "artists": {}},
            SETTINGS,
            [],
            [],
            NOW,
            "recent-copy",
            play_events=[{"id": "1", "viewed_at": NOW - 60}],
        )
        self.assertNotIn("2", {item["id"] for item in result["items"]})

    def test_unicode_letters_are_valid_metadata(self):
        self.assertTrue(daily_mix._valid(track(1, "봄날", "아이유")))
        self.assertTrue(daily_mix._valid(track(2, "さくら", "きゃりーぱみゅぱみゅ")))

    def test_preview_does_not_commit_rotation_history(self):
        engine = FakeEngine(
            {
                "daily_generation_counter": 0,
                "daily_rotation_history": [],
                "daily_similarity_cache": {},
                "daily_policy_diagnostic_history": [],
                "product_settings": {"behavior_enabled": False},
            }
        )
        result = daily_mix.recommend_rotating_v035(
            engine,
            legacy_recommend,
            [track(i) for i in range(1, 16)],
            {},
            {"tracks": {}, "artists": {}},
            SETTINGS,
            [],
            [],
            NOW,
            "preview-only",
            behavior={},
        )
        daily_mix.save_v035_plan(engine, {"daily_plan": result})
        self.assertEqual([], engine.store.get("daily_rotation_history"))

    def test_similarity_fallback_is_diagnostic_not_a_user_warning(self):
        engine = FakeEngine(
            {
                "daily_generation_counter": 0,
                "daily_rotation_history": [],
                "daily_similarity_cache": {},
                "daily_policy_diagnostic_history": [],
                "product_settings": {"behavior_enabled": False},
            }
        )

        result = daily_mix.recommend_rotating_v035(
            engine,
            legacy_recommend,
            [track(i) for i in range(1, 16)],
            {},
            {"tracks": {}, "artists": {}},
            SETTINGS,
            [],
            [],
            NOW,
            "fallback-diagnostic",
            behavior={},
        )

        self.assertEqual("曲库关系", result["stats"]["similarity_source"])
        self.assertNotIn(
            "Plex 相似接口没有可用结果",
            "\n".join(result["warnings"]),
        )

    def test_saved_fallback_notice_is_hidden_but_real_warning_is_preserved(self):
        from helper.extra_web import public_daily

        result = public_daily(FakeStore({
            "daily_plan": {
                "id": "old-preview",
                "warnings": [
                    "Plex 相似接口没有可用结果，本批使用歌手、专辑、流派和已有分类关系寻找相近歌曲。",
                    "Plex 历史接口暂不可用，已使用曲库内的播放时间和次数。",
                ],
            }
        }))

        self.assertEqual(
            ["Plex 历史接口暂不可用，已使用曲库内的播放时间和次数。"],
            result["warnings"],
        )


if __name__ == "__main__":
    unittest.main()
