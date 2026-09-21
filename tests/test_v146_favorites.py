import sys
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class _Store:
    profile_id = "default"

    def __init__(self, catalog):
        self.values = {"settings": {}, "catalog": catalog}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


class _RatingPlex:
    def __init__(self, readback=None):
        self.readback = readback
        self.ratings = []

    def rate_track(self, track_id, rating):
        self.ratings.append((str(track_id), float(rating)))
        return float(rating if self.readback is None else self.readback)


class _Engine:
    def __init__(self, store, plex):
        self.store = store
        self.plex = plex

    def plex_factory(self, _settings):
        return self.plex


class FavoritePlaylistTests(unittest.TestCase):
    def catalog(self):
        return [
            {"id": "1", "title": "四星", "artist": "甲", "user_rating": 8, "available": True},
            {"id": "2", "title": "五星", "artist": "乙", "user_rating": 10, "available": True},
            {"id": "3", "title": "三星", "artist": "丙", "user_rating": 6, "available": True},
            {"id": "4", "title": "离线五星", "artist": "丁", "user_rating": 10, "available": False},
        ]

    def test_existing_four_and_five_star_tracks_are_favorites(self):
        from helper.playlist_hub import favorite_playlist_detail

        detail = favorite_playlist_detail(_Store(self.catalog()))

        self.assertEqual(["1", "2"], [row["id"] for row in detail["tracks"]])
        self.assertTrue(all(row["liked"] for row in detail["tracks"]))
        self.assertEqual(("favorite", "liked"), (detail["kind"], detail["key"]))

    def test_like_writes_ten_and_commits_only_verified_readback(self):
        from helper.playlist_hub import set_track_liked

        store = _Store([{"id": "1", "title": "歌", "user_rating": 0, "available": True}])
        plex = _RatingPlex()
        result = set_track_liked(_Engine(store, plex), "1", True, now=100)

        self.assertEqual(("1", 10.0), plex.ratings[-1])
        self.assertTrue(result["liked"])
        self.assertEqual(10, store.get("catalog")[0]["user_rating"])

    def test_mismatched_readback_keeps_old_catalog_rating(self):
        from helper.engine import SafetyError
        from helper.playlist_hub import set_track_liked

        store = _Store([{"id": "1", "title": "歌", "user_rating": 0, "available": True}])
        plex = _RatingPlex(readback=0)
        with self.assertRaisesRegex(SafetyError, "评分"):
            set_track_liked(_Engine(store, plex), "1", True, now=100)
        self.assertEqual(0, store.get("catalog")[0]["user_rating"])

    def test_search_and_favorite_detail_expose_one_binary_liked_state(self):
        from helper.playlist_hub import search_library

        rows = search_library(_Store(self.catalog()), "星")
        self.assertEqual([True, True, False], [row["liked"] for row in rows])
        self.assertEqual([8, 10, 6], [row["user_rating"] for row in rows])

    def test_malformed_legacy_rating_is_treated_as_not_liked(self):
        from helper.playlist_hub import favorite_playlist_detail, search_library

        store = _Store([{
            "id": "1", "title": "旧数据", "artist": "甲",
            "user_rating": "unknown", "available": True,
        }])
        self.assertEqual([], favorite_playlist_detail(store)["tracks"])
        self.assertFalse(search_library(store, "旧数据")[0]["liked"])

    def test_client_rating_uses_plex_rate_endpoint_and_verified_metadata(self):
        from helper.clients import PlexClient

        calls = []
        client = object.__new__(PlexClient)

        def xml(path, method="GET", params=None):
            calls.append((path, method, params))
            if path == "/:/rate":
                return ET.Element("MediaContainer")
            return ET.fromstring(
                '<MediaContainer><Track ratingKey="1" title="歌" userRating="10">'
                '<Media><Part file="/music/song.flac" /></Media></Track></MediaContainer>'
            )

        client._xml = xml
        self.assertEqual(10, client.rate_track("1", 10))
        self.assertEqual(("/:/rate", "PUT"), calls[0][:2])
        self.assertEqual("1", calls[0][2]["key"])
        self.assertEqual(10.0, calls[0][2]["rating"])


if __name__ == "__main__":
    unittest.main()
