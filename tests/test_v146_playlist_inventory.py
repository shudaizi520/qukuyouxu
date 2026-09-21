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

    def exclusive(self):
        from contextlib import nullcontext
        return nullcontext()


class _WritablePlex(_Plex):
    def __init__(self):
        super().__init__([
            {"ratingKey": "10", "title": "工作", "playlistType": "audio", "smart": "0"},
            {"ratingKey": "11", "title": "四星", "playlistType": "audio", "smart": "1"},
        ], {
            "10": {"id": "10", "title": "工作", "summary": "", "smart": False,
                   "items": [{"id": "10", "item_id": "100", "title": "歌一"}]},
            "11": {"id": "11", "title": "四星", "summary": "", "smart": True,
                   "items": [{"id": "10", "item_id": "110", "title": "歌一"}]},
        })
        self.mutations = []

    def append(self, playlist_id, track_ids):
        self.mutations.append(("append", str(playlist_id), list(track_ids)))
        for track_id in track_ids:
            self.views[str(playlist_id)]["items"].append({
                "id": str(track_id), "item_id": str(200 + len(self.views[str(playlist_id)]["items"])),
            })

    def remove_items(self, playlist_id, item_ids):
        self.mutations.append(("remove_items", str(playlist_id), list(item_ids)))
        wanted = {str(item_id) for item_id in item_ids}
        self.views[str(playlist_id)]["items"] = [
            row for row in self.views[str(playlist_id)]["items"]
            if str(row.get("item_id")) not in wanted
        ]

    def rename(self, playlist_id, title):
        self.mutations.append(("rename", str(playlist_id), str(title)))
        self.views[str(playlist_id)]["title"] = str(title)
        for row in self.rows:
            if str(row.get("ratingKey")) == str(playlist_id):
                row["title"] = str(title)

    def delete_playlist(self, playlist_id):
        self.mutations.append(("delete_playlist", str(playlist_id)))
        self.rows = [row for row in self.rows if str(row.get("ratingKey")) != str(playlist_id)]
        self.views.pop(str(playlist_id), None)

    def read_playlist_view_until(self, playlist_id, predicate, attempts=8, delay=0.25):
        row = self.playlist_view(playlist_id)
        return row if predicate(row) else row

    def read_playlists_until(self, predicate, attempts=8, delay=0.25):
        rows = self.playlists()
        return rows if predicate(rows) else rows


class PlaylistInventoryTests(unittest.TestCase):
    def writable_engine(self):
        store = _Store({
            "settings": {},
            "catalog": [
                {"id": "10", "title": "歌一", "available": True},
                {"id": "20", "title": "歌二", "available": True},
            ],
        })
        plex = _WritablePlex()
        return _Engine(store, plex), plex

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

    def test_malformed_native_updated_time_does_not_hide_playlist(self):
        from helper.playlist_inventory import normalize_native_playlist_rows

        rows = normalize_native_playlist_rows([{
            "ratingKey": "10", "title": "工作", "playlistType": "audio",
            "smart": "0", "updatedAt": "unknown",
        }])

        self.assertEqual("10", rows[0]["playlist_id"])
        self.assertEqual(0, rows[0]["updated_at"])

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

    def test_native_regular_playlist_writes_only_after_membership_validation(self):
        from helper.playlist_hub import edit_playlist_track

        engine, plex = self.writable_engine()
        result = edit_playlist_track(engine, "plex", "10", "20", "add")

        self.assertEqual("已加入歌单", result["message"])
        self.assertEqual(["10", "20"], [row["id"] for row in plex.playlist_view("10")["items"]])
        self.assertEqual([("append", "10", ["20"])], plex.mutations)

    def test_native_smart_playlist_rejects_manual_membership_changes(self):
        from helper.playlist_hub import edit_playlist_track

        engine, plex = self.writable_engine()
        with self.assertRaisesRegex(ValueError, "智能歌单"):
            edit_playlist_track(engine, "plex", "11", "20", "add")
        self.assertEqual([], plex.mutations)

    def test_native_rename_and_delete_require_fresh_membership_and_title(self):
        from helper.engine import SafetyError
        from helper.playlist_hub import remove_playlist, rename_playlist

        engine, plex = self.writable_engine()
        renamed = rename_playlist(engine, "plex", "10", "新的工作歌单")
        self.assertEqual("新的工作歌单", renamed["title"])

        with self.assertRaises(ValueError):
            remove_playlist(engine, "plex", "999", "伪造")
        with self.assertRaisesRegex(SafetyError, "名称"):
            remove_playlist(engine, "plex", "10", "旧名称")

        removed = remove_playlist(engine, "plex", "10", "新的工作歌单")
        self.assertIn("不删除音乐文件", removed["message"])
        self.assertNotIn("10", [row["ratingKey"] for row in plex.playlists()])
        self.assertEqual("delete_playlist", plex.mutations[-1][0])


if __name__ == "__main__":
    unittest.main()
