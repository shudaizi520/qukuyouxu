import tempfile
import unittest
from pathlib import Path


NOW = 2_000_000_000.0


def track(index):
    return {
        "source_track_key": f"song-{index}",
        "position": index,
        "source_track_id": str(index),
        "title": f"歌曲{index}",
        "artists": [f"歌手{index}"],
        "album": "专辑",
        "duration_ms": 180_000,
        "version_flags": [],
        "version_label": "",
        "source_url": f"https://y.qq.com/n/ryqq/songDetail/{index}",
    }


def snapshot(provider="qq", external_id="42", count=1):
    return {
        "provider": provider,
        "external_id": external_id,
        "url": f"https://example.invalid/{provider}/{external_id}",
        "title": "百万收藏",
        "revision": f"revision-{count}",
        "tracks": [track(index) for index in range(count)],
    }


class ExternalStoreV130Tests(unittest.TestCase):
    def setUp(self):
        from helper.external_store import ExternalRepository
        from helper.profiles import ProfileRegistry
        from helper.store import Store

        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.registry = ProfileRegistry(self.store)
        self.registry.create("长辈", "home", profile_id="parent")
        self.repo = ExternalRepository(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_same_external_id_is_isolated_by_profile(self):
        one = self.repo.upsert_source("default", snapshot(), NOW)
        two = self.repo.upsert_source("parent", snapshot(), NOW)

        self.assertNotEqual(one["id"], two["id"])
        self.assertEqual([one["id"]], [row["id"] for row in self.repo.list_sources("default")])
        self.assertEqual([two["id"]], [row["id"] for row in self.repo.list_sources("parent")])

    def test_failed_refresh_keeps_last_good_tracks_and_schedules_retry(self):
        source = self.repo.upsert_source("default", snapshot(count=2), NOW)

        failed = self.repo.record_failure("default", source["id"], "QQ暂时不可用", NOW + 60)

        self.assertEqual(2, len(self.repo.list_tracks("default", source["id"])))
        self.assertEqual("QQ暂时不可用", failed["last_error"])
        self.assertEqual(1, failed["failure_count"])
        self.assertEqual(NOW + 60 + 900, failed["next_retry_at"])

    def test_profile_delete_removes_only_that_profiles_external_rows(self):
        self.repo.upsert_source("default", snapshot(), NOW)
        self.repo.upsert_source("parent", snapshot(), NOW)

        self.registry.remove("parent")

        self.assertEqual([], self.repo.list_sources("parent"))
        self.assertEqual(1, len(self.repo.list_sources("default")))

    def test_replacing_snapshot_is_atomic_and_clears_old_matches(self):
        source = self.repo.upsert_source("default", snapshot(count=2), NOW)
        self.repo.replace_matches("default", source["id"], [{
            "source_track_key": "song-0", "status": "matched",
            "plex_track_id": "77", "candidate_ids": [], "reason": "matched",
            "manual": False,
        }], "catalog-1")

        replaced = self.repo.replace_snapshot("default", source["id"], snapshot(count=3), NOW + 1)

        self.assertEqual("revision-3", replaced["revision"])
        self.assertEqual(3, len(self.repo.list_tracks("default", source["id"])))
        self.assertEqual([], self.repo.list_matches("default", source["id"]))
        with self.assertRaises(ValueError):
            self.repo.replace_snapshot("default", source["id"], {**snapshot(count=1), "tracks": [{"title": "坏数据"}]}, NOW + 2)
        self.assertEqual(3, len(self.repo.list_tracks("default", source["id"])))

    def test_matches_managed_and_runs_round_trip_without_cross_profile_access(self):
        source = self.repo.upsert_source("default", snapshot(), NOW)
        self.repo.replace_matches("default", source["id"], [{
            "source_track_key": "song-0", "status": "review", "plex_track_id": "",
            "candidate_ids": ["7", "8"], "reason": "ambiguous", "manual": False,
        }], "catalog-1")
        self.repo.save_managed("default", source["id"], {
            "id": "99", "title": "百万收藏", "fingerprint": "fp", "revision": "revision-1",
        })
        self.repo.append_run("default", source["id"], {
            "kind": "import", "status": "completed", "started_at": NOW, "finished_at": NOW + 1,
            "message": "完成",
        })

        matches = self.repo.list_matches("default", source["id"])
        self.assertEqual(["7", "8"], matches[0]["candidate_ids"])
        self.assertEqual("99", self.repo.get_managed("default", source["id"])["id"])
        self.assertEqual("completed", self.repo.list_runs("default", source["id"])[0]["status"])
        with self.assertRaises(ValueError):
            self.repo.get_source("parent", source["id"])

    def test_schema_creation_preserves_existing_state_and_database_integrity(self):
        self.store.set("important_existing_value", {"kept": True})
        from helper.external_store import ensure_external_schema

        with self.store.lock, self.store._db() as db:
            ensure_external_schema(db)
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]

        self.assertEqual({"kept": True}, self.store.get("important_existing_value"))
        self.assertEqual("ok", integrity)


if __name__ == "__main__":
    unittest.main()
