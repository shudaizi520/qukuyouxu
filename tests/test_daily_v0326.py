import pathlib
import sys
import types
import fastapi
import unittest
from xml.etree import ElementTree


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


class FakeRegistry:
    def __init__(self, profile):
        self.profile = profile

    def get(self, profile_id):
        if profile_id != self.profile["id"]:
            raise KeyError(profile_id)
        return self.profile


def legacy_recommend(tracks, features, feedback, settings, history, seed_ids, now, seed, behavior=None):
    return {"items": []}


class DailyMixV0326Tests(unittest.TestCase):
    def test_owner_cloud_account_is_resolved_to_server_playback_account(self):
        class Plex:
            def __init__(self):
                self.history_params = None

            def _xml(self, path, params=None):
                if path == "/accounts":
                    return ElementTree.fromstring(
                        '<MediaContainer><Account id="1" name="owner" /></MediaContainer>'
                    )
                if path == "/status/sessions/history/all":
                    self.history_params = dict(params or {})
                    return ElementTree.fromstring(
                        f'<MediaContainer totalSize="1"><Track ratingKey="1" '
                        f'viewedAt="{NOW - 60}" accountID="1" type="track" /></MediaContainer>'
                    )
                return ElementTree.fromstring('<MediaContainer totalSize="0" />')

        engine = FakeEngine({
            "settings": {"section": "15"},
            "daily_generation_counter": 0,
            "daily_rotation_history": [],
            "daily_similarity_cache": {},
            "daily_policy_diagnostic_history": [],
            "product_settings": {"behavior_enabled": True},
        })
        engine.store.profile_id = "owner-profile"
        engine.store.registry = FakeRegistry({
            "id": "owner-profile",
            "kind": "owner",
            "account": {"id": "10", "username": "owner"},
            "server": {"machine": "machine-a"},
            "library": {"id": "15"},
        })
        plex = Plex()
        engine.plex_factory = lambda _settings: plex

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
            "owner-local-account",
            behavior={},
        )

        self.assertEqual("1", plex.history_params["accountID"])
        cache = result["_v035_state"]["history_cache"]
        self.assertEqual("1", cache["account_id"])
        self.assertEqual("10", cache["profile_account_id"])
        self.assertEqual("owner", cache["profile_username"])

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
