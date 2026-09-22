import sys
import types
import fastapi
import unittest
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if "fastapi" not in sys.modules:
    fastapi = types.ModuleType("fastapi")
    fastapi.Request = object
    sys.modules["fastapi"] = fastapi


class FakeStore:
    def __init__(self, profile_id, token=True):
        self.values = {
            "settings": {"plex_token": "token" if token else "", "section": "15", "plex_url": "http://plex"},
            "daily_settings": {"enabled": False},
            "daily_managed": {"scope": "scope-" + profile_id},
            "daily_plan": None,
            "snapshots": [],
        }

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


class FakeEngine:
    def __init__(self, profile_id, fail=False, token=True):
        self.profile_id = profile_id
        self.fail = fail
        self.store = FakeStore(profile_id, token)
        self.published = []
        self.exclusive_entries = 0
        self.in_exclusive = False

    @contextmanager
    def exclusive(self):
        self.exclusive_entries += 1
        self.in_exclusive = True
        try:
            yield
        finally:
            self.in_exclusive = False

    def preview_daily(self, now=None):
        if self.fail:
            raise ValueError("该档案尚未完成授权")
        plan = {"id": "plan-" + self.profile_id, "items": [{"id": "1"}], "blocked": [], "warnings": [], "applied": False}
        self.store.set("daily_plan", plan)
        return plan

    def publish_daily(self, plan_id, now=None):
        self.published.append(plan_id)
        self.store.set("daily_plan", {**(self.store.get("daily_plan") or {}), "applied": True})
        return {"written": 1, "playlist_id": "playlist-" + self.profile_id}

    def daily_scope(self):
        return "scope-" + self.profile_id


class FakeRegistry:
    def list_public(self):
        return [
            {
                "id": "default", "name": "我的 Plex", "enabled": True,
                "account": {"username": "shudaizi"}, "library": {"name": "音乐"},
            },
            {
                "id": "friend", "name": "朋友", "enabled": True,
                "account": {"username": "shudai6"}, "library": {"name": "经典音乐"},
            },
            {"id": "off", "name": "停用", "enabled": False},
        ]


class FakeRuntime:
    def __init__(self):
        self.engines = {
            "default": FakeEngine("default"),
            "friend": FakeEngine("friend", fail=True),
            "off": FakeEngine("off"),
        }

    def engine(self, profile_id):
        return self.engines[profile_id]


class BatchDailyV047Tests(unittest.TestCase):
    def test_status_exposes_profile_pause_and_safety_reason(self):
        from helper.smart_mix_web import batch_daily_status

        runtime = FakeRuntime()
        runtime.engines["friend"].store.set("daily_auto_suspension", {"reason": "每日推荐存在阻止项"})
        runtime.engines["friend"].store.set("daily_plan", {"id": "blocked", "blocked": ["Plex 中没有这个项目"], "items": []})
        row = batch_daily_status(runtime, FakeRegistry())["items"][1]
        self.assertFalse(row["auto_enabled"])
        self.assertEqual("Plex 中没有这个项目", row["blocked"][0])
        self.assertEqual("每日推荐存在阻止项", row["suspension_reason"])

    def test_enabling_one_profile_requires_safe_preview_and_publish(self):
        from helper.engine import SafetyError
        from helper.smart_mix_web import set_profile_daily_schedule

        runtime = FakeRuntime()
        runtime.engines["friend"].store.set("daily_auto_suspension", {"reason": "每日推荐存在阻止项"})
        with self.assertRaisesRegex(SafetyError, "手动预览并发布"):
            set_profile_daily_schedule(runtime, FakeRegistry(), "friend", True)
        self.assertFalse(runtime.engines["friend"].store.get("daily_settings")["enabled"])
        result = set_profile_daily_schedule(runtime, FakeRegistry(), "default", True)
        self.assertTrue(runtime.engines["default"].store.get("daily_settings")["enabled"])
        self.assertEqual("default", result["items"][0]["profile_id"])

    def test_opted_out_profile_cannot_show_enabled_without_republish(self):
        from helper.engine import SafetyError
        from helper.smart_mix_web import set_profile_daily_schedule

        runtime = FakeRuntime()
        runtime.engines["default"].store.set("daily_auto_opt_out", True)
        with self.assertRaisesRegex(SafetyError, "手动预览并发布"):
            set_profile_daily_schedule(runtime, FakeRegistry(), "default", True)

    def test_preview_reports_each_profile_without_cross_profile_failure(self):
        from helper.smart_mix_web import batch_preview_daily

        runtime = FakeRuntime()
        result = batch_preview_daily(runtime, FakeRegistry(), now=100)
        self.assertEqual(["default", "friend"], [row["profile_id"] for row in result["items"]])
        self.assertEqual("ready", result["items"][0]["status"])
        self.assertEqual("shudaizi · 音乐", result["items"][0]["display_name"])
        self.assertEqual("error", result["items"][1]["status"])
        self.assertNotIn("ValueError", result["items"][1]["error"])

    def test_publish_requires_explicit_plan_per_profile(self):
        from helper.smart_mix_web import batch_publish_daily

        runtime = FakeRuntime()
        result = batch_publish_daily(runtime, FakeRegistry(), {"default": "plan-default"}, now=101)
        self.assertEqual("published", result["items"][0]["status"])
        self.assertEqual([], runtime.engines["friend"].published)
        self.assertEqual(["plan-default"], runtime.engines["default"].published)

    def test_status_restores_saved_preview_and_reports_mixed_automation(self):
        from helper.smart_mix_web import batch_daily_status

        runtime = FakeRuntime()
        runtime.engines["default"].store.set("daily_plan", {
            "id": "saved-default", "items": [{"id": "1"}, {"id": "2"}],
            "blocked": [], "warnings": [], "applied": False,
        })
        runtime.engines["friend"].store.set("daily_plan", {
            "id": "saved-friend", "items": [{"id": "3"}],
            "blocked": [], "warnings": [], "applied": False,
        })
        runtime.engines["friend"].store.set("daily_settings", {"enabled": True})

        result = batch_daily_status(runtime, FakeRegistry())

        self.assertEqual("partial", result["auto_state"])
        self.assertEqual(2, result["ready"])
        self.assertEqual(
            ["saved-default", "saved-friend"],
            [row["plan_id"] for row in result["items"]],
        )
        self.assertEqual(
            ["shudaizi · 音乐", "shudai6 · 经典音乐"],
            [row["display_name"] for row in result["items"]],
        )

    def test_batch_schedule_validates_every_profile_before_changing_any(self):
        from helper.engine import SafetyError
        from helper.smart_mix_web import set_batch_daily_schedule

        runtime = FakeRuntime()
        runtime.engines["friend"].store.set("daily_managed", None)

        with self.assertRaisesRegex(SafetyError, "shudai6 · 经典音乐"):
            set_batch_daily_schedule(runtime, FakeRegistry(), True)

        self.assertFalse(runtime.engines["default"].store.get("daily_settings")["enabled"])
        self.assertFalse(runtime.engines["friend"].store.get("daily_settings")["enabled"])

    def test_batch_schedule_enables_and_disables_every_published_profile(self):
        from helper.smart_mix_web import set_batch_daily_schedule

        runtime = FakeRuntime()

        enabled = set_batch_daily_schedule(runtime, FakeRegistry(), True)
        self.assertEqual("on", enabled["auto_state"])
        self.assertTrue(runtime.engines["default"].store.get("daily_settings")["enabled"])
        self.assertTrue(runtime.engines["friend"].store.get("daily_settings")["enabled"])

        disabled = set_batch_daily_schedule(runtime, FakeRegistry(), False)
        self.assertEqual("off", disabled["auto_state"])
        self.assertFalse(runtime.engines["default"].store.get("daily_settings")["enabled"])
        self.assertFalse(runtime.engines["friend"].store.get("daily_settings")["enabled"])

    def test_batch_schedule_changes_all_profiles_under_one_shared_operation_lock(self):
        from helper.smart_mix_web import set_batch_daily_schedule

        runtime = FakeRuntime()
        set_batch_daily_schedule(runtime, FakeRegistry(), True)

        self.assertEqual(1, runtime.engines["default"].exclusive_entries)
        self.assertEqual(0, runtime.engines["friend"].exclusive_entries)


if __name__ == "__main__":
    unittest.main()
