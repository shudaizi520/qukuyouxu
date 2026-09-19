import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


class _Engine:
    def __init__(self, store, calls):
        import threading
        self.store = store
        self.calls = calls
        self.stop = threading.Event()
        self.job = {"running": False}
        self.status_lock = threading.Lock()
        self.job_during_operation = None

    def daily_auto(self, schedule=None):
        self.job_during_operation = dict(self.job)
        self.calls.append(("daily", self.store.profile_id, self.store.get("settings")["plex_token"]))
        return {"ok": True}

    def auto(self):
        self.job_during_operation = dict(self.job)
        self.calls.append(("library", self.store.profile_id, self.store.get("settings")["plex_token"]))
        return {"ok": True}


class ProfileRuntimeV040Tests(unittest.TestCase):
    def setUp(self):
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ScopedStore
        from helper.store import Store

        self.temp = tempfile.TemporaryDirectory()
        self.base = Store(Path(self.temp.name))
        self.registry = ProfileRegistry(self.base)
        self.registry.create(name="Friend", kind="shared", profile_id="friend-42", token="friend-secret")
        owner = ScopedStore(self.base, "default")
        friend = ScopedStore(self.base, "friend-42")
        owner_cfg = owner.get("settings")
        owner_cfg["plex_token"] = "owner-secret"
        owner.set_many({"settings": owner_cfg, "daily_history": ["owner-history"]})
        friend_cfg = friend.get("settings")
        friend_cfg["plex_token"] = "friend-secret"
        friend.set_many({"settings": friend_cfg, "daily_history": ["friend-history"]})

    def tearDown(self):
        self.temp.cleanup()

    def enable_due_daily(self, runtime, profile_ids, now):
        from helper.automation import PROFILE_STATE_KEY, save_automation_settings
        from helper.scoped_store import ScopedStore

        saved = save_automation_settings(self.base, {
            "daily": {"enabled": True, "hour": 6},
            "smart": {"enabled": False, "interval_days": 7, "hour": 3},
            "library": {"enabled": False, "hour": 0},
        })
        for profile_id in profile_ids:
            scoped = ScopedStore(self.base, profile_id)
            scoped.set("daily_managed", {"id": f"daily-{profile_id}"})
            scoped.set(PROFILE_STATE_KEY, {
                "revision": saved["revision"],
                "tasks": {
                    "daily": {
                        "config": saved["daily"],
                        "next_at": now,
                        "slot": now,
                    }
                },
            })

    def test_fixed_engines_keep_tokens_and_history_isolated(self):
        from helper.profile_runtime import ProfileRuntime

        calls = []
        runtime = ProfileRuntime(self.base, self.registry, engine_factory=lambda store: _Engine(store, calls))
        owner = runtime.engine("default")
        friend = runtime.engine("friend-42")
        self.assertEqual("owner-secret", owner.store.get("settings")["plex_token"])
        self.assertEqual("friend-secret", friend.store.get("settings")["plex_token"])
        self.assertEqual(["owner-history"], owner.store.get("daily_history"))
        self.assertEqual(["friend-history"], friend.store.get("daily_history"))
        self.assertIs(owner.gate, friend.gate)

    def test_idle_run_does_not_call_any_profile_operation(self):
        from helper.profile_runtime import ProfileRuntime

        calls = []
        runtime = ProfileRuntime(self.base, self.registry, engine_factory=lambda store: _Engine(store, calls))
        result = []
        for now in range(60, 601, 60):
            result.extend(runtime.run_due(now=now))
        self.assertEqual([], calls)
        self.assertEqual([], result)

    def test_due_profiles_run_serially_in_registry_order(self):
        from helper.profile_runtime import ProfileRuntime
        calls = []
        runtime = ProfileRuntime(self.base, self.registry, engine_factory=lambda store: _Engine(store, calls))
        self.enable_due_daily(runtime, ["default", "friend-42"], 100)
        result = runtime.run_due(now=100)
        self.assertEqual(["default", "friend-42"], [row["profile_id"] for row in result])
        self.assertEqual(["default", "friend-42"], [row[1] for row in calls])

    def test_scheduled_operation_publishes_running_and_finished_job_state(self):
        from helper.profile_runtime import ProfileRuntime
        calls = []
        runtime = ProfileRuntime(self.base, self.registry, engine_factory=lambda store: _Engine(store, calls))
        self.enable_due_daily(runtime, ["default"], 1_000)

        with patch("helper.profile_runtime.time.time", return_value=1_007):
            result = runtime.run_due(now=1_000)
        engine = runtime.engine("default")

        self.assertEqual("daily", result[0]["kind"])
        self.assertTrue(engine.job_during_operation["running"])
        self.assertEqual("daily", engine.job_during_operation["kind"])
        self.assertEqual(1_000, engine.job_during_operation["started_at"])
        self.assertFalse(engine.job["running"])
        self.assertEqual(1_007, engine.job["finished_at"])
        self.assertEqual(1_007, engine.store.get("last_run"))
        self.assertEqual("", engine.job["error"])

    def test_scheduled_failure_is_visible_in_job_state_and_result(self):
        from helper.profile_runtime import ProfileRuntime
        from helper.scoped_store import ScopedStore

        class FailingEngine(_Engine):
            def daily_auto(self, schedule=None):
                self.job_during_operation = dict(self.job)
                raise RuntimeError("temporary failure")

        runtime = ProfileRuntime(self.base, self.registry, engine_factory=lambda store: FailingEngine(store, []))
        self.enable_due_daily(runtime, ["default"], 2_000)

        with patch("helper.profile_runtime.time.time", return_value=2_009):
            result = runtime.run_due(now=2_000)
        engine = runtime.engine("default")

        self.assertTrue(engine.job_during_operation["running"])
        self.assertFalse(engine.job["running"])
        self.assertEqual(2_009, engine.job["finished_at"])
        self.assertEqual(2_009, engine.store.get("last_run"))
        self.assertIn("RuntimeError", engine.job["error"])
        self.assertNotIn("temporary failure", engine.job["error"])
        self.assertEqual("RuntimeError", result[0]["error"])

    def test_active_engine_proxy_follows_admin_selection(self):
        from helper.profile_runtime import ActiveEngineProxy, ProfileRuntime

        calls = []
        runtime = ProfileRuntime(self.base, self.registry, engine_factory=lambda store: _Engine(store, calls))
        active = ActiveEngineProxy(runtime, self.registry)
        self.assertEqual("default", active.store.profile_id)
        self.registry.select("friend-42")
        self.assertEqual("friend-42", active.store.profile_id)

    def test_pinned_request_keeps_the_original_profile_after_global_selection_changes(self):
        from helper.profile_runtime import ActiveEngineProxy, ProfileRuntime

        calls = []
        runtime = ProfileRuntime(self.base, self.registry, engine_factory=lambda store: _Engine(store, calls))
        active = ActiveEngineProxy(runtime, self.registry)

        with self.registry.fixed_active():
            self.assertEqual("default", active.store.profile_id)
            self.registry.select("friend-42")
            self.assertEqual("default", active.store.profile_id)
        self.assertEqual("friend-42", active.store.profile_id)

    def test_only_one_profile_can_admit_a_background_job_at_a_time(self):
        from helper.engine import Engine, SafetyError
        from helper.profile_runtime import ProfileRuntime

        release = threading.Event()
        runtime = ProfileRuntime(self.base, self.registry, engine_factory=Engine)
        owner = runtime.engine("default")
        friend = runtime.engine("friend-42")
        owner.preview = lambda *_args, **_kwargs: release.wait(2)
        friend.preview = lambda *_args, **_kwargs: None

        owner.start_job("preview")
        try:
            with self.assertRaisesRegex(SafetyError, "已有任务"):
                friend.start_job("preview")
        finally:
            release.set()
            deadline = time.time() + 2
            while owner.job["running"] and time.time() < deadline:
                time.sleep(0.01)


if __name__ == "__main__":
    unittest.main()
