"""Bounded, token-free status projections for frequent UI polling."""
from __future__ import annotations

import time

from . import __version__
from .automation import PROFILE_STATE_KEY
from .engine import digest
from .extra_web import extensions_status
from .match import CONVERSION_AVAILABLE
from .profile_runtime import current_qq_status
from .smart_mix_web import smart_mix_settings


def _public_settings(store):
    settings = store.get("settings")
    return {
        **{key: value for key, value in settings.items() if key != "plex_token"},
        "token_present": bool(settings.get("plex_token")),
    }


def _connection_scope(settings):
    return digest([settings.get("plex_url", ""), settings.get("plex_token", "")])


def _public_connection(store):
    cached = store.get("plex_connection")
    if not cached:
        return None
    settings = store.get("settings")
    if not settings.get("plex_url") or not settings.get("plex_token"):
        return None
    if cached.get("scope") != _connection_scope(settings):
        pending = store.get("plex_login_pending", {}) or {}
        if cached.get("source") not in (None, "official_login") or pending.get("status") != "connected":
            return None
    return cached.get("result")


def _public_name_plan(store):
    plan = store.get("name_plan")
    if not plan:
        return None
    return {
        **{key: plan.get(key) for key in ("id", "created_at", "applied", "result")},
        "groups": [
            {
                **{
                    key: group.get(key)
                    for key in (
                        "category_id", "playlist_id", "old_title", "new_title",
                        "blocked", "action",
                    )
                },
                "count": len((group.get("before") or {}).get("items", [])),
            }
            for group in plan.get("groups", [])
        ],
    }


def scheduler_health(runtime, store, engine, now=None):
    now = time.time() if now is None else float(now)
    health = runtime.scheduler_status(now=now)
    heartbeat = health.get("heartbeat_at")
    stale_after = max(180.0, float(getattr(runtime, "scheduler_interval", 60.0)) * 3)
    unhealthy = (
        not health.get("alive")
        or heartbeat is None
        or now - float(heartbeat) > stale_after
    )
    schedule = store.get(PROFILE_STATE_KEY, {}) or {}
    smart_settings = smart_mix_settings(store)
    smart_paused = dict(smart_settings.get("auto_paused_reasons") or {})
    smart_retry = dict(smart_settings.get("auto_retry_state") or {})
    attention = [
        {"kind": str(kind), "message": str(message)[:300]}
        for kind, message in smart_paused.items()
        if message and kind not in smart_retry
        or message and any(marker in str(message) for marker in (
            "写入结果待核对", "禁止重复写入", "不会覆盖", "写入前",
        ))
    ]
    attention_kinds = {row["kind"] for row in attention}
    retryable_smart = any(kind not in attention_kinds for kind in smart_retry)
    retry_times = [
        float(task.get("retry_at") or 0)
        for task_name, task in (schedule.get("tasks") or {}).items()
        if isinstance(task, dict)
        and float(task.get("retry_at") or 0) > 0
        and not (task_name == "smart" and attention_kinds and not retryable_smart)
    ]
    next_retry_at = min(retry_times) if retry_times else None
    if unhealthy:
        state = "error"
    elif (getattr(engine, "job", {}) or {}).get("running"):
        state = "running"
    elif attention:
        state = "attention"
    elif next_retry_at is not None:
        state = "retrying"
    else:
        state = "normal"
    return {
        "state": state,
        "heartbeat_at": heartbeat,
        "last_error_at": health.get("last_error_at"),
        "last_error": str(health.get("last_error") or "")[:300],
        "next_retry_at": next_retry_at,
        "attention": attention,
    }


def build_status_summary(app, store, engine, runtime, now=None):
    now = time.time() if now is None else float(now)
    sources = [
        {key: value for key, value in source.items() if key != "csv_tracks"}
        for source in store.get("sources")
    ]
    plan = store.get("plan")
    summary = (
        {
            key: plan.get(key)
            for key in (
                "id", "created_at", "library_count", "covered", "coverage",
                "applied", "result",
            )
        }
        if plan else None
    )
    return {
        "version": __version__,
        "settings": _public_settings(store),
        "sources": sources,
        "job": dict(engine.job),
        "managed": store.get("managed"),
        "conversion": CONVERSION_AVAILABLE,
        "last_run": store.get("last_run"),
        "summary": summary,
        "plex_connection": _public_connection(store),
        "qq_auth": current_qq_status(app, engine),
        "single": engine.single_status() if hasattr(engine, "single_status") else {"running": False},
        "qq_tags": store.get("qq_tags", []),
        "name_plan": _public_name_plan(store),
        "scheduler": scheduler_health(runtime, store, engine, now),
        **extensions_status(store, now=now),
    }


def build_status_details(store):
    from .restart import normalize_daily_text

    events = []
    for row in list(store.get("events") or [])[-30:]:
        if isinstance(row, dict):
            events.append({**row, "message": normalize_daily_text(row.get("message"))})
        else:
            events.append(normalize_daily_text(row))
    return {"events": events}
