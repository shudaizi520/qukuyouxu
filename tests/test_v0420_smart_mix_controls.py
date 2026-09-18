import sys
import tempfile
import types
import fastapi
import unittest
from unittest.mock import patch
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if "fastapi" not in sys.modules:
    fastapi = types.ModuleType("fastapi")
    fastapi.Request = object
    sys.modules["fastapi"] = fastapi

NOW = 1_800_000_000
BEIJING = timezone(timedelta(hours=8))


def tracks():
    return [
        {
            "id": str(index), "title": f"Song {index}", "artist": f"Artist {index}",
            "album": f"Album {index}", "duration": 180, "available": True,
            "view_count": 100 - index, "last_viewed_at": NOW - index * 86400,
            "added_at": NOW - index * 86400, "user_rating": 8, "genres": ["流行"],
            "guid": f"guid:{index}", "paths": [f"/{index}.flac"],
        }
        for index in range(1, 31)
    ]


class FakePlex:
    def __init__(self):
        self.rows = tracks()
        self.states = {}
        self.created = 0

    def identity(self):
        return {"machine": "machine-a", "server": "Main"}

    def tracks(self, section):
        return [dict(row) for row in self.rows]

    def playlists(self):
        return [{"ratingKey": key, "title": state["title"]} for key, state in self.states.items()]

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
        next_id = max([int(row["item_id"]) for row in state["items"]] or [0]) + 1
        state["items"].extend(
            {"id": str(value), "item_id": str(next_id + index)} for index, value in enumerate(ids)
        )

    def remove_items(self, pid, item_ids):
        wanted = set(map(str, item_ids))
        self.states[str(pid)]["items"] = [
            row for row in self.states[str(pid)]["items"] if row["item_id"] not in wanted
        ]

    def move_item(self, pid, item_id, after_item_id):
        rows = self.states[str(pid)]["items"]
        moving = next(row for row in rows if row["item_id"] == str(item_id))
        rows.remove(moving)
        if after_item_id is None:
            rows.insert(0, moving)
        else:
            rows.insert(next(i for i, row in enumerate(rows) if row["item_id"] == str(after_item_id)) + 1, moving)


class SmartMixControlsV0420Tests(unittest.TestCase):
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

    def publish(self, kind="weekly", now=NOW):
        from helper.smart_mix_web import preview_smart_mix, publish_smart_mix

        options = {"size": 10, "recent_days": 30} if kind == "weekly" else {"size": 10, "added_days": 3650}
        plan = preview_smart_mix(self.engine, kind, options, now=now)
        publish_smart_mix(self.engine, plan["id"], now=now + 1)
        return self.store.get("smart_mix_managed")[kind]

    def test_safe_delete_removes_only_owned_playlist_and_keeps_restore_snapshot(self):
        from helper.smart_mix_web import remove_smart_mix

        managed = self.publish()
        result = remove_smart_mix(self.engine, "weekly", "每周常听", now=NOW + 2)

        self.assertNotIn(managed["id"], self.plex.states)
        self.assertNotIn("weekly", self.store.get("smart_mix_managed"))
        snapshot = self.store.get("snapshots")[-1]
        self.assertEqual("remove", snapshot["action"])
        self.assertEqual("applied", snapshot["status"])
        self.assertEqual(snapshot["id"], result["snapshot_id"])
        self.assertEqual(snapshot["id"], self.store.get("smart_mix_removed")["weekly"]["snapshot_id"])

    def test_manual_change_blocks_delete_without_changing_plex_or_state(self):
        from helper.engine import SafetyError
        from helper.smart_mix_web import remove_smart_mix

        managed = self.publish()
        self.plex.states[managed["id"]]["items"].append({"id": "30", "item_id": "999"})

        with self.assertRaisesRegex(SafetyError, "手动修改"):
            remove_smart_mix(self.engine, "weekly", "每周常听", now=NOW + 2)

        self.assertIn(managed["id"], self.plex.states)
        self.assertIn("weekly", self.store.get("smart_mix_managed"))

    def test_removed_playlist_can_be_restored_but_automatic_update_stays_off(self):
        from helper.smart_mix_web import remove_smart_mix, restore_removed_smart_mix, set_weekly_schedule

        self.publish()
        set_weekly_schedule(self.engine, True, now=NOW + 2)
        removed = remove_smart_mix(self.engine, "weekly", "每周常听", now=NOW + 3)
        self.assertFalse(self.store.get("smart_mix_settings")["weekly_auto_enabled"])

        result = restore_removed_smart_mix(self.engine, removed["snapshot_id"])

        self.assertIn("playlist_id", result)
        restored = self.store.get("smart_mix_managed")["weekly"]
        self.assertIn(restored["id"], self.plex.states)
        self.assertEqual({}, self.store.get("smart_mix_removed"))
        self.assertFalse(self.store.get("smart_mix_settings")["weekly_auto_enabled"])

    def test_weekly_schedule_is_off_by_default_and_requires_a_managed_playlist(self):
        from helper.engine import SafetyError
        from helper.smart_mix_web import smart_mix_settings, set_weekly_schedule

        self.assertFalse(smart_mix_settings(self.store)["weekly_auto_enabled"])
        with self.assertRaisesRegex(SafetyError, "先发布"):
            set_weekly_schedule(self.engine, True, now=NOW)

        self.publish()
        result = set_weekly_schedule(self.engine, True, now=NOW + 2)
        self.assertTrue(result["settings"]["weekly_auto_enabled"])

    def test_weekly_due_uses_monday_0300_beijing_and_only_one_attempt_per_week(self):
        from helper.smart_mix_web import set_weekly_schedule, weekly_auto_due, weekly_schedule_slot

        self.publish()
        enabled_at = datetime(2027, 1, 6, 12, tzinfo=BEIJING).timestamp()
        set_weekly_schedule(self.engine, True, now=enabled_at)
        monday = datetime(2027, 1, 11, 3, tzinfo=BEIJING).timestamp()

        self.assertFalse(weekly_auto_due(self.engine, monday - 1))
        self.assertTrue(weekly_auto_due(self.engine, monday))
        settings = self.store.get("smart_mix_settings")
        settings["weekly_last_slot"] = weekly_schedule_slot(monday)
        self.store.set("smart_mix_settings", settings)
        self.assertFalse(weekly_auto_due(self.engine, monday + 3600))

    def test_automatic_weekly_update_marks_the_slot_and_keeps_the_same_playlist(self):
        from helper.smart_mix_web import run_weekly_auto, set_weekly_schedule, weekly_auto_due

        managed = self.publish(now=NOW)
        enabled_at = datetime(2027, 1, 6, 12, tzinfo=BEIJING).timestamp()
        set_weekly_schedule(self.engine, True, now=enabled_at)
        monday = datetime(2027, 1, 11, 3, tzinfo=BEIJING).timestamp()

        result = run_weekly_auto(self.engine, monday)

        self.assertEqual(managed["id"], result["playlist_id"])
        self.assertEqual(managed["id"], self.store.get("smart_mix_managed")["weekly"]["id"])
        self.assertFalse(weekly_auto_due(self.engine, monday + 60))

    def test_ui_explains_collapsed_preview_and_exposes_safe_controls(self):
        script = (ROOT / "src" / "helper" / "static" / "mixes.js").read_text(encoding="utf-8")
        page = (ROOT / "src" / "helper" / "static" / "mixes.html").read_text(encoding="utf-8")
        css = (ROOT / "src" / "helper" / "static" / "product.css").read_text(encoding="utf-8")

        self.assertIn("另有 ${plan.items.length-10} 首，下面仅展示前 10 首；发布时一并写入", script)
        self.assertIn("/api/mixes/remove", script)
        self.assertIn("/api/mixes/auto-schedule", script)
        self.assertIn('class="danger remove-mix"', page)
        self.assertIn('id="smartMixAuto"', page)
        self.assertIn("每周自动更新", page)
        self.assertIn(".mix-master", css)

    def test_profile_scheduler_dispatches_a_due_weekly_update_once(self):
        from helper.profile_runtime import ProfileRuntime
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ScopedStore
        from helper.store import Store

        class RuntimeEngine:
            def __init__(self, store):
                import threading
                self.store = store
                self.job = {"running": False}
                self.stop = threading.Event()

            def daily_due(self, now=None):
                return False

        with tempfile.TemporaryDirectory() as root:
            base = Store(Path(root))
            registry = ProfileRegistry(base)
            owner = ScopedStore(base, "default")
            settings = owner.get("settings")
            settings.update(plex_token="owner-secret", auto_enabled=False)
            owner.set("settings", settings)
            runtime = ProfileRuntime(base, registry, engine_factory=RuntimeEngine)
            with patch("helper.smart_mix_web.smart_mix_auto_due", return_value=["weekly"]), patch(
                "helper.smart_mix_web.run_smart_mix_auto", return_value={"items": {"weekly": {"status": "published"}}}
            ) as run:
                result = runtime.run_due(now=NOW)

            self.assertEqual("smart_mixes", result[0]["kind"])
            run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
