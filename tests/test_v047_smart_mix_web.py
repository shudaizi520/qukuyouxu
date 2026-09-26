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

    def append(self, pid, ids):
        state = self.states[str(pid)]
        next_item = max([int(row["item_id"]) for row in state["items"]] or [0]) + 1
        state["items"].extend(
            {"id": str(value), "item_id": str(next_item + index)}
            for index, value in enumerate(ids)
        )

    def remove_items(self, pid, item_ids):
        removed = set(map(str, item_ids))
        state = self.states[str(pid)]
        state["items"] = [row for row in state["items"] if row["item_id"] not in removed]


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

    def test_next_update_restores_a_manually_changed_owned_playlist(self):
        from helper.smart_mix_web import preview_smart_mix, publish_smart_mix

        plan = preview_smart_mix(self.engine, "recent_additions", {"size": 10}, now=NOW)
        publish_smart_mix(self.engine, plan["id"], now=NOW + 1)
        pid = self.store.get("smart_mix_managed")["recent_additions"]["id"]
        self.plex.states[pid]["items"].append({"id": "999", "item_id": "999"})

        next_plan = preview_smart_mix(self.engine, "recent_additions", {"size": 10}, now=NOW + 2)
        self.assertEqual([], next_plan["blocked"])
        publish_smart_mix(self.engine, next_plan["id"], now=NOW + 3)
        self.assertNotIn("999", {row["id"] for row in self.plex.states[pid]["items"]})

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
            "scope": self.engine.daily_scope() + ":15:1",
            "account_id": "1",
            "profile_account_id": "10",
            "profile_username": "owner",
            "events": [{"id": "1", "viewed_at": NOW - 10, "account_id": "1"}],
        })
        self.assertEqual("1", _history_events(self.engine)[0]["id"])
        self.store.set("product_settings", {"behavior_enabled": False, "behavior_account_id": "10"})
        self.assertEqual([], _history_events(self.engine))
        self.store.set("product_settings", {"behavior_enabled": True, "behavior_account_id": "11"})
        self.assertEqual([], _history_events(self.engine))


class SmartMixCrossLibraryTests(unittest.TestCase):
    def setUp(self):
        from helper.engine import Engine
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ScopedStore
        from helper.store import Store

        self.temp = tempfile.TemporaryDirectory()
        self.base = Store(Path(self.temp.name))
        self.registry = ProfileRegistry(self.base)
        self.registry.update(
            "default", name="owner", kind="owner",
            account={"id": "10", "username": "owner"},
            server={"machine": "machine-a", "url": "http://plex:32400"},
            library={"id": "11", "name": "音乐"}, token="owner-secret",
        )
        second = self.registry.create_for_library(
            "default", {"id": "15", "name": "经典音乐"}
        )
        self.plex = FakePlex()
        for row in self.plex.rows:
            row["last_viewed_at"] = NOW - 220 * 86400
        self.first = Engine(
            ScopedStore(self.base, "default", registry=self.registry),
            plex_factory=lambda _cfg: self.plex,
        )
        self.second = Engine(
            ScopedStore(self.base, second["id"], registry=self.registry),
            plex_factory=lambda _cfg: self.plex,
        )
        for engine, section in ((self.first, "11"), (self.second, "15")):
            settings = engine.store.get("settings", {}) or {}
            settings.update(
                plex_url="http://plex:32400", plex_token="owner-secret",
                section=section, account_label="owner",
            )
            engine.store.set("settings", settings)

    def tearDown(self):
        self.temp.cleanup()

    def test_same_account_other_library_can_publish_its_own_weekly_playlist(self):
        from helper.smart_mix_web import preview_smart_mix, publish_smart_mix

        first_plan = preview_smart_mix(self.first, "weekly", {"size": 10}, now=NOW)
        self.assertTrue(first_plan["items"])
        publish_smart_mix(self.first, first_plan["id"], now=NOW + 1)
        first_id = self.first.store.get("smart_mix_managed")["weekly"]["id"]
        first_state = self.plex.playlist_state(first_id)

        second_plan = preview_smart_mix(self.second, "weekly", {"size": 10}, now=NOW + 2)
        self.assertTrue(second_plan["items"])
        self.assertEqual([], second_plan["blocked"])
        publish_smart_mix(self.second, second_plan["id"], now=NOW + 3)

        second_id = self.second.store.get("smart_mix_managed")["weekly"]["id"]
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(first_state, self.plex.playlist_state(first_id))
        self.assertEqual("每周常听", self.plex.playlist_state(second_id)["title"])

    def test_other_library_owned_time_capsule_does_not_block_new_profile(self):
        from helper.smart_mix_web import preview_smart_mix, publish_smart_mix

        first = preview_smart_mix(self.first, "time_capsule", {"size": 10}, now=NOW)
        self.assertTrue(first["items"])
        publish_smart_mix(self.first, first["id"], now=NOW + 1)

        second = preview_smart_mix(self.second, "time_capsule", {"size": 10}, now=NOW + 2)
        self.assertEqual([], second["blocked"])
        publish_smart_mix(self.second, second["id"], now=NOW + 3)
        ids = [
            engine.store.get("smart_mix_managed")["time_capsule"]["id"]
            for engine in (self.first, self.second)
        ]
        self.assertNotEqual(ids[0], ids[1])

    def test_manual_same_title_still_blocks_when_other_library_has_owned_mix(self):
        from helper.smart_mix_web import preview_smart_mix, publish_smart_mix

        first = preview_smart_mix(self.first, "weekly", {"size": 10}, now=NOW)
        publish_smart_mix(self.first, first["id"], now=NOW + 1)
        self.plex.foreign_title = "每周常听"

        second = preview_smart_mix(self.second, "weekly", {"size": 10}, now=NOW + 2)
        self.assertTrue(any("同名" in reason for reason in second["blocked"]))
        self.assertEqual(1, self.plex.created)

    def test_manual_same_title_appearing_after_preview_still_blocks_publish(self):
        from helper.engine import SafetyError
        from helper.smart_mix_web import preview_smart_mix, publish_smart_mix

        first = preview_smart_mix(self.first, "weekly", {"size": 10}, now=NOW)
        publish_smart_mix(self.first, first["id"], now=NOW + 1)
        second = preview_smart_mix(self.second, "weekly", {"size": 10}, now=NOW + 2)
        self.assertEqual([], second["blocked"])
        self.plex.foreign_title = "每周常听"

        with self.assertRaisesRegex(SafetyError, "同名"):
            publish_smart_mix(self.second, second["id"], now=NOW + 3)
        self.assertEqual(1, self.plex.created)

    def test_removed_mix_can_be_restored_while_other_library_keeps_its_mix(self):
        from helper.smart_mix_web import (
            preview_smart_mix, publish_smart_mix, remove_smart_mix,
            restore_removed_smart_mix,
        )

        for engine, at in ((self.first, NOW), (self.second, NOW + 2)):
            plan = preview_smart_mix(engine, "weekly", {"size": 10}, now=at)
            publish_smart_mix(engine, plan["id"], now=at + 1)
        second_id = self.second.store.get("smart_mix_managed")["weekly"]["id"]
        second_state = self.plex.playlist_state(second_id)
        removed = remove_smart_mix(self.first, "weekly", "每周常听", now=NOW + 4)

        restored = restore_removed_smart_mix(self.first, removed["snapshot_id"])

        self.assertNotEqual(second_id, restored["playlist_id"])
        self.assertEqual(second_state, self.plex.playlist_state(second_id))

    def test_changed_sibling_mix_is_not_treated_as_verified_ownership(self):
        from helper.smart_mix_web import preview_smart_mix, publish_smart_mix

        first = preview_smart_mix(self.first, "weekly", {"size": 10}, now=NOW)
        publish_smart_mix(self.first, first["id"], now=NOW + 1)
        first_id = self.first.store.get("smart_mix_managed")["weekly"]["id"]
        self.plex.states[first_id]["items"].append({"id": "20", "item_id": "999"})

        second = preview_smart_mix(self.second, "weekly", {"size": 10}, now=NOW + 2)

        self.assertTrue(any("同名" in reason for reason in second["blocked"]))
        self.assertEqual(1, self.plex.created)

    def test_sibling_modified_after_preview_blocks_publication(self):
        from helper.engine import SafetyError
        from helper.smart_mix_web import preview_smart_mix, publish_smart_mix

        first = preview_smart_mix(self.first, "weekly", {"size": 10}, now=NOW)
        publish_smart_mix(self.first, first["id"], now=NOW + 1)
        second = preview_smart_mix(self.second, "weekly", {"size": 10}, now=NOW + 2)
        self.assertEqual([], second["blocked"])
        first_id = self.first.store.get("smart_mix_managed")["weekly"]["id"]
        self.plex.states[first_id]["items"].append({"id": "20", "item_id": "999"})

        with self.assertRaisesRegex(SafetyError, "同名"):
            publish_smart_mix(self.second, second["id"], now=NOW + 3)
        self.assertEqual(1, self.plex.created)

    def test_manual_same_title_still_blocks_cross_library_restore(self):
        from helper.engine import SafetyError
        from helper.smart_mix_web import (
            preview_smart_mix, publish_smart_mix, remove_smart_mix,
            restore_removed_smart_mix,
        )

        for engine, at in ((self.first, NOW), (self.second, NOW + 2)):
            plan = preview_smart_mix(engine, "weekly", {"size": 10}, now=at)
            publish_smart_mix(engine, plan["id"], now=at + 1)
        removed = remove_smart_mix(self.first, "weekly", "每周常听", now=NOW + 4)
        self.plex.foreign_title = "每周常听"

        with self.assertRaisesRegex(SafetyError, "同名"):
            restore_removed_smart_mix(self.first, removed["snapshot_id"])
        self.assertEqual(2, self.plex.created)


if __name__ == "__main__":
    unittest.main()
