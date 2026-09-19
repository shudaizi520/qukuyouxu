import inspect
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
BEIJING = timezone(timedelta(hours=8))


class _Engine:
    def __init__(self, store, calls):
        self.store = store
        self.calls = calls
        self.stop = threading.Event()
        self.status_lock = threading.Lock()
        self.job = {"running": False, "error": ""}

    def daily_scope(self):
        return f"scope:{self.store.profile_id}"

    def refresh_new_tracks(self):
        self.calls.append((self.store.profile_id, "library"))
        return {"ok": True}

    def daily_auto(self, schedule=None):
        self.calls.append((self.store.profile_id, "daily"))
        return {"ok": True, "schedule": schedule}


def _configured_runtime():
    from helper.profile_runtime import ProfileRuntime
    from helper.profiles import ProfileRegistry
    from helper.scoped_store import ScopedStore
    from helper.store import Store

    temp = tempfile.TemporaryDirectory()
    base = Store(Path(temp.name))
    registry = ProfileRegistry(base)
    calls = []
    runtime = ProfileRuntime(base, registry, engine_factory=lambda store: _Engine(store, calls))
    scoped = ScopedStore(base, "default")
    settings = scoped.get("settings")
    settings.update(plex_url="http://plex:32400", plex_token="secret", section="15")
    scoped.set_many({
        "settings": settings,
        "managed": {"theme": {"id": "category-1"}},
        "smart_mix_managed": {"weekly": {"id": "smart-1"}},
        "daily_managed": {"id": "daily-1", "scope": "scope:default"},
    })
    return temp, base, registry, runtime, scoped, calls


def test_default_global_schedule_has_three_jobs():
    from helper.automation import automation_settings

    temp, base, registry, runtime, _scoped, _calls = _configured_runtime()
    try:
        result = automation_settings(base, registry, runtime, now=1_800_000_000)
    finally:
        temp.cleanup()

    assert result["daily"] == {"enabled": False, "hour": 6}
    assert result["smart"] == {"enabled": False, "interval_days": 7, "hour": 3}
    assert result["library"] == {"enabled": False, "hour": 0}


def test_legacy_profile_switches_migrate_to_global_enabled_state():
    from helper.automation import automation_settings

    temp, base, registry, runtime, scoped, _calls = _configured_runtime()
    try:
        scoped.set("daily_settings", {"enabled": True, "hour": 8})
        result = automation_settings(base, registry, runtime, now=1_800_000_000)
    finally:
        temp.cleanup()

    assert result["daily"] == {"enabled": True, "hour": 8}


def test_same_time_jobs_run_library_then_smart_then_daily():
    from helper.automation import PROFILE_STATE_KEY, save_automation_settings

    temp, base, _registry, runtime, scoped, calls = _configured_runtime()
    now = datetime(2027, 1, 10, 6, tzinfo=BEIJING).timestamp()
    try:
        saved = save_automation_settings(base, {
            "daily": {"enabled": True, "hour": 6},
            "smart": {"enabled": True, "interval_days": 7, "hour": 6},
            "library": {"enabled": True, "hour": 6},
        }, now=now - 60)
        scoped.set(PROFILE_STATE_KEY, {
            "revision": saved["revision"],
            "tasks": {
                "library": {"config": saved["library"], "next_at": now, "slot": now},
                "smart": {"config": saved["smart"], "next_at": now, "slot": now},
                "daily": {"config": saved["daily"], "next_at": now, "slot": now},
            },
        })
        with patch("helper.smart_mix_web.run_smart_mix_auto") as smart:
            smart.side_effect = lambda engine, *_args, **_kwargs: calls.append(
                (engine.store.profile_id, "smart_mixes")
            ) or {"items": {}}
            runtime.run_due(now)
    finally:
        temp.cleanup()

    assert [kind for _profile_id, kind in calls] == ["library", "smart_mixes", "daily"]


def test_missed_slots_run_once_and_advance_to_the_future():
    from helper.automation import PROFILE_STATE_KEY, save_automation_settings

    temp, base, _registry, runtime, scoped, calls = _configured_runtime()
    now = datetime(2027, 1, 10, 12, tzinfo=BEIJING).timestamp()
    try:
        saved = save_automation_settings(base, {
            "daily": {"enabled": False, "hour": 6},
            "smart": {"enabled": False, "interval_days": 7, "hour": 3},
            "library": {"enabled": True, "hour": 0},
        }, now=now - 60)
        scoped.set(PROFILE_STATE_KEY, {
            "revision": saved["revision"],
            "tasks": {
                "library": {
                    "config": saved["library"],
                    "next_at": now - 3 * 86400,
                    "slot": now - 3 * 86400,
                }
            },
        })
        runtime.run_due(now)
        runtime.run_due(now + 1)
        state = scoped.get(PROFILE_STATE_KEY)
    finally:
        temp.cleanup()

    assert calls.count(("default", "library")) == 1
    assert state["tasks"]["library"]["next_at"] > now


def _configure_profile(base, registry, profile_id, *, daily=True):
    from helper.scoped_store import ScopedStore

    if profile_id != "default":
        registry.create(name=profile_id, kind="shared", profile_id=profile_id, token=f"{profile_id}-token")
    scoped = ScopedStore(base, profile_id)
    settings = scoped.get("settings")
    settings.update(plex_url="http://plex:32400", plex_token=f"{profile_id}-token", section="15")
    values = {"settings": settings, "managed": {"theme": {"id": f"{profile_id}-category"}}}
    if daily:
        values["daily_managed"] = {"id": f"{profile_id}-daily", "scope": f"scope:{profile_id}"}
    scoped.set_many(values)
    return scoped


def _set_due(scoped, saved, task, now):
    from helper.automation import PROFILE_STATE_KEY

    scoped.set(PROFILE_STATE_KEY, {
        "revision": saved["revision"],
        "tasks": {
            task: {
                "config": saved[task],
                "next_at": now,
                "slot": now,
            }
        },
    })


def test_ineligible_profile_is_skipped_without_blocking_eligible_profile():
    from helper.automation import save_automation_settings

    temp, base, registry, runtime, owner, calls = _configured_runtime()
    now = datetime(2027, 1, 10, 6, tzinfo=BEIJING).timestamp()
    try:
        owner.set("daily_managed", None)
        friend = _configure_profile(base, registry, "eligible")
        saved = save_automation_settings(base, {
            "daily": {"enabled": True, "hour": 6},
            "smart": {"enabled": False, "interval_days": 7, "hour": 3},
            "library": {"enabled": False, "hour": 0},
        })
        _set_due(owner, saved, "daily", now)
        _set_due(friend, saved, "daily", now)

        runtime.run_due(now)
    finally:
        temp.cleanup()

    assert calls == [("eligible", "daily")]


def test_one_profile_failure_does_not_stop_remaining_profiles():
    from helper.automation import save_automation_settings

    temp, base, registry, runtime, owner, calls = _configured_runtime()
    now = datetime(2027, 1, 10, 6, tzinfo=BEIJING).timestamp()
    try:
        friend = _configure_profile(base, registry, "second")
        saved = save_automation_settings(base, {
            "daily": {"enabled": False, "hour": 6},
            "smart": {"enabled": False, "interval_days": 7, "hour": 3},
            "library": {"enabled": True, "hour": 6},
        })
        _set_due(owner, saved, "library", now)
        _set_due(friend, saved, "library", now)
        owner_engine = runtime.engine("default")
        owner_engine.refresh_new_tracks = lambda: (_ for _ in ()).throw(RuntimeError("owner failed"))

        results = runtime.run_due(now)
    finally:
        temp.cleanup()

    assert any(row.get("error") for row in results)
    assert ("second", "library") in calls


def test_smart_mix_partial_failure_keeps_original_slot_and_uses_retry_time():
    from helper.automation import PROFILE_STATE_KEY, save_automation_settings

    temp, base, _registry, runtime, scoped, _calls = _configured_runtime()
    now = datetime(2027, 1, 10, 6, tzinfo=BEIJING).timestamp()
    retry_at = now + 900
    try:
        saved = save_automation_settings(base, {
            "daily": {"enabled": False, "hour": 6},
            "smart": {"enabled": True, "interval_days": 7, "hour": 6},
            "library": {"enabled": False, "hour": 0},
        })
        _set_due(scoped, saved, "smart", now)

        def partial_failure(engine, *_args, **_kwargs):
            engine.store.set("smart_mix_settings", {
                "auto_retry_state": {"weekly": {"failures": 1, "next_retry_at": retry_at}}
            })
            return {"items": {"weekly": {"status": "error", "error": "temporary"}}}

        with patch("helper.smart_mix_web.run_smart_mix_auto", side_effect=partial_failure):
            runtime.run_due(now)
        task = scoped.get(PROFILE_STATE_KEY)["tasks"]["smart"]
    finally:
        temp.cleanup()

    assert task["next_at"] == retry_at
    assert task["slot"] == now
    assert task["retry_kinds"] == ["weekly"]


def test_automation_post_route_uses_framework_request_injection():
    from helper.automation import attach_automation_routes

    class Routes:
        def __init__(self):
            self.handlers = {}

        def get(self, path):
            return lambda handler: self.handlers.setdefault(("GET", path), handler) or handler

        def post(self, path):
            def register(handler):
                self.handlers[("POST", path)] = handler
                return handler
            return register

    routes = Routes()
    attach_automation_routes(routes, object(), object(), object(), lambda request: {})
    parameter = inspect.signature(routes.handlers[("POST", "/api/automation")]).parameters["request"]

    assert parameter.annotation == "Request"
