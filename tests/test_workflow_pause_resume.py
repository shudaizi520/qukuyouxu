import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class _StoppedScanEngine:
    job = {"running": False}

    def single_status(self):
        return {"state": {"status": "paused", "processed": 12, "library_count": 100}}

    def theme_status(self):
        return {"settings": {}, "topics": [], "unavailable": [], "skipped_references": []}


class _PausedPreviewEngineMixin:
    def _enrich_singles(self, **_kwargs):
        return {"status": "paused", "message": "已暂停，已完成结果已保存"}


class WorkflowPauseResumeTests(unittest.TestCase):
    def test_pause_requested_before_scanner_entry_is_not_cleared(self):
        from helper.library_engine import LibraryEngine
        from helper.store import Store

        class TinyPlex:
            def identity(self):
                return {"machine": "machine-1"}

            def tracks(self, _section):
                return [{
                    "id": "1", "title": "七里香", "artist": "周杰伦",
                    "album": "七里香", "duration": 299, "available": True,
                    "guid": "local://1", "paths": [],
                }]

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            settings = store.get("settings")
            settings.update(plex_url="http://plex", plex_token="token", section="11")
            store.set("settings", settings)
            engine = LibraryEngine(store, plex_factory=lambda _cfg: TinyPlex())
            engine.workflow_pause.set()
            engine.single_pause.set()

            with patch.object(engine, "_single_lookup", side_effect=AssertionError("暂停后不应查询 QQ")):
                result = engine._enrich_singles(new_only=False, auto_connect=True)

            self.assertEqual("paused", result["status"])

    def test_preview_pause_reaches_the_active_single_scanner(self):
        from helper.library_engine import LibraryEngine
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            engine = LibraryEngine(Store(Path(root)))
            engine.job = {"running": True, "kind": "preview"}

            engine.request_workflow_pause()

            self.assertTrue(engine.workflow_pause.is_set())
            self.assertTrue(engine.single_pause.is_set())

    def test_paused_preview_is_persisted_instead_of_reported_as_completed(self):
        from helper.engine import WorkflowPaused
        from helper.library_engine import LibraryEngine
        from helper.store import Store

        class PausedPreviewEngine(_PausedPreviewEngineMixin, LibraryEngine):
            pass

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            engine = PausedPreviewEngine(store)

            with self.assertRaises(WorkflowPaused):
                engine.analyze_library()

            pause = store.get("workflow_pause_state")
            self.assertTrue(pause["active"])
            self.assertEqual("preview", pause["kind"])

    def test_pause_arriving_after_enrichment_stops_before_preview_planning(self):
        from helper.engine import WorkflowPaused
        from helper.library_engine import LibraryEngine
        from helper.store import Store

        class BoundaryPauseEngine(LibraryEngine):
            def _enrich_singles(inner_self, **_kwargs):
                inner_self.workflow_pause.set()
                return {"status": "completed", "message": "扫描完成"}

            def _preview(inner_self, _force_sources=False):
                self.fail("暂停后不应继续生成或写入歌单预览")

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            engine = BoundaryPauseEngine(store)

            with self.assertRaises(WorkflowPaused):
                engine.analyze_library()

            self.assertEqual("preview", store.get("workflow_pause_state")["kind"])

    def test_paused_incremental_scan_persists_its_resume_kind(self):
        from helper.library_engine import LibraryEngine
        from helper.store import Store

        class PausedIncrementalEngine(_PausedPreviewEngineMixin, LibraryEngine):
            pass

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            result = PausedIncrementalEngine(store).refresh_new_tracks()

            self.assertEqual("paused", result["status"])
            pause = store.get("workflow_pause_state")
            self.assertTrue(pause["active"])
            self.assertEqual("incremental", pause["kind"])

    def test_pause_during_final_incremental_apply_is_not_overwritten_as_completed(self):
        from helper.library_engine import LibraryEngine
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            store.set("sources", [{"id": "topic-1", "approved": True, "enabled": True}])
            engine = LibraryEngine(store)

            def apply_then_pause(*_args, **_kwargs):
                engine.workflow_pause.set()
                return {"errors": []}

            with patch.object(engine, "_enrich_singles", return_value={
                "status": "completed", "new_count": 1, "processed": 1,
            }), patch.object(engine, "_preview", return_value={"id": "plan-1"}), \
                    patch.object(engine, "_apply", side_effect=apply_then_pause):
                result = engine.refresh_new_tracks()

            self.assertEqual("paused", result["status"])
            self.assertEqual("incremental", store.get("workflow_pause_state")["kind"])

    def test_late_preview_pause_is_persisted_before_job_reports_completion(self):
        from helper.library_engine import LibraryEngine
        from helper.store import Store

        class LatePauseEngine(LibraryEngine):
            def analyze_library(inner_self, _force_sources=False):
                inner_self.workflow_pause.set()
                return {"status": "completed"}

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            engine = LatePauseEngine(store)
            engine.start_job("preview")
            deadline = time.time() + 2
            while engine.job.get("running") and time.time() < deadline:
                time.sleep(0.01)

            self.assertFalse(engine.job.get("running"))
            pause = store.get("workflow_pause_state")
            self.assertTrue(pause["active"])
            self.assertEqual("preview", pause["kind"])
            self.assertNotIn("任务完成", engine.job.get("message", ""))

    def test_pause_cannot_be_accepted_after_atomic_success_finalization_starts(self):
        from helper.engine import SafetyError
        from helper.library_engine import LibraryEngine
        from helper.store import Store

        checked = threading.Event()
        release = threading.Event()

        class FinalizationBarrierEngine(LibraryEngine):
            def analyze_library(inner_self, _force_sources=False):
                return {"status": "completed"}

            def _check_workflow_pause(inner_self):
                super()._check_workflow_pause()
                checked.set()
                release.wait(2)

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            engine = FinalizationBarrierEngine(store)
            engine.start_job("preview")
            self.assertTrue(checked.wait(2))
            outcome = {}

            def request_pause():
                try:
                    outcome["result"] = engine.request_workflow_pause()
                except Exception as exc:
                    outcome["error"] = exc

            requester = threading.Thread(target=request_pause)
            requester.start()
            time.sleep(0.02)
            release.set()
            requester.join(2)
            deadline = time.time() + 2
            while engine.job.get("running") and time.time() < deadline:
                time.sleep(0.01)

            self.assertIsInstance(outcome.get("error"), SafetyError)
            self.assertNotIn("result", outcome)
            self.assertFalse(engine.job.get("running"))
            self.assertIsNone(store.get("workflow_pause_state"))

    def test_pause_is_rejected_once_job_has_entered_error_finalization(self):
        from helper.engine import SafetyError
        from helper.library_engine import LibraryEngine
        from helper.store import Store

        logging_error = threading.Event()
        release_log = threading.Event()

        class ErrorEngine(LibraryEngine):
            def analyze_library(inner_self, _force_sources=False):
                raise RuntimeError("boom")

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))

            def blocked_log(*_args, **_kwargs):
                logging_error.set()
                release_log.wait(2)

            store.log = blocked_log
            engine = ErrorEngine(store)
            engine.start_job("preview")
            self.assertTrue(logging_error.wait(2))
            pause_error = None
            try:
                engine.request_workflow_pause()
            except Exception as exc:
                pause_error = exc
            finally:
                release_log.set()

            deadline = time.time() + 2
            while not store.get("last_run") and time.time() < deadline:
                time.sleep(0.01)
            self.assertIsInstance(pause_error, SafetyError)
            self.assertFalse(engine.job.get("running"))
            self.assertTrue(engine.job.get("error"))
            self.assertTrue(store.get("last_run"))

    def test_restart_recovers_an_interrupted_incremental_scan_as_paused(self):
        from helper.library_engine import LibraryEngine
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            store.set("single_state", {
                "status": "running", "job_kind": "incremental",
                "processed": 12, "library_count": 100,
            })

            LibraryEngine(store)

            self.assertEqual("paused", store.get("single_state")["status"])
            pause = store.get("workflow_pause_state")
            self.assertTrue(pause["active"])
            self.assertEqual("incremental", pause["kind"])

    def test_status_exposes_the_exact_job_that_continue_must_resume(self):
        from helper.store import Store
        from helper.workflow_v0317 import build_workflow_status

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            store.set("workflow_pause_state", {
                "active": True, "kind": "incremental", "message": "新增歌曲检查已暂停",
            })

            state = build_workflow_status(
                store,
                _StoppedScanEngine(),
                {"logged_in": True, "phase": "ready"},
            )["workflow"]["state"]

            self.assertEqual("paused", state["phase"])
            self.assertEqual("incremental", state["resume_kind"])

    def test_continue_chooses_the_persisted_job_kind(self):
        from helper import workflow_state

        chooser = getattr(workflow_state, "resume_job_kind", None)
        self.assertIsNotNone(chooser)
        self.assertEqual("incremental", chooser({"active": True, "kind": "incremental"}))
        self.assertEqual("preview", chooser({"active": True, "kind": "preview"}))
        self.assertEqual("preview", chooser(None))

    def test_run_action_resumes_incremental_work_instead_of_starting_full_analysis(self):
        from helper import workflow_v0317
        from helper.store import Store

        class RecordingEngine:
            def __init__(self):
                self.calls = []

            def start_job(self, kind, **kwargs):
                self.calls.append((kind, kwargs))
                return {"message": "started"}

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            store.set("workflow_pause_state", {
                "active": True, "kind": "incremental", "message": "已暂停",
            })
            engine = RecordingEngine()
            runner = getattr(workflow_v0317, "start_or_resume_workflow", None)

            self.assertIsNotNone(runner)
            self.assertEqual({"message": "started"}, runner(store, engine))
            self.assertEqual([("incremental", {})], engine.calls)


if __name__ == "__main__":
    unittest.main()
