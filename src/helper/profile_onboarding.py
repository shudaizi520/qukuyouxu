"""Durable, profile-local first publication after a Plex user/library is added."""
from __future__ import annotations

import time

from .engine import safe_error


STATE_KEY = "profile_prepare_v1"
SMART_DEFAULTS = {
    "weekly": {"size": 30, "recent_days": 30},
    "time_capsule": {"size": 30, "stale_days": 180},
    "recent_additions": {"size": 30, "added_days": 90},
}
KINDS = ("daily", *SMART_DEFAULTS)


def queue_new_profile(runtime, profile_id):
    profile = runtime.registry.get(profile_id)
    if profile.get("enabled") is False:
        raise ValueError("该用户已停用")
    store = runtime.engine(profile_id).store
    saved = store.get(STATE_KEY)
    if isinstance(saved, dict):
        return saved
    state = {"status": "pending", "completed": [], "errors": {}}
    store.set(STATE_KEY, state)
    return state


def _already_published(store, kind):
    if kind == "daily":
        return bool((store.get("daily_managed") or {}).get("id"))
    return bool(((store.get("smart_mix_managed", {}) or {}).get(kind) or {}).get("id"))


def prepare_new_profile(runtime, profile_id):
    """Retry unfinished kinds only; one failed playlist cannot block the others."""
    from .smart_mix_web import preview_smart_mix, publish_smart_mix

    engine = runtime.engine(profile_id)
    store = engine.store
    state = dict(store.get(STATE_KEY) or queue_new_profile(runtime, profile_id))
    if (runtime.registry.get(profile_id).get("enabled") is False
            or store.get("profile_removal_v1")):
        return state
    if state.get("status") == "done":
        return state
    if not runtime.job_gate.acquire(blocking=False):
        return state
    try:
        state["status"] = "running"
        state["completed"] = list(state.get("completed") or [])
        state["errors"] = dict(state.get("errors") or {})
        store.set(STATE_KEY, state)
        for kind in KINDS:
            if kind in state["completed"]:
                continue
            try:
                if _already_published(store, kind):
                    pass
                else:
                    plan = (engine.preview_daily() if kind == "daily" else
                            preview_smart_mix(engine, kind, SMART_DEFAULTS[kind]))
                    if not plan.get("items"):
                        state["errors"][kind] = "waiting_for_data"
                        continue
                    if plan.get("blocked"):
                        state["errors"][kind] = "needs_attention"
                        continue
                    if kind == "daily":
                        engine.publish_daily(plan["id"])
                    else:
                        publish_smart_mix(engine, plan["id"])
                state["completed"].append(kind)
                state["errors"].pop(kind, None)
            except Exception as exc:
                state["errors"][kind] = safe_error(exc)
            finally:
                store.set(STATE_KEY, state)
        # A verified owner may already have category copies to push. No scan runs here.
        try:
            from .library_sharing import owner_for_recipient, sync_recipient
            owner_id = owner_for_recipient(runtime, profile_id)
            if owner_id and (runtime.engine(owner_id).store.get("managed", {}) or {}):
                with runtime.operation_gate:
                    result = sync_recipient(runtime, owner_id, profile_id)
                if result.get("errors"):
                    state["errors"]["category"] = "needs_attention"
                else:
                    state["errors"].pop("category", None)
        except Exception as exc:
            state["errors"]["category"] = safe_error(exc)
        state["status"] = ("done" if not state["errors"] else
                           "waiting_for_data" if set(state["errors"].values()) == {"waiting_for_data"}
                           else "needs_attention")
        state["next_retry_at"] = (0 if state["status"] == "done" else
                                  time.time() + (21600 if state["status"] == "waiting_for_data" else 300))
        store.set(STATE_KEY, state)
        return state
    finally:
        runtime.job_gate.release()
