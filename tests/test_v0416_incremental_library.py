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

    def test_incremental_refresh_reports_auto_additions_and_keeps_new_candidates_for_review(self):
        """Applying managed lists must not consume newly discovered playlist candidates."""
        from helper.library_engine import LibraryEngine
        from helper.store import Store
        from helper.workflow_v0317 import build_workflow_status

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            store.set_many({
                "managed": {
                    "base:existing": {"id": "base-playlist"},
                    "theme:existing": {"id": "theme-playlist"},
                },
                "sources": [
                    {"id": "theme:existing", "enabled": True, "approved": True},
                    {"id": "theme:new", "enabled": True, "approved": False},
                ],
            })
            engine = object.__new__(LibraryEngine)
            import threading
            engine.store = store
            engine.gate = threading.Lock()
            engine.single_pause = threading.Event()
            engine.workflow_pause = threading.Event()
            engine.job = {}
            engine.progress = lambda _message: None
            engine.base_signature = lambda: "theme:" + ",".join(
                row["id"] for row in (store.get("plan") or {}).get("groups", [])
            )
            engine._enrich_singles = lambda **_kwargs: {
                "status": "completed", "new_count": 4, "processed": 100,
            }
            engine.external = type("External", (), {
                "rematch_missing": lambda _self: {},
                "auto_refresh": lambda _self: {},
            })()

            base_calls = 0
            theme_calls = 0
            base_theme_evidence_seen = set()

            def base_preview():
                nonlocal base_calls, base_theme_evidence_seen
                base_calls += 1
                if base_calls > 1:
                    raise AssertionError("候选不应依赖第二次 Plex 读取才能保留")
                base_theme_evidence_seen = {
                    row["id"] for row in (store.get("plan") or {}).get("groups", [])
                }
                plan = {
                    "id": f"base-{base_calls}", "created_at": time.time(),
                    "applied": False, "library_count": 100,
                    "signature": engine.base_signature(),
                    "groups": [
                        {"id": "base:existing", "title": "已有基础", "kind": "base",
                         "dimension": "genre", "desired": ["101", "102"],
                         "add": ["101", "102"], "action": "update", "blocked": []},
                        {"id": "base:children", "title": "儿歌", "kind": "base",
                         "dimension": "audience", "desired": [str(value) for value in range(20)],
                         "add": [str(value) for value in range(20)], "action": "create", "blocked": []},
                    ],
                }
                store.set("base_plan", plan)
                return plan

            def theme_preview(_force=False):
                nonlocal theme_calls
                theme_calls += 1
                if theme_calls > 1:
                    raise AssertionError("候选不应依赖第二次 Plex 读取才能保留")
                plan = {
                    "id": f"theme-{theme_calls}", "created_at": time.time(),
                    "applied": False, "library_count": 100,
                    "groups": [
                        {"id": "theme:existing", "title": "已有主题", "kind": "qq_category",
                         "desired": ["102", "103"],
                         "add": ["102", "103"], "action": "update", "blocked": []},
                        {"id": "theme:new", "title": "新主题", "kind": "qq_category",
                         "desired": [str(value) for value in range(20, 30)],
                         "add": [str(value) for value in range(20, 30)], "action": "create", "blocked": []},
                    ],
                }
                store.set("plan", plan)
                return plan

            engine._preview_base = base_preview
            engine._preview = theme_preview
            engine._apply_base = lambda *_args, **_kwargs: {
                "written": 1, "unchanged": 0, "skipped": 1, "errors": [],
                "added_ids": ["101", "102"],
            }
            engine._apply = lambda *_args, **_kwargs: {
                "written": 1, "unchanged": 0, "skipped": 1, "errors": [],
                "added_ids": ["102", "103"],
            }

            result = engine.refresh_new_tracks()
            workflow = build_workflow_status(
                store, engine, {"logged_in": True, "phase": "ready"},
            )["workflow"]
            saved_base_plan = store.get("base_plan")
            expected_base_signature = engine.base_signature()

        self.assertEqual(3, result["auto_added_count"])
        self.assertEqual(2, result["candidate_count"])
        self.assertIn("3 首已自动加入已有歌单", result["message"])
        self.assertIn("2 个新歌单等待确认", result["message"])
        self.assertEqual(1, base_calls)
        self.assertEqual(1, theme_calls)
        self.assertEqual({"theme:existing", "theme:new"}, base_theme_evidence_seen)
        self.assertEqual(expected_base_signature, saved_base_plan["signature"])
        self.assertEqual("review", workflow["state"]["phase"])
        self.assertEqual(
            {"base:children", "theme:new"},
            {group["id"] for group in workflow["review"]["groups"] if group["action"] == "create"},
        )

    def test_partial_maintenance_failure_keeps_candidates_and_counts_successful_additions(self):
        """One failed managed list must not consume unrelated discovery work."""
        from helper.library_engine import LibraryEngine
        from helper.store import Store
        from helper.workflow_v0317 import build_workflow_status

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            store.set_many({
                "managed": {"base:existing": {"id": "base-playlist"}},
                "sources": [],
            })
            engine = object.__new__(LibraryEngine)
            import threading
            engine.store = store
            engine.gate = threading.Lock()
            engine.single_pause = threading.Event()
            engine.workflow_pause = threading.Event()
            engine.job = {}
            engine.progress = lambda _message: None
            engine._enrich_singles = lambda **_kwargs: {
                "status": "completed", "new_count": 2, "processed": 2,
            }
            engine.external = type("External", (), {
                "rematch_missing": lambda _self: {},
                "auto_refresh": lambda _self: {},
            })()
            calls = 0

            def base_preview():
                nonlocal calls
                calls += 1
                if calls > 1:
                    raise AssertionError("失败后不应再次依赖 Plex 才能恢复候选")
                plan = {
                    "id": f"base-{calls}", "created_at": time.time(),
                    "applied": False, "library_count": 100,
                    "groups": [
                        {"id": "base:existing", "title": "已有基础", "kind": "base",
                         "dimension": "genre", "desired": ["101"],
                         "add": ["101"], "action": "update", "blocked": []},
                        {"id": "base:children", "title": "儿歌", "kind": "base",
                         "dimension": "audience", "desired": [str(value) for value in range(20)],
                         "add": [str(value) for value in range(20)], "action": "create", "blocked": []},
                    ],
                }
                store.set("base_plan", plan)
                return plan

            engine._preview_base = base_preview
            engine._apply_base = lambda *_args, **_kwargs: {
                "written": 1, "unchanged": 0, "skipped": 1,
                "errors": ["另一个已有歌单同步失败"],
                "retryable_errors": ["另一个已有歌单同步失败"],
                "added_ids": ["101"],
            }

            from helper.scheduler_retry import TransientScheduleError
            with self.assertRaises(TransientScheduleError):
                engine.refresh_new_tracks()
            result = store.get("incremental_status")
            workflow = build_workflow_status(
                store, engine, {"logged_in": True, "phase": "ready"},
            )["workflow"]
            saved_base_plan = store.get("base_plan")
            confirmed_plan = dict(saved_base_plan)
            confirmed_plan["applied"] = True
            confirmed_plan["created_at"] = time.time() + 1
            confirmed_plan["result"] = {"written": 1, "errors": []}
            store.set("base_plan", confirmed_plan)
            after_confirmation = build_workflow_status(
                store, engine, {"logged_in": True, "phase": "ready"},
            )["workflow"]

        self.assertEqual("attention", result["status"])
        self.assertEqual(1, result["auto_added_count"])
        self.assertEqual(1, result["candidate_count"])
        self.assertEqual(1, calls)
        self.assertFalse(saved_base_plan["applied"])
        self.assertEqual("review", workflow["state"]["phase"])
        self.assertIn("base:children", {
            row["id"] for row in workflow["review"]["groups"] if row["action"] == "create"
        })
        self.assertIn("1 首已自动加入", result["message"])
        self.assertIn("1 个新歌单等待确认", result["message"])
        self.assertEqual("attention", after_confirmation["state"]["phase"])
        self.assertIn("另一个已有歌单同步失败", after_confirmation["state"]["result"]["errors"])

    def test_paused_incremental_does_not_erase_unresolved_maintenance_attention(self):
        from helper.library_engine import LibraryEngine
        from helper.store import Store
        from helper.workflow_v0317 import build_workflow_status

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            prior = {
                "status": "attention", "message": "国语歌单同步失败",
                "errors": ["国语歌单同步失败"], "updated_at": time.time() - 10,
            }
            store.set("library_maintenance_attention", prior)
            engine = LibraryEngine(store)
            engine._enrich_singles = lambda **_kwargs: {
                "status": "blocked", "new_count": 0, "processed": 0,
                "message": "QQ 暂时不可用",
            }

            result = engine.refresh_new_tracks()
            self.assertEqual("blocked", result["status"])
            self.assertEqual(prior, store.get("library_maintenance_attention"))
            store.set("workflow_pause_state", None)
            workflow = build_workflow_status(
                store, engine, {"logged_in": True, "phase": "ready"},
            )["workflow"]

        self.assertEqual("attention", workflow["state"]["phase"])
        self.assertIn("国语歌单同步失败", workflow["state"]["result"]["errors"])

    def test_theme_partial_failure_is_checkpointed_before_a_later_base_read_failure(self):
        from helper.clients import PlexError
        from helper.library_engine import LibraryEngine
        from helper.scheduler_retry import TransientScheduleError
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            store.set_many({
                "managed": {"base:existing": {"id": "base-playlist"}},
                "sources": [
                    {"id": "theme:existing", "enabled": True, "approved": True},
                    {"id": "theme:new", "enabled": True, "approved": False},
                ],
            })
            engine = LibraryEngine(store)
            engine._enrich_singles = lambda **_kwargs: {
                "status": "completed", "new_count": 1, "processed": 1,
            }
            engine.external = type("External", (), {
                "rematch_missing": lambda _self: {},
                "auto_refresh": lambda _self: {},
            })()

            def theme_preview(_force=False):
                plan = {
                    "id": "theme-plan", "created_at": time.time(), "applied": False,
                    "library_count": 100, "groups": [
                        {"id": "theme:existing", "title": "已有主题", "desired": ["1"],
                         "add": ["1"], "action": "update", "blocked": []},
                        {"id": "theme:new", "title": "新主题", "desired": [str(i) for i in range(20)],
                         "add": [str(i) for i in range(20)], "action": "create", "blocked": []},
                    ],
                }
                store.set("plan", plan)
                return plan

            engine._preview = theme_preview
            engine._apply = lambda *_args, **_kwargs: {
                "written": 0, "unchanged": 0, "skipped": 1,
                "errors": ["主题歌单：Plex timeout"],
                "retryable_errors": ["主题歌单：Plex timeout"], "added_ids": [],
            }
            engine._preview_base = lambda: (_ for _ in ()).throw(PlexError("still down"))

            with self.assertRaises(TransientScheduleError):
                engine.refresh_new_tracks()

            attention = store.get("library_maintenance_attention")
            incremental = store.get("incremental_status")
            pending_theme = store.get("plan")

        self.assertEqual("attention", attention["status"])
        self.assertIn("主题歌单：Plex timeout", attention["errors"])
        self.assertEqual("attention", incremental["status"])
        self.assertEqual(1, incremental["candidate_count"])
        self.assertFalse(pending_theme["applied"])
        self.assertEqual(["theme:new"], pending_theme["review_group_ids"])

    def test_theme_success_count_is_checkpointed_before_a_later_base_read_failure(self):
        from helper.clients import PlexError
        from helper.library_engine import LibraryEngine
        from helper.scheduler_retry import TransientScheduleError
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            store.set_many({
                "managed": {"base:existing": {"id": "base-playlist"}},
                "sources": [
                    {"id": "theme:existing", "enabled": True, "approved": True},
                    {"id": "theme:new", "enabled": True, "approved": False},
                ],
            })
            engine = LibraryEngine(store)
            engine._enrich_singles = lambda **_kwargs: {
                "status": "completed", "new_count": 1, "processed": 1,
            }
            engine.external = type("External", (), {
                "rematch_missing": lambda _self: {},
                "auto_refresh": lambda _self: {},
            })()
            plan = {
                "id": "theme-plan", "created_at": time.time(), "applied": False,
                "library_count": 100, "groups": [
                    {"id": "theme:existing", "title": "已有主题", "desired": ["7"],
                     "add": ["7"], "action": "update", "blocked": []},
                    {"id": "theme:new", "title": "新主题", "desired": [str(i) for i in range(20)],
                     "add": [str(i) for i in range(20)], "action": "create", "blocked": []},
                ],
            }
            engine._preview = lambda _force=False: store.set("plan", plan) or plan
            engine._apply = lambda *_args, **_kwargs: {
                "written": 1, "unchanged": 0, "skipped": 1,
                "errors": [], "retryable_errors": [], "added_ids": ["7"],
            }
            engine._preview_base = lambda: (_ for _ in ()).throw(PlexError("still down"))

            with self.assertRaises(TransientScheduleError):
                engine.refresh_new_tracks()

            incremental = store.get("incremental_status")
            pending_theme = store.get("plan")

        self.assertEqual(1, incremental["auto_added_count"])
        self.assertEqual(1, incremental["candidate_count"])
        self.assertEqual(["theme:new"], pending_theme["review_group_ids"])

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
            before_revision = int(store.get("single_revision", 0))
            current = adopt_matching_records(store, "single_result:new-scope:", [track], {})

            self.assertEqual(["1"], sorted(current))
            adopted = store.get("single_result:new-scope:1")
            self.assertEqual(old["fingerprint"], adopted["fingerprint"])
            self.assertTrue(adopted["adopted_from_prior_connection"])
            self.assertEqual(before_revision + 1, store.get("single_revision"))

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
