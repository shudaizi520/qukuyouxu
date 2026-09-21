import sys
import unittest
import asyncio
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class _Store:
    def __init__(self, catalog, profile_id="default"):
        self.profile_id = profile_id
        self.values = {"settings": {"section": "15" if profile_id == "second" else "11"}, "catalog": catalog}

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

    def track_section(self, track_id):
        return "15" if str(track_id) == "2" else "11"


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

    def test_plexamp_one_star_mode_uses_full_rating_not_five_star_one(self):
        from helper.playlist_hub import favorite_playlist_detail, search_library

        store = _Store([
            {"id": "1", "title": "单星模式已点星", "user_rating": 10, "available": True},
            {"id": "2", "title": "五星模式一星", "user_rating": 2, "available": True},
            {"id": "3", "title": "五星模式四星", "user_rating": 8, "available": True},
        ])
        self.assertEqual(["1", "3"], [row["id"] for row in favorite_playlist_detail(store)["tracks"]])
        self.assertEqual([True, False, True], [row["liked"] for row in search_library(store, "星")])

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

    def test_favorites_do_not_merge_other_library_of_same_plex_account(self):
        from helper.playlist_hub import favorite_playlist_detail_for_profiles

        profiles = _FavoriteProfiles([
            _profile("default", "owner", "account-a", "server-a", "11"),
            _profile("second", "owner", "account-a", "server-a", "15"),
            _profile("other", "owner", "account-b", "server-a", "16"),
            _profile("disabled", "owner", "account-a", "server-a", "17", enabled=False),
        ])
        stores = {
            "default": _Store([{"id": "1", "title": "甲", "user_rating": 10, "available": True}], "default"),
            "second": _Store([{"id": "2", "title": "乙", "user_rating": 8, "available": True}], "second"),
            "other": _Store([{"id": "3", "title": "丙", "user_rating": 10, "available": True}], "other"),
            "disabled": _Store([{"id": "4", "title": "丁", "user_rating": 10, "available": True}], "disabled"),
        }
        runtime = _FavoriteRuntime(stores)

        detail = favorite_playlist_detail_for_profiles(profiles, runtime, "default")

        self.assertEqual([("1", "default")],
                         [(row["id"], row["profile_id"]) for row in detail["tracks"]])
        self.assertEqual([1], [row["position"] for row in detail["tracks"]])
        self.assertEqual(1, detail["count"])

    def test_favorite_mutation_rejects_unrelated_or_disabled_profile(self):
        from helper.playlist_hub import resolve_favorite_profile_id

        profiles = _FavoriteProfiles([
            _profile("default", "owner", "account-a", "server-a", "11"),
            _profile("second", "owner", "account-a", "server-a", "15"),
            _profile("other", "owner", "account-b", "server-a", "16"),
            _profile("disabled", "owner", "account-a", "server-a", "17", enabled=False),
        ])
        self.assertEqual("default", resolve_favorite_profile_id(profiles, "default", "default"))
        with self.assertRaisesRegex(ValueError, "当前曲库"):
            resolve_favorite_profile_id(profiles, "default", "second")
        with self.assertRaisesRegex(ValueError, "当前曲库"):
            resolve_favorite_profile_id(profiles, "default", "other")
        with self.assertRaisesRegex(ValueError, "当前曲库"):
            resolve_favorite_profile_id(profiles, "default", "disabled")

    def test_sibling_managed_playlist_ids_are_hidden_from_native_inventory(self):
        from helper.playlist_hub import sibling_owned_playlist_ids

        profiles = _FavoriteProfiles([
            _profile("default", "owner", "account-a", "server-a", "11"),
            _profile("second", "owner", "account-a", "server-a", "15"),
            _profile("archived", "owner", "account-a", "server-a", "17", enabled=False),
            _profile("other", "owner", "account-b", "server-a", "16"),
        ])
        runtime = _FavoriteRuntime({pid: _Store([], pid) for pid in profiles.rows})

        def rows(store):
            return [{"playlist_id": {"default": "10", "second": "20", "archived": "25", "other": "30"}[store.profile_id]}]

        with patch("helper.playlist_hub.assistant_playlist_rows", side_effect=rows):
            hidden = sibling_owned_playlist_ids(profiles, runtime, "default")

        self.assertEqual({"20", "25"}, hidden)

    def test_favorite_api_lists_and_updates_only_current_library(self):
        from fastapi import FastAPI
        from helper.playlist_hub import attach_playlist_hub_routes

        profiles = _FavoriteProfiles([
            _profile("default", "owner", "account-a", "server-a", "11"),
            _profile("second", "owner", "account-a", "server-a", "15"),
        ])
        stores = {
            "default": _Store([{"id": "1", "title": "甲", "user_rating": 10, "available": True}], "default"),
            "second": _Store([{"id": "2", "title": "乙", "user_rating": 8, "available": True}], "second"),
        }
        plex = _RatingPlex()
        runtime = _FavoriteRuntime(stores, plex)
        app = FastAPI()

        async def body(request):
            return request.payload

        with patch("helper.playlist_hub.assistant_playlist_rows", return_value=[]), patch("helper.playlist_hub.playlist_rows", return_value=[{
            "kind": "favorite", "key": "liked", "count": 1,
        }]):
            attach_playlist_hub_routes(app, stores["default"], runtime, profiles, body, lambda: None)
            route = lambda path: next(row.endpoint for row in app.routes if row.path == path)
            self.assertEqual(1, route("/api/playlists")()["items"][0]["count"])
            self.assertEqual(["1"], [row["id"] for row in
                             route("/api/playlists/{kind}/{key}")("favorite", "liked")["tracks"]])
            request = type("Request", (), {"payload": {
                "track_id": "2", "liked": False, "profile_id": "second", "confirm": True,
            }})()
            with self.assertRaisesRegex(ValueError, "当前曲库"):
                asyncio.run(route("/api/playlists/tracks/liked")(request))

        self.assertEqual(8, stores["second"].get("catalog")[0]["user_rating"])
        self.assertEqual(10, stores["default"].get("catalog")[0]["user_rating"])

    def test_native_remove_route_rejects_playlist_owned_by_sibling_library(self):
        from fastapi import FastAPI
        from helper.playlist_hub import attach_playlist_hub_routes

        profiles = _FavoriteProfiles([
            _profile("default", "owner", "account-a", "server-a", "11"),
            _profile("second", "owner", "account-a", "server-a", "15"),
        ])
        stores = {pid: _Store([], pid) for pid in profiles.rows}
        runtime = _FavoriteRuntime(stores, _RatingPlex())
        app = FastAPI()

        async def body(request):
            return request.payload

        attach_playlist_hub_routes(app, stores["default"], runtime, profiles, body, lambda: None)
        route = next(row.endpoint for row in app.routes if row.path == "/api/playlists/remove")
        detail_route = next(row.endpoint for row in app.routes if row.path == "/api/playlists/{kind}/{key}")
        request = type("Request", (), {"payload": {
            "kind": "plex", "key": "41", "title": "其他曲库", "confirm": True,
        }})()

        def rows(store):
            return [{"playlist_id": "41"}] if store.profile_id == "second" else []

        with patch("helper.playlist_hub.assistant_playlist_rows", side_effect=rows):
            with self.assertRaisesRegex(ValueError, "其他曲库"):
                detail_route("plex", "41")
            with self.assertRaisesRegex(ValueError, "其他曲库"):
                asyncio.run(route(request))


def _profile(profile_id, kind, account_id, machine, library_id, enabled=True):
    return {"id": profile_id, "kind": kind, "enabled": enabled,
            "account": {"id": account_id}, "server": {"machine": machine},
            "library": {"id": library_id}}


class _FavoriteProfiles:
    def __init__(self, rows):
        self.rows = {row["id"]: row for row in rows}

    def get(self, profile_id):
        return self.rows[profile_id]

    def list_public(self, enabled_only=False):
        return [row for row in self.rows.values() if not enabled_only or row["enabled"]]


class _FavoriteRuntime:
    def __init__(self, stores, plex=None):
        self.stores = stores
        self.plex = plex

    def engine(self, profile_id):
        store = self.stores[profile_id]
        upstream = self.plex or _RatingPlex()

        class ScopedPlex:
            def tracks(self, _section):
                return store.get("catalog")

            def track_section(self, track_id):
                return upstream.track_section(track_id)

            def rate_track(self, track_id, rating):
                return upstream.rate_track(track_id, rating)

        return type("Engine", (), {
            "store": store,
            "exclusive": lambda _self: nullcontext(),
            "plex_factory": lambda _self, _settings: ScopedPlex(),
        })()


if __name__ == "__main__":
    unittest.main()
