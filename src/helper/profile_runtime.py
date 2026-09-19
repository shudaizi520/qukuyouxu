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
                instance = self.engine_factory(ScopedStore(self.base_store, profile_id))
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
        now = time.time() if now is None else float(now)
        results = []
        for profile in self.registry.list_public():
            if profile.get("enabled") is False:
                continue
            engine = self.engine(profile["id"])
            if engine.job.get("running"):
                continue
            settings = engine.store.get("settings", {}) or {}
            if not settings.get("plex_token"):
                continue
            kind = ""
            try:
                if engine.daily_due(now):
                    kind = "daily"
                    result = self._run_job(engine, kind, engine.daily_auto, now)
                else:
                    library_due = False
                    next_at = float(engine.store.get("library_auto_next_at", 0) or 0)
                    if settings.get("auto_enabled") and next_at <= 0:
                        engine.store.set("library_auto_next_at", next_beijing_midnight(now))
                    elif settings.get("auto_enabled") and now >= next_at:
                        library_due = True
                    if library_due:
                        kind = "library"
                        refresh = getattr(engine, "refresh_new_tracks", None)
                        operation = refresh if refresh else engine.auto
                        result = self._run_job(engine, kind, operation, now)
                    else:
                        from .smart_mix_web import run_smart_mix_auto, smart_mix_auto_due
                        if not smart_mix_auto_due(engine, now):
                            continue
                        kind = "smart_mixes"
                        result = self._run_job(engine, kind, lambda: run_smart_mix_auto(engine, now), now)
                results.append({"profile_id": profile["id"], "kind": kind, "result": result})
            except Exception as exc:
                engine.store.log(str(exc)[:300], "error")
                results.append({"profile_id": profile["id"], "kind": kind or "check", "error": type(exc).__name__})
            finally:
                if kind == "library":
                    engine.store.set_many({
                        "library_auto_last_attempt": now,
                        "library_auto_next_at": next_beijing_midnight(now),
                    })
        return results

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
