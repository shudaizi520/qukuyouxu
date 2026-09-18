import sys
import tempfile
import types
import fastapi
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if "fastapi" not in sys.modules:
    fastapi = types.ModuleType("fastapi")
    fastapi.Request = object
    sys.modules["fastapi"] = fastapi
NOW = 1_800_000_000


def tracks():
    return [
        {
            "id": str(index), "title": f"Song {index}", "artist": f"Artist {index}",
            "album": f"Album {index}", "duration": 180, "available": True,
            "view_count": index, "last_viewed_at": NOW - index * 86400,
            "added_at": NOW - index * 86400, "user_rating": 8, "genres": ["流行"],
            "guid": f"guid:{index}", "paths": [f"/{index}.flac"],
        }
        for index in range(1, 21)
    ]


class FakePlex:
    def __init__(self, foreign_title=None):
        self.rows = tracks()
        self.states = {}
        self.created = 0
        self.foreign_title = foreign_title

    def identity(self):
        return {"machine": "machine-a", "server": "Main"}

    def tracks(self, section):
        return [dict(row) for row in self.rows]

    def playlists(self):
        rows = [{"ratingKey": key, "title": state["title"]} for key, state in self.states.items()]
        if self.foreign_title:
            rows.append({"ratingKey": "999", "title": self.foreign_title})
        return rows

    def create(self, title, ids, marker, description=None):
        self.created += 1
        pid = str(100 + self.created)
        self.states[pid] = {
            "id": pid, "title": title, "summary": marker + "\n" + (description or ""),
            "items": [{"id": str(value), "item_id": str(i + 1)} for i, value in enumerate(ids)],
        }
        return self.playlist_state(pid)

    def playlist_state(self, pid):
        state = self.states[str(pid)]
        return {**state, "items": [dict(row) for row in state["items"]]}

    def delete_playlist(self, pid):
        del self.states[str(pid)]


class SmartMixLifecycleV047Tests(unittest.TestCase):
    def setUp(self):
        from helper.engine import Engine
        from helper.store import Store

        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        cfg = self.store.get("settings")
        cfg.update(plex_url="http://plex:32400", plex_token="owner-secret", section="15", account_label="owner")
        self.store.set("settings", cfg)
        self.plex = FakePlex()
        self.engine = Engine(self.store, plex_factory=lambda _cfg: self.plex)

    def tearDown(self):
        self.temp.cleanup()

    def test_preview_is_read_only_then_publish_creates_owned_playlist_and_snapshot(self):
        from helper.smart_mix_web import preview_smart_mix, publish_smart_mix

        plan = preview_smart_mix(self.engine, "recent_additions", {"size": 10}, now=NOW)
        self.assertEqual(0, self.plex.created)
        self.assertEqual(10, len(plan["items"]))

        result = publish_smart_mix(self.engine, plan["id"], now=NOW + 1)
        self.assertEqual(1, self.plex.created)
        self.assertEqual(10, result["written"])
        managed = self.store.get("smart_mix_managed")
        self.assertIn("recent_additions", managed)
        self.assertEqual("applied", self.store.get("snapshots")[-1]["status"])

    def test_same_title_foreign_playlist_blocks_preview_and_publish(self):
        from helper.engine import SafetyError
        from helper.smart_mix_web import preview_smart_mix, publish_smart_mix

        self.plex.foreign_title = "最近新增"
        plan = preview_smart_mix(self.engine, "recent_additions", {"size": 10}, now=NOW)
        self.assertTrue(plan["blocked"])
        with self.assertRaises(SafetyError):
            publish_smart_mix(self.engine, plan["id"], now=NOW + 1)
        self.assertEqual(0, self.plex.created)

    def test_manually_changed_owned_playlist_is_not_overwritten(self):
        from helper.smart_mix_web import preview_smart_mix, publish_smart_mix

        plan = preview_smart_mix(self.engine, "recent_additions", {"size": 10}, now=NOW)
        publish_smart_mix(self.engine, plan["id"], now=NOW + 1)
        pid = self.store.get("smart_mix_managed")["recent_additions"]["id"]
        self.plex.states[pid]["items"].append({"id": "20", "item_id": "999"})

        next_plan = preview_smart_mix(self.engine, "recent_additions", {"size": 10}, now=NOW + 2)
        self.assertTrue(any("手动修改" in message for message in next_plan["blocked"]))

    def test_new_smart_mix_can_be_rolled_back_without_touching_other_playlists(self):
        from helper.smart_mix_web import preview_smart_mix, publish_smart_mix

        plan = preview_smart_mix(self.engine, "recent_additions", {"size": 10}, now=NOW)
        publish_smart_mix(self.engine, plan["id"], now=NOW + 1)
        snapshot_id = self.store.get("smart_mix_managed")["recent_additions"]["snapshot_id"]
        result = self.engine.restore(snapshot_id)
        self.assertIn("恢复", result["message"])
        self.assertEqual({}, self.store.get("smart_mix_managed"))
        self.assertEqual({}, self.plex.states)

    def test_weekly_reads_only_cache_scoped_to_the_verified_account(self):
        from helper.smart_mix_web import _history_events

        self.store.set("product_settings", {"behavior_enabled": True, "behavior_account_id": "10"})
        self.store.set("plex_history_cache", {
            "scope": self.engine.daily_scope() + ":15:10",
            "events": [{"id": "1", "viewed_at": NOW - 10, "account_id": "10"}],
        })
        self.assertEqual("1", _history_events(self.engine)[0]["id"])
        self.store.set("product_settings", {"behavior_enabled": True, "behavior_account_id": "11"})
        self.assertEqual([], _history_events(self.engine))


if __name__ == "__main__":
    unittest.main()
