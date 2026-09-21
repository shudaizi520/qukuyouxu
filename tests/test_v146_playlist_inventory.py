import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class _Store:
    profile_id = "default"

    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


class _Plex:
    def __init__(self, rows, views=None, error=None):
        self.rows = list(rows)
        self.views = dict(views or {})
        self.error = error

    def playlists(self):
        if self.error:
            raise self.error
        return [dict(row) for row in self.rows]

    def playlist_view(self, playlist_id):
        return {
            **self.views[str(playlist_id)],
            "items": [dict(row) for row in self.views[str(playlist_id)]["items"]],
        }


class _Engine:
    def __init__(self, store, plex):
        self.store = store
        self.plex = plex

    def plex_factory(self, _settings):
        return self.plex


class PlaylistInventoryTests(unittest.TestCase):
    def test_assistant_rows_win_by_rating_key_and_native_rows_become_custom(self):
        from helper.playlist_inventory import merge_playlist_rows

        assistant = [{
            "kind": "daily", "playlist_id": "9", "title": "每日推荐",
            "section": "smart", "source": "assistant",
        }]
        plex = [
            {"ratingKey": "9", "title": "duplicate", "playlistType": "audio", "smart": "0"},
            {"ratingKey": "10", "title": "工作", "playlistType": "audio", "smart": "0", "leafCount": "4"},
            {"ratingKey": "11", "title": "四星", "playlistType": "audio", "smart": "1", "leafCount": "8"},
            {"ratingKey": "12", "title": "视频", "playlistType": "video", "smart": "0"},
        ]

        rows = merge_playlist_rows(assistant, plex)

        self.assertEqual(["9", "10", "11"], [row["playlist_id"] for row in rows])
        self.assertEqual("custom", rows[1]["section"])
        self.assertEqual("plex", rows[1]["kind"])
        self.assertTrue(rows[1]["can_add_tracks"])
        self.assertTrue(rows[1]["can_remove_tracks"])
        self.assertTrue(rows[1]["can_rename"])
        self.assertTrue(rows[1]["can_delete"])
        self.assertFalse(rows[2]["can_add_tracks"])
        self.assertFalse(rows[2]["can_remove_tracks"])
        self.assertTrue(rows[2]["can_rename"])
        self.assertTrue(rows[2]["can_delete"])

    def test_inventory_caches_only_successful_native_rows_and_marks_fallback_stale(self):
        from helper.playlist_hub import playlist_rows

        store = _Store({"settings": {}})
        live = _Plex([{
            "ratingKey": "10", "title": "工作", "playlistType": "audio",
            "smart": "0", "leafCount": "4",
        }])
        with patch("helper.playlist_hub.assistant_playlist_rows", return_value=[]):
            rows = playlist_rows(_Engine(store, live))
            self.assertEqual(["10"], [row["playlist_id"] for row in rows if row["source"] == "plex"])
            self.assertFalse(rows[-1]["stale"])

            cached = store.get("playlist_native_cache_v1")
            failed_rows = playlist_rows(_Engine(store, _Plex([], error=RuntimeError("offline"))))
        self.assertEqual(cached, store.get("playlist_native_cache_v1"))
        self.assertEqual("10", failed_rows[-1]["playlist_id"])
        self.assertTrue(failed_rows[-1]["stale"])

    def test_native_detail_filters_tracks_outside_the_scoped_catalog(self):
        from helper.playlist_hub import playlist_detail

        store = _Store({
            "settings": {},
            "catalog": [{
                "id": "10", "title": "曲库标题", "artist": "歌手",
                "album": "专辑", "duration": 123, "available": True,
            }],
        })
        plex = _Plex(
            [{"ratingKey": "20", "title": "工作", "playlistType": "audio", "smart": "0"}],
            {"20": {
                "id": "20", "title": "工作", "summary": "", "smart": False,
                "items": [
                    {"id": "10", "item_id": "1", "title": "Plex 标题"},
                    {"id": "999", "item_id": "2", "title": "其他曲库"},
                ],
            }},
        )

        detail = playlist_detail(_Engine(store, plex), "plex", "20")

        self.assertEqual(["10"], [row["id"] for row in detail["tracks"]])
        self.assertEqual(1, detail["unavailable_count"])
        self.assertEqual("plex", detail["kind"])
        self.assertNotIn("audio_url", detail["tracks"][0])

    def test_client_playlist_view_accepts_smart_audio_but_writer_state_rejects_it(self):
        from helper.clients import PlexClient, PlexError

        client = object.__new__(PlexClient)
        client._xml = lambda _path: ET.fromstring(
            '<MediaContainer><Playlist ratingKey="11" title="四星" summary="动态" '
            'playlistType="audio" smart="1" /></MediaContainer>'
        )
        client._page = lambda _path, _tag: [ET.fromstring(
            '<Track ratingKey="10" playlistItemID="101" title="歌" '
            'grandparentTitle="歌手" parentTitle="专辑" duration="123000" thumb="/thumb" />'
        )]

        view = client.playlist_view("11")
        self.assertTrue(view["smart"])
        self.assertEqual("10", view["items"][0]["id"])
        with self.assertRaisesRegex(PlexError, "普通音乐歌单"):
            client.playlist_state("11")


if __name__ == "__main__":
    unittest.main()
