"""Authenticated, profile-pinned HTTP routes for external playlists."""
from __future__ import annotations

import base64
import binascii
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import Response

from .auth import COOKIE_NAME
from .engine import SafetyError
from .external_audio import stream_local_audio
from .external_export import format_missing_csv, format_missing_text, missing_download_name
from .external_sources import validate_source_reference


def _source_summary(data, last_run=None):
    summary = {
        key: data.get(key) for key in (
            "id", "title", "provider", "external_id", "source_url", "revision", "fetched_at",
            "follow_updates", "needs_confirmation", "failure_count", "next_retry_at", "counts", "managed",
        )
    }
    summary["last_run"] = ({
        key: last_run.get(key) for key in ("kind", "status", "started_at", "finished_at", "message")
    } if last_run else None)
    return summary


def _public_track(row, catalog=None):
    candidate_ids = list(row.get("candidate_ids") or [])[:3]
    public = {
        "source_track_key": row.get("source_track_key"),
        "position": row.get("position"),
        "title": row.get("title"),
        "artists": list(row.get("artists") or []),
        "album": row.get("album"),
        "duration_ms": row.get("duration_ms"),
        "version_label": row.get("version_label"),
        "source_url": row.get("source_url"),
        "status": row.get("status"),
        "plex_track_id": row.get("plex_track_id"),
        "candidate_ids": candidate_ids,
        "reason": row.get("reason"),
        "manual": bool(row.get("manual")),
    }
    catalog = catalog or {}
    public["candidates"] = [
        {
            "id": track_id, "title": catalog[track_id].get("title"),
            "artist": catalog[track_id].get("artist"), "album": catalog[track_id].get("album"),
            "duration": catalog[track_id].get("duration"),
        }
        for track_id in candidate_ids if track_id in catalog
    ]
    return public


def attach_external_routes(app, store, engine, runtime, profiles, body, ensure_idle):
    def fixed_engine():
        profile_id = str(store.profile_id)
        profiles.get(profile_id)
        return runtime.engine(profile_id)

    def service():
        return fixed_engine().external

    def public_summary(current, data):
        runs = current.repository.list_runs(current.profile_id, data["id"], limit=1)
        return _source_summary(data, runs[0] if runs else None)

    @app.get("/api/external/sources")
    async def list_sources():
        current = service()
        items = [
            public_summary(current, current.public_source(row["id"]))
            for row in current.repository.list_sources(current.profile_id)
        ]
        return {"items": items}

    @app.get("/api/external/sources/{source_id}")
    async def source_detail(source_id: str, status: str = "", page: int = 1, limit: int = 100):
        if status not in ("", "matched", "review", "missing", "ignored"):
            raise ValueError("筛选状态无效")
        if isinstance(page, bool) or isinstance(limit, bool) or page < 1 or not 1 <= limit <= 200:
            raise ValueError("分页参数无效")
        current = service()
        data = current.public_source(source_id)
        catalog = {
            str(row.get("id")): row for row in (current.store.get("catalog", []) or [])
            if isinstance(row, dict) and row.get("id") is not None
        }
        rows = [
            _public_track(row, catalog) for row in data["tracks"]
            if not status or row.get("status") == status
        ]
        start = (page - 1) * limit
        return {
            **public_summary(current, data), "tracks": rows[start:start + limit],
            "total": len(rows), "page": page, "limit": limit,
        }

    @app.post("/api/external/import")
    async def import_source(request: Request):
        data = await body(request)
        value = str(data.get("url") or "").strip()
        filename = str(data.get("filename") or "").strip()
        encoded = data.get("content_base64")
        if value:
            validate_source_reference(value)
            content = None
        else:
            if not filename or not isinstance(encoded, str):
                raise ValueError("请填写歌单链接或选择文件")
            try:
                content = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                raise ValueError("上传文件编码无效") from None
            if len(content) > 2 * 1024 * 1024:
                raise ValueError("上传文件超过2MB")
        ensure_idle()
        target = fixed_engine()
        return target.start_job("external_import", value=value or None, filename=filename or None, content=content)

    @app.post("/api/external/sources/{source_id}/refresh")
    async def refresh_source(source_id: str, request: Request):
        data = await body(request)
        if data.get("confirm_large_removal") not in (None, True, False):
            raise ValueError("刷新确认状态无效")
        service().repository.get_source(service().profile_id, source_id)
        ensure_idle()
        return fixed_engine().start_job(
            "external_refresh", source_id=source_id,
            force=bool(data.get("confirm_large_removal")), bypass_retry=True,
        )

    @app.post("/api/external/sources/{source_id}/confirm")
    async def confirm_track(source_id: str, request: Request):
        data = await body(request)
        track_key = str(data.get("track_key") or "")
        choice = data.get("choice")
        if not track_key or not isinstance(choice, dict):
            raise ValueError("歌曲确认内容无效")
        ensure_idle()
        target = fixed_engine()
        with target.exclusive():
            return target.external.confirm(source_id, track_key, choice)

    @app.post("/api/external/sources/{source_id}/publish")
    async def publish_source(source_id: str, request: Request):
        data = await body(request)
        if data.get("confirm") is not True:
            raise SafetyError("请明确确认在 Plex 创建或更新这个歌单")
        current = service().repository.get_source(service().profile_id, source_id)
        revision = str(data.get("revision") or "")
        if revision != current["revision"]:
            raise SafetyError("外部歌单已经变化，请刷新后再发布")
        title = str(data.get("title") or "").strip()
        if not title:
            raise ValueError("请填写 Plex 歌单名称")
        ensure_idle()
        return fixed_engine().start_job(
            "external_publish", source_id=source_id, title=title, expected_revision=revision
        )

    @app.post("/api/external/sources/{source_id}/settings")
    async def source_settings(source_id: str, request: Request):
        data = await body(request)
        enabled = data.get("follow_updates")
        if not isinstance(enabled, bool):
            raise ValueError("自动刷新开关无效")
        ensure_idle()
        target = fixed_engine()
        with target.exclusive():
            return target.external.set_follow_updates(source_id, enabled)

    @app.post("/api/external/sources/{source_id}/remove")
    async def remove_source(source_id: str, request: Request):
        data = await body(request)
        if data.get("confirm") is not True:
            raise SafetyError("请明确确认移除外部歌单")
        current_service = service()
        source = current_service.public_source(source_id)
        expected = source["managed"]["title"] if source.get("managed") else source["title"]
        title = str(data.get("title") or "")
        if title != expected:
            raise SafetyError("确认名称不一致，不删除")
        ensure_idle()
        return fixed_engine().start_job("external_delete", source_id=source_id, confirm_title=title)

    @app.get("/api/external/sources/{source_id}/export")
    async def export_missing(source_id: str, format: str = "text"):
        data = service().public_source(source_id)
        if format == "text":
            content = format_missing_text(data["tracks"]).encode("utf-8")
            extension, media_type = "txt", "text/plain; charset=utf-8"
        elif format == "csv":
            content = format_missing_csv(data["tracks"])
            extension, media_type = "csv", "text/csv; charset=utf-8"
        else:
            raise ValueError("只支持 text 或 csv 导出")
        filename = missing_download_name(data["title"], extension)
        disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
        return Response(content, media_type=media_type, headers={"Content-Disposition": disposition})

    @app.get("/api/external/sources/{source_id}/tracks/{track_key}/audio")
    def audition_track(source_id: str, track_key: str, request: Request, candidate: str = "", profile_id: str = ""):
        selected_profile = str(profile_id or store.profile_id)
        with profiles.fixed_active(selected_profile, enabled_only=True):
            current = runtime.engine(selected_profile).external
            return stream_local_audio(
                current.store, current.plex_factory, source_id, track_key,
                str(request.headers.get("range") or ""),
                str(request.cookies.get(COOKIE_NAME) or ""), candidate_id=candidate,
            )
