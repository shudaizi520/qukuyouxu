"""Compatibility API joining the polished library UI to the existing safe engine."""
from __future__ import annotations

import copy
import json
import time

from fastapi import Request
from fastapi.responses import Response

from . import __version__
from .managed_cleanup_v0317 import forget_missing_managed_playlist
from .qq_auth_v0320 import QQAuthError, QQAuthManager
from .scoped_store import ScopedStore
from .theme import DEFAULT_THEME, TOPICS, theme_key
from .workflow_state import current_review_plan, incremental_is_current


class ProfileQQAuthManager:
    """Resolve QQ credentials and cookies from the currently selected Plex profile."""
    def __init__(self, store, engine, manager_factory=QQAuthManager):
        self.store = store
        self.engine = engine
        self.manager_factory = manager_factory
        self._managers = {}

    def _active_id(self):
        return str(getattr(self.store, "profile_id", "default") or "default")

    def _manager(self):
        profile_id = self._active_id()
        manager = self._managers.get(profile_id)
        if manager is None:
            base = getattr(self.store, "base", self.store)
            fixed_store = ScopedStore(base, profile_id) if hasattr(base, "root") else self.store
            runtime = getattr(self.engine, "runtime", None)
            profile_engine = runtime.engine(profile_id) if runtime is not None else self.engine
            manager = self.manager_factory(fixed_store, profile_engine.qq)
            self._managers[profile_id] = manager
        return manager

    @property
    def qq_client(self):
        return self._manager().qq_client

    def __getattr__(self, name):
        return getattr(self._manager(), name)


def _topic_sources(store):
    return [row for row in (store.get("sources", []) or []) if str(row.get("kind") or "").startswith("qq_")]


def _review(plan, sources):
    if not plan or plan.get("applied"):
        return None
    source_map = {str(row.get("id")): row for row in sources}
    groups = []
    for item in plan.get("groups", []) or []:
        blocked = [str(value) for value in (item.get("blocked") or [])]
        kind = "theme" if str(item.get("kind") or "").startswith("qq_") else str(item.get("kind") or "category")
        before = item.get("before") or {}
        groups.append({
            "id": str(item.get("id") or ""), "title": str(item.get("title") or ""), "kind": kind,
            "dimension": str(item.get("dimension") or ""), "count": len(item.get("desired") or []),
            "existing_count": len(before.get("items") or []), "add_count": len(item.get("add") or []),
            "action": str(item.get("action") or "unchanged"), "blocked": blocked,
            "default_selected": not blocked and source_map.get(str(item.get("id") or ""), {}).get("enabled", True) is not False,
            "reference_count": int(item.get("reference_count") or 0),
            "verified_mid_count": int(item.get("verified_mid_count") or 0),
            "theme_stats": item.get("theme_stats") or {},
        })
    expired = time.time() - float(plan.get("created_at") or 0) > 1800
    return {
        "id": str(plan.get("id") or ""), "created_at": plan.get("created_at"), "groups": groups,
        "expired": expired, "problem": "预览超过30分钟，请重新整理预览。" if expired else "",
        "cache_only": bool(plan.get("cache_only")),
    }


def build_workflow_status(store, engine, qq_status):
    settings = store.get("settings", {}) or {}
    sources = list(store.get("sources", []) or [])
    saved_plan = store.get("plan") or None
    job = dict(getattr(engine, "job", {}) or {})
    incremental = dict(store.get("incremental_status", {}) or {})
    plan = current_review_plan(saved_plan, incremental)
    incremental_current = incremental_is_current(saved_plan, incremental)
    running = bool(job.get("running"))
    error = str(job.get("error") or "")
    paused = dict(store.get("workflow_pause_state", {}) or {})
    if running:
        kind = str(job.get("kind") or "")
        phase = "publishing" if kind in {"apply", "auto"} else "enriching" if kind == "incremental" else "checking"
        message = str(job.get("message") or "任务正在执行")
        if any(word in message for word in ("分类", "来源", "预览")):
            phase = "planning"
    elif error:
        phase, message = "error", error
    elif paused.get("active"):
        phase, message = "paused", str(paused.get("message") or "整理已暂停，进度已保留。")
    elif incremental_current:
        status = str(incremental.get("status") or "completed")
        phase = "paused" if status in {"paused", "blocked"} else "attention" if status in {"attention", "error"} else "ready"
        message = str(incremental.get("message") or "新增歌曲检查完成。")
    elif plan and not plan.get("applied"):
        phase, message = "review", "分类预览已准备好，请确认要同步的歌单。"
    elif plan and plan.get("applied"):
        phase, message = "ready", "最近一次同步已经完成。"
    else:
        phase, message = "idle", "点击整理新增歌曲，先生成预览，确认前不会修改 Plex。"
    catalog = store.get("catalog", []) or []
    matched = int((saved_plan or {}).get("covered") or 0)
    review_count = int((saved_plan or {}).get("metadata_review_count") or 0)
    topics = _topic_sources(store)
    if hasattr(engine, "theme_status"):
        theme_status = dict(engine.theme_status() or {})
    else:
        theme_settings = copy.deepcopy(DEFAULT_THEME)
        theme_settings.update(store.get("theme_settings", {}) or {})
        theme_status = {
            "settings": theme_settings,
            "topics": copy.deepcopy(TOPICS),
            "unavailable": list(store.get("theme_unavailable", []) or []),
            "skipped_references": [],
        }
    result = (plan or {}).get("result")
    if result:
        result = {
            "written": int(result.get("written") or 0), "unchanged": int(result.get("unchanged") or 0),
            "blocked": int(result.get("skipped") or 0), "errors": [str(value)[:300] for value in result.get("errors", [])],
        }
    return {
        "version": __version__,
        "workflow": {
            "needs_setup": not all(settings.get(key) for key in ("plex_url", "plex_token", "section")),
            "state": {"phase": phase, "message": message, "cache_only": bool((plan or {}).get("cache_only")), "result": result},
            "job": {
                **{key: job.get(key) for key in ("running", "kind", "message", "error", "started_at", "finished_at", "progress_current", "progress_total")},
                "can_pause": bool(running and str(job.get("kind") or "") in {"preview", "incremental"}),
            },
            "summary": {"library_count": len(catalog) or int((saved_plan or {}).get("library_count") or 0), "matched": matched, "review_count": review_count, "managed": len(store.get("managed", {}) or {})},
            "settings": {"initialized": bool(store.get("managed", {}) or (saved_plan and saved_plan.get("applied"))), "enabled": bool(settings.get("auto_enabled")), "schedule": "daily_midnight_beijing", "next_run": store.get("library_auto_next_at")},
            "review": _review(plan, sources), "qq_auth": dict(qq_status or {}),
            "theme": theme_status,
            "single": engine.single_status() if hasattr(engine, "single_status") else {"state": {}},
            "incremental": {key: incremental.get(key) for key in ("status", "message", "new_count", "processed", "updated_at")},
            "notice": "",
        },
    }


def save_theme_settings(store, selected, enabled):
    if not isinstance(enabled, bool) or not isinstance(selected, list):
        raise ValueError("主题设置格式无效")
    selected = [str(value) for value in selected]
    if len(selected) != len(set(selected)):
        raise ValueError("主题选项不能重复")
    sources = list(store.get("sources", []) or [])
    allowed = {str(row["key"]) for row in TOPICS}
    if not set(selected).issubset(allowed):
        raise ValueError("主题选项已经变化，请刷新后重新选择")
    settings = {**DEFAULT_THEME, **(store.get("theme_settings", {}) or {})}
    settings.update({"enabled": enabled, "selected": selected})
    tags = store.get("qq_tags", []) or []
    for row in sources:
        key = theme_key(row, tags)
        if key in allowed:
            row["enabled"] = bool(enabled and key in selected)
            if not row["enabled"]:
                row["approved"] = False
    store.set_many({"theme_settings": settings, "sources": sources, "plan": None})
    return {"message": "主题选择已保存；下次整理时生效。"}


def apply_exclusions(plan, exclusions, min_tracks=5):
    revised = copy.deepcopy(plan)
    if not revised:
        return revised
    for group in revised.get("groups", []) or []:
        removed = {str(value) for value in (exclusions or {}).get(str(group.get("id")), [])}
        if not removed:
            continue
        group["desired"] = [value for value in group.get("desired", []) if str(value) not in removed]
        group["add"] = [value for value in group.get("add", []) if str(value) not in removed]
        group["matched_rows"] = [row for row in group.get("matched_rows", []) if str((row.get("local") or {}).get("id")) not in removed]
        group["matched"] = len(group["desired"])
        if len(group["desired"]) < int(min_tracks or 1):
            blocked = list(group.get("blocked") or [])
            if "排除后可靠匹配不足最低歌曲数" not in blocked:
                blocked.append("排除后可靠匹配不足最低歌曲数")
            group["blocked"] = blocked
        group["action"] = "append" if group.get("before") and group["add"] else "unchanged" if group.get("before") else "create"
    return revised


def _attach_exclusion_filter(store, engine):
    if getattr(engine, "_v0317_exclusions_attached", False):
        return
    original = engine._preview

    def filtered_preview(force_sources=False):
        plan = original(force_sources)
        revised = apply_exclusions(plan, store.get("theme_exclusions", {}) or {}, (store.get("settings", {}) or {}).get("min_tracks", 5))
        store.set("plan", revised)
        return revised

    engine._preview = filtered_preview
    engine._v0317_exclusions_attached = True


def _cache_only_preview(store, engine):
    now = time.time()
    cache = dict(store.get("cache", {}) or {})
    for row in store.get("sources", []) or []:
        if row.get("enabled", True) is False:
            continue
        key = str(row.get("id") or "")
        entry = dict(cache.get(key, {}) or {})
        entry["last_attempt"] = now
        if entry.get("data"):
            entry["fetched_at"] = now
            entry["error"] = ""
        cache[key] = entry
    store.set("cache", cache)
    result = engine.start_job("preview", force_sources=False)
    store.set("workflow_cache_only_pending", True)
    return result


def _redacted_report(store, engine, qq_status):
    plan = store.get("plan") or {}
    return {
        "version": __version__, "generated_at": time.time(), "job": dict(getattr(engine, "job", {}) or {}),
        "qq": dict(qq_status or {}), "events": list(store.get("events", []) or [])[-100:],
        "summary": {"library_count": int(plan.get("library_count") or 0), "covered": int(plan.get("covered") or 0), "groups": len(plan.get("groups", []) or []), "managed": len(store.get("managed", {}) or {})},
    }


def attach_routes(app, store, engine, body, ensure_idle):
    if getattr(app.state, "v0317_routes_attached", False):
        return
    app.state.v0317_routes_attached = True
    from .restart import attach_restart_routes
    from .rotation import attach_reconciliation_routes
    attach_restart_routes(app, store, engine, body, ensure_idle)
    attach_reconciliation_routes(app, store, engine, body, ensure_idle)
    _attach_exclusion_filter(store, engine)
    qq_auth = ProfileQQAuthManager(store, engine)
    app.state.qq_auth = qq_auth

    @app.get("/api/workflow/status")
    def workflow_status():
        plan = store.get("plan") or {}
        if store.get("workflow_cache_only_pending") and plan and not getattr(engine, "job", {}).get("running"):
            plan["cache_only"] = True
            store.set_many({"plan": plan, "workflow_cache_only_pending": False})
        return build_workflow_status(store, engine, qq_auth.public_status())

    @app.post("/api/workflow/run")
    async def workflow_run(req: Request):
        data = await body(req)
        if data.get("confirm") is not True:
            raise ValueError("请确认开始整理")
        ensure_idle()
        store.set("workflow_cache_only_pending", False)
        return engine.start_job("preview", force_sources=True)

    @app.post("/api/workflow/incremental")
    async def workflow_incremental(req: Request):
        data = await body(req)
        if data.get("confirm") is not True:
            raise ValueError("请确认检查新增歌曲")
        ensure_idle()
        if not hasattr(engine, "refresh_new_tracks"):
            raise ValueError("新增歌曲检查功能尚未加载，请重启应用")
        return engine.start_job("incremental")

    @app.post("/api/workflow/cached")
    async def workflow_cached(req: Request):
        data = await body(req)
        if data.get("confirm") is not True:
            raise ValueError("请确认只使用已有资料")
        ensure_idle()
        return _cache_only_preview(store, engine)

    @app.post("/api/workflow/confirm")
    async def workflow_confirm(req: Request):
        data = await body(req)
        if data.get("confirm") is not True:
            raise ValueError("请确认同步歌单")
        ensure_idle()
        plan = copy.deepcopy(store.get("plan") or {})
        if not plan or str(plan.get("id")) != str(data.get("review_id") or "") or plan.get("applied"):
            raise ValueError("预览已经变化，请重新整理")
        selected = {str(value) for value in (data.get("selected_ids") or [])}
        allowed = {str(row.get("id")) for row in plan.get("groups", []) or []}
        if not selected or not selected.issubset(allowed):
            raise ValueError("请选择当前预览中的歌单")
        for group in plan.get("groups", []) or []:
            if str(group.get("id")) not in selected:
                group["blocked"] = list(group.get("blocked") or []) + ["本次未选择"]
        store.set("plan", plan)
        return engine.start_job("apply", plan_id=plan["id"])

    @app.post("/api/workflow/schedule")
    async def workflow_schedule(req: Request):
        data = await body(req)
        if data.get("confirm") is not True or not isinstance(data.get("enabled"), bool):
            raise ValueError("自动整理设置无效")
        ensure_idle()
        settings = dict(store.get("settings", {}) or {})
        settings["auto_enabled"] = data["enabled"]
        settings["interval_minutes"] = 1440
        from .profile_runtime import next_beijing_midnight
        store.set_many({
            "settings": settings,
            "library_auto_next_at": next_beijing_midnight(time.time()) if data["enabled"] else None,
        })
        return {"message": "自动整理已开启：每天北京时间 00:00 检查一次新歌。" if data["enabled"] else "自动整理已关闭。"}

    @app.post("/api/workflow/pause")
    def workflow_pause():
        job = dict(getattr(engine, "job", {}) or {})
        if job.get("running") and job.get("kind") in {"preview", "incremental"}:
            return engine.request_workflow_pause()
        raise ValueError("当前步骤不能中途暂停；完成后会自动停在确认页，关闭网页不会取消任务")

    @app.get("/api/workflow/report")
    def workflow_report():
        payload = json.dumps(_redacted_report(store, engine, qq_auth.public_status()), ensure_ascii=False, indent=2).encode("utf-8")
        return Response(payload, media_type="application/json", headers={"Content-Disposition": "attachment; filename=music-workflow-report.json"})

    @app.post("/api/qq-auth/start")
    async def qq_auth_start(req: Request):
        data = await body(req)
        if data.get("confirm") is not True:
            raise ValueError("请确认开始 QQ 授权")
        ensure_idle()
        try:
            result = qq_auth.start()
        except QQAuthError as exc:
            raise ValueError(str(exc)) from None
        except Exception:
            raise ValueError('QQ 音乐授权初始化失败，请查看容器日志') from None
        return {**result, "message": result.get("message") or "二维码已生成"}

    @app.get("/api/qq-auth/qrcode")
    def qq_auth_qrcode():
        return Response(qq_auth.qrcode(), media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.post("/api/qq-auth/logout")
    async def qq_auth_logout(req: Request):
        data = await body(req)
        if data.get("confirm") is not True:
            raise ValueError("请确认退出 QQ 授权")
        ensure_idle()
        result = qq_auth.logout()
        return {**result, "message": "已退出助手中的 QQ 授权。"}

    @app.post("/api/themes/settings")
    async def themes_settings(req: Request):
        data = await body(req)
        if data.get("confirm") is not True:
            raise ValueError("请确认保存主题")
        ensure_idle()
        return save_theme_settings(store, data.get("selected"), data.get("enabled"))

    @app.get("/api/themes/evidence")
    def themes_evidence(category_id: str, offset: int = 0, limit: int = 40, added_only: bool = True):
        plan = store.get("plan") or {}
        group = next((row for row in plan.get("groups", []) or [] if str(row.get("id")) == str(category_id)), None)
        if not group:
            raise ValueError("当前预览中找不到这个主题")
        wanted = {str(value) for value in (group.get("add") if added_only else group.get("desired")) or []}
        items = []
        for row in group.get("matched_rows", []) or []:
            local, source = row.get("local") or {}, row.get("source") or {}
            track_id = str(local.get("id") or "")
            if track_id not in wanted:
                continue
            items.append({"id": track_id, "title": str(local.get("title") or source.get("title") or ""), "artist": str(local.get("artist") or source.get("artist") or ""), "methods": ["metadata"], "origins": group.get("origins") or []})
        offset, limit = max(0, offset), max(1, min(100, limit))
        chunk = items[offset:offset + limit]
        next_offset = offset + limit if offset + limit < len(items) else None
        return {"title": str(group.get("title") or "主题"), "total": len(items), "items": chunk, "next": next_offset}

    @app.post("/api/themes/exclude")
    async def themes_exclude(req: Request):
        data = await body(req)
        if data.get("confirm") is not True or data.get("excluded") is not True:
            raise ValueError("请确认排除这首歌")
        ensure_idle()
        category_id, track_id = str(data.get("category_id") or ""), str(data.get("track_id") or "")
        plan = store.get("plan") or {}
        group = next((row for row in plan.get("groups", []) or [] if str(row.get("id")) == category_id), None)
        if not group or track_id not in {str(value) for value in group.get("desired", []) or []}:
            raise ValueError("歌曲或主题已经变化，请刷新后重试")
        exclusions = dict(store.get("theme_exclusions", {}) or {})
        values = set(str(value) for value in exclusions.get(category_id, []) or [])
        values.add(track_id)
        exclusions[category_id] = sorted(values)
        revised = apply_exclusions(plan, exclusions, (store.get("settings", {}) or {}).get("min_tracks", 5))
        store.set_many({"theme_exclusions": exclusions, "plan": revised})
        return {"message": "已从这个主题的后续补充中排除；不会删除 Plex 中已有歌曲。"}

    @app.post("/api/managed/forget")
    async def managed_forget(req: Request):
        data = await body(req)
        if data.get("confirm") is not True:
            raise ValueError("请确认只清除助手本地记录")
        ensure_idle()
        with engine.exclusive():
            return forget_missing_managed_playlist(engine, str(data.get("category_id") or ""), str(data.get("playlist_id") or ""), str(data.get("title") or ""))
