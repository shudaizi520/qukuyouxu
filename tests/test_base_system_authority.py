import copy
import json
import sqlite3
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


def test_additive_childrens_policy_upgrade_preserves_existing_automation_authority():
    """An additive category must not silently turn off an approved maintenance switch."""
    from helper.base import BASE_POLICY

    with tempfile.TemporaryDirectory() as root:
        database_path = Path(root) / "helper.sqlite3"
        with sqlite3.connect(database_path) as database:
            database.execute("CREATE TABLE state (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
            rows = {
                "base_policy": "v0.1.7-single-provenance",
                "base_settings": {
                    "enabled": True, "approved": True,
                    "approved_policy": "v0.1.7-single-provenance",
                    "interval_hours": 24,
                },
                "base_plan": {"id": "old-plan", "applied": True},
                "managed": {"base:国语": {"id": "playlist-1"}},
            }
            database.executemany(
                "INSERT INTO state(k,v) VALUES(?,?)",
                [(key, json.dumps(value, ensure_ascii=False)) for key, value in rows.items()],
            )

        store = Store(Path(root))

        settings = store.get("base_settings")
        self_plan = store.get("base_plan")
        assert settings["enabled"] is True
        assert settings["approved"] is True
        assert settings["approved_policy"] == BASE_POLICY
        assert self_plan["invalidated_reason"]
        assert store.get("managed")["base:国语"]["id"] == "playlist-1"


def test_verified_childrens_daily_policy_upgrade_preserves_user_diversity_settings():
    from helper.recommend import DAILY_POLICY

    with tempfile.TemporaryDirectory() as root:
        database_path = Path(root) / "helper.sqlite3"
        with sqlite3.connect(database_path) as database:
            database.execute("CREATE TABLE state (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
            rows = {
                "daily_policy": "v0.2.3-childrens-isolation",
                "daily_settings": {
                    "enabled": True, "size": 40, "hour": 7,
                    "artist_cap": 3, "album_cap": 2,
                },
                "daily_plan": {"id": "old-plan", "applied": False},
            }
            database.executemany(
                "INSERT INTO state(k,v) VALUES(?,?)",
                [(key, json.dumps(value, ensure_ascii=False)) for key, value in rows.items()],
            )

        store = Store(Path(root))

        settings = store.get("daily_settings")
        assert store.get("daily_policy") == DAILY_POLICY
        assert settings["enabled"] is True
        assert settings["size"] == 40
        assert settings["hour"] == 7
        assert settings["artist_cap"] == 3
        assert settings["album_cap"] == 2
        assert store.get("daily_plan") is None


class _BasePlex:
    def __init__(self):
        self.create_calls = 0
        self.read_error = None
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
        if self.state is None:
            return []
        return [{"ratingKey": self.state["id"], "title": self.state["title"],
                 "summary": self.state.get("summary", "")}]

    def owned_playlists(self, marker):
        return [row for row in self.playlists()
                if marker in str(row.get("summary") or "").splitlines()]

    def tracks(self, _section):
        return copy.deepcopy(getattr(self, "tracks_data", []))

    def playlist_state(self, playlist_id):
        from helper.clients import PlexNotFound

        if self.read_error:
            raise self.read_error
        if self.state is None or str(playlist_id) != self.state["id"]:
            raise PlexNotFound("missing")
        return copy.deepcopy(self.state)

    def create(self, title, ids, marker, description=None):
        self.create_calls += 1
        self.state = {
            "id": "701", "title": title,
            "summary": marker + ("\n" + description if description else ""),
            "items": [{"id": str(track_id), "item_id": f"new-{index}"}
                      for index, track_id in enumerate(ids, 1)],
        }
        return self.playlist_state("701")

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

    def remove_items(self, playlist_id, item_ids):
        assert str(playlist_id) == self.state["id"]
        removed = {str(value) for value in item_ids}
        self.state["items"] = [row for row in self.state["items"]
                               if str(row["item_id"]) not in removed]

    def update_playlist_summary(self, playlist_id, summary):
        assert str(playlist_id) == self.state["id"]
        self.state["summary"] = str(summary)


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
        self.plex.state["summary"] = "人工说明"
        self.plex.state["items"] = [
            {"id": "1", "item_id": "item-1"},
            {"id": "3", "item_id": "item-3"},
        ]
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
        self.assertEqual(["2"], result["added_ids"])
        self.assertEqual("国语", self.plex.state["title"])
        self.assertEqual(["1", "2"], [row["id"] for row in self.plex.state["items"]])
        self.assertIn(self.engine.marker(self.cid), self.plex.state["summary"])

    def test_transient_managed_playlist_read_is_not_mislabeled_as_a_conflict(self):
        from helper.clients import PlexError

        self.plex.read_error = PlexError("read timeout")
        group = {
            "id": self.cid, "title": "国语", "kind": "base",
            "desired": ["1", "2"], "matched": 2, "evidence": {},
            "inferred_count": 0, "blocked": [],
        }

        with patch.object(self.engine, "_read_base_catalog", return_value=(self.tracks, self.tracks, {})), \
                patch.object(self.engine, "single_attach", side_effect=lambda rows, _machine: rows), \
                patch("helper.base_mixin.prepare_catalog", side_effect=lambda rows, _overrides: (rows, {})), \
                patch("helper.base_mixin.album_genre_eligibility", return_value=({}, [])), \
                patch("helper.base_mixin.build_base_groups", return_value=[group]):
            with self.assertRaises(PlexError):
                self.engine.preview_base()

    def test_deleted_category_is_recreated_and_managed_id_is_replaced(self):
        self.plex.state = None
        group = {
            "id": self.cid, "title": "国语", "kind": "base",
            "desired": ["1", "2"], "matched": 2, "evidence": {},
            "inferred_count": 0, "blocked": [],
        }

        with patch.object(self.engine, "_read_base_catalog", return_value=(self.tracks, self.tracks, {})), \
                patch.object(self.engine, "single_attach", side_effect=lambda rows, _machine: rows), \
                patch("helper.base_mixin.prepare_catalog", side_effect=lambda rows, _overrides: (rows, {})), \
                patch("helper.base_mixin.album_genre_eligibility", return_value=({}, [])), \
                patch("helper.base_mixin.build_base_groups", return_value=[group]):
            plan = self.engine.preview_base()
            result = self.engine.apply_base(plan["id"])

        self.assertEqual([], result["errors"])
        self.assertEqual("701", self.store.get("managed")[self.cid]["id"])
        self.assertEqual(["1", "2"], [row["id"] for row in self.plex.state["items"]])

    def test_empty_category_evidence_preserves_existing_playlist(self):
        original = copy.deepcopy(self.plex.state)
        group = {
            "id": self.cid, "title": "国语", "kind": "base",
            "desired": [], "matched": 0, "evidence": {},
            "inferred_count": 0, "blocked": [],
        }

        with patch.object(self.engine, "_read_base_catalog", return_value=(self.tracks, self.tracks, {})), \
                patch.object(self.engine, "single_attach", side_effect=lambda rows, _machine: rows), \
                patch("helper.base_mixin.prepare_catalog", side_effect=lambda rows, _overrides: (rows, {})), \
                patch("helper.base_mixin.album_genre_eligibility", return_value=({}, [])), \
                patch("helper.base_mixin.build_base_groups", return_value=[group]):
            plan = self.engine.preview_base()
            planned = next(row for row in plan["groups"] if row["id"] == self.cid)
            self.assertTrue(planned["blocked"])
            result = self.engine.apply_base(plan["id"])

        self.assertEqual(1, result["skipped"])
        self.assertEqual(original, self.plex.state)

    def test_next_theme_update_restores_an_owned_playlist_changed_in_plex(self):
        cid = "theme:work"
        self.plex.state.update(
            title="Plex 手工改名",
            summary=self.engine.marker(cid),
            items=[{"id": "1", "item_id": "item-1"},
                   {"id": "3", "item_id": "item-3"}],
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
        self.assertEqual(["2"], result["added_ids"])
        self.assertEqual("工作陪伴", self.plex.state["title"])
        self.assertEqual(["1", "2"], [row["id"] for row in self.plex.state["items"]])


if __name__ == "__main__":
    unittest.main()
