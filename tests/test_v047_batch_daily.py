import sys
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


class FakeStore:
    def __init__(self, token=True):
        self.values = {"settings": {"plex_token": "token" if token else "", "section": "15", "plex_url": "http://plex"}}

    def get(self, key, default=None):
        return self.values.get(key, default)


class FakeEngine:
    def __init__(self, profile_id, fail=False, token=True):
        self.profile_id = profile_id
        self.fail = fail
        self.store = FakeStore(token)
        self.published = []

    def preview_daily(self, now=None):
        if self.fail:
            raise ValueError("该档案尚未完成授权")
        return {"id": "plan-" + self.profile_id, "items": [{"id": "1"}], "blocked": [], "warnings": []}

    def publish_daily(self, plan_id, now=None):
        self.published.append(plan_id)
        return {"written": 1, "playlist_id": "playlist-" + self.profile_id}


class FakeRegistry:
    def list_public(self):
        return [
            {"id": "default", "name": "我", "enabled": True},
            {"id": "friend", "name": "朋友", "enabled": True},
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
    def test_preview_reports_each_profile_without_cross_profile_failure(self):
        from helper.smart_mix_web import batch_preview_daily

        runtime = FakeRuntime()
        result = batch_preview_daily(runtime, FakeRegistry(), now=100)
        self.assertEqual(["default", "friend"], [row["profile_id"] for row in result["items"]])
        self.assertEqual("ready", result["items"][0]["status"])
        self.assertEqual("error", result["items"][1]["status"])
        self.assertNotIn("ValueError", result["items"][1]["error"])

    def test_publish_requires_explicit_plan_per_profile(self):
        from helper.smart_mix_web import batch_publish_daily

        runtime = FakeRuntime()
        result = batch_publish_daily(runtime, FakeRegistry(), {"default": "plan-default"}, now=101)
        self.assertEqual("published", result["items"][0]["status"])
        self.assertEqual([], runtime.engines["friend"].published)
        self.assertEqual(["plan-default"], runtime.engines["default"].published)


if __name__ == "__main__":
    unittest.main()
