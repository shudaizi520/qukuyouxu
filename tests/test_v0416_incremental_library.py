import tempfile
import time
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class _Plex:
    def __init__(self, tracks):
        self._tracks = tracks

    def identity(self):
        return {"machine": "machine-1"}

    def tracks(self, section):
        return list(self._tracks)


class IncrementalLibraryV0416Tests(unittest.TestCase):
    def _track(self):
        return {
            "id": "1", "title": "七里香", "artist": "周杰伦", "album": "七里香",
            "duration": 299, "available": True, "guid": "local://1", "paths": [],
        }

    def test_pause_after_scan_stops_before_any_playlist_write(self):
        from helper.library_engine import LibraryEngine
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            store.set("managed", {"base:language:国语": {"id": "playlist-1"}})
            engine = LibraryEngine(store)

            def completed_scan_with_pause(**_kwargs):
                engine.single_pause.set()
                return {"status": "completed", "new_count": 1, "processed": 1, "message": "扫描完成"}

            with patch.object(engine, "_enrich_singles", side_effect=completed_scan_with_pause), \
                    patch.object(engine, "_preview_base") as preview_base, \
                    patch.object(engine, "_preview") as preview_theme:
                result = engine.refresh_new_tracks()

            self.assertEqual("paused", result["status"])
            preview_base.assert_not_called()
            preview_theme.assert_not_called()

    def test_full_analysis_builds_theme_and_qq_field_category_previews(self):
        from helper.library_engine import LibraryEngine
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            engine = LibraryEngine(store)
            theme = {"id": "theme-plan", "groups": []}
            base = {"id": "base-plan", "groups": []}
            with patch.object(engine, "_enrich_singles", return_value={"status": "completed"}), \
                    patch.object(engine, "_preview", return_value=theme) as preview_theme, \
                    patch.object(engine, "_preview_base", return_value=base) as preview_base:
                result = engine.analyze_library()

            self.assertEqual({"theme": theme, "base": base}, result)
            preview_theme.assert_called_once_with(True)
            preview_base.assert_called_once_with()

    def test_confirmed_workflow_applies_selected_base_and_theme_groups_together(self):
        from helper.engine import Engine
        from helper.library_engine import LibraryEngine
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            store.set("plan", {
                "id": "theme-plan", "groups": [
                    {"id": "theme:keep", "blocked": []},
                    {"id": "theme:skip", "blocked": []},
                ],
            })
            store.set("base_plan", {
                "id": "base-plan", "groups": [{"id": "base:pop", "blocked": []}],
            })
            engine = LibraryEngine(store)
            with patch.object(engine, "_apply_base", return_value={"written": 1, "errors": []}) as apply_base, \
                    patch.object(Engine, "_apply", return_value={"written": 1, "errors": []}) as apply_theme:
                result = engine.apply_workflow(
                    "theme-plan", "base-plan", {"theme:keep", "base:pop"}
                )

            apply_base.assert_called_once_with("base-plan", False, allowed_ids={"base:pop"})
            apply_theme.assert_called_once_with(engine, "theme-plan", False)
            self.assertIn("本次未选择", store.get("plan")["groups"][1]["blocked"])
            self.assertEqual(2, result["written"])

    def test_confirmed_workflow_can_apply_theme_without_a_base_preview(self):
        from helper.engine import Engine
        from helper.library_engine import LibraryEngine
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            store.set("plan", {"id": "theme-plan", "groups": [{"id": "theme:keep", "blocked": []}]})
            engine = LibraryEngine(store)
            with patch.object(engine, "_apply_base") as apply_base, \
                    patch.object(Engine, "_apply", return_value={"written": 1, "errors": []}) as apply_theme:
                result = engine.apply_workflow("theme-plan", "", {"theme:keep"})

            apply_base.assert_not_called()
            apply_theme.assert_called_once_with(engine, "theme-plan", False)
            self.assertEqual(1, result["written"])

    def test_confirmed_workflow_can_apply_base_without_a_theme_preview(self):
        from helper.engine import Engine
        from helper.library_engine import LibraryEngine
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            store.set("base_plan", {"id": "base-plan", "groups": [{"id": "base:pop", "blocked": []}]})
            engine = LibraryEngine(store)
            with patch.object(engine, "_apply_base", return_value={"written": 1, "errors": []}) as apply_base, \
                    patch.object(Engine, "_apply") as apply_theme:
                result = engine.apply_workflow("", "base-plan", {"base:pop"})

            apply_base.assert_called_once_with("base-plan", False, allowed_ids={"base:pop"})
            apply_theme.assert_not_called()
            self.assertEqual(1, result["written"])

    def test_new_only_reuses_expired_record_when_track_identity_is_unchanged(self):
        from helper.library_engine import LibraryEngine
        from helper.single import SINGLE_POLICY, match_fingerprint
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            settings = store.get("settings")
            settings.update(plex_url="http://plex", plex_token="token-token", section="11")
            store.set("settings", settings)
            track = self._track()
            clients = []
            engine = LibraryEngine(
                store,
                plex_factory=lambda cfg: _Plex([track]),
                single_factory=lambda **kwargs: clients.append(kwargs),
            )
            scope = engine._single_scope("machine-1")
            store.set(
                "single_result:" + scope + ":1",
                {
                    "id": "1", "status": "matched", "policy": SINGLE_POLICY,
                    "fingerprint": match_fingerprint(track), "expires_at": time.time() - 86400,
                    "checked_at": time.time() - 40 * 86400,
                    "detail": {"mid": "004Z8Ihr0JIu5s"},
                    "fields": {"languages": ["国语"], "genres": ["流行"]},
                },
            )

            result = engine._enrich_singles(new_only=True, auto_connect=True)

            self.assertEqual("completed", result["status"])
            self.assertEqual(0, result["new_count"])
            self.assertEqual(1, result["cached"])
            self.assertEqual([], clients, "没有新增歌曲时不应访问 QQ，连探测请求也不需要")

    def test_new_only_retries_expired_negative_record_for_unchanged_track(self):
        from helper.library_engine import LibraryEngine
        from helper.single import SINGLE_POLICY, match_fingerprint
        from helper.store import Store

        class FakeSingleClient:
            count = 0

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            settings = store.get("settings")
            settings.update(plex_url="http://plex", plex_token="token-token", section="11")
            store.set("settings", settings)
            track = self._track()
            engine = LibraryEngine(
                store,
                plex_factory=lambda cfg: _Plex([track]),
                single_factory=lambda **kwargs: FakeSingleClient(),
            )
            scope = engine._single_scope("machine-1")
            store.set(
                "single_result:" + scope + ":1",
                {
                    "id": "1", "status": "no_candidate", "policy": SINGLE_POLICY,
                    "fingerprint": match_fingerprint(track), "expires_at": time.time() - 1,
                    "checked_at": time.time() - 8 * 86400,
                },
            )
            store.set(
                "single_connection",
                {"ok": True, "scope": engine.single_connection_scope(), "checked_at": time.time()},
            )

            replacement = {
                "status": "matched", "detail": {"mid": "004Z8Ihr0JIu5s", "fetched_at": time.time()},
                "fields": {"languages": ["国语"], "genres": ["流行"]},
            }
            with patch.object(engine, "_single_lookup", return_value=replacement) as lookup:
                result = engine._enrich_singles(new_only=True, auto_connect=True)

            self.assertEqual("completed", result["status"])
            self.assertEqual(1, result["new_count"])
            self.assertEqual(1, lookup.call_count)
            self.assertEqual("matched", store.get("single_result:" + scope + ":1")["status"])

    def test_new_only_adopts_matching_record_from_prior_connection_scope(self):
        from helper.single import SINGLE_POLICY, match_fingerprint
        from helper.single_mixin import adopt_matching_records
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            settings = store.get("settings")
            settings.update(plex_url="http://plex", plex_token="token-token", section="11")
            store.set("settings", settings)
            track = self._track()
            old = {
                "id": "1", "status": "matched", "policy": SINGLE_POLICY,
                "fingerprint": match_fingerprint(track), "expires_at": time.time() - 86400,
                "checked_at": time.time() - 40 * 86400,
                "detail": {"mid": "004Z8Ihr0JIu5s"},
                "fields": {"languages": ["国语"], "genres": ["流行"]},
            }
            store.set("single_result:old-scope:1", old)
            current = adopt_matching_records(store, "single_result:new-scope:", [track], {})

            self.assertEqual(["1"], sorted(current))
            adopted = store.get("single_result:new-scope:1")
            self.assertEqual(old["fingerprint"], adopted["fingerprint"])
            self.assertTrue(adopted["adopted_from_prior_connection"])

    def test_token_refresh_does_not_change_playlist_or_single_cache_scope(self):
        from helper.library_engine import LibraryEngine
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            settings = store.get("settings")
            settings.update(plex_url="http://plex", plex_token="old-token", section="11", account_label="owner")
            store.set("settings", settings)
            engine = LibraryEngine(store)
            before = (engine.daily_scope(), engine.single_connection_scope())
            settings["plex_token"] = "new-token"
            store.set("settings", settings)
            self.assertEqual(before, (engine.daily_scope(), engine.single_connection_scope()))

    def test_full_enrichment_adopts_valid_record_from_prior_scope_without_qq_request(self):
        from helper.library_engine import LibraryEngine
        from helper.single import SINGLE_POLICY, match_fingerprint
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            settings = store.get("settings")
            settings.update(plex_url="http://plex", plex_token="token", section="11")
            store.set("settings", settings)
            track = self._track()
            old = {
                "id": "1", "status": "matched", "policy": SINGLE_POLICY,
                "fingerprint": match_fingerprint(track), "expires_at": time.time() + 86400,
                "checked_at": time.time(), "detail": {"mid": "004Z8Ihr0JIu5s"},
                "fields": {"languages": ["国语"], "genres": ["流行"]},
            }
            store.set("single_result:legacy-token-scope:1", old)
            clients = []
            engine = LibraryEngine(
                store, plex_factory=lambda cfg: _Plex([track]),
                single_factory=lambda **kwargs: clients.append(kwargs),
            )

            result = engine._enrich_singles(new_only=False, auto_connect=True)

            self.assertEqual("completed", result["status"])
            self.assertEqual(1, result["cached"])
            self.assertEqual([], clients)

    def test_default_profile_runtime_uses_incremental_library_engine(self):
        from helper.library_engine import LibraryEngine
        from helper.profile_runtime import ProfileRuntime
        from helper.profiles import ProfileRegistry
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            base = Store(Path(root))
            runtime = ProfileRuntime(base, ProfileRegistry(base))
            self.assertIsInstance(runtime.engine("default"), LibraryEngine)

    def test_midnight_automation_uses_incremental_refresh(self):
        from helper.automation import PROFILE_STATE_KEY, save_automation_settings
        from helper.profile_runtime import ProfileRuntime
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ScopedStore
        from helper.store import Store

        class FakeEngine:
            def __init__(self, store, calls):
                import threading
                self.store = store
                self.calls = calls
                self.stop = threading.Event()
                self.job = {"running": False}

            def daily_due(self, now=None):
                return False

            def refresh_new_tracks(self):
                self.calls.append(("incremental", self.store.profile_id))
                return {"status": "completed"}

            def auto(self):
                raise AssertionError("定时任务不应绕过新增歌曲扫描")

        with tempfile.TemporaryDirectory() as root:
            base = Store(Path(root))
            registry = ProfileRegistry(base)
            owner = ScopedStore(base, "default")
            settings = owner.get("settings")
            settings.update(plex_token="owner-secret")
            owner.set_many({"settings": settings, "managed": {"theme": {"id": "playlist-1"}}})
            calls = []
            runtime = ProfileRuntime(base, registry, engine_factory=lambda store: FakeEngine(store, calls))
            saved = save_automation_settings(base, {
                "daily": {"enabled": False, "hour": 6},
                "smart": {"enabled": False, "interval_days": 7, "hour": 3},
                "library": {"enabled": True, "hour": 0},
            })
            owner.set(PROFILE_STATE_KEY, {"revision": saved["revision"], "tasks": {
                "library": {"config": saved["library"], "next_at": 1000, "slot": 1000},
            }})

            result = runtime.run_due(now=1000)

            self.assertEqual([("incremental", "default")], calls)
            self.assertEqual("library", result[0]["kind"])

    def test_library_page_has_one_simple_manual_incremental_action(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "src/helper/static/home.html").read_text(encoding="utf-8")
        script = (root / "src/helper/static/home.js").read_text(encoding="utf-8")
        routes = (root / "src/helper/workflow_v0317.py").read_text(encoding="utf-8")

        self.assertEqual(1, html.count('id="incrementalAction"'))
        self.assertIn("检查新增歌曲", html)
        self.assertIn("/api/workflow/incremental", script)
        self.assertIn('@app.post("/api/workflow/incremental")', routes)
        self.assertNotIn("新增歌曲请求数", html)
        self.assertNotIn("缓存命中详情", html)


if __name__ == "__main__":
    unittest.main()
