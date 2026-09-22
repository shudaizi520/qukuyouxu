"""Plex remains the authority for playlist tracks and audio, not a prior scan."""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class _Store:
    profile_id = "default"

    def __init__(self, catalog=None):
        self.values = {"settings": {"section": "11"}, "catalog": catalog or []}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def catalog_tracks(self, track_ids):
        wanted = {str(track_id) for track_id in track_ids}
        return {str(row["id"]): row for row in self.values["catalog"]
                if str(row.get("id")) in wanted}

    def set(self, key, value):
        self.values[key] = value


class DirectPlexTests(unittest.TestCase):
    def test_search_can_find_plex_tracks_without_catalog(self):
        from helper.playlist_hub import search_library_live

        class Plex:
            def tracks(self, section):
                self.section = section
                return [{"id": "7", "title": "一首歌", "artist": "歌手", "album": "专辑", "user_rating": 10}]

        plex = Plex()
        engine = SimpleNamespace(store=_Store(), plex_factory=lambda _settings: plex)
        result = search_library_live(engine, "一首", 40)
        self.assertEqual("11", plex.section)
        self.assertEqual(["7"], [row["id"] for row in result])
        self.assertTrue(result[0]["liked"])

    def test_uncached_library_audio_checks_plex_section(self):
        from helper.playlist_hub import stream_library_audio

        with patch("helper.playlist_hub.stream_track_audio", return_value="audio") as stream:
            engine = SimpleNamespace(store=_Store(), plex_factory=lambda _settings: object())
            self.assertEqual("audio", stream_library_audio(engine, "7", "", "session"))
        self.assertTrue(stream.call_args.kwargs["allow_uncached"])

    def test_native_playlist_can_add_uncached_track_only_from_same_section(self):
        from helper.playlist_hub import edit_playlist_track

        class Plex:
            def __init__(self, section):
                self.section = section

            def track_section(self, _track_id):
                return self.section

        for section, allowed in (("11", True), ("22", False)):
            plex = Plex(section)
            engine = SimpleNamespace(store=_Store(), plex_factory=lambda _settings: plex)
            with patch("helper.playlist_hub._edit_native_playlist_track", return_value={"count": 1}) as edit:
                if allowed:
                    self.assertEqual({"count": 1}, edit_playlist_track(engine, "plex", "42", "7", "add"))
                    edit.assert_called_once()
                else:
                    with self.assertRaisesRegex(ValueError, "当前曲库"):
                        edit_playlist_track(engine, "plex", "42", "7", "add")
                    edit.assert_not_called()

    def test_uncached_artwork_uses_scoped_plex_metadata(self):
        from helper.playlist_hub import stream_library_artwork

        class Plex:
            def __init__(self, section):
                self.section = section

            def track_metadata(self, _track_id):
                return {"id": "7", "thumb": "/library/metadata/7/thumb", "library_section_id": self.section}

        with patch("helper.playlist_hub._stream_artwork", return_value="image") as artwork:
            engine = SimpleNamespace(store=_Store(), plex_factory=lambda _settings: Plex("11"))
            self.assertEqual("image", stream_library_artwork(engine, "7"))
            artwork.assert_called_once()
            engine.plex_factory = lambda _settings: Plex("22")
            with self.assertRaisesRegex(ValueError, "当前曲库"):
                stream_library_artwork(engine, "7")

    def test_native_playlist_shows_plex_items_without_a_local_catalog(self):
        from helper.playlist_hub import playlist_detail

        engine = SimpleNamespace(store=_Store(), plex_factory=lambda _settings: object())
        state = {"id": "42", "title": "Plex 歌单", "smart": False,
                 "items": [{"id": "7", "title": "一首歌", "artist": "歌手", "duration": 180,
                            "library_section_id": "11", "user_rating": 10}]}
        with patch("helper.playlist_hub._native_playlist", return_value=({"smart": "0"}, state)):
            detail = playlist_detail(engine, "plex", "42")
        self.assertEqual(["7"], [row["id"] for row in detail["tracks"]])
        self.assertEqual(True, detail["tracks"][0]["liked"])

    def test_native_playlist_does_not_expose_other_selected_music_library(self):
        from helper.playlist_hub import playlist_detail

        engine = SimpleNamespace(store=_Store(), plex_factory=lambda _settings: object())
        state = {"id": "42", "title": "混合歌单", "smart": False,
                 "items": [{"id": "7", "title": "当前库", "library_section_id": "11"},
                           {"id": "8", "title": "其他库", "library_section_id": "22"}]}
        with patch("helper.playlist_hub._native_playlist", return_value=({"smart": "0"}, state)):
            detail = playlist_detail(engine, "plex", "42")
        self.assertEqual(["7"], [row["id"] for row in detail["tracks"]])

    def test_native_playlist_verifies_missing_section_before_showing_track(self):
        from helper.playlist_hub import playlist_detail

        class Plex:
            def track_section(self, track_id):
                return {"7": "11", "8": "22"}[track_id]

        engine = SimpleNamespace(store=_Store(), plex_factory=lambda _settings: Plex())
        state = {"id": "42", "title": "混合歌单", "smart": False,
                 "items": [{"id": "7", "title": "当前库"},
                           {"id": "8", "title": "其他库"}]}
        with patch("helper.playlist_hub._native_playlist", return_value=({"smart": "0"}, state)):
            detail = playlist_detail(engine, "plex", "42")
        self.assertEqual(["7"], [row["id"] for row in detail["tracks"]])
        self.assertEqual(1, detail["unavailable_count"])

    def test_native_playlist_missing_plex_rating_uses_existing_catalog_rating(self):
        from helper.playlist_hub import playlist_detail

        engine = SimpleNamespace(
            store=_Store([{"id": "7", "title": "一首歌", "user_rating": 10}]),
            plex_factory=lambda _settings: object(),
        )
        state = {"id": "42", "title": "歌单", "smart": False,
                 "items": [{"id": "7", "library_section_id": "11", "user_rating": None}]}
        with patch("helper.playlist_hub._native_playlist", return_value=({"smart": "0"}, state)):
            detail = playlist_detail(engine, "plex", "42")
        self.assertTrue(detail["tracks"][0]["liked"])

    def test_uncached_favorite_count_is_unknown_not_false_zero(self):
        from helper.playlist_hub import assistant_playlist_rows

        with patch("helper.playlist_hub.ExternalRepository") as repository:
            repository.return_value.list_sources.return_value = []
            rows = assistant_playlist_rows(_Store())
        favorite = next(row for row in rows if row["kind"] == "favorite")
        self.assertIsNone(favorite["count"])

    def test_plex_playlist_view_retains_section_and_rating_from_plex(self):
        from helper.clients import PlexClient

        client = object.__new__(PlexClient)
        client._xml = lambda _path: ET.fromstring('<MediaContainer><Playlist ratingKey="42" title="歌单" playlistType="audio"/></MediaContainer>')
        client._page = lambda _path, _tag: [ET.fromstring(
            '<Track ratingKey="7" playlistItemID="9" title="一首歌" '
            'librarySectionID="11" userRating="10" duration="180000"/>')]
        state = client.playlist_view("42")
        self.assertEqual("11", state["items"][0]["library_section_id"])
        self.assertEqual(10.0, state["items"][0]["user_rating"])

    def test_track_section_uses_one_plex_metadata_lookup(self):
        from helper.clients import PlexClient

        client = object.__new__(PlexClient)
        paths = []

        def xml(path):
            paths.append(path)
            return ET.fromstring('<MediaContainer><Track ratingKey="7" librarySectionID="11"/></MediaContainer>')

        client._xml = xml
        self.assertEqual("11", client.track_section("7"))
        self.assertEqual(["/library/metadata/7"], paths)

    def test_uncached_playlist_audio_uses_plex_but_checks_section(self):
        from helper.external_audio import stream_track_audio
        from tests.test_v130_external_audio import FakeAudioResponse

        class Plex:
            def __init__(self, section):
                self.section = section
                self.streamed = []

            def track_section(self, track_id):
                return self.section

            def open_browser_audio(self, track_id, range_header="", offset_seconds=0):
                self.streamed.append((track_id, range_header, offset_seconds))
                return FakeAudioResponse()

        allowed = Plex("11")
        result = stream_track_audio(_Store(), lambda _settings: allowed, "7", "bytes=0-1023",
                                    "uncached-allowed", allow_uncached=True)
        self.assertEqual([("7", "bytes=0-1023", 0)], allowed.streamed)
        asyncio.run(result.background())

        denied = Plex("22")
        with self.assertRaisesRegex(ValueError, "当前曲库"):
            stream_track_audio(_Store(), lambda _settings: denied, "7", "",
                               "uncached-denied", allow_uncached=True)
        self.assertEqual([], denied.streamed)

    def test_liking_uncached_plex_track_keeps_library_isolation(self):
        from helper.playlist_hub import set_track_liked

        class Plex:
            def __init__(self, section):
                self.section = section
                self.rated = []

            def track_section(self, track_id):
                return self.section

            def rate_track(self, track_id, rating):
                self.rated.append((track_id, rating))
                return rating

        store = _Store()
        allowed = Plex("11")
        engine = SimpleNamespace(store=store, plex_factory=lambda _settings: allowed)
        result = set_track_liked(engine, "7", True)
        self.assertEqual(True, result["liked"])
        self.assertEqual([("7", 10.0)], allowed.rated)

        denied = Plex("22")
        engine.plex_factory = lambda _settings: denied
        with self.assertRaisesRegex(ValueError, "当前曲库"):
            set_track_liked(engine, "8", True)
        self.assertEqual([], denied.rated)

    def test_favorites_can_be_read_from_plex_without_a_prior_catalog_scan(self):
        from helper.playlist_hub import favorite_playlist_detail_for_profiles

        class Profiles:
            def get(self, _profile_id):
                return {"id": "default", "kind": "owner", "account": {"id": "a"},
                        "server": {"machine": "m"}, "library": {"id": "11"}}

            def list_public(self, enabled_only=False):
                return [self.get("default")]

        class Plex:
            def tracks(self, section):
                self.section = section
                return [{"id": "7", "title": "已喜欢", "artist": "歌手", "user_rating": 10,
                         "available": True},
                        {"id": "8", "title": "未喜欢", "user_rating": 0, "available": True}]

        plex = Plex()
        engine = SimpleNamespace(store=_Store(), plex_factory=lambda _settings: plex)
        runtime = SimpleNamespace(engine=lambda _profile_id: engine)
        result = favorite_playlist_detail_for_profiles(Profiles(), runtime, "default")
        self.assertEqual(["7"], [row["id"] for row in result["tracks"]])
        self.assertEqual("11", plex.section)

    def test_favorites_reflect_plex_even_when_local_rating_is_stale(self):
        from helper.playlist_hub import favorite_playlist_detail_for_profiles

        class Profiles:
            def get(self, _profile_id):
                return {"id": "default", "kind": "owner", "account": {"id": "a"},
                        "server": {"machine": "m"}, "library": {"id": "11"}}

            def list_public(self, enabled_only=False):
                return [self.get("default")]

        class Plex:
            def tracks(self, section):
                return [{"id": "7", "title": "Plex 新喜欢", "user_rating": 10,
                         "available": True}]

        store = _Store([{"id": "7", "title": "旧状态", "user_rating": 0, "available": True}])
        engine = SimpleNamespace(store=store, plex_factory=lambda _settings: Plex())
        result = favorite_playlist_detail_for_profiles(
            Profiles(), SimpleNamespace(engine=lambda _profile_id: engine), "default",
        )
        self.assertEqual(["Plex 新喜欢"], [row["title"] for row in result["tracks"]])

    def test_favorite_playlist_detail_uses_plex_without_cache(self):
        from helper.playlist_hub import playlist_detail

        class Plex:
            def tracks(self, section):
                return [{"id": "7", "title": "Plex 歌曲", "user_rating": 10,
                         "available": True}]

        engine = SimpleNamespace(store=_Store(), plex_factory=lambda _settings: Plex())
        result = playlist_detail(engine, "favorite", "liked")
        self.assertEqual(["7"], [row["id"] for row in result["tracks"]])


if __name__ == "__main__":
    unittest.main()
