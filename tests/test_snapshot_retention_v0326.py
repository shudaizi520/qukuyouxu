import pathlib
import sys
import types
import fastapi
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if "fastapi" not in sys.modules:
    fastapi = types.ModuleType("fastapi")
    fastapi.Request = object
    sys.modules["fastapi"] = fastapi

from helper import engine


NOW = 1_800_000_000
DAY = 86_400


def snapshot(identifier, category="theme", age_days=400, status="applied"):
    return {
        "id": identifier,
        "category_id": category,
        "created_at": NOW - age_days * DAY,
        "status": status,
    }


class SnapshotRetentionV0326Tests(unittest.TestCase):
    def test_retention_preserves_protected_and_bounds_completed_history(self):
        retain = getattr(engine, "retain_snapshots", None)
        self.assertTrue(callable(retain), "retain_snapshots must exist")
        if not callable(retain):
            return
        rows = [
            snapshot("referenced", age_days=900),
            snapshot("uncertain", age_days=900, status="uncertain"),
            snapshot("prepared", age_days=900, status="prepared"),
            snapshot("restoring", age_days=900, status="restoring"),
            snapshot("recent", age_days=100),
            {"id": "bad-time", "category_id": "theme", "created_at": "invalid", "status": "restored"},
        ]
        rows.extend(snapshot(f"old-{index:02d}", age_days=500 + index) for index in range(60))
        kept = retain(rows, {"referenced"}, NOW, days=365, per_stream=50)
        kept_ids = {row["id"] for row in kept}
        for identifier in ("referenced", "uncertain", "prepared", "restoring", "recent", "bad-time"):
            self.assertIn(identifier, kept_ids)
        old_kept = [identifier for identifier in kept_ids if identifier.startswith("old-")]
        self.assertEqual(50, len(old_kept))
        self.assertIn("old-00", kept_ids)
        self.assertNotIn("old-59", kept_ids)

    def test_daily_and_category_streams_each_keep_fifty(self):
        retain = getattr(engine, "retain_snapshots", None)
        self.assertTrue(callable(retain), "retain_snapshots must exist")
        if not callable(retain):
            return
        rows = [snapshot(f"theme-{i:02d}", "theme", 500 + i) for i in range(55)]
        rows += [snapshot(f"daily-{i:02d}", "daily", 500 + i) for i in range(55)]
        kept = retain(rows, set(), NOW, days=365, per_stream=50)
        self.assertEqual(50, sum(row["category_id"] == "theme" for row in kept))
        self.assertEqual(50, sum(row["category_id"] == "daily" for row in kept))

    def test_save_preserves_flat_daily_managed_reference(self):
        class Store:
            def __init__(self, values):
                self.values = values

            def get(self, key, default=None):
                return self.values.get(key, default)

            def set(self, key, value):
                self.values[key] = value

        rows = [snapshot("daily-ref", "daily", age_days=900)]
        rows.extend(snapshot(f"daily-old-{i:02d}", "daily", age_days=500 + i) for i in range(50))
        store = Store({
            "snapshots": rows,
            "managed": {},
            "daily_managed": {"id": "playlist-1", "snapshot_id": "daily-ref"},
            "retired_managed": {},
        })
        instance = engine.Engine.__new__(engine.Engine)
        instance.store = store
        instance._save_snapshot(snapshot("daily-new", "daily", age_days=0))
        kept_ids = {row["id"] for row in store.get("snapshots")}
        self.assertIn("daily-ref", kept_ids)


if __name__ == "__main__":
    unittest.main()
