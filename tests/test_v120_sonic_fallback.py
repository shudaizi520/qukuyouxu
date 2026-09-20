import unittest
from xml.etree import ElementTree


NOW = 2_000_000_000


def track(index, *, count=0):
    return {
        "id": str(index), "title": f"Song {index}", "artist": f"Artist {index}",
        "album": f"Album {index}", "duration": 180, "available": True,
        "view_count": count, "last_viewed_at": 0, "user_rating": 0,
        "added_at": NOW - 1000000, "genres": [],
    }


class Store:
    def __init__(self, values):
        self.values = dict(values)

    def get(self, key, default=None):
        return self.values.get(key, default)


class Engine:
    def __init__(self, plex):
        self.store = Store({
            "settings": {"section": "15"},
            "daily_generation_counter": 0,
            "daily_similarity_cache": {},
            "product_settings": {"behavior_enabled": True},
        })
        self.plex_factory = lambda _settings: plex

    def daily_scope(self):
        return "scope"


def legacy(tracks, features, feedback, settings, history, seed_ids, now, seed, behavior=None):
    return {"items": []}


class SonicFallbackTests(unittest.TestCase):
    def test_sonic_neighbors_boost_fresh_tracks_but_do_not_override_cooldown(self):
        from helper.daily_mix_v2 import select_daily_mix_v2

        rows = [track(i, count=2) for i in range(1, 13)]
        result = select_daily_mix_v2(
            rows, {}, {"tracks": {}, "artists": {}}, {"size": 10},
            [], [], NOW, "sonic", play_events=[],
            similar_ids={"1": ["2", "3"]},
            behavior={"3": {"cooldown_until": NOW + 1000}}, user_state={},
        )
        ids = [row["id"] for row in result["items"]]
        self.assertIn("2", ids)
        self.assertNotIn("3", ids)

    def test_sonic_exception_falls_back_to_metadata(self):
        from helper.daily_mix_v2 import recommend_rotating_v2

        class Plex:
            def _xml(self, path, params=None):
                if path == "/accounts":
                    return ElementTree.fromstring('<MediaContainer><Account id="1" name="owner" /></MediaContainer>')
                if path == "/status/sessions/history/all":
                    return ElementTree.fromstring(
                        f'<MediaContainer totalSize="1"><Track ratingKey="1" viewedAt="{NOW - 60}" '
                        'accountID="1" type="track" /></MediaContainer>'
                    )
                if path.endswith("/nearest") or path.endswith("/similar"):
                    raise TimeoutError("sonic timeout")
                return ElementTree.fromstring('<MediaContainer totalSize="0" />')

        engine = Engine(Plex())
        engine.store.profile_id = "profile-owner"
        engine.store.registry = type("Registry", (), {"get": lambda _self, _profile_id: {
            "id": "profile-owner", "kind": "owner",
            "account": {"id": "10", "username": "owner"},
            "server": {"machine": "machine"}, "library": {"id": "15"},
        }})()
        result = recommend_rotating_v2(
            engine, legacy, [track(i) for i in range(1, 16)], {},
            {"tracks": {}, "artists": {}}, {"size": 10}, [], [], NOW,
            "fallback", behavior={}, user_state={},
        )

        self.assertEqual("曲库关系", result["stats"]["similarity_source"])
        self.assertTrue(result["items"])


if __name__ == "__main__":
    unittest.main()
