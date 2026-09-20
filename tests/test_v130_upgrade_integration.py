import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests.test_v130_external_api import request
from tests.test_v130_external_store import NOW, snapshot


def state_rows(store):
    with store.lock, store._db() as database:
        return list(database.execute("SELECT k,v FROM state ORDER BY k"))


class UpgradeIntegrationV130Tests(unittest.TestCase):
    def setUp(self):
        from helper.external_store import ExternalRepository
        from helper.profiles import ProfileRegistry
        from helper.store import Store

        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root)
        self.registry = ProfileRegistry(self.store)
        self.registry.create("长辈", "home", profile_id="parent")
        self.repository = ExternalRepository(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_old_database_opens_without_rewriting_existing_json_state(self):
        self.store.set("release_1_2_1_sentinel", {"songs": ["甲", "乙"], "enabled": True})
        before = state_rows(self.store)
        with self.store.lock, self.store._db() as database:
            for table in ("external_match", "external_track", "external_managed", "external_run", "external_source"):
                database.execute(f"DROP TABLE {table}")

        from helper.store import Store

        reopened = Store(self.root)
        self.assertEqual(before, state_rows(reopened))
        with reopened.lock, reopened._db() as database:
            self.assertEqual("ok", database.execute("PRAGMA integrity_check").fetchone()[0])
            self.assertIsNotNone(database.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='external_source'"
            ).fetchone())

    def test_new_library_profile_starts_with_no_external_sources(self):
        created = self.registry.create_for_library(
            "default", {"id": "22", "name": "长辈音乐"}, profile_id="parent-library"
        )
        self.assertEqual([], self.repository.list_sources(created["id"]))

    def test_music_cache_archive_excludes_external_playlist_rows(self):
        from helper.cache_archive import export_music_cache

        source = self.repository.upsert_source("default", snapshot(), NOW)
        self.store.set("profile:default:single_revision", 9)
        archive = self.root / "cache.json"
        export_music_cache(self.store.path, archive)
        document = json.loads(archive.read_text(encoding="utf-8"))
        rendered = json.dumps(document, ensure_ascii=False)
        self.assertNotIn(source["id"], rendered)
        self.assertNotIn("百万收藏", rendered)
        self.assertEqual(["profile:default:single_revision"], [row["key"] for row in document["records"]])

    def test_profile_removal_cleans_unmanaged_rows_but_blocks_managed_external_playlist(self):
        source = self.repository.upsert_source("parent", snapshot(), NOW)
        self.repository.save_managed("parent", source["id"], {
            "id": "99", "title": "百万收藏", "fingerprint": "fp", "revision": "revision-1",
        })
        with self.assertRaisesRegex(ValueError, "托管歌单"):
            self.registry.remove("parent")
        self.assertEqual(1, len(self.repository.list_sources("parent")))

        self.repository.save_managed("parent", source["id"], None)
        self.registry.remove("parent")
        self.assertEqual([], self.repository.list_sources("parent"))
        with sqlite3.connect(self.store.path) as database:
            for table in ("external_source", "external_track", "external_match", "external_managed", "external_run"):
                self.assertEqual(0, database.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE profile_id='parent'"
                ).fetchone()[0])

    def test_copied_application_data_opens_http_app_without_changing_existing_counts(self):
        from helper.auth import AuthManager, COOKIE_NAME
        from helper.behavior_store import BehaviorRepository
        from helper.scoped_store import ScopedStore
        from helper.store import Store
        from helper.web import create_app

        scoped = ScopedStore(self.store, "default", registry=self.registry)
        scoped.set("cache", {"track-1": {"title": "旧缓存"}})
        scoped.set("managed", {"theme:work": {"id": "88", "title": "工作伴伴"}})
        scoped.set("automation_v114", {"daily": {"enabled": True, "hour": 6}})
        BehaviorRepository(self.store).append_event("default", {
            "event_key": "upgrade-event", "track_id": "track-1", "kind": "complete",
            "value": 1.0, "at": NOW,
        })
        source = self.repository.upsert_source("default", snapshot(), NOW)
        self.repository.replace_matches("default", source["id"], [{
            "source_track_key": "song-0", "status": "matched", "plex_track_id": "track-1",
            "candidate_ids": [], "reason": "matched", "manual": False,
        }], "catalog-1")
        auth = AuthManager(self.store)
        auth.create_account("admin", "safe-password")
        token, _ = auth.create_session("admin")

        tables = (
            "state", "behavior_event", "behavior_track_state", "behavior_user_state",
            "external_source", "external_track", "external_match", "external_managed", "external_run",
        )

        def counts(store):
            with store.lock, store._db() as database:
                return {table: database.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}

        before_state = state_rows(self.store)
        before_counts = counts(self.store)
        with tempfile.TemporaryDirectory() as copied_temp:
            copied_root = Path(copied_temp) / "data"
            shutil.copytree(self.root, copied_root)
            copied = Store(copied_root)
            self.assertEqual(before_state, state_rows(copied))
            self.assertEqual(before_counts, counts(copied))
            with copied.lock, copied._db() as database:
                self.assertEqual("ok", database.execute("PRAGMA integrity_check").fetchone()[0])

            app = create_app(store=copied, start_scheduler=False)
            try:
                health = next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/healthz")()
                external_page = next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/external")()
                self.assertEqual({"ok": True, "version": "1.3.1"}, health)
                self.assertEqual(200, external_page.status_code)
                status, _, payload = request(
                    app, "/api/external/sources",
                    headers={"cookie": f"{COOKIE_NAME}={token}", "X-Plex-Profile": "default"},
                )
                self.assertEqual(200, status)
                self.assertEqual(source["id"], json.loads(payload)["items"][0]["id"])
                after_counts = counts(copied)
                self.assertEqual(
                    {key: value for key, value in before_counts.items() if key != "state"},
                    {key: value for key, value in after_counts.items() if key != "state"},
                )
                after_state = dict(state_rows(copied))
                for key, value in before_state:
                    self.assertEqual(value, after_state[key])
            finally:
                app.state.profile_runtime.close()


if __name__ == "__main__":
    unittest.main()
