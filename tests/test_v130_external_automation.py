import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.test_v130_external_service import CatalogPlex, FakeProviders, local, snapshot


class ExternalAutomationV130Tests(unittest.TestCase):
    def test_retry_ladder_manual_bypass_success_reset_and_other_source_progress(self):
        from helper.external_service import ExternalPlaylistService
        from helper.external_sources import ExternalSourceError
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ScopedStore
        from helper.store import Store

        class RoutingProviders:
            def __init__(self):
                self.results = {}

            def fetch(self, recognized):
                result = self.results[str(recognized["external_id"])].pop(0)
                if isinstance(result, Exception):
                    raise result
                return result

        with tempfile.TemporaryDirectory() as root:
            base = Store(Path(root))
            ProfileRegistry(base)
            store = ScopedStore(base, "default")
            settings = store.get("settings")
            settings.update(plex_url="http://plex", plex_token="token-token", section="11")
            store.set("settings", settings)
            plex = CatalogPlex([local("10", "已有一", "歌手甲")])
            now = [2_000_000_000.0]
            provider = RoutingProviders()
            provider.results["123"] = [snapshot()]
            service = ExternalPlaylistService(store, lambda cfg: plex, provider, clock=lambda: now[0])
            first = service.import_source(value="https://y.qq.com/n/ryqq/playlist/123")
            other_snapshot = snapshot(revision="other")
            other_snapshot["external_id"] = "456"
            other_snapshot["url"] = "https://y.qq.com/n/ryqq/playlist/456"
            provider.results["456"] = [other_snapshot]
            second = service.import_source(value=other_snapshot["url"])
            service.set_follow_updates(first["id"], True)
            service.set_follow_updates(second["id"], True)

            delays = [900, 3600, 21600, 86400]
            for index, delay in enumerate(delays, 1):
                provider.results["123"] = [
                    ExternalSourceError("暂时失败", retryable=True, kind="upstream"),
                ]
                next_other = snapshot(revision=f"other-{index}")
                next_other["external_id"] = "456"
                next_other["url"] = other_snapshot["url"]
                provider.results["456"] = [next_other]
                outcome = service.auto_refresh()
                failed = service.repository.get_source("default", first["id"])
                self.assertEqual(now[0] + delay, failed["next_retry_at"])
                self.assertTrue(any(row.get("source_id") == second["id"] and row.get("status") == "updated" for row in outcome["sources"]))
                skipped = service.auto_refresh()
                self.assertTrue(any(row.get("source_id") == first["id"] and row.get("status") == "waiting" for row in skipped["sources"]))
                now[0] += delay

            provider.results["123"] = [snapshot(revision="recovered")]
            service.refresh(first["id"], force=True)
            recovered = service.repository.get_source("default", first["id"])
            self.assertEqual(0, recovered["failure_count"])
            self.assertIsNone(recovered["next_retry_at"])

    def test_incremental_library_runs_external_work_even_with_no_new_qq_rows(self):
        from helper.library_engine import LibraryEngine
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            engine = LibraryEngine(store)
            engine.external = Mock()
            engine.external.rematch_missing.return_value = {"missing": 1}
            engine.external.auto_refresh.return_value = {"updated": 1}
            with patch.object(engine, "_enrich_singles", return_value={
                "status": "completed", "new_count": 0, "processed": 0, "message": "",
            }):
                result = engine.refresh_new_tracks()
            engine.external.rematch_missing.assert_called_once_with()
            engine.external.auto_refresh.assert_called_once_with()
            self.assertEqual(1, result["external"]["refresh"]["updated"])

    def test_library_schedule_is_eligible_for_an_external_managed_playlist(self):
        from helper.external_store import ExternalRepository
        from helper.profile_runtime import ProfileRuntime
        from helper.scoped_store import ScopedStore
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            base = Store(Path(root))
            store = ScopedStore(base, "default")
            repository = ExternalRepository(store)
            source = repository.upsert_source("default", snapshot(), 2_000_000_000)
            repository.save_managed("default", source["id"], {"id": "99", "title": "百万收藏"})
            engine = type("Engine", (), {"store": store})()
            self.assertTrue(ProfileRuntime._eligible_for_task(engine, "library"))


if __name__ == "__main__":
    unittest.main()
