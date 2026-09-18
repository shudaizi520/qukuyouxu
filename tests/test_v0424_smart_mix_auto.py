import sys
import tempfile
import types
import fastapi
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if "fastapi" not in sys.modules:
    fastapi = types.ModuleType("fastapi")
    fastapi.Request = object
    sys.modules["fastapi"] = fastapi

from tests.test_v0420_smart_mix_controls import FakePlex


NOW = 1_800_000_000
BEIJING = timezone(timedelta(hours=8))
AUTO_KINDS = ("weekly", "time_capsule", "recent_additions")


class SmartMixAutoV0424Tests(unittest.TestCase):
    def setUp(self):
        from helper.engine import Engine
        from helper.store import Store

        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        cfg = self.store.get("settings")
        cfg.update(plex_url="http://plex:32400", plex_token="secret-token", section="15")
        self.store.set("settings", cfg)
        self.plex = FakePlex()
        self.engine = Engine(self.store, plex_factory=lambda _cfg: self.plex)

    def tearDown(self):
        self.temp.cleanup()

    def publish(self, kind, options):
        from helper.smart_mix_web import preview_smart_mix, publish_smart_mix

        plan = preview_smart_mix(self.engine, kind, options, now=NOW)
        self.assertFalse(plan["blocked"])
        publish_smart_mix(self.engine, plan["id"], now=NOW + 1)

    def test_one_master_schedule_covers_exactly_the_three_built_in_playlists(self):
        from helper.smart_mix_web import set_smart_mix_schedule, smart_mix_auto_due

        self.publish("weekly", {"size": 10, "recent_days": 30})
        self.publish("time_capsule", {"size": 10, "stale_days": 30})
        self.publish("recent_additions", {"size": 10, "added_days": 90})
        self.publish("custom", {"size": 10})
        enabled_at = datetime(2027, 1, 6, 12, tzinfo=BEIJING).timestamp()
        set_smart_mix_schedule(self.engine, True, now=enabled_at)
        monday = datetime(2027, 1, 11, 3, tzinfo=BEIJING).timestamp()

        self.assertEqual(list(AUTO_KINDS), smart_mix_auto_due(self.engine, monday))

    def test_missing_built_in_playlist_is_skipped_without_disabling_master_switch(self):
        from helper.smart_mix_web import run_smart_mix_auto, set_smart_mix_schedule, smart_mix_auto_due

        self.publish("weekly", {"size": 10, "recent_days": 30})
        enabled_at = datetime(2027, 1, 6, 12, tzinfo=BEIJING).timestamp()
        set_smart_mix_schedule(self.engine, True, now=enabled_at)
        monday = datetime(2027, 1, 11, 3, tzinfo=BEIJING).timestamp()

        result = run_smart_mix_auto(self.engine, monday)

        self.assertEqual("skipped", result["items"]["time_capsule"]["status"])
        self.assertEqual("skipped", result["items"]["recent_additions"]["status"])
        self.assertTrue(self.store.get("smart_mix_settings")["auto_enabled"])

    def test_one_kind_failure_does_not_stop_the_other_due_kinds(self):
        from helper.smart_mix_web import run_smart_mix_auto, set_smart_mix_schedule, smart_mix_auto_due

        for kind, options in (
            ("weekly", {"size": 10, "recent_days": 30}),
            ("time_capsule", {"size": 10, "stale_days": 30}),
            ("recent_additions", {"size": 10, "added_days": 90}),
        ):
            self.publish(kind, options)
        enabled_at = datetime(2027, 1, 6, 12, tzinfo=BEIJING).timestamp()
        set_smart_mix_schedule(self.engine, True, now=enabled_at)
        monday = datetime(2027, 1, 11, 3, tzinfo=BEIJING).timestamp()
        from helper import smart_mix_web
        original = smart_mix_web.preview_smart_mix

        def fail_one(engine, kind, options=None, now=None):
            if kind == "time_capsule":
                raise RuntimeError("temporary failure")
            return original(engine, kind, options, now)

        with patch("helper.smart_mix_web.preview_smart_mix", side_effect=fail_one):
            result = run_smart_mix_auto(self.engine, monday)

        self.assertEqual("error", result["items"]["time_capsule"]["status"])
        self.assertIn(result["items"]["weekly"]["status"], ("unchanged", "published"))
        self.assertIn(result["items"]["recent_additions"]["status"], ("unchanged", "published"))
        self.assertEqual([], smart_mix_auto_due(self.engine, monday + 60))
        self.assertEqual(
            ["time_capsule"],
            smart_mix_auto_due(self.engine, monday + 900),
            "失败歌单应在同一周重试，但不能每分钟请求 Plex",
        )

        with patch("helper.smart_mix_web.preview_smart_mix", side_effect=fail_one):
            second = run_smart_mix_auto(self.engine, monday + 900)
        self.assertEqual("error", second["items"]["time_capsule"]["status"])
        self.assertEqual([], smart_mix_auto_due(self.engine, monday + 900 + 3599))
        self.assertEqual(["time_capsule"], smart_mix_auto_due(self.engine, monday + 900 + 3600))


if __name__ == "__main__":
    unittest.main()
