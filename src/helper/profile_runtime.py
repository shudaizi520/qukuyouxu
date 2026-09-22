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
        self.wake = threading.Event()

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
                instance.profile_runtime = self
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
        from .profile_onboarding import prepare_new_profile, STATE_KEY as PREPARE_KEY
        for profile in self.registry.list_public(enabled_only=True):
            state = ScopedStore(self.base_store, profile["id"], registry=self.registry).get(PREPARE_KEY)
            if (isinstance(state, dict) and state.get("status") != "done"
                    and float(state.get("next_retry_at") or 0) <= now):
                prepare_new_profile(self, profile["id"])
        settings = automation_settings(self.base_store, self.registry, self, now=now)
        results = []
        profiles = [row for row in self.registry.list_public() if row.get("enabled") is not False]
        scheduled_profiles = []
        for profile in profiles:
            engine = self.engine(profile["id"])
            state = ensure_profile_schedule(engine.store, settings, now)
            scheduled_profiles.append((profile, engine, state))
        for task in TASK_ORDER:
            if task == "library" and not settings[task]["enabled"]:
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
        self.sync_library_shares_due(now)
        return results

    def sync_library_shares_due(self, now):
        """Default same-library category copies, independent of QQ/scan schedules."""
        from .engine import digest, safe_error
        from .library_sharing import REVISIONS_KEY, STATE_KEY, owner_for_recipient, recover_owner_revisions, sync_recipient

        if self.job_gate.locked() or self.operation_gate.locked():
            return
        pairs = []
        for profile in self.registry.list_public(enabled_only=True):
            owner_id = owner_for_recipient(self, profile["id"])
            if not owner_id:
                continue
            owner_engine = self.engine(owner_id)
            if not callable(getattr(owner_engine, "plex_factory", None)):
                continue
            managed = owner_engine.store.get("managed", {}) or {}
            revisions = owner_engine.store.get(REVISIONS_KEY, {}) or {}
            pending_revision = any(
                (row.get("targets") or {}).get(profile["id"]) == "pending"
                for history in revisions.values() for row in history
            )
            child_store = self.engine(profile["id"]).store
            shared = child_store.get("managed", {}) or {}
            if not managed and not pending_revision and not any(
                isinstance(row, dict) and row.get("shared_from") == owner_id
                for row in shared.values()
            ):
                continue
            sources = {str(row.get("id")): row for row in owner_engine.store.get("sources", []) or []}
            manifest = [
                (key, row.get("id"), row.get("title"), row.get("fingerprint"),
                 sources.get(key, {}).get("enabled", True))
                for key, row in sorted(managed.items()) if isinstance(row, dict)
            ]
            revision = digest([manifest, revisions])
            share = child_store.get(STATE_KEY, {}) or {}
            checked_at = float(share.get("checked_at") or 0)
            if not pending_revision and share.get("owner_digest") == revision and 0 <= now - checked_at < 900:
                continue
            pairs.append((owner_id, profile["id"], revision, child_store))
        if not pairs or not self.job_gate.acquire(blocking=False):
            return
        if not self.operation_gate.acquire(blocking=False):
            self.job_gate.release()
            return
        try:
            for owner_id in {owner_id for owner_id, _recipient_id, _revision, _store in pairs}:
                recover_owner_revisions(self, owner_id)
            for owner_id, recipient_id, revision, store in pairs:
                try:
                    outcome = sync_recipient(self, owner_id, recipient_id, now=now)
                    share = dict(store.get(STATE_KEY, {}) or {})
                    share["owner_digest"] = revision
                    share["last_result"] = outcome
                    store.set(STATE_KEY, share)
                except Exception as exc:
                    store.log("同库歌单同步已暂停：" + safe_error(exc), "error")
                    share = dict(store.get(STATE_KEY, {}) or {})
                    share.update(checked_at=now, owner_digest=revision,
                                 last_result={"errors": [safe_error(exc)]})
                    store.set(STATE_KEY, share)
        finally:
            self.operation_gate.release()
            self.job_gate.release()

    @staticmethod
    def _eligible_for_task(engine, task):
        from .profile_controls import read_controls
        controls = read_controls(engine.store)
        if task == "library":
            registry = getattr(engine.store, "registry", None)
            profile_id = str(getattr(engine.store, "profile_id", "default") or "default")
            if registry is not None and registry.get(profile_id).get("kind") != "owner":
                return False
            from .external_store import ExternalRepository
            managed = engine.store.get("managed", {}) or {}
            own_categories = any(
                not isinstance(record, dict) or not record.get("shared_from")
                for record in managed.values()
            )
            return own_categories or ExternalRepository(engine.store).has_managed(profile_id)
        if task == "daily":
            if not controls["daily"]:
                return False
            if engine.store.get("daily_auto_opt_out"):
                return False
            if engine.store.get("daily_managed"):
                return True
            if any(row.get("kind") == "daily_remove" and row.get("status") == "applied"
                   for row in engine.store.get("snapshots", []) or [] if isinstance(row, dict)):
                return False
            settings = engine.store.get("settings", {}) or {}
            return bool(
                settings.get("plex_url") and settings.get("plex_token")
                and settings.get("section")
            )
        keys = {
            "smart": "smart_mix_managed",
        }
        if controls["smart"] and (engine.store.get(keys[task], {}) or {}):
            return True
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
        while not self.stop.is_set():
            self.wake.wait(60)
            self.wake.clear()
            if self.stop.is_set():
                break
            self.run_due()

    def close(self):
        self.stop.set()
        self.wake.set()
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
