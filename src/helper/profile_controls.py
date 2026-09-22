"""The three switches owned by each Plex user/library profile."""
from __future__ import annotations

from .scoped_store import ScopedStore


CONTROL_FIELDS = {
    "learning": ("product_settings", "behavior_enabled"),
    "daily": ("daily_settings", "enabled"),
    "smart": ("smart_mix_settings", "auto_enabled"),
}
MIGRATION_KEY = "profile_controls_v2"


def read_controls(store):
    product = store.get("product_settings", {}) or {}
    daily = store.get("daily_settings", {}) or {}
    smart = store.get("smart_mix_settings", {}) or {}
    return {
        "learning": product.get("behavior_enabled", True) is not False,
        "daily": bool(daily.get("enabled")),
        "smart": bool(smart.get("auto_enabled")),
    }


def write_control(store, key, enabled):
    if key not in CONTROL_FIELDS or type(enabled) is not bool:
        raise ValueError("用户开关无效")
    if key == "daily" and enabled:
        suspension = store.get("daily_auto_suspension") or {}
        if suspension:
            raise ValueError(str(suspension.get("reason") or "每日推荐安全暂停") + "；请先手动预览并发布确认")
        if store.get("daily_auto_opt_out"):
            raise ValueError("该用户已退出每日推荐自动更新；请先手动预览并发布确认")
        if any(row.get("kind") == "daily" and row.get("status") in ("prepared", "uncertain", "restoring")
               for row in store.get("snapshots", []) or [] if isinstance(row, dict)):
            raise ValueError("每日推荐变更尚待核对，不能开启自动更新")
    state_key, field = CONTROL_FIELDS[key]
    value = dict(store.get(state_key, {}) or {})
    changed = key == "learning" and (value.get(field, True) is not False) != enabled
    value[field] = enabled
    if key == "smart":
        value["weekly_auto_enabled"] = enabled  # old reader compatibility
    if changed:
        # Previews depend on learned behavior; history and published lists stay.
        store.set_many({state_key: value, "daily_plan": None, "smart_mix_plans": {}})
    else:
        store.set(state_key, value)
    return read_controls(store)


def migrate_controls(runtime, base_store=None, registry=None):
    """Freeze each old profile's actually executable state exactly once."""
    base = base_store or runtime.base_store
    registry = registry or runtime.registry
    schedule = base.get("automation_settings_v1")
    if not isinstance(schedule, dict) or schedule.get("version") != 1:
        from .automation import _legacy_settings
        schedule = _legacy_settings(registry, runtime)
    global_daily = bool((schedule.get("daily") or {}).get("enabled"))
    global_smart = bool((schedule.get("smart") or {}).get("enabled"))
    for profile in registry.list_public():
        store = ScopedStore(base, profile["id"], registry=registry)
        if store.get(MIGRATION_KEY):
            continue
        daily = bool((store.get("daily_settings", {}) or {}).get("enabled"))
        daily = daily and not store.get("daily_auto_suspension") and not store.get("daily_auto_opt_out")
        daily = daily and not any(row.get("kind") == "daily" and row.get("status") in ("prepared", "uncertain", "restoring")
                                  for row in store.get("snapshots", []) or [] if isinstance(row, dict))
        smart = store.get("smart_mix_settings", {}) or {}
        smart_enabled = bool(smart.get("auto_enabled") or smart.get("weekly_auto_enabled"))
        write_control(store, "daily", global_daily and daily)
        write_control(store, "smart", global_smart and smart_enabled)
        store.set(MIGRATION_KEY, {"version": 2})
