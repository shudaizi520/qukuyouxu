"""Guarded preview/publish lifecycle for application-owned smart mixes."""
from __future__ import annotations

import time
import uuid
from contextlib import nullcontext
from datetime import datetime, time as datetime_time, timedelta, timezone
from fastapi import Request

from .behavior import profile_behavior_subject
from .engine import SafetyError, fingerprint, safe_error, state_ids, track_fingerprint
from .playlist_sync import has_exact_members, sync_owned_items
from .smart_mixes import KINDS, select_smart_mix


PLAN_TTL = 1800
SMART_MIX_POLICY = "v0.4.19-childrens-isolation"
MANAGED_KEY = "smart_mix_managed"
PLANS_KEY = "smart_mix_plans"
REMOVED_KEY = "smart_mix_removed"
SETTINGS_KEY = "smart_mix_settings"
BEIJING = timezone(timedelta(hours=8))
DEFAULT_SMART_MIX_SETTINGS = {
    "auto_enabled": False,
    "auto_last_slots": {},
    "auto_last_success_at": {},
    "auto_paused_reasons": {},
    "auto_retry_state": {},
    "weekly_auto_enabled": False,
    "weekly_last_slot": 0,
    "weekly_last_success_at": 0,
    "weekly_paused_reason": "",
}
AUTO_KINDS = ("weekly", "time_capsule", "recent_additions")
AUTO_RETRY_DELAYS = (15 * 60, 60 * 60, 6 * 60 * 60, 24 * 60 * 60)


def _configured(engine):
    cfg = engine.store.get("settings") or {}
    if not cfg.get("plex_url") or not cfg.get("plex_token") or not cfg.get("section"):
        raise SafetyError("先连接 Plex 并选择音乐资料库")
    return cfg


def _history_events(engine):
    """Use only already-isolated account history; never borrow another profile."""
    if (engine.store.get("product_settings", {}) or {}).get("behavior_enabled", True) is False:
        return []
    cached = engine.store.get("plex_history_cache", {}) or {}
    subject = profile_behavior_subject(engine.store)
    expected = str(subject.get("account_id") or "")
    username = str(subject.get("username") or "").casefold()
    local_account_id = str(cached.get("account_id") or "")
    scope = str(cached.get("scope") or "")
    same_profile = (
        expected
        and str(cached.get("profile_account_id") or "") == expected
        and (
            not username
            or str(cached.get("profile_username") or "").casefold() == username
        )
    )
    if (
        same_profile
        and local_account_id
        and scope == f"{engine.daily_scope()}:{engine.store.get('settings', {}).get('section')}:{local_account_id}"
    ):
        return list(cached.get("events") or [])
    return []


def _plan_map(store):
    value = store.get(PLANS_KEY, {}) or {}
    return value if isinstance(value, dict) else {}


def smart_mix_settings(store):
    value = store.get(SETTINGS_KEY, {}) or {}
    settings = {**DEFAULT_SMART_MIX_SETTINGS, **(value if isinstance(value, dict) else {})}
    settings["auto_enabled"] = bool(settings.get("auto_enabled") or settings.get("weekly_auto_enabled"))
    settings["auto_last_slots"] = dict(settings.get("auto_last_slots") or {})
    settings["auto_last_success_at"] = dict(settings.get("auto_last_success_at") or {})
    settings["auto_paused_reasons"] = dict(settings.get("auto_paused_reasons") or {})
    settings["auto_retry_state"] = dict(settings.get("auto_retry_state") or {})
    if settings.get("weekly_last_slot") and "weekly" not in settings["auto_last_slots"]:
        settings["auto_last_slots"]["weekly"] = settings["weekly_last_slot"]
    if settings.get("weekly_last_success_at") and "weekly" not in settings["auto_last_success_at"]:
        settings["auto_last_success_at"]["weekly"] = settings["weekly_last_success_at"]
    if settings.get("weekly_paused_reason") and "weekly" not in settings["auto_paused_reasons"]:
        settings["auto_paused_reasons"]["weekly"] = settings["weekly_paused_reason"]
    return settings


def weekly_schedule_slot(now):
    """Return the latest Monday 03:00 Beijing slot at or before ``now``."""
    local = datetime.fromtimestamp(float(now), BEIJING)
    monday = local.date() - timedelta(days=local.weekday())
    slot = datetime.combine(monday, datetime_time(hour=3), tzinfo=BEIJING)
    if local < slot:
        slot -= timedelta(days=7)
    return slot.timestamp()


def weekly_auto_due(engine, now=None):
    now = time.time() if now is None else float(now)
    settings = smart_mix_settings(engine.store)
    slot = weekly_schedule_slot(now)
    legacy_slot = float(settings.get("weekly_last_slot") or 0)
    return "weekly" in smart_mix_auto_due(engine, now) and legacy_slot < slot


def smart_mix_auto_due(engine, now=None):
    now = time.time() if now is None else float(now)
    settings = smart_mix_settings(engine.store)
    if not settings["auto_enabled"]:
        return []
    slot = weekly_schedule_slot(now)
    if now < slot:
        return []
    return [
        kind for kind in AUTO_KINDS
        if float(settings["auto_last_slots"].get(kind) or 0) < slot
        and float((settings["auto_retry_state"].get(kind) or {}).get("next_retry_at") or 0) <= now
    ]


def set_weekly_schedule(engine, enabled, now=None):
    result = set_smart_mix_schedule(engine, enabled, now=now)
    result["settings"]["weekly_auto_enabled"] = result["settings"]["auto_enabled"]
    return result


def set_smart_mix_schedule(engine, enabled, now=None):
    now = time.time() if now is None else float(now)
    with engine.exclusive():
        settings = smart_mix_settings(engine.store)
        enabled = enabled is True
        if enabled:
            managed = engine.store.get(MANAGED_KEY, {}) or {}
            records = [(kind, managed.get(kind)) for kind in AUTO_KINDS if managed.get(kind)]
            if not records:
                raise SafetyError("请先发布至少一个内置智能歌单，再开启每周自动更新")
            cfg = _configured(engine)
            plex = engine.plex_factory(cfg)
            for kind, record in records:
                current = plex.playlist_state(record["id"])
                if (record.get("scope") != engine.daily_scope()
                        or record.get("machine") != plex.identity()["machine"]
                        or engine.marker("smart:" + kind) not in current.get("summary", "")
                        or fingerprint(current) != record.get("fingerprint")):
                    raise SafetyError("已有智能歌单被手动修改或所属资料库变化，不能自动更新")
            settings["auto_enabled"] = True
            settings["weekly_auto_enabled"] = True
            settings["auto_last_slots"] = {
                **settings["auto_last_slots"],
                **{kind: weekly_schedule_slot(now) for kind in AUTO_KINDS},
            }
            settings["auto_paused_reasons"] = {}
            settings["auto_retry_state"] = {}
        else:
            settings["auto_enabled"] = False
            settings["weekly_auto_enabled"] = False
            settings["auto_paused_reasons"] = {}
            settings["auto_retry_state"] = {}
        engine.store.set(SETTINGS_KEY, settings)
        return {
            "message": "每周自动更新已开启。" if enabled else "每周自动更新已关闭。",
            "settings": settings,
        }


def preview_smart_mix(engine, kind, options=None, now=None):
    now = time.time() if now is None else float(now)
    if kind not in KINDS:
        raise SafetyError("未知智能歌单类型")
    with engine.exclusive():
        cfg = _configured(engine)
        plex = engine.plex_factory(cfg)
        identity = plex.identity()
        tracks = plex.tracks(cfg["section"])
        if not tracks:
            raise SafetyError("Plex 没有返回可用曲目，不创建空歌单")
        try:
            selected = select_smart_mix(
                kind, tracks, _history_events(engine), options or {}, now,
                engine.store.get("installation_id") + "|" + kind + "|" + str(int(now // 86400)),
            )
        except ValueError as exc:
            raise SafetyError(str(exc)) from None

        cid = "smart:" + kind
        marker = engine.marker(cid)
        managed_all = engine.store.get(MANAGED_KEY, {}) or {}
        managed = managed_all.get(kind)
        blocked = []
        before = None
        if any(
            row.get("category_id") == cid and row.get("status") in ("prepared", "uncertain", "restoring")
            for row in engine.store.get("snapshots", [])
        ):
            blocked.append("上一次智能歌单写入结果待核对，禁止重复写入")
        if managed:
            try:
                if managed.get("scope") != engine.daily_scope() or managed.get("machine") != identity["machine"]:
                    raise SafetyError("智能歌单所属账户、服务器或资料库已变化")
                before = plex.playlist_state(managed["id"])
                if marker not in before.get("summary", "") or fingerprint(before) != managed.get("fingerprint"):
                    raise SafetyError("歌单被手动修改或管理标记变化，不会覆盖")
            except Exception as exc:
                blocked.append(safe_error(exc))
        elif any(row.get("title") == selected["title"] for row in plex.playlists()):
            blocked.append("存在同名非本助手托管的歌单，不会接管或覆盖")
        if not selected["items"]:
            blocked.append("没有符合条件的曲目，保留现有歌单")

        ids = [str(row["id"]) for row in selected["items"]]
        plan = {
            **selected,
            "policy": SMART_MIX_POLICY,
            "id": uuid.uuid4().hex,
            "created_at": now,
            "machine": identity["machine"],
            "scope": engine.daily_scope(),
            "before": before,
            "blocked": blocked,
            "applied": False,
            "track_fingerprints": {str(row["id"]): track_fingerprint(row) for row in tracks if str(row.get("id")) in ids},
        }
        plans = _plan_map(engine.store)
        plans[kind] = plan
        engine.store.set(PLANS_KEY, plans)
        engine.store.log(f"智能歌单预览：{selected['title']}，{len(ids)}首；尚未写入")
        return plan


def _find_plan(store, plan_id):
    return next((row for row in _plan_map(store).values() if row.get("id") == plan_id), None)


def publish_smart_mix(engine, plan_id, now=None):
    now = time.time() if now is None else float(now)
    with engine.exclusive():
        plan = _find_plan(engine.store, plan_id)
        if not plan:
            raise SafetyError("智能歌单预览不存在，请重新生成")
        if plan.get("policy") != SMART_MIX_POLICY:
            raise SafetyError("智能歌单规则版本已更新，请重新生成预览")
        if plan.get("applied"):
            raise SafetyError("这份预览已经发布，不重复写入")
        if plan.get("blocked"):
            raise SafetyError("暂不能发布：" + "；".join(plan["blocked"]))
        if not 0 <= now - float(plan.get("created_at", 0)) <= PLAN_TTL:
            raise SafetyError("预览超过30分钟，请重新生成")
        cfg = _configured(engine)
        plex = engine.plex_factory(cfg)
        if plex.identity()["machine"] != plan.get("machine") or plan.get("scope") != engine.daily_scope():
            raise SafetyError("Plex 身份或资料库已变化，停止写入")
        ids = [str(row["id"]) for row in plan.get("items", [])]
        planned_ids = list(ids)
        from .playlist_hub import apply_manual_edits
        ids = apply_manual_edits(engine.store, "smart", plan["kind"], ids)
        fresh = {str(row["id"]): track_fingerprint(row) for row in plex.tracks(cfg["section"])}
        if (not ids or any(tid not in fresh for tid in ids)
                or any(fresh.get(tid) != plan.get("track_fingerprints", {}).get(tid) for tid in planned_ids)):
            raise SafetyError("候选歌曲在预览后变化，请重新生成")

        kind = plan["kind"]
        cid = "smart:" + kind
        marker = engine.marker(cid)
        managed_all = engine.store.get(MANAGED_KEY, {}) or {}
        managed = managed_all.get(kind)
        before = plan.get("before")
        if before:
            current = plex.playlist_state(before["id"])
            if (
                not managed or managed.get("id") != before["id"]
                or fingerprint(current) != fingerprint(before)
                or fingerprint(current) != managed.get("fingerprint")
                or marker not in current.get("summary", "")
            ):
                raise SafetyError("歌单在预览后被手动修改，不会覆盖")
        elif managed or any(row.get("title") == plan["title"] for row in plex.playlists()):
            raise SafetyError("预览后出现同名歌单或托管状态变化，请重新生成")

        snapshot = {
            "id": uuid.uuid4().hex, "kind": "smart_mix", "category_id": cid,
            "title": plan["title"], "created_at": now, "status": "prepared",
            "before": before, "after": None, "add": ids, "marker": marker,
            "plan_id": plan_id, "machine": plan["machine"], "scope": plan["scope"],
            "before_smart_record": managed,
        }
        engine._save_snapshot(snapshot)
        try:
            if before:
                after = sync_owned_items(plex, before, ids)
            else:
                after = plex.create(
                    plan["title"], ids, marker,
                    description="由曲库有序管理；只使用当前 Plex 音乐资料库，发布前必须预览确认。",
                )
            if (after["title"] != plan["title"] or marker not in after.get("summary", "")
                    or not has_exact_members(after, ids)):
                raise SafetyError("写入后回读与预览不一致，停止自动维护")
            snapshot.update(status="applied", after=after)
            engine._save_snapshot(snapshot)
            managed_all[kind] = {
                "id": after["id"], "title": after["title"], "fingerprint": fingerprint(after),
                "snapshot_id": snapshot["id"], "machine": plan["machine"], "scope": plan["scope"],
                "updated_at": now, "options": dict(plan.get("options") or {}),
                "count": len(after.get("items", [])),
            }
            plan.update(applied=True, result={"written": len(ids), "playlist_id": after["id"]})
            plans = _plan_map(engine.store)
            plans[kind] = plan
            removed = dict(engine.store.get(REMOVED_KEY, {}) or {})
            removed.pop(kind, None)
            engine.store.set_many({MANAGED_KEY: managed_all, PLANS_KEY: plans, REMOVED_KEY: removed})
            engine.store.log(f"智能歌单已发布：{plan['title']}，{len(ids)}首")
            return plan["result"]
        except Exception as exc:
            snapshot.update(status="uncertain", error=safe_error(exc))
            engine._save_snapshot(snapshot)
            raise SafetyError("智能歌单写入结果待核对，不会自动重试：" + safe_error(exc)) from None


def remove_smart_mix(engine, kind, confirm_title, now=None):
    now = time.time() if now is None else float(now)
    if kind not in KINDS:
        raise SafetyError("未知智能歌单类型")
    with engine.exclusive():
        managed_all = dict(engine.store.get(MANAGED_KEY, {}) or {})
        record = managed_all.get(kind)
        if not record:
            raise SafetyError("这不是助手托管的智能歌单；不会按名称删除")
        if any(
            row.get("category_id") == "smart:" + kind
            and row.get("status") in ("prepared", "uncertain", "restoring")
            for row in engine.store.get("snapshots", [])
        ):
            raise SafetyError("该歌单有待核对的操作，不能删除")
        cfg = _configured(engine)
        plex = engine.plex_factory(cfg)
        identity = plex.identity()
        current = plex.playlist_state(record["id"])
        title = str(current.get("title") or "")
        marker = engine.marker("smart:" + kind)
        if str(confirm_title or "") != title:
            raise SafetyError("歌单名称已经变化，请刷新后重新确认")
        if record.get("scope") != engine.daily_scope() or record.get("machine") != identity["machine"]:
            raise SafetyError("账户、服务器或资料库已经变化，拒绝删除")
        if marker not in current.get("summary", ""):
            raise SafetyError("助手管理标记缺失，拒绝删除")
        if fingerprint(current) != record.get("fingerprint"):
            raise SafetyError("歌单已被手动修改，拒绝删除")
        snapshot = {
            "id": uuid.uuid4().hex, "kind": "smart_mix", "action": "remove",
            "category_id": "smart:" + kind, "title": title, "created_at": now,
            "status": "prepared", "before": current, "after": None, "add": [],
            "marker": marker, "plan_id": "", "machine": identity["machine"],
            "scope": engine.daily_scope(), "before_smart_record": record,
        }
        engine._save_snapshot(snapshot)
        try:
            plex.delete_playlist(current["id"])
            snapshot["status"] = "applied"
            engine._save_snapshot(snapshot)
            managed_all.pop(kind, None)
            plans = _plan_map(engine.store)
            plans.pop(kind, None)
            removed = dict(engine.store.get(REMOVED_KEY, {}) or {})
            removed[kind] = {"snapshot_id": snapshot["id"], "title": title, "removed_at": now}
            settings = smart_mix_settings(engine.store)
            if kind == "weekly":
                settings.update(weekly_auto_enabled=False, weekly_paused_reason="")
            engine.store.set_many({
                MANAGED_KEY: managed_all, PLANS_KEY: plans, REMOVED_KEY: removed,
                SETTINGS_KEY: settings,
            })
            engine.store.log("已移除助手托管智能歌单：" + title)
            return {
                "message": "已从 Plex 删除该歌单；音乐文件未删除。",
                "snapshot_id": snapshot["id"],
            }
        except Exception as exc:
            snapshot.update(status="uncertain", error=safe_error(exc))
            engine._save_snapshot(snapshot)
            raise SafetyError("删除结果需要核对；助手不会自动重试：" + safe_error(exc)) from None


def _restore_removed_smart_mix(engine, snapshot):
    kind = str(snapshot.get("category_id") or "").removeprefix("smart:")
    removed = dict(engine.store.get(REMOVED_KEY, {}) or {})
    retired = removed.get(kind)
    if (
        snapshot.get("action") != "remove"
        or snapshot.get("status") != "applied"
        or not retired
        or retired.get("snapshot_id") != snapshot.get("id")
    ):
        raise SafetyError("只能恢复本页最近删除的智能歌单")
    managed_all = dict(engine.store.get(MANAGED_KEY, {}) or {})
    if kind in managed_all:
        raise SafetyError("该智能歌单已经存在，拒绝重复创建")
    if snapshot.get("scope") != engine.daily_scope():
        raise SafetyError("账户或资料库已经变化，不能恢复")
    cfg = _configured(engine)
    plex = engine.plex_factory(cfg)
    if plex.identity()["machine"] != snapshot.get("machine"):
        raise SafetyError("Plex 服务器已经变化，不能恢复")
    before = snapshot.get("before") or {}
    if any(row.get("title") == before.get("title") for row in plex.playlists()):
        raise SafetyError("Plex 中已经存在同名歌单，不能重复恢复")
    ids = state_ids(before)
    available = {str(row.get("id")) for row in plex.tracks(cfg["section"]) if row.get("available", True)}
    if not ids or any(track_id not in available for track_id in ids):
        raise SafetyError("原歌单包含当前曲库中已不可用的歌曲，未恢复")
    snapshot["status"] = "restoring"
    engine._save_snapshot(snapshot)
    try:
        after = plex.create(
            before["title"], ids, snapshot["marker"],
            description="从助手安全快照恢复；每周自动更新保持关闭。",
        )
        if after.get("title") != before.get("title") or snapshot["marker"] not in after.get("summary", "") or state_ids(after) != ids:
            raise SafetyError("恢复后的歌单回读不一致")
        previous = dict(snapshot.get("before_smart_record") or {})
        managed_all[kind] = {
            **previous, "id": after["id"], "title": after["title"],
            "fingerprint": fingerprint(after), "snapshot_id": "",
        }
        removed.pop(kind, None)
        plans = _plan_map(engine.store)
        plans.pop(kind, None)
        settings = smart_mix_settings(engine.store)
        if kind == "weekly":
            settings.update(weekly_auto_enabled=False, weekly_paused_reason="")
        engine.store.set_many({
            MANAGED_KEY: managed_all, REMOVED_KEY: removed, PLANS_KEY: plans,
            SETTINGS_KEY: settings,
        })
        snapshot["status"] = "restored"
        engine._save_snapshot(snapshot)
        engine.store.log("已从快照恢复智能歌单：" + before["title"])
        return {"message": "歌单已恢复；自动更新保持关闭。", "playlist_id": str(after["id"])}
    except Exception as exc:
        snapshot.update(status="uncertain", error=safe_error(exc))
        engine._save_snapshot(snapshot)
        raise SafetyError("恢复结果需要核对：" + safe_error(exc)) from None


def restore_removed_smart_mix(engine, snapshot_id):
    with engine.exclusive():
        snapshot = next(
            (row for row in engine.store.get("snapshots", []) if row.get("id") == snapshot_id),
            None,
        )
        if not snapshot:
            raise SafetyError("找不到可恢复的删除快照")
        return _restore_removed_smart_mix(engine, snapshot)


def run_weekly_auto(engine, now=None):
    result = run_smart_mix_auto(engine, now=now)
    weekly = result["items"].get("weekly") or {}
    if weekly.get("status") == "error":
        raise SafetyError("每周常听自动更新失败：" + weekly.get("error", "未知错误"))
    return weekly.get("result") or {
        "playlist_id": ((engine.store.get(MANAGED_KEY, {}) or {}).get("weekly") or {}).get("id"),
        "unchanged": weekly.get("status") == "unchanged",
    }


def run_smart_mix_auto(engine, now=None, due_kinds=None, slot=None):
    """Run all three built-in weekly jobs independently under one switch."""
    now = time.time() if now is None else float(now)
    due = smart_mix_auto_due(engine, now) if due_kinds is None else [kind for kind in due_kinds if kind in AUTO_KINDS]
    if not due:
        raise SafetyError("智能歌单尚未到更新时间")
    slot = weekly_schedule_slot(now) if slot is None else float(slot)
    settings = smart_mix_settings(engine.store)
    managed = engine.store.get(MANAGED_KEY, {}) or {}
    results = {}
    for kind in due:
        record = managed.get(kind)
        try:
            if not record:
                results[kind] = {"status": "skipped", "reason": "尚未发布"}
                continue
            defaults = {
                "weekly": {"size": 30, "recent_days": 30},
                "time_capsule": {"size": 30, "stale_days": 180},
                "recent_additions": {"size": 30, "added_days": 90},
            }
            plan = preview_smart_mix(engine, kind, dict(record.get("options") or defaults[kind]), now=now)
            if plan.get("blocked"):
                raise SafetyError("；".join(plan["blocked"]))
            desired = [str(row["id"]) for row in plan.get("items", [])]
            current = [str(row.get("id")) for row in (plan.get("before") or {}).get("items", [])]
            if desired == current:
                plans = _plan_map(engine.store)
                plans.pop(kind, None)
                engine.store.set(PLANS_KEY, plans)
                results[kind] = {"status": "unchanged", "count": len(desired)}
            else:
                published = publish_smart_mix(engine, plan["id"], now=now + 1)
                results[kind] = {"status": "published", "result": published}
            settings["auto_last_success_at"][kind] = now
            settings["auto_paused_reasons"].pop(kind, None)
            settings["auto_retry_state"].pop(kind, None)
        except Exception as exc:
            message = safe_error(exc)[:300]
            settings["auto_paused_reasons"][kind] = message
            previous = settings["auto_retry_state"].get(kind) or {}
            failures = int(previous.get("failures") or 0) + 1
            delay = AUTO_RETRY_DELAYS[min(failures - 1, len(AUTO_RETRY_DELAYS) - 1)]
            settings["auto_retry_state"][kind] = {
                "failures": failures,
                "next_retry_at": now + delay,
            }
            results[kind] = {"status": "error", "error": message}
        finally:
            if results.get(kind, {}).get("status") != "error":
                settings["auto_last_slots"][kind] = slot
    settings["weekly_last_slot"] = settings["auto_last_slots"].get("weekly", 0)
    settings["weekly_last_success_at"] = settings["auto_last_success_at"].get("weekly", 0)
    settings["weekly_paused_reason"] = settings["auto_paused_reasons"].get("weekly", "")
    engine.store.set(SETTINGS_KEY, settings)
    return {"slot": slot, "items": results}


def public_plan(plan):
    if not plan:
        return None
    return {key: value for key, value in plan.items() if key not in ("before", "track_fingerprints")}


def profile_daily_display_name(profile):
    """Use the same account-and-library identity everywhere in the settings UI."""
    account = profile.get("account") or {}
    library = profile.get("library") or {}
    account_name = account.get("username") or account.get("title") or profile.get("name") or profile["id"]
    library_name = library.get("name") or library.get("id")
    return f"{account_name} · {library_name}" if library_name else str(account_name)


def _batch_daily_summary(items):
    enabled = [bool(row.get("auto_enabled")) for row in items]
    auto_state = "on" if enabled and all(enabled) else "partial" if any(enabled) else "off"
    return {
        "items": items,
        "ready": sum(row.get("status") == "ready" for row in items),
        "auto_state": auto_state,
    }


def _batch_daily_row(profile, engine):
    plan = engine.store.get("daily_plan") or {}
    managed = engine.store.get("daily_managed") or {}
    row = {
        "profile_id": profile["id"],
        "name": profile.get("name") or profile["id"],
        "display_name": profile_daily_display_name(profile),
        "auto_enabled": bool((engine.store.get("daily_settings", {}) or {}).get("enabled")),
    }
    if plan.get("id") and not plan.get("applied"):
        blocked = list(plan.get("blocked") or [])
        invalidated = str(plan.get("invalidated_reason") or "").strip()
        if invalidated:
            blocked.append(invalidated)
        row.update(
            status="blocked" if blocked else "ready",
            plan_id=plan.get("id"), count=len(plan.get("items") or []),
            blocked=blocked, warnings=list(plan.get("warnings") or []),
        )
    elif managed:
        row.update(status="published", count=int(managed.get("count") or 0))
    else:
        row.update(status="idle", count=0)
    return row


def batch_daily_status(runtime, registry):
    """Restore persisted previews and automation state after any page navigation."""
    items = []
    for profile in registry.list_public():
        if profile.get("enabled") is False:
            continue
        items.append(_batch_daily_row(profile, runtime.engine(profile["id"])))
    return _batch_daily_summary(items)


def _daily_schedule_blocker(engine):
    managed = engine.store.get("daily_managed") or {}
    if not managed or managed.get("scope") != engine.daily_scope():
        return "请先预览并发布一次"
    unresolved = any(
        row.get("category_id") == "daily"
        and row.get("status") in ("prepared", "uncertain", "restoring")
        for row in (engine.store.get("snapshots", []) or [])
        if isinstance(row, dict)
    )
    return "有待核对的发布变更" if unresolved else ""


def set_batch_daily_schedule(runtime, registry, enabled):
    """Enable or pause daily updates for every active profile as one validated action."""
    if not isinstance(enabled, bool):
        raise ValueError("每日自动更新开关无效")
    profiles = [row for row in registry.list_public() if row.get("enabled") is not False]
    engines = [(profile, runtime.engine(profile["id"])) for profile in profiles]
    # Every profile engine shares the runtime operation gate. Holding it once
    # keeps validation, writes, and rollback indivisible across all profiles.
    operation = engines[0][1].exclusive() if engines else nullcontext()
    with operation:
        if enabled:
            blocked = [
                f"{profile_daily_display_name(profile)}：{reason}"
                for profile, engine in engines
                if (reason := _daily_schedule_blocker(engine))
            ]
            if blocked:
                raise SafetyError("不能开启全部每日更新；" + "；".join(blocked))
        original = []
        try:
            for _profile, engine in engines:
                saved = dict(engine.store.get("daily_settings", {}) or {})
                original.append((engine, saved))
                engine.store.set("daily_settings", {**saved, "enabled": enabled})
        except Exception:
            for engine, saved in original:
                engine.store.set("daily_settings", saved)
            raise
    result = batch_daily_status(runtime, registry)
    result["message"] = "全部用户的每日自动更新已开启" if enabled else "全部用户的每日自动更新已暂停"
    return result


def batch_preview_daily(runtime, registry, now=None):
    """Preview each enabled profile independently; one failure never aborts others."""
    now = time.time() if now is None else float(now)
    items = []
    for profile in registry.list_public():
        if profile.get("enabled") is False:
            continue
        row = {
            "profile_id": profile["id"],
            "name": profile.get("name") or profile["id"],
            "display_name": profile_daily_display_name(profile),
        }
        engine = None
        try:
            engine = runtime.engine(profile["id"])
            cfg = engine.store.get("settings", {}) or {}
            if not cfg.get("plex_token") or not cfg.get("plex_url") or not cfg.get("section"):
                raise SafetyError("该档案尚未完成 Plex 授权或选择音乐资料库")
            plan = engine.preview_daily(now=now)
            row.update(
                status="blocked" if plan.get("blocked") else "ready",
                plan_id=plan.get("id"), count=len(plan.get("items") or []),
                blocked=list(plan.get("blocked") or []), warnings=list(plan.get("warnings") or []),
            )
        except Exception as exc:
            row.update(status="error", error=str(exc)[:300] or "预览失败")
        row["auto_enabled"] = bool(
            engine and (engine.store.get("daily_settings", {}) or {}).get("enabled")
        )
        items.append(row)
    return _batch_daily_summary(items)


def batch_publish_daily(runtime, registry, plan_ids, now=None):
    """Publish only explicitly supplied profile/plan pairs."""
    now = time.time() if now is None else float(now)
    if not isinstance(plan_ids, dict) or not plan_ids:
        raise SafetyError("没有已确认的档案预览可发布")
    profiles = {row["id"]: row for row in registry.list_public() if row.get("enabled") is not False}
    items = []
    for profile_id, plan_id in plan_ids.items():
        if profile_id not in profiles:
            items.append({"profile_id": str(profile_id), "name": str(profile_id), "status": "error", "error": "档案不存在或已停用"})
            continue
        profile = profiles[profile_id]
        row = {
            "profile_id": profile_id,
            "name": profile.get("name") or profile_id,
            "display_name": profile_daily_display_name(profile),
        }
        try:
            result = runtime.engine(profile_id).publish_daily(str(plan_id), now=now)
            row.update(status="published", result=result)
        except Exception as exc:
            row.update(status="error", error=str(exc)[:300] or "发布失败")
        items.append(row)
    return {"items": items, "published": sum(row.get("status") == "published" for row in items)}


def restore_smart_mix(engine, snapshot):
    """Restore only the latest app-owned smart mix snapshot."""
    if snapshot.get("action") == "remove":
        return _restore_removed_smart_mix(engine, snapshot)
    kind = str(snapshot.get("category_id") or "").removeprefix("smart:")
    managed_all = engine.store.get(MANAGED_KEY, {}) or {}
    record = managed_all.get(kind)
    if not record or record.get("snapshot_id") != snapshot.get("id"):
        raise SafetyError("只能恢复该智能歌单最近一次变更")
    if record.get("scope") != engine.daily_scope() or snapshot.get("scope") != engine.daily_scope():
        raise SafetyError("账户、服务器或资料库已变化，不能恢复旧快照")
    plex = engine.plex_factory(engine.store.get("settings"))
    if plex.identity()["machine"] != snapshot.get("machine"):
        raise SafetyError("Plex 服务器身份已变化，不能恢复")
    current = plex.playlist_state(record["id"])
    marker = engine.marker("smart:" + kind)
    if fingerprint(current) != fingerprint(snapshot.get("after") or {}) or marker not in current.get("summary", ""):
        raise SafetyError("智能歌单已被手动改动，停止恢复")
    snapshot["status"] = "restoring"
    engine._save_snapshot(snapshot)
    try:
        before = snapshot.get("before")
        if before is None:
            plex.delete_playlist(current["id"])
            managed_all.pop(kind, None)
        else:
            restored = sync_owned_items(plex, current, state_ids(before))
            if (restored.get("title") != before.get("title")
                    or restored.get("summary", "") != before.get("summary", "")
                    or not has_exact_members(restored, state_ids(before))):
                raise SafetyError("恢复后回读不一致")
            previous = snapshot.get("before_smart_record") or {}
            managed_all[kind] = {**previous, "id": restored["id"], "fingerprint": fingerprint(restored)}
        plans = _plan_map(engine.store)
        plans.pop(kind, None)
        engine.store.set_many({MANAGED_KEY: managed_all, PLANS_KEY: plans})
        snapshot["status"] = "restored"
        engine._save_snapshot(snapshot)
        engine.store.log("智能歌单已按快照恢复：" + snapshot.get("title", kind))
        return {"message": "已恢复智能歌单；其他歌单和音乐文件没有修改"}
    except Exception as exc:
        snapshot.update(status="uncertain", error=safe_error(exc))
        engine._save_snapshot(snapshot)
        raise SafetyError("恢复结果待核对，不会继续自动操作") from None


def attach_smart_mix_routes(app, store, engine, runtime, registry, body, ensure_idle):
    @app.get("/api/mixes/status")
    def smart_mix_status():
        managed = store.get(MANAGED_KEY, {}) or {}
        return {
            "plans": {kind: public_plan(plan) for kind, plan in _plan_map(store).items()},
            "managed": {
                kind: {key: value for key, value in row.items() if key != "fingerprint"}
                for kind, row in managed.items()
            },
            "removed": dict(store.get(REMOVED_KEY, {}) or {}),
            "settings": smart_mix_settings(store),
            "profiles": registry.list_public(),
        }

    @app.post("/api/mixes/preview")
    async def smart_mix_preview(request: Request):
        data = await body(request)
        ensure_idle()
        options = data.get("options") or {}
        if not isinstance(options, dict):
            raise ValueError("筛选条件需要 JSON 对象")
        return {"plan": public_plan(preview_smart_mix(engine, str(data.get("kind") or ""), options))}

    @app.post("/api/mixes/publish")
    async def smart_mix_publish(request: Request):
        data = await body(request)
        ensure_idle()
        if data.get("confirm") is not True:
            raise SafetyError("请先查看预览并明确确认发布")
        return publish_smart_mix(engine, str(data.get("plan_id") or ""))

    @app.post("/api/mixes/remove")
    async def smart_mix_remove(request: Request):
        data = await body(request)
        ensure_idle()
        if data.get("confirm") is not True:
            raise SafetyError("请明确确认删除助手托管的歌单")
        return remove_smart_mix(
            engine, str(data.get("kind") or ""), str(data.get("title") or "")
        )

    @app.post("/api/mixes/restore")
    async def smart_mix_restore(request: Request):
        data = await body(request)
        ensure_idle()
        if data.get("confirm") is not True:
            raise SafetyError("请明确确认恢复刚删除的歌单")
        return restore_removed_smart_mix(engine, str(data.get("snapshot_id") or ""))

    @app.post("/api/mixes/weekly-schedule")
    async def smart_mix_weekly_schedule(request: Request):
        data = await body(request)
        ensure_idle()
        if not isinstance(data.get("enabled"), bool):
            raise ValueError("每周自动更新开关无效")
        return set_weekly_schedule(engine, data["enabled"])

    @app.post("/api/mixes/auto-schedule")
    async def smart_mix_auto_schedule(request: Request):
        data = await body(request)
        ensure_idle()
        if not isinstance(data.get("enabled"), bool):
            raise ValueError("每周自动更新开关无效")
        return set_smart_mix_schedule(engine, data["enabled"])

    @app.post("/api/profiles/daily/batch-preview")
    async def daily_batch_preview(request: Request):
        await body(request)
        return batch_preview_daily(runtime, registry)

    @app.get("/api/profiles/daily/batch-status")
    def daily_batch_status():
        return batch_daily_status(runtime, registry)

    @app.post("/api/profiles/daily/batch-publish")
    async def daily_batch_publish(request: Request):
        data = await body(request)
        if data.get("confirm") is not True:
            raise SafetyError("请先核对每个档案的预览，再明确确认批量发布")
        return batch_publish_daily(runtime, registry, data.get("plans") or {})

    @app.post("/api/profiles/daily/batch-schedule")
    async def daily_batch_schedule(request: Request):
        data = await body(request)
        return set_batch_daily_schedule(runtime, registry, data.get("enabled"))
