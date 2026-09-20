"""Unified, profile-scoped browsing and management for assistant-owned playlists."""
from __future__ import annotations

import time
import uuid
from urllib.parse import quote

from fastapi import Request
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from .auth import COOKIE_NAME
from .engine import SafetyError, fingerprint
from .external_audio import stream_track_audio
from .external_playlist_sync import external_marker
from .external_store import ExternalRepository
from .playlist_ownership import legacy_external_marker, legacy_pch_marker, replace_marker


KIND_LABELS = {
    "daily": "每日推荐",
    "smart": "智能歌单",
    "category": "分类歌单",
    "external": "外部歌单",
}

MAX_ARTWORK_BYTES = 12 * 1024 * 1024


def _safe_key(value):
    value = str(value or "").strip()
    if not value or len(value) > 120 or any(ord(char) < 32 for char in value):
        raise ValueError("歌单标识无效")
    return value


def _item(kind, key, record, count, updated_at, manage_url):
    if record.get("count") is not None:
        count = record.get("count")
    return {
        "kind": kind,
        "kind_label": KIND_LABELS[kind],
        "key": str(key),
        "playlist_id": str(record.get("id") or ""),
        "title": str(record.get("title") or KIND_LABELS[kind]),
        "count": None if count is None else max(0, int(count)),
        "updated_at": float(updated_at or 0),
        "manage_url": manage_url,
        "status": "已建立",
    }


def _plan_count(plan):
    if not isinstance(plan, dict):
        return None
    items = plan.get("items")
    if isinstance(items, list):
        return len(items)
    result = plan.get("result") or {}
    return int(result["written"]) if str(result.get("written", "")).isdigit() else None


def playlist_rows(store):
    """Return every playlist owned by this profile without making Plex network calls."""
    rows = []
    daily = store.get("daily_managed") or {}
    if daily:
        published = store.get("daily_published_view") or {}
        count = published.get("count")
        if count is None and isinstance(published.get("items"), list):
            count = len(published["items"])
        rows.append(_item(
            "daily", "daily", daily, count,
            daily.get("published_at") or published.get("published_at"), "/daily",
        ))
    else:
        rows.append({
            "kind": "daily", "kind_label": KIND_LABELS["daily"], "key": "daily",
            "playlist_id": "", "title": KIND_LABELS["daily"], "count": 0,
            "updated_at": 0, "manage_url": "/daily", "status": "未建立",
        })

    smart_plans = store.get("smart_mix_plans", {}) or {}
    for kind, record in (store.get("smart_mix_managed", {}) or {}).items():
        rows.append(_item(
            "smart", kind, record, _plan_count(smart_plans.get(kind)),
            record.get("updated_at"), "/mixes",
        ))

    groups = {
        str(group.get("id") or group.get("category_id") or ""): group
        for group in ((store.get("plan") or {}).get("groups") or [])
        if isinstance(group, dict)
    }
    snapshots = list(store.get("snapshots", []) or [])
    for category_id, record in (store.get("managed", {}) or {}).items():
        group = groups.get(str(category_id), {})
        desired = group.get("desired")
        count = len(desired) if isinstance(desired, list) else None
        updated = max([
            float(row.get("created_at") or 0) for row in snapshots
            if str(row.get("category_id") or "") == str(category_id)
        ] or [0])
        rows.append(_item("category", category_id, record, count, updated, "/library"))

    profile_id = str(getattr(store, "profile_id", "default") or "default")
    repository = ExternalRepository(store)
    for source in repository.list_sources(profile_id):
        managed = repository.get_managed(profile_id, source["id"])
        if not managed:
            continue
        matched = sum(
            row.get("status") == "matched"
            for row in repository.list_matches(profile_id, source["id"])
        )
        rows.append(_item(
            "external", source["id"], managed, matched, source.get("fetched_at"),
            "/external?source=" + quote(source["id"]),
        ))
    return rows


def _playlist_record(engine, kind, key):
    kind, key = str(kind or ""), _safe_key(key)
    store = engine.store
    if kind == "daily" and key == "daily":
        record = store.get("daily_managed")
        marker = engine.marker("daily")
    elif kind == "smart":
        record = (store.get("smart_mix_managed", {}) or {}).get(key)
        marker = engine.marker("smart:" + key)
    elif kind == "category":
        record = (store.get("managed", {}) or {}).get(key)
        marker = engine.marker(key)
    elif kind == "external":
        profile_id = str(getattr(store, "profile_id", "default") or "default")
        record = ExternalRepository(store).get_managed(profile_id, key)
        marker = external_marker(store.get("installation_id"), key)
    else:
        raise ValueError("歌单类型无效")
    if not isinstance(record, dict) or not record.get("id") or not record.get("title"):
        raise ValueError("当前用户没有这个歌单")
    return record, marker


def _manual_key(kind, key):
    return str(kind) + ":" + str(key)


def apply_manual_edits(store, kind, key, desired):
    """Apply stable user inclusions/exclusions without introducing duplicates."""
    edits = (store.get("playlist_manual_edits", {}) or {}).get(_manual_key(kind, key), {}) or {}
    excluded = {str(value) for value in edits.get("exclude", []) if str(value).isdigit()}
    included = [str(value) for value in edits.get("include", []) if str(value).isdigit()]
    catalog = list(store.get("catalog", []) or [])
    available = {
        str(row.get("id")) for row in catalog
        if isinstance(row, dict) and row.get("available", True) and str(row.get("id") or "").isdigit()
    }
    if not catalog:
        available = {str(value) for value in [*(desired or []), *included] if str(value).isdigit()}
    result, seen = [], set()
    for value in [*map(str, desired or []), *included]:
        if value in excluded or value in seen or value not in available:
            continue
        result.append(value)
        seen.add(value)
    return result


def _save_playlist_record(engine, kind, key, record):
    store = engine.store
    if kind == "daily":
        store.set("daily_managed", record)
    elif kind == "smart":
        rows = dict(store.get("smart_mix_managed", {}) or {})
        rows[key] = record
        store.set("smart_mix_managed", rows)
    elif kind == "category":
        rows = dict(store.get("managed", {}) or {})
        rows[key] = record
        store.set("managed", rows)
    elif kind == "external":
        profile_id = str(getattr(store, "profile_id", "default") or "default")
        ExternalRepository(store).save_managed(profile_id, key, record)
    else:
        raise ValueError("歌单类型无效")


def _pch_category_id(kind, key):
    if kind == "daily":
        return "daily"
    if kind == "smart":
        return "smart:" + str(key)
    if kind == "category":
        return str(key)
    return ""


def _ensure_playlist_ownership(engine, kind, key, record, state, marker, plex):
    """Validate ownership and safely restamp an unchanged pre-restore marker."""
    summary = str(state.get("summary") or "")
    if marker and marker in summary:
        return state, record
    category_id = _pch_category_id(kind, key)
    historical = (
        legacy_external_marker(summary, key)
        if kind == "external"
        else legacy_pch_marker(summary, category_id)
    )
    if (not marker or not historical or not record.get("fingerprint")
            or fingerprint(state) != record.get("fingerprint")):
        raise SafetyError("助手管理标记缺失，不能读取这个歌单")
    previous_ids = [str(row.get("id")) for row in state.get("items", [])]
    plex.update_playlist_summary(record["id"], replace_marker(summary, historical, marker))
    migrated = plex.read_playlist_until(
        record["id"], lambda row: marker in str(row.get("summary") or ""),
    )
    if (str(migrated.get("id")) != str(state.get("id"))
            or str(migrated.get("title")) != str(state.get("title"))
            or [str(row.get("id")) for row in migrated.get("items", [])] != previous_ids
            or marker not in str(migrated.get("summary") or "")):
        raise SafetyError("旧管理标记迁移后回读不一致，停止操作")
    revised = {
        **record, "fingerprint": fingerprint(migrated),
        "marker": marker, "count": len(migrated.get("items", [])),
    }
    _save_playlist_record(engine, kind, key, revised)
    engine.store.log("已安全迁移旧版歌单管理标记：" + str(migrated.get("title") or record.get("title")))
    return migrated, revised


def edit_playlist_track(engine, kind, key, track_id, operation):
    kind, key, track_id = str(kind or ""), _safe_key(key), str(track_id or "")
    if operation not in ("add", "remove") or not track_id.isdigit():
        raise ValueError("歌曲调整请求无效")
    catalog = {
        str(row.get("id")): row for row in (engine.store.get("catalog", []) or [])
        if isinstance(row, dict) and row.get("available", True)
    }
    if track_id not in catalog:
        raise ValueError("这首歌已不在当前曲库中")
    record, marker = _playlist_record(engine, kind, key)
    plex = engine.plex_factory(engine.store.get("settings"))
    before = plex.playlist_state(record["id"])
    before, record = _ensure_playlist_ownership(
        engine, kind, key, record, before, marker, plex,
    )
    if record.get("fingerprint") and fingerprint(before) != record.get("fingerprint"):
        raise SafetyError("歌单已在 Plex 中被修改，请刷新后再操作")
    matching = [row for row in before.get("items", []) if str(row.get("id")) == track_id]
    if operation == "add" and not matching:
        plex.append(record["id"], [track_id])
        after = plex.read_playlist_until(
            record["id"], lambda row: any(str(item.get("id")) == track_id for item in row.get("items", [])),
        )
    elif operation == "remove" and matching:
        item_ids = [str(row.get("item_id") or "") for row in matching]
        if any(not value.isdigit() for value in item_ids):
            raise SafetyError("Plex 歌单条目标识无效，拒绝修改")
        plex.remove_items(record["id"], item_ids)
        after = plex.read_playlist_until(
            record["id"], lambda row: all(str(item.get("id")) != track_id for item in row.get("items", [])),
        )
    else:
        after = before
    actual = any(str(row.get("id")) == track_id for row in after.get("items", []))
    if actual != (operation == "add"):
        raise SafetyError("Plex 没有确认这次歌曲调整，请刷新后核对")
    edits_all = dict(engine.store.get("playlist_manual_edits", {}) or {})
    edit_key = _manual_key(kind, key)
    edits = dict(edits_all.get(edit_key, {}) or {})
    includes = [str(value) for value in edits.get("include", []) if str(value).isdigit() and str(value) != track_id]
    excludes = [str(value) for value in edits.get("exclude", []) if str(value).isdigit() and str(value) != track_id]
    (includes if operation == "add" else excludes).append(track_id)
    edits_all[edit_key] = {"include": includes, "exclude": excludes, "updated_at": time.time()}
    revised = {**record, "fingerprint": fingerprint(after), "count": len(after.get("items", []))}
    _save_playlist_record(engine, kind, key, revised)
    engine.store.set("playlist_manual_edits", edits_all)
    engine.store.log(("已手动加入歌曲：" if operation == "add" else "已从歌单移除歌曲：") + str(catalog[track_id].get("title") or track_id))
    return {"message": "已加入歌单" if operation == "add" else "已从歌单移除", "count": len(after.get("items", []))}


def search_library(store, query, limit=40):
    query = str(query or "").strip().casefold()
    if len(query) < 1 or len(query) > 100:
        raise ValueError("请输入 1—100 个字搜索")
    tokens = [token for token in query.split() if token]
    result = []
    for row in store.get("catalog", []) or []:
        if not isinstance(row, dict) or not row.get("available", True):
            continue
        haystack = " ".join(str(row.get(field) or "") for field in ("title", "artist", "album")).casefold()
        if all(token in haystack for token in tokens):
            result.append({field: row.get(field) for field in ("id", "title", "artist", "album", "duration", "thumb")})
        if len(result) >= max(1, min(int(limit), 100)):
            break
    return result


def playlist_detail(engine, kind, key):
    record, marker = _playlist_record(engine, kind, key)
    plex = engine.plex_factory(engine.store.get("settings"))
    state = plex.playlist_state(record["id"])
    if str(state.get("id") or "") != str(record["id"]):
        raise SafetyError("Plex 歌单标识已经变化")
    if str(state.get("title") or "") != str(record.get("title") or ""):
        raise SafetyError("Plex 歌单名称已经变化，请先核对")
    state, record = _ensure_playlist_ownership(
        engine, str(kind), str(key), record, state, marker, plex,
    )
    catalog = {
        str(row.get("id")): row for row in (engine.store.get("catalog", []) or [])
        if isinstance(row, dict) and row.get("id") is not None
    }
    tracks = []
    for position, playlist_row in enumerate(state.get("items") or [], 1):
        track_id = str(playlist_row.get("id") or "")
        metadata = catalog.get(track_id, {})
        tracks.append({
            "id": track_id,
            "position": position,
            "title": str(playlist_row.get("title") or metadata.get("title") or "未知歌曲"),
            "artist": str(playlist_row.get("artist") or metadata.get("artist") or "未知歌手"),
            "album": str(playlist_row.get("album") or metadata.get("album") or ""),
            "duration": float(playlist_row.get("duration") or metadata.get("duration") or 0),
            "thumb": str(playlist_row.get("thumb") or metadata.get("thumb") or ""),
        })
    return {
        "kind": str(kind), "key": str(key), "playlist_id": str(record["id"]),
        "title": str(state["title"]), "count": len(tracks), "tracks": tracks,
    }


def stream_playlist_audio(engine, kind, key, track_id, range_header, session_key):
    detail = playlist_detail(engine, kind, key)
    track_id = str(track_id or "")
    if track_id not in {row["id"] for row in detail["tracks"]}:
        raise ValueError("当前歌单中没有这首可试听歌曲")
    return stream_track_audio(
        engine.store, engine.plex_factory, track_id, range_header, session_key,
    )


def stream_playlist_artwork(engine, kind, key, track_id):
    detail = playlist_detail(engine, kind, key)
    track_id = str(track_id or "")
    track = next((row for row in detail["tracks"] if row["id"] == track_id), None)
    if not track:
        raise ValueError("当前歌单中没有这首歌曲")
    if not track.get("thumb"):
        raise ValueError("这首歌没有可用封面")
    upstream = engine.plex_factory(engine.store.get("settings")).open_artwork(track["thumb"])
    try:
        size = int(upstream.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        upstream.close()
        raise ValueError("Plex封面长度无效") from None
    if size < 0 or size > MAX_ARTWORK_BYTES:
        upstream.close()
        raise ValueError("Plex封面文件过大")
    content_type = str(upstream.headers.get("Content-Type") or "image/jpeg").split(";", 1)[0]
    if content_type not in ("image/jpeg", "image/png", "image/webp"):
        upstream.close()
        raise ValueError("Plex封面格式无效")

    def chunks():
        total = 0
        for chunk in upstream.iter_content(64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_ARTWORK_BYTES:
                raise ValueError("Plex封面文件过大")
            yield chunk

    return StreamingResponse(
        chunks(), media_type=content_type,
        headers={"Content-Length": str(size)} if size else {},
        background=BackgroundTask(upstream.close),
    )


def _remove_daily(engine, confirm_title, now=None):
    now = time.time() if now is None else float(now)
    store = engine.store
    record = store.get("daily_managed") or {}
    if not record:
        raise SafetyError("当前没有助手托管的每日推荐")
    if any(
        row.get("category_id") == "daily"
        and row.get("status") in ("prepared", "uncertain", "restoring")
        for row in store.get("snapshots", []) or []
    ):
        raise SafetyError("每日推荐有待核对的操作，暂时不能删除")
    plex = engine.plex_factory(store.get("settings"))
    identity = plex.identity()
    current = plex.playlist_state(record["id"])
    current, record = _ensure_playlist_ownership(
        engine, "daily", "daily", record, current, engine.marker("daily"), plex,
    )
    title = str(current.get("title") or "")
    if str(confirm_title or "") != title:
        raise SafetyError("歌单名称已经变化，请刷新后重试")
    if record.get("scope") != engine.daily_scope() or record.get("machine") != identity.get("machine"):
        raise SafetyError("账户、服务器或音乐库已经变化，拒绝删除")
    if fingerprint(current) != record.get("fingerprint"):
        raise SafetyError("每日推荐已被手工修改，拒绝删除")
    snapshot = {
        "id": uuid.uuid4().hex, "kind": "daily_remove", "action": "remove",
        "category_id": "daily", "title": title, "created_at": now,
        "status": "prepared", "before": current, "after": None, "add": [],
        "marker": engine.marker("daily"), "plan_id": "",
        "machine": identity.get("machine", ""), "scope": engine.daily_scope(),
        "before_daily_record": record,
    }
    engine._save_snapshot(snapshot)
    try:
        plex.delete_playlist(record["id"])
        snapshot["status"] = "applied"
        engine._save_snapshot(snapshot)
        settings = dict(store.get("daily_settings", {}) or {})
        settings["enabled"] = False
        store.set_many({
            "daily_managed": None, "daily_plan": None, "daily_published_view": None,
            "daily_settings": settings, "daily_auto_suspension": None,
        })
        store.log("已从首页删除每日推荐歌单：" + title)
        return {"message": "已从 Plex 删除该歌单；音乐文件未删除。"}
    except Exception as exc:
        snapshot.update(status="uncertain", error=str(exc)[:300])
        engine._save_snapshot(snapshot)
        raise SafetyError("删除结果需要核对；助手不会自动重试") from None


def remove_playlist(engine, kind, key, confirm_title):
    kind, key = str(kind or ""), _safe_key(key)
    if kind == "daily" and key == "daily":
        with engine.exclusive():
            return _remove_daily(engine, confirm_title)
    if kind == "smart":
        from .smart_mix_web import remove_smart_mix
        return remove_smart_mix(engine, key, confirm_title)
    if kind == "category":
        from .daily_mix_v036 import remove_managed_playlist
        return remove_managed_playlist(engine, key, confirm_title)
    if kind == "external":
        with engine.exclusive():
            return engine.external.remove(key, confirm_title)
    raise ValueError("歌单类型无效")


def attach_playlist_hub_routes(app, store, runtime, profiles, body, ensure_idle):
    def fixed_engine():
        profile_id = str(store.profile_id)
        profiles.get(profile_id)
        return runtime.engine(profile_id)

    @app.get("/api/playlists")
    def list_playlists():
        return {"items": playlist_rows(fixed_engine().store)}

    @app.get("/api/playlists/search")
    def playlist_search(q: str = "", limit: int = 40):
        return {"items": search_library(fixed_engine().store, q, limit)}

    @app.get("/api/playlists/{kind}/{key}")
    def playlist(kind: str, key: str):
        target = fixed_engine()
        with target.exclusive():
            return playlist_detail(target, kind, key)

    @app.get("/api/playlists/{kind}/{key}/tracks/{track_id}/audio")
    def playlist_audio(
        kind: str, key: str, track_id: str, request: Request, profile_id: str = "",
    ):
        selected_profile = str(profile_id or store.profile_id)
        with profiles.fixed_active(selected_profile, enabled_only=True):
            target = runtime.engine(selected_profile)
            with target.exclusive():
                return stream_playlist_audio(
                    target, kind, key, track_id,
                    str(request.headers.get("range") or ""),
                    str(request.cookies.get(COOKIE_NAME) or ""),
                )

    @app.get("/api/playlists/{kind}/{key}/tracks/{track_id}/artwork")
    def playlist_artwork(
        kind: str, key: str, track_id: str, profile_id: str = "",
    ):
        selected_profile = str(profile_id or store.profile_id)
        with profiles.fixed_active(selected_profile, enabled_only=True):
            target = runtime.engine(selected_profile)
            with target.exclusive():
                return stream_playlist_artwork(target, kind, key, track_id)

    @app.post("/api/playlists/remove")
    async def remove(request: Request):
        data = await body(request)
        if data.get("confirm") is not True:
            raise SafetyError("请确认删除助手托管的歌单")
        ensure_idle()
        target = fixed_engine()
        kind = str(data.get("kind") or "")
        return remove_playlist(
            target, kind, data.get("key"), str(data.get("title") or ""),
        )

    @app.post("/api/playlists/tracks/edit")
    async def edit_track(request: Request):
        data = await body(request)
        if data.get("confirm") is not True:
            raise SafetyError("请确认修改歌单歌曲")
        ensure_idle()
        target = fixed_engine()
        with target.exclusive():
            return edit_playlist_track(
                target, str(data.get("kind") or ""), data.get("key"),
                data.get("track_id"), str(data.get("operation") or ""),
            )
