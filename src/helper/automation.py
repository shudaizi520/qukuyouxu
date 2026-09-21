"""Global automatic-task settings with per-profile persistent schedules."""
from __future__ import annotations

import copy
import math
import time
from datetime import datetime, time as datetime_time, timedelta, timezone

from fastapi import Request


AUTOMATION_KEY = "automation_settings_v1"
PROFILE_STATE_KEY = "automation_schedule_v1"
BEIJING = timezone(timedelta(hours=8))
TASK_ORDER = ("library", "smart", "daily")
SMART_INTERVALS = frozenset({3, 5, 7, 10, 14, 20})
DEFAULT_AUTOMATION = {
    "version": 1,
    "revision": 1,
    "migrated": True,
    "daily": {"enabled": True, "hour": 6},
    "smart": {"enabled": False, "interval_days": 7, "hour": 3},
    "library": {"enabled": False, "hour": 0},
}


def _public(value):
    result = copy.deepcopy(value)
    result.pop("migrated", None)
    result.pop("version", None)
    return result


def _legacy_settings(registry, runtime):
    profiles = [row for row in registry.list_public() if row.get("enabled") is not False]
    active_id = registry.active_id()
    active = runtime.engine(active_id).store
    active_daily = dict(active.get("daily_settings", {}) or {})
    daily_enabled = True
    smart_enabled = False
    library_enabled = False
    for profile in profiles:
        store = runtime.engine(profile["id"]).store
        daily_enabled = daily_enabled or bool((store.get("daily_settings", {}) or {}).get("enabled"))
        smart = store.get("smart_mix_settings", {}) or {}
        smart_enabled = smart_enabled or bool(smart.get("auto_enabled") or smart.get("weekly_auto_enabled"))
        library_enabled = library_enabled or bool((store.get("settings", {}) or {}).get("auto_enabled"))
    return {
        **copy.deepcopy(DEFAULT_AUTOMATION),
        "daily": {"enabled": daily_enabled, "hour": int(active_daily.get("hour", 6) or 0)},
        "smart": {"enabled": smart_enabled, "interval_days": 7, "hour": 3},
        "library": {"enabled": library_enabled, "hour": 0},
    }


def automation_settings(base_store, registry, runtime, now=None):
    """Return the single global schedule, migrating legacy profile switches once."""
    del now
    saved = base_store.get(AUTOMATION_KEY)
    if not isinstance(saved, dict) or saved.get("version") != 1:
        saved = _legacy_settings(registry, runtime)
        base_store.set(AUTOMATION_KEY, saved)
    elif saved.get("revision") == 1 and not (saved.get("daily") or {}).get("enabled"):
        # The original untouched schedule defaulted to off, even though every
        # profile showed a daily entry. Explicit user changes increment revision.
        saved = copy.deepcopy(saved)
        saved["daily"]["enabled"] = True
        saved["revision"] = 2
        base_store.set(AUTOMATION_KEY, saved)
    return _public(saved)


def _validate_hour(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 23:
        raise ValueError(f"{label}时间必须是 0～23 点")
    return value


def _validate_enabled(value, label):
    if not isinstance(value, bool):
        raise ValueError(f"{label}开关无效")
    return value


def _validated_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("自动任务设置无效")
    for key in ("daily", "smart", "library"):
        if not isinstance(payload.get(key), dict):
            raise ValueError("自动任务设置不完整")
    interval = payload["smart"].get("interval_days")
    if isinstance(interval, bool) or not isinstance(interval, int) or interval not in SMART_INTERVALS:
        raise ValueError("智能歌单周期无效")
    return {
        "daily": {
            "enabled": _validate_enabled(payload["daily"].get("enabled"), "每日推荐"),
            "hour": _validate_hour(payload["daily"].get("hour"), "每日推荐"),
        },
        "smart": {
            "enabled": _validate_enabled(payload["smart"].get("enabled"), "智能歌单"),
            "interval_days": interval,
            "hour": _validate_hour(payload["smart"].get("hour"), "智能歌单"),
        },
        "library": {
            "enabled": _validate_enabled(payload["library"].get("enabled"), "新增歌曲整理"),
            "hour": _validate_hour(payload["library"].get("hour"), "新增歌曲整理"),
        },
    }


def save_automation_settings(base_store, payload, now=None):
    """Validate and atomically replace the global schedule."""
    del now
    values = _validated_payload(payload)
    previous = base_store.get(AUTOMATION_KEY, {}) or {}
    revision = max(1, int(previous.get("revision") or 0)) + 1
    saved = {"version": 1, "revision": revision, "migrated": True, **values}
    base_store.set(AUTOMATION_KEY, saved)
    return _public(saved)


def next_clock_slot(now, hour):
    local = datetime.fromtimestamp(float(now), BEIJING)
    candidate = datetime.combine(local.date(), datetime_time(hour=int(hour)), tzinfo=BEIJING)
    if candidate.timestamp() <= float(now):
        candidate += timedelta(days=1)
    return candidate.timestamp()


def advance_slot(slot, now, task, settings):
    """Advance from the scheduled slot, skipping missed history without drift."""
    if task in ("daily", "library"):
        return next_clock_slot(now, settings[task]["hour"])
    step = int(settings["smart"]["interval_days"]) * 86400
    candidate = float(slot or 0) + step
    if candidate <= float(now):
        candidate += math.floor((float(now) - candidate) / step + 1) * step
    return candidate


def ensure_profile_schedule(store, settings, now):
    """Create or refresh one profile's schedule without disturbing unchanged tasks."""
    state = copy.deepcopy(store.get(PROFILE_STATE_KEY, {}) or {})
    tasks = state.get("tasks") if isinstance(state.get("tasks"), dict) else {}
    revision_changed = state.get("revision") != settings["revision"]
    for task in TASK_ORDER:
        config = copy.deepcopy(settings[task])
        previous = tasks.get(task) if isinstance(tasks.get(task), dict) else {}
        changed = revision_changed or previous.get("config") != config
        if not config["enabled"]:
            tasks[task] = {"config": config, "next_at": None, "slot": None}
        elif changed or not previous.get("next_at"):
            next_at = next_clock_slot(now, config["hour"])
            tasks[task] = {"config": config, "next_at": next_at, "slot": next_at}
        else:
            tasks[task] = {**previous, "config": config}
    profile = store.get("settings", {}) or {}
    bootstrap_scheduled = bool(state.get("daily_bootstrap_scheduled"))
    if (settings["daily"]["enabled"] and not bootstrap_scheduled
            and not store.get("daily_managed")
            and not store.get("daily_auto_opt_out")
            and not store.get("daily_last_attempt")
            and profile.get("plex_url") and profile.get("plex_token")
            and profile.get("section")):
        tasks["daily"]["next_at"] = float(now)
        tasks["daily"]["slot"] = float(now)
        bootstrap_scheduled = True
    state = {"revision": settings["revision"], "tasks": tasks,
             "daily_bootstrap_scheduled": bootstrap_scheduled}
    store.set(PROFILE_STATE_KEY, state)
    return state


def attach_automation_routes(app, base_store, registry, runtime, body):
    @app.get("/api/automation")
    def get_automation():
        return automation_settings(base_store, registry, runtime)

    @app.post("/api/automation")
    async def set_automation(request: Request):
        data = await body(request)
        saved = save_automation_settings(base_store, data)
        return {**saved, "message": "自动任务已保存"}
