import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from helper.library_engine import LibraryEngine
from helper.store import Store


class _DailyPlex:
    def __init__(self, existing=True):
        self.playlists_by_id = {}
        self.create_calls = 0
        if existing:
            self.playlists_by_id["900"] = {
                "id": "900",
                "title": "每日推荐",
                "summary": "用户原有歌单",
                "items": [
                    {"id": "4", "item_id": "4004"},
                    {"id": "5", "item_id": "4005"},
                ],
            }

    def identity(self):
        return {"machine": "machine-a", "server": "Plex"}

    def tracks(self, _section):
        return [
            {
                "id": str(index),
                "title": f"歌曲 {index}",
                "artist": "歌手",
                "album": "专辑",
                "duration": 180,
                "available": True,
                "guid": f"guid-{index}",
                "paths": [],
            }
            for index in range(1, 6)
        ]

    def playlists(self):
        return [copy.deepcopy(row) for row in self.playlists_by_id.values()]

    def playlist_state(self, playlist_id):
        return copy.deepcopy(self.playlists_by_id[str(playlist_id)])

    def create(self, title, ids, marker, description=None):
        self.create_calls += 1
        state = {
            "id": "901",
            "title": title,
            "summary": marker + "\n" + (description or ""),
            "items": [
                {"id": str(track_id), "item_id": f"new-{index}"}
                for index, track_id in enumerate(ids, 1)
            ],
        }
        self.playlists_by_id[state["id"]] = state
        return copy.deepcopy(state)

    def append(self, playlist_id, ids):
        state = self.playlists_by_id[str(playlist_id)]
        start = len(state["items"]) + 1
        for offset, track_id in enumerate(ids):
            state["items"].append({"id": str(track_id), "item_id": f"added-{start + offset}"})

    def remove_items(self, playlist_id, item_ids):
        state = self.playlists_by_id[str(playlist_id)]
        removed = set(map(str, item_ids))
        state["items"] = [row for row in state["items"] if row["item_id"] not in removed]

    def move_item(self, playlist_id, item_id, after=None):
        state = self.playlists_by_id[str(playlist_id)]
        moving = next(row for row in state["items"] if row["item_id"] == str(item_id))
        state["items"].remove(moving)
        if after is None:
            state["items"].insert(0, moving)
            return
        position = next(index for index, row in enumerate(state["items"]) if row["item_id"] == str(after))
        state["items"].insert(position + 1, moving)


def _recommendation(*_args, **_kwargs):
    return {
        "items": [
            {"id": "1", "title": "歌曲 1", "artist": "歌手", "song_key": "song-1"},
            {"id": "2", "title": "歌曲 2", "artist": "歌手", "song_key": "song-2"},
        ],
        "stats": {"positive_seed_count": 0},
        "warnings": [],
    }


def _next_recommendation(*_args, **_kwargs):
    return {
        "items": [
            {"id": "3", "title": "歌曲 3", "artist": "歌手", "song_key": "song-3"},
            {"id": "4", "title": "歌曲 4", "artist": "歌手", "song_key": "song-4"},
        ],
        "stats": {"positive_seed_count": 0},
        "warnings": [],
    }


class DailyFixedPlaylistTests(unittest.TestCase):
    def make_engine(self, plex):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        store = Store(Path(root.name))
        settings = store.get("settings")
        settings.update(plex_url="http://plex:32400", plex_token="token", section="11")
        store.set("settings", settings)
        return store, LibraryEngine(store, plex_factory=lambda _settings: plex)

    def test_preview_uses_existing_same_name_playlist_without_blocking(self):
        plex = _DailyPlex(existing=True)
        _store, engine = self.make_engine(plex)

        with patch("helper.daily.recommend_rotating", side_effect=_recommendation):
            plan = engine.preview_daily(now=1_800_000_000)

        self.assertEqual([], plan["blocked"])
        self.assertEqual("900", plan["before"]["id"])

    def test_publish_replaces_existing_same_name_playlist_in_place(self):
        plex = _DailyPlex(existing=True)
        store, engine = self.make_engine(plex)
        with patch("helper.daily.recommend_rotating", side_effect=_recommendation):
            plan = engine.preview_daily(now=1_800_000_000)

        result = engine.publish_daily(plan["id"], now=1_800_000_010)

        self.assertEqual("900", result["playlist_id"])
        self.assertEqual(0, plex.create_calls)
        self.assertEqual(["1", "2"], [row["id"] for row in plex.playlist_state("900")["items"]])
        self.assertEqual("900", store.get("daily_managed")["id"])

    def test_first_publish_creates_daily_playlist_when_none_exists(self):
        plex = _DailyPlex(existing=False)
        _store, engine = self.make_engine(plex)
        with patch("helper.daily.recommend_rotating", side_effect=_recommendation):
            plan = engine.preview_daily(now=1_800_000_000)

        result = engine.publish_daily(plan["id"], now=1_800_000_010)

        self.assertEqual("901", result["playlist_id"])
        self.assertEqual(1, plex.create_calls)
        self.assertEqual(["1", "2"], [row["id"] for row in plex.playlist_state("901")["items"]])

    def test_later_publish_keeps_updating_the_same_adopted_playlist(self):
        plex = _DailyPlex(existing=True)
        _store, engine = self.make_engine(plex)
        with patch("helper.daily.recommend_rotating", side_effect=_recommendation):
            first = engine.preview_daily(now=1_800_000_000)
        engine.publish_daily(first["id"], now=1_800_000_010)

        with patch("helper.daily.recommend_rotating", side_effect=_next_recommendation):
            second = engine.preview_daily(now=1_800_086_400)
        result = engine.publish_daily(second["id"], now=1_800_086_410)

        self.assertEqual([], second["blocked"])
        self.assertEqual("900", result["playlist_id"])
        self.assertEqual(0, plex.create_calls)
        self.assertEqual(["3", "4"], [row["id"] for row in plex.playlist_state("900")["items"]])

    def test_old_same_name_warning_does_not_keep_an_existing_preview_blocked(self):
        from helper.extra_web import public_daily

        plex = _DailyPlex(existing=True)
        store, engine = self.make_engine(plex)
        with patch("helper.daily.recommend_rotating", side_effect=_recommendation):
            plan = engine.preview_daily(now=1_800_000_000)
        plan["blocked"] = ["存在同名非本助手托管的“每日推荐”，不接管"]
        store.set("daily_plan", plan)

        self.assertEqual([], public_daily(store)["blocked"])
        result = engine.publish_daily(plan["id"], now=1_800_000_010)

        self.assertEqual("900", result["playlist_id"])
        self.assertEqual(["1", "2"], [row["id"] for row in plex.playlist_state("900")["items"]])


if __name__ == "__main__":
    unittest.main()
