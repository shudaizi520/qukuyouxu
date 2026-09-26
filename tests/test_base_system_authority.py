import copy
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from helper.engine import fingerprint, track_fingerprint
from helper.library_discovery import DISCOVERY_POLICY
from helper.library_engine import LibraryEngine
from helper.store import Store


class _BasePlex:
    def __init__(self):
        self.state = {
            "id": "700",
            "title": "国语",
            "summary": "",
            "items": [
                {"id": "1", "item_id": "item-1"},
                {"id": "2", "item_id": "item-2"},
            ],
        }

    def identity(self):
        return {"machine": "machine-a", "server": "Plex"}

    def playlists(self):
        return [{"ratingKey": self.state["id"], "title": self.state["title"]}]

    def tracks(self, _section):
        return copy.deepcopy(getattr(self, "tracks_data", []))

    def playlist_state(self, playlist_id):
        assert str(playlist_id) == self.state["id"]
        return copy.deepcopy(self.state)

    def read_playlist_until(self, playlist_id, predicate, attempts=8, delay=0.25):
        state = self.playlist_state(playlist_id)
        if not predicate(state):
            raise AssertionError("playlist state did not satisfy predicate")
        return state

    def rename(self, playlist_id, title):
        assert str(playlist_id) == self.state["id"]
        self.state["title"] = str(title)

    def append(self, playlist_id, ids):
        assert str(playlist_id) == self.state["id"]
        start = len(self.state["items"]) + 1
        self.state["items"].extend(
            {"id": str(track_id), "item_id": f"item-{start + offset}"}
            for offset, track_id in enumerate(ids)
        )


class BaseSystemAuthorityTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.store = Store(Path(root.name))
        settings = self.store.get("settings")
        settings.update(
            plex_url="http://plex:32400",
            plex_token="token",
            section="11",
            min_tracks=1,
        )
        self.store.set("settings", settings)
        self.plex = _BasePlex()
        self.engine = LibraryEngine(self.store, plex_factory=lambda _settings: self.plex)
        self.cid = "base:国语"
        self.plex.state["summary"] = self.engine.marker(self.cid)
        original = self.plex.playlist_state("700")
        self.store.set("managed", {
            self.cid: {
                "id": "700",
                "title": "国语",
                "fingerprint": fingerprint(original),
            },
        })
        self.tracks = [
            {
                "id": str(index), "title": f"歌曲 {index}", "artist": "歌手",
                "album": "专辑", "duration": 180, "available": True,
                "guid": f"guid-{index}", "paths": [],
            }
            for index in (1, 2)
        ]
        self.plex.tracks_data = self.tracks

    def test_next_category_update_restores_an_owned_playlist_changed_in_plex(self):
        self.plex.state["title"] = "Plex 手工改名"
        self.plex.state["items"] = [{"id": "1", "item_id": "item-1"}]
        group = {
            "id": self.cid,
            "title": "国语",
            "kind": "base",
            "desired": ["1", "2"],
            "matched": 2,
            "evidence": {},
            "inferred_count": 0,
            "blocked": [],
        }

        with patch.object(self.engine, "_read_base_catalog", return_value=(self.tracks, self.tracks, {})), \
                patch.object(self.engine, "single_attach", side_effect=lambda rows, _machine: rows), \
                patch("helper.base_mixin.prepare_catalog", side_effect=lambda rows, _overrides: (rows, {})), \
                patch("helper.base_mixin.album_genre_eligibility", return_value=({}, [])), \
                patch("helper.base_mixin.build_base_groups", return_value=[group]):
            plan = self.engine.preview_base()
            planned = next(row for row in plan["groups"] if row["id"] == self.cid)
            self.assertEqual([], planned["blocked"])
            result = self.engine.apply_base(plan["id"])

        self.assertEqual([], result["errors"])
        self.assertEqual("国语", self.plex.state["title"])
        self.assertEqual(["1", "2"], [row["id"] for row in self.plex.state["items"]])

    def test_next_theme_update_restores_an_owned_playlist_changed_in_plex(self):
        cid = "theme:work"
        self.plex.state.update(
            title="Plex 手工改名",
            summary=self.engine.marker(cid),
            items=[{"id": "1", "item_id": "item-1"}],
        )
        self.store.set("managed", {
            cid: {
                "id": "700", "title": "工作陪伴",
                "fingerprint": fingerprint({
                    **self.plex.state,
                    "title": "工作陪伴",
                    "items": [
                        {"id": "1", "item_id": "item-1"},
                        {"id": "2", "item_id": "item-2"},
                    ],
                }),
            },
        })
        self.store.set("sources", [{
            "id": cid, "name": "工作陪伴", "kind": "theme",
            "enabled": True, "approved": True,
        }])
        before = self.plex.playlist_state("700")
        plan = {
            "id": "theme-plan", "created_at": time.time(),
            "signature": self.engine.signature(), "machine": "machine-a",
            "discovery_policy": DISCOVERY_POLICY, "library_count": len(self.tracks),
            "track_fingerprints": {row["id"]: track_fingerprint(row) for row in self.tracks},
            "applied": False,
            "groups": [{
                "id": cid, "title": "工作陪伴", "desired": ["1", "2"],
                "add": ["2"], "blocked": [], "before": before,
            }],
        }
        self.store.set("plan", plan)

        result = self.engine.apply(plan["id"])

        self.assertEqual([], result["errors"])
        self.assertEqual("工作陪伴", self.plex.state["title"])
        self.assertEqual(["1", "2"], [row["id"] for row in self.plex.state["items"]])


if __name__ == "__main__":
    unittest.main()
