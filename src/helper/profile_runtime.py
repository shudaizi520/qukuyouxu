"""Fixed-scope engines and one serial scheduler for all Plex profiles."""
from __future__ import annotations

import threading
import time
from datetime import datetime, time as datetime_time, timedelta, timezone

from .scoped_store import ScopedStore


BEIJING = timezone(timedelta(hours=8))


def next_beijing_midnight(now):
    local = datetime.fromtimestamp(float(now), BEIJING)
    next_date = (local + timedelta(days=1)).date()
    return datetime.combine(next_date, datetime_time.min, tzinfo=BEIJING).timestamp()


def current_qq_status(app, engine):
    """Use the workflow authorization shown by the library page everywhere."""
    manager = getattr(getattr(app, "state", None), "qq_auth", None)
    if manager is not None and hasattr(manager, "public_status"):
        return manager.public_status()
    legacy = getattr(engine, "qq_auth", None)
    if legacy is not None and hasattr(legacy, "status"):
        return legacy.status()
    return {"logged_in": False, "phase": "unavailable", "message": ""}


class ProfileRuntime:
    def __init__(self, base_store, registry, engine_factory=None):
        if engine_factory is None:
            from .library_engine import LibraryEngine
            engine_factory = LibraryEngine
        self.base_store = base_store
        self.registry = registry
        self.engine_factory = engine_factory
        self._engines = {}
        self._lock = threading.RLock()
        self.operation_gate = threading.Lock()
        self.job_gate = threading.Lock()
        self.stop = threading.Event()

    def engine(self, profile_id):
        self.registry.get(profile_id)
        with self._lock:
            if profile_id not in self._engines:
                instance = self.engine_factory(
                    ScopedStore(self.base_store, profile_id, registry=self.registry)
                )
                # Every profile shares one mutation gate. This keeps the active profile
                # stable for the full duration of an operation that uses ActiveEngineProxy.
                instance.gate = self.operation_gate
                instance.job_gate = self.job_gate
                self._engines[profile_id] = instance
            return self._engines[profile_id]

    def _run_job(self, engine, kind, operation, now):
        from .engine import safe_error

        if not self.job_gate.acquire(blocking=False):
            from .engine import SafetyError
            raise SafetyError("已有任务在执行，请等完成")
        lock = getattr(engine, "status_lock", self._lock)
        with lock:
            engine.job = {
                "running": True, "kind": kind, "message": "自动任务运行中",
                "error": "", "started_at": now,
            }
        try:
            result = operation()
            with lock:
                engine.job["message"] = "自动任务已完成"
            return result
        except Exception as exc:
            with lock:
                engine.job["error"] = safe_error(exc)
                engine.job["message"] = "自动任务失败"
            raise
        finally:
            finished_at = time.time()
            try:
                engine.store.set("last_run", finished_at)
            finally:
                with lock:
                    engine.job["running"] = False
                    engine.job["finished_at"] = finished_at
                self.job_gate.release()

    def run_due(self, now=None):
        from .automation import (
            PROFILE_STATE_KEY,
            TASK_ORDER,
            advance_slot,
            automation_settings,
            ensure_profile_schedule,
        )

        now = time.time() if now is None else float(now)
        if self.job_gate.locked():
            return []
        settings = automation_settings(self.base_store, self.registry, self, now=now)
        results = []
        profiles = [row for row in self.registry.list_public() if row.get("enabled") is not False]
        scheduled_profiles = []
        for profile in profiles:
            engine = self.engine(profile["id"])
            state = ensure_profile_schedule(engine.store, settings, now)
            scheduled_profiles.append((profile, engine, state))
        for task in TASK_ORDER:
            if not settings[task]["enabled"]:
                continue
            for profile, engine, state in scheduled_profiles:
                profile_settings = engine.store.get("settings", {}) or {}
                if engine.job.get("running") or not profile_settings.get("plex_token"):
                    continue
                scheduled = state["tasks"][task]
                if float(scheduled.get("next_at") or 0) > now:
                    continue
                if not self._eligible_for_task(engine, task):
                    scheduled["next_at"] = advance_slot(scheduled.get("slot"), now, task, settings)
                    scheduled["slot"] = scheduled["next_at"]
                    engine.store.set(PROFILE_STATE_KEY, state)
                    continue
                kind = "smart_mixes" if task == "smart" else task
                result = None
                smart_normal_due = task == "smart" and float(scheduled.get("slot") or 0) <= now
                try:
                    operation = self._scheduled_operation(engine, task, scheduled, settings, now)
                    result = self._run_job(engine, kind, operation, now)
                    results.append({"profile_id": profile["id"], "kind": kind, "result": result})
                except Exception as exc:
                    engine.store.log(str(exc)[:300], "error")
                    results.append({"profile_id": profile["id"], "kind": kind, "error": type(exc).__name__})
                finally:
                    if task == "smart":
                        retry = self._smart_retry(engine, result)
                        dispatch_slot = float((result or {}).get("slot") or scheduled.get("slot") or now) if isinstance(result, dict) else float(scheduled.get("slot") or now)
                        if smart_normal_due:
                            scheduled["slot"] = advance_slot(scheduled.get("slot"), now, task, settings)
                        if retry:
                            scheduled["retry_at"] = retry["next_at"]
                            scheduled["retry_kinds"] = retry["kinds"]
                            scheduled["retry_slot"] = dispatch_slot
                        else:
                            scheduled.pop("retry_at", None)
                            scheduled.pop("retry_kinds", None)
                            scheduled.pop("retry_slot", None)
                        candidates = [float(scheduled.get("slot") or 0), float(scheduled.get("retry_at") or 0)]
                        scheduled["next_at"] = min(value for value in candidates if value > 0)
                    elif isinstance(result, dict) and result.get("status") == "deferred":
                        scheduled["next_at"] = max(now + 60, float(result.get("retry_at") or now + 300))
                    else:
                        scheduled["next_at"] = advance_slot(scheduled.get("slot"), now, task, settings)
                        scheduled["slot"] = scheduled["next_at"]
                    engine.store.set(PROFILE_STATE_KEY, state)
        return results

    @staticmethod
    def _eligible_for_task(engine, task):
        keys = {
            "library": "managed",
            "smart": "smart_mix_managed",
            "daily": "daily_managed",
        }
        if engine.store.get(keys[task], {}) or {}:
            return True
        if task == "library":
            from .external_store import ExternalRepository
            profile_id = str(getattr(engine.store, "profile_id", "default") or "default")
            return ExternalRepository(engine.store).has_managed(profile_id)
        return False

    @staticmethod
    def _scheduled_operation(engine, task, scheduled, settings, now):
        if task == "library":
            return getattr(engine, "refresh_new_tracks", None) or engine.auto
        if task == "daily":
            def run_daily():
                import inspect
                parameters = inspect.signature(engine.daily_auto).parameters
                if "scheduled" in parameters and "now" in parameters:
                    return engine.daily_auto(schedule=settings["daily"], scheduled=True, now=now)
                return engine.daily_auto(schedule=settings["daily"])
            return run_daily

        from .smart_mix_web import run_smart_mix_auto

        managed = engine.store.get("smart_mix_managed", {}) or {}
        normal_due = float(scheduled.get("slot") or 0) <= float(now)
        due_kinds = list(managed) if normal_due else list(scheduled.get("retry_kinds") or [])
        dispatch_slot = scheduled.get("slot") if normal_due else scheduled.get("retry_slot")
        return lambda: run_smart_mix_auto(
            engine,
            now,
            due_kinds=due_kinds,
            slot=dispatch_slot,
        )

    @staticmethod
    def _smart_retry(engine, result):
        items = (result or {}).get("items", {}) if isinstance(result, dict) else {}
        failed = [kind for kind, row in items.items() if row.get("status") == "error"]
        if not failed:
            return None
        retry_state = (engine.store.get("smart_mix_settings", {}) or {}).get("auto_retry_state", {}) or {}
        times = [float((retry_state.get(kind) or {}).get("next_retry_at") or 0) for kind in failed]
        times = [value for value in times if value > 0]
        return {"kinds": failed, "next_at": min(times)} if times else None

    def scheduler(self):
        while not self.stop.wait(60):
            self.run_due()

    def close(self):
        self.stop.set()
        with self._lock:
            for engine in self._engines.values():
                engine.stop.set()


class ActiveEngineProxy:
    def __init__(self, runtime, registry):
        object.__setattr__(self, "runtime", runtime)
        object.__setattr__(self, "registry", registry)

    def _engine(self):
        return self.runtime.engine(self.registry.active_id())

    def __getattr__(self, name):
        return getattr(self._engine(), name)

    def __setattr__(self, name, value):
        if name in ("runtime", "registry"):
            object.__setattr__(self, name, value)
        else:
            setattr(self._engine(), name, value)
