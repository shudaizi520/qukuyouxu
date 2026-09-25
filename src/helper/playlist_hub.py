"""Unified, profile-scoped browsing and management for assistant-owned playlists."""
from __future__ import annotations

import time
import uuid
import math
import hashlib
import hmac
from urllib.parse import quote

from fastapi import Request, Response
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from .auth import COOKIE_NAME
from .clients import PlexError
from .engine import SafetyError, fingerprint
from .external_audio import stream_track_audio
from .external_playlist_sync import external_marker, rename_owned_external_playlist
from .external_store import ExternalRepository
from .favorite_smart import ensure_profile_favorites
from .lyrics import read_track_lyrics
from .playlist_inventory import assistant_playlist_row, merge_playlist_rows
from .playlist_ownership import legacy_external_marker, legacy_pch_marker, replace_marker
from .profiles import profile_identity


KIND_LABELS = {
    "daily": "每日推荐",
    "smart": "智能歌单",
    "favorite": "我喜欢",
    "category": "分类歌单",
    "external": "外部歌单",
}

MAX_ARTWORK_BYTES = 12 * 1024 * 1024


def artwork_etag(store, session_key, profile_id, profile_revision, path, representation, period, *, now=None):
    """A short-lived validator bound to one login and one profile incarnation."""
    if not session_key:
        return ''
    now = time.time() if now is None else float(now)
    secret = str(store.get('installation_id') or '').encode()
    message = '\0'.join((str(session_key), str(profile_id), str(profile_revision),
                         str(path), str(representation), str(int(now // period)))).encode()
    return 'W/"' + hmac.new(secret, message, hashlib.sha256).hexdigest()[:32] + '"'


def _safe_number(value, default=0.0):
    try:
        number = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return float(default)
    return number if math.isfinite(number) else float(default)


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


def assistant_playlist_rows(store):
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
            daily.get("published_at") or published.get("published_at"), "/mixes",
        ))
    else:
        rows.append({
            "kind": "daily", "kind_label": KIND_LABELS["daily"], "key": "daily",
            "playlist_id": "", "title": KIND_LABELS["daily"], "count": 0,
            "updated_at": 0, "manage_url": "/mixes", "status": "未建立",
        })

    smart_plans = store.get("smart_mix_plans", {}) or {}
    for kind, record in (store.get("smart_mix_managed", {}) or {}).items():
        rows.append(_item(
            "smart", kind, record, _plan_count(smart_plans.get(kind)),
            record.get("updated_at"), "/mixes",
        ))

    favorite_state = store.get("favorite_smart_v2") or {}
    favorite_playlist_id = str(favorite_state.get("playlist_id") or "")
    favorite_owned = (favorite_playlist_id.isdigit()
                      and str(favorite_state.get("section") or "")
                      == str((store.get("settings") or {}).get("section") or ""))
    favorite_synced = favorite_owned and favorite_state.get("status") == "synced"
    rows.append({
        "kind": "favorite", "kind_label": KIND_LABELS["favorite"], "key": "liked",
        "playlist_id": favorite_playlist_id if favorite_owned else "",
        "title": KIND_LABELS["favorite"], "count": None,
        "updated_at": 0, "manage_url": "",
        "status": "上次已同步 Plex" if favorite_synced else "未同步 Plex",
    })

    managed = store.get("managed", {}) or {}
    groups = store.plan_group_counts() if managed else {}
    snapshots = list(store.get("snapshots", []) or [])
    for category_id, record in managed.items():
        count = groups.get(str(category_id))
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


def playlist_rows(engine, hidden_playlist_ids=()):
    """Return assistant and native Plex playlists, retaining the last good native list."""
    store = engine.store
    hidden_playlist_ids = {str(value) for value in hidden_playlist_ids}
    assistant = [assistant_playlist_row(row) for row in assistant_playlist_rows(store)]
    plex = engine.plex_factory(store.get("settings"))
    try:
        section = str((store.get("settings") or {}).get("section") or "")
        native_rows = []
        for row in plex.playlists():
            row = dict(row)
            if str(row.get("ratingKey") or "") in hidden_playlist_ids:
                continue
            if str(row.get("smart") or "") == "1" and section:
                try:
                    source_section = plex.playlist_source_section(row.get("ratingKey"))
                except Exception:
                    continue
                if source_section != section:
                    continue
                row["source_section"] = source_section
            native_rows.append(row)
        merged = merge_playlist_rows(assistant, native_rows)
        native = [dict(row) for row in merged if row.get("source") == "plex"]
        store.set("playlist_native_cache_v1", native)
        return merged
    except Exception:
        cached = []
        section = str((store.get("settings") or {}).get("section") or "")
        for row in store.get("playlist_native_cache_v1", []) or []:
            if not isinstance(row, dict) or row.get("source") != "plex":
                continue
            if str(row.get("playlist_id") or "") in hidden_playlist_ids:
                continue
            if section and row.get("smart") and str(row.get("source_section") or "") != section:
                continue
            cached.append({**row, "stale": True})
        return [
            {**row, "stale": True} if row.get("source") == "plex" else row
            for row in merge_playlist_rows(assistant, cached)
        ]


def _native_playlist(plex, playlist_id, section="", hidden_playlist_ids=()):
    playlist_id = str(playlist_id or "")
    if not playlist_id.isdigit():
        raise ValueError("歌单标识无效")
    if playlist_id in {str(value) for value in hidden_playlist_ids}:
        raise ValueError("这个歌单属于其他曲库，不能在当前曲库中修改")
    listed = {
        str(row.get("ratingKey") or ""): row for row in plex.playlists()
        if isinstance(row, dict) and row.get("playlistType") == "audio"
    }
    if playlist_id not in listed:
        raise ValueError("当前 Plex 账户没有这个音乐歌单")
    if str(listed[playlist_id].get("smart") or "") == "1" and section:
        source_section = plex.playlist_source_section(playlist_id)
        if source_section != str(section):
            raise ValueError("这个智能歌单属于其他曲库，请切换到对应曲库查看")
    state = plex.playlist_view(playlist_id)
    if str(state.get("id") or "") != playlist_id:
        raise SafetyError("Plex 歌单标识已经变化")
    return listed[playlist_id], state


def _native_smart(listed, state):
    return bool(state.get("smart")) or str(listed.get("smart") or "") == "1"


def _edit_native_playlist_track(plex, key, track_id, operation, section="", hidden_playlist_ids=()):
    listed, before = _native_playlist(plex, key, section, hidden_playlist_ids)
    if _native_smart(listed, before):
        raise ValueError("Plex 智能歌单不能逐首添加或移除")
    matching = [row for row in before.get("items", []) if str(row.get("id")) == track_id]
    if operation == "add" and not matching:
        plex.append(key, [track_id])
        after = plex.read_playlist_view_until(
            key, lambda row: any(str(item.get("id")) == track_id for item in row.get("items", [])),
        )
    elif operation == "remove" and matching:
        item_ids = [str(row.get("item_id") or "") for row in matching]
        if any(not value.isdigit() for value in item_ids):
            raise SafetyError("Plex 歌单条目标识无效，拒绝修改")
        plex.remove_items(key, item_ids)
        after = plex.read_playlist_view_until(
            key, lambda row: all(str(item.get("id")) != track_id for item in row.get("items", [])),
        )
    else:
        after = before
    actual = any(str(row.get("id")) == track_id for row in after.get("items", []))
    if actual != (operation == "add"):
        raise SafetyError("Plex 没有确认这次歌曲调整，请刷新后核对")
    return {
        "message": "已加入歌单" if operation == "add" else "已从歌单移除",
        "count": len(after.get("items", [])),
    }


def rename_playlist(engine, kind, key, title, hidden_playlist_ids=()):
    kind, key, title = str(kind or ""), _safe_key(key), str(title or "").strip()
    if not title or len(title) > 80 or any(ord(char) < 32 for char in title):
        raise ValueError("歌单标题无效")
    plex = engine.plex_factory(engine.store.get("settings"))
    if kind == "external":
        store = engine.store
        profile_id = str(getattr(store, "profile_id", "default") or "default")
        repository = ExternalRepository(store)
        managed = repository.get_managed(profile_id, key)
        if not managed:
            raise ValueError("当前用户没有这个导入歌单")
        _after, revised = rename_owned_external_playlist(
            plex, store.get("installation_id"), key, managed, title,
        )
        repository.save_managed(profile_id, key, revised)
        return {"message": "歌单已重命名", "title": title}
    if kind != "plex":
        raise ValueError("这个歌单请在对应管理页调整名称")
    section = str((engine.store.get("settings") or {}).get("section") or "")
    _listed, before = _native_playlist(plex, key, section, hidden_playlist_ids)
    if str(before.get("title") or "") != title:
        plex.rename(key, title)
        after = plex.read_playlist_view_until(
            key, lambda row: str(row.get("id") or "") == key and str(row.get("title") or "") == title,
        )
    else:
        after = before
    if str(after.get("id") or "") != key or str(after.get("title") or "") != title:
        raise SafetyError("Plex 没有确认歌单重命名，请刷新后核对")
    return {"message": "歌单已重命名", "title": title}


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


def _ensure_playlist_ownership(engine, kind, key, record, state, marker, plex, *, migrate=True):
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
    if not migrate:
        return state, record
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


def edit_playlist_track(engine, kind, key, track_id, operation, hidden_playlist_ids=()):
    kind, key, track_id = str(kind or ""), _safe_key(key), str(track_id or "")
    if operation not in ("add", "remove") or not track_id.isdigit():
        raise ValueError("歌曲调整请求无效")
    if operation == "add" and kind in {"daily", "smart", "category"}:
        raise ValueError("自动生成的歌单不能手动添加歌曲，请调整生成规则")
    catalog = {
        str(row.get("id")): row for row in (engine.store.get("catalog", []) or [])
        if isinstance(row, dict) and row.get("available", True)
    }
    if kind == "plex":
        plex = engine.plex_factory(engine.store.get("settings"))
        section = str((engine.store.get("settings") or {}).get("section") or "")
        if track_id not in catalog and (not section.isdigit() or plex.track_section(track_id) != section):
            raise ValueError("这首歌不属于当前曲库")
        return _edit_native_playlist_track(plex, key, track_id, operation, section, hidden_playlist_ids)
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


def _search_library_rows(rows, query, limit=40):
    query = str(query or "").strip().casefold()
    if len(query) < 1 or len(query) > 100:
        raise ValueError("请输入 1—100 个字搜索")
    tokens = [token for token in query.split() if token]
    result = []
    for row in rows or []:
        if not isinstance(row, dict) or not row.get("available", True):
            continue
        haystack = " ".join(str(row.get(field) or "") for field in ("title", "artist", "album")).casefold()
        if all(token in haystack for token in tokens):
            item = {field: row.get(field) for field in (
                "id", "title", "artist", "album", "duration", "thumb", "user_rating",
            )}
            item["user_rating"] = _safe_number(item.get("user_rating"))
            item["liked"] = item["user_rating"] >= 8
            result.append(item)
        if len(result) >= max(1, min(int(limit), 100)):
            break
    return result


def search_library(store, query, limit=40):
    return _search_library_rows(store.get("catalog", []), query, limit)


def search_library_live(engine, query, limit=40):
    settings = engine.store.get("settings") or {}
    section = str(settings.get("section") or "")
    if not section.isdigit():
        raise ValueError("请先选择 Plex 音乐库")
    if not str(query or "").strip() or len(str(query).strip()) > 100:
        raise ValueError("请输入 1—100 个字搜索")
    return _search_library_rows(engine.plex_factory(settings).tracks(section), query, limit)


def favorite_playlist_detail(store, catalog=None):
    tracks = []
    for row in (store.get("catalog", []) if catalog is None else catalog) or []:
        if not isinstance(row, dict) or not row.get("available", True):
            continue
        rating = _safe_number(row.get("user_rating"))
        if rating < 8:
            continue
        tracks.append({
            "id": str(row.get("id") or ""), "position": len(tracks) + 1,
            "title": str(row.get("title") or "未知歌曲"),
            "artist": str(row.get("artist") or "未知歌手"),
            "album": str(row.get("album") or ""),
            "duration": float(row.get("duration") or 0),
            "thumb": str(row.get("thumb") or ""),
            "user_rating": rating, "liked": True,
        })
    return {
        "kind": "favorite", "key": "liked", "playlist_id": "",
        "title": "我喜欢", "count": len(tracks), "tracks": tracks,
        "unavailable_count": 0,
    }


def favorite_playlist_detail_live(engine):
    settings = engine.store.get("settings") or {}
    section = str(settings.get("section") or "")
    catalog = engine.plex_factory(settings).tracks(section) if section.isdigit() else []
    return favorite_playlist_detail(engine.store, catalog)


def related_favorite_profile_ids(profiles, current_profile_id):
    # An app profile is one Plex user *and* one music library. Favorites from
    # another library must never leak into this profile's virtual playlist.
    profiles.get(current_profile_id)
    return [current_profile_id]


def sibling_owned_playlist_ids(profiles, runtime, current_profile_id):
    owned = set()
    identity = profile_identity(profiles.get(current_profile_id))[:3]
    if not identity[1] or not identity[2]:
        return owned
    for profile in profiles.list_public(enabled_only=False):
        profile_id = profile["id"]
        if profile_id == current_profile_id:
            continue
        if profile_identity(profile)[:3] != identity:
            continue
        for row in assistant_playlist_rows(runtime.engine(profile_id).store):
            playlist_id = str(row.get("playlist_id") or "")
            if playlist_id.isdigit():
                owned.add(playlist_id)
        for playlist_id in runtime.engine(profile_id).store.get("manual_plex_playlist_ids", []) or []:
            if str(playlist_id).isdigit():
                owned.add(str(playlist_id))
    return owned


def create_manual_playlist(engine, title):
    title = str(title or "").strip()
    if not title or len(title) > 80 or any(ord(char) < 32 for char in title):
        raise ValueError("歌单名称需为 1—80 个字，且不能包含控制字符")
    store = engine.store
    plex = engine.plex_factory(store.get("settings"))
    try:
        state = plex.create_blank(title, (store.get("settings") or {}).get("section"))
    except PlexError as exc:
        raise SafetyError(str(exc)[:300]) from None
    playlist_id = str(state.get("id") or "")
    if not playlist_id.isdigit() or state.get("smart") or state.get("items"):
        raise SafetyError("Plex 创建结果需要核对；不会自动重试创建")
    owned = [str(value) for value in (store.get("manual_plex_playlist_ids", []) or [])
             if str(value).isdigit()]
    if playlist_id not in owned:
        store.set("manual_plex_playlist_ids", [*owned, playlist_id])
    try:
        rows = plex.read_playlists_until(
            lambda items: any(str(row.get("ratingKey") or "") == playlist_id for row in items),
        )
    except Exception:
        raise SafetyError("Plex 已返回新歌单编号，但列表读取失败；请刷新，不要重复创建") from None
    matching = next((row for row in rows if str(row.get("ratingKey") or "") == playlist_id), None)
    if not matching:
        raise SafetyError("Plex 已返回新歌单编号，但列表暂未同步；请刷新，不要重复创建")
    if matching.get("playlistType") != "audio" or str(matching.get("smart") or "0") != "0":
        raise SafetyError("Plex 返回的歌单类型与普通音乐歌单不符，请核对，不要重复创建")
    return {"kind": "plex", "key": playlist_id, "title": str(state.get("title") or title)}


def resolve_favorite_profile_id(profiles, current_profile_id, requested_profile_id):
    target = str(requested_profile_id or current_profile_id)
    if target not in related_favorite_profile_ids(profiles, current_profile_id):
        raise ValueError("只能修改当前曲库的喜欢状态")
    return target


def favorite_playlist_detail_for_profiles(profiles, runtime, current_profile_id):
    tracks = []
    for profile_id in related_favorite_profile_ids(profiles, current_profile_id):
        engine = runtime.engine(profile_id)
        detail = favorite_playlist_detail_live(engine)
        for row in detail["tracks"]:
            tracks.append({**row, "profile_id": profile_id, "position": len(tracks) + 1})
    return {
        "kind": "favorite", "key": "liked", "playlist_id": "",
        "title": "我喜欢", "count": len(tracks), "tracks": tracks,
        "unavailable_count": 0,
    }


def set_track_liked(engine, track_id, liked, now=None):
    track_id = str(track_id or "")
    if not track_id.isdigit() or not isinstance(liked, bool):
        raise ValueError("喜欢状态无效")
    settings = engine.store.get("settings") or {}
    plex = engine.plex_factory(settings)
    section = str(settings.get("section") or "")
    if not section.isdigit() or plex.track_section(track_id) != section:
        raise ValueError("这首歌不属于当前曲库")
    catalog = list(engine.store.get("catalog", []) or [])
    index = next((
        position for position, row in enumerate(catalog)
        if isinstance(row, dict) and str(row.get("id") or "") == track_id
        and row.get("available", True)
    ), None)
    expected = 10.0 if liked else 0.0
    actual = float(plex.rate_track(track_id, expected))
    if actual != expected:
        raise SafetyError("Plex 评分回读不一致，喜欢状态未保存")
    if index is not None:
        revised = dict(catalog[index])
        revised["user_rating"] = expected
        catalog[index] = revised
        engine.store.set("catalog", catalog)
    return {"track_id": track_id, "liked": liked, "user_rating": expected}


def playlist_detail(engine, kind, key, hidden_playlist_ids=(), *, migrate_ownership=True, include_catalog=True):
    if str(kind) == "favorite" and str(key) == "liked":
        return favorite_playlist_detail_live(engine)
    if str(kind) == "plex":
        key = _safe_key(key)
        if not key.isdigit():
            raise ValueError("歌单标识无效")
        plex = engine.plex_factory(engine.store.get("settings"))
        section = str((engine.store.get("settings") or {}).get("section") or "")
        listed, state = _native_playlist(plex, key, section, hidden_playlist_ids)
        catalog = engine.store.catalog_tracks(
            [row.get("id") for row in state.get("items") or []]
        ) if include_catalog else {}
        catalog = {track_id: row for track_id, row in catalog.items()
                   if row.get("available", True)}
        tracks = []
        for playlist_row in state.get("items") or []:
            track_id = str(playlist_row.get("id") or "")
            source_section=str(playlist_row.get("library_section_id") or "")
            if not track_id.isdigit():
                continue
            if not source_section:
                try:
                    source_section = plex.track_section(track_id)
                except PlexError:
                    continue
            if source_section != section:
                continue
            metadata = catalog.get(track_id) or {}
            plex_rating = playlist_row.get("user_rating")
            rating=_safe_number(metadata.get("user_rating") if plex_rating is None else plex_rating)
            tracks.append({
                "id": track_id, "position": len(tracks) + 1,
                "title": str(playlist_row.get("title") or metadata.get("title") or "未知歌曲"),
                "artist": str(playlist_row.get("artist") or metadata.get("artist") or "未知歌手"),
                "album": str(playlist_row.get("album") or metadata.get("album") or ""),
                "duration": float(playlist_row.get("duration") or metadata.get("duration") or 0),
                "thumb": str(playlist_row.get("thumb") or metadata.get("thumb") or ""),
                "user_rating": rating,
                "liked": rating >= 8,
            })
        return {
            "kind": "plex", "key": key, "playlist_id": key,
            "title": str(state.get("title") or listed.get("title") or "未命名歌单"),
            "smart": bool(state.get("smart")), "count": len(tracks), "tracks": tracks,
            "unavailable_count": max(0, len(state.get("items") or []) - len(tracks)),
        }
    record, marker = _playlist_record(engine, kind, key)
    plex = engine.plex_factory(engine.store.get("settings"))
    state = plex.playlist_state(record["id"])
    if str(state.get("id") or "") != str(record["id"]):
        raise SafetyError("Plex 歌单标识已经变化")
    if str(state.get("title") or "") != str(record.get("title") or ""):
        raise SafetyError("Plex 歌单名称已经变化，请先核对")
    state, record = _ensure_playlist_ownership(
        engine, str(kind), str(key), record, state, marker, plex,
        migrate=migrate_ownership,
    )
    catalog = engine.store.catalog_tracks(
        [row.get("id") for row in state.get("items") or []]
    ) if include_catalog else {}
    tracks = []
    for position, playlist_row in enumerate(state.get("items") or [], 1):
        track_id = str(playlist_row.get("id") or "")
        metadata = catalog.get(track_id, {})
        plex_rating = playlist_row.get("user_rating")
        rating = _safe_number(metadata.get("user_rating") if plex_rating is None else plex_rating)
        tracks.append({
            "id": track_id,
            "position": position,
            "title": str(playlist_row.get("title") or metadata.get("title") or "未知歌曲"),
            "artist": str(playlist_row.get("artist") or metadata.get("artist") or "未知歌手"),
            "album": str(playlist_row.get("album") or metadata.get("album") or ""),
            "duration": float(playlist_row.get("duration") or metadata.get("duration") or 0),
            "thumb": str(playlist_row.get("thumb") or metadata.get("thumb") or ""),
            "user_rating": rating,
            "liked": rating >= 8,
        })
    return {
        "kind": str(kind), "key": str(key), "playlist_id": str(record["id"]),
        "title": str(state["title"]), "count": len(tracks), "tracks": tracks,
    }


def playlist_cover_candidates(engine, kind, key, hidden_playlist_ids=()):
    """Return a small, profile-scoped set of tracks with Plex artwork."""
    detail = playlist_detail(engine, kind, key, hidden_playlist_ids,
                             migrate_ownership=False)
    track_ids = []
    for row in detail.get("tracks") or []:
        track_id = str(row.get("id") or "")
        if track_id.isdigit() and row.get("thumb") and track_id not in track_ids:
            track_ids.append(track_id)
            if len(track_ids) == 4:
                break
    return {"track_ids": track_ids}


def stream_playlist_audio(engine, kind, key, track_id, range_header, session_key, offset_seconds=0, hidden_playlist_ids=()):
    detail = playlist_detail(engine, kind, key, hidden_playlist_ids, include_catalog=False)
    track_id = str(track_id or "")
    if track_id not in {row["id"] for row in detail["tracks"]}:
        raise ValueError("当前歌单中没有这首可试听歌曲")
    return stream_track_audio(
        engine.store, engine.plex_factory, track_id, range_header, session_key,
        offset_seconds, allow_uncached=True,
    )


def stream_library_audio(engine, track_id, range_header, session_key, offset_seconds=0):
    return stream_track_audio(
        engine.store, engine.plex_factory, str(track_id), range_header, session_key,
        offset_seconds, allow_uncached=True,
    )


def _stream_artwork(engine, track):
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


def stream_playlist_artwork(engine, kind, key, track_id, hidden_playlist_ids=()):
    return _stream_artwork(engine, playlist_artwork_track(engine, kind, key, track_id, hidden_playlist_ids))


def playlist_artwork_track(engine, kind, key, track_id, hidden_playlist_ids=()):
    detail = playlist_detail(engine, kind, key, hidden_playlist_ids)
    track_id = str(track_id or "")
    track = next((row for row in detail["tracks"] if row["id"] == track_id), None)
    if not track:
        raise ValueError("当前歌单中没有这首歌曲")
    return track


def stream_library_artwork(engine, track_id):
    return _stream_artwork(engine, library_artwork_track(engine, track_id))


def library_artwork_track(engine, track_id):
    settings = engine.store.get("settings") or {}
    section = str(settings.get("section") or "")
    track = engine.plex_factory(settings).track_metadata(track_id)
    if (not section.isdigit() or str(track.get("library_section_id") or "") != section
            or not track.get("available", True)):
        raise ValueError("这首歌不属于当前曲库")
    if not track.get("thumb"):
        cached = engine.store.catalog_tracks([track_id]).get(str(track_id)) or {}
        track = {**track, "thumb": cached.get("thumb") or ""}
    return track


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
            "daily_auto_opt_out": True,
        })
        store.log("已从首页删除每日推荐歌单：" + title)
        return {"message": "已从 Plex 删除该歌单；音乐文件未删除。"}
    except Exception as exc:
        snapshot.update(status="uncertain", error=str(exc)[:300])
        engine._save_snapshot(snapshot)
        raise SafetyError("删除结果需要核对；助手不会自动重试") from None


def remove_playlist(engine, kind, key, confirm_title, hidden_playlist_ids=()):
    kind, key = str(kind or ""), _safe_key(key)
    if kind == "daily" and key == "daily":
        with engine.exclusive():
            return _remove_daily(engine, confirm_title)
    if kind == "smart":
        from .smart_mix_web import remove_smart_mix
        return remove_smart_mix(engine, key, confirm_title)
    if kind == "category":
        from .plex_lifecycle import remove_managed_playlist
        return remove_managed_playlist(engine, key, confirm_title)
    if kind == "external":
        with engine.exclusive():
            return engine.external.remove(key, confirm_title)
    if kind == "plex":
        with engine.exclusive():
            plex = engine.plex_factory(engine.store.get("settings"))
            section = str((engine.store.get("settings") or {}).get("section") or "")
            _listed, current = _native_playlist(plex, key, section, hidden_playlist_ids)
            if str(confirm_title or "") != str(current.get("title") or ""):
                raise SafetyError("歌单名称已经变化，请刷新后重试")
            plex.delete_playlist(key)
            rows = plex.read_playlists_until(
                lambda items: all(str(row.get("ratingKey") or "") != key for row in items),
            )
            if any(str(row.get("ratingKey") or "") == key for row in rows):
                raise SafetyError("Plex 没有确认删除结果，请刷新后核对")
            manual_ids = [str(value) for value in (engine.store.get("manual_plex_playlist_ids", []) or [])]
            if key in manual_ids:
                engine.store.set("manual_plex_playlist_ids", [value for value in manual_ids if value != key])
            return {"message": "已删除歌单；仅删除歌单，不删除音乐文件。"}
    raise ValueError("歌单类型无效")


def attach_playlist_hub_routes(app, store, runtime, profiles, body, ensure_idle):
    def fixed_engine():
        profile_id = str(store.profile_id)
        profiles.get(profile_id)
        return runtime.engine(profile_id)

    @app.get("/api/playlists")
    def list_playlists():
        items = playlist_rows(
            fixed_engine(), sibling_owned_playlist_ids(profiles, runtime, str(store.profile_id)),
        )
        return {"items": items}

    @app.get("/api/playlists/search")
    def playlist_search(q: str = "", limit: int = 40):
        target = fixed_engine()
        try:
            return {"items": search_library_live(target, q, limit)}
        except PlexError:
            return {"items": search_library(target.store, q, limit)}

    @app.post("/api/playlists/create")
    async def create_playlist(request: Request):
        data = await body(request)
        if data.get("confirm") is not True:
            raise SafetyError("请确认新建 Plex 歌单")
        ensure_idle()
        target = fixed_engine()
        with target.exclusive():
            return create_manual_playlist(target, data.get("title"))

    @app.post("/api/playlists/favorite/ensure")
    def ensure_favorite_playlist():
        ensure_idle()
        target = fixed_engine()
        with target.exclusive():
            return ensure_profile_favorites(target)

    @app.get("/api/playlists/library/tracks/{track_id}/audio")
    def library_audio(
        track_id: str, request: Request, profile_id: str = "", offset: str = "0",
    ):
        selected_profile = str(profile_id or store.profile_id)
        with profiles.fixed_active(selected_profile, enabled_only=True):
            target = runtime.engine(selected_profile)
            return stream_library_audio(
                target, track_id, str(request.headers.get("range") or ""),
                str(request.cookies.get(COOKIE_NAME) or ""),
                offset,
            )

    @app.get("/api/playlists/library/tracks/{track_id}/lyrics")
    def library_lyrics(track_id: str, profile_id: str = ""):
        selected_profile = str(profile_id or store.profile_id)
        with profiles.fixed_active(selected_profile, enabled_only=True):
            target = runtime.engine(selected_profile)
            return read_track_lyrics(target, track_id)

    @app.get("/api/playlists/library/tracks/{track_id}/artwork")
    def library_artwork(track_id: str, request: Request, profile_id: str = ""):
        selected_profile = str(profile_id or store.profile_id)
        with profiles.fixed_active(selected_profile, enabled_only=True):
            profile = profiles.get(selected_profile)
            target = runtime.engine(selected_profile)
            track = library_artwork_track(target, track_id)
            tag = artwork_etag(store, request.cookies.get(COOKIE_NAME), selected_profile,
                               profile.get('created_at'), request.url.path, track.get('thumb'), 300)
            if tag and hmac.compare_digest(request.headers.get('if-none-match', ''), tag):
                return Response(status_code=304, headers={'ETag': tag})
            result = _stream_artwork(target, track)
            if tag:
                result.headers['ETag'] = tag
            return result

    @app.get("/api/playlists/{kind}/{key}")
    def playlist(kind: str, key: str):
        if kind == "favorite" and key == "liked":
            return favorite_playlist_detail_for_profiles(
                profiles, runtime, str(store.profile_id),
            )
        target = fixed_engine()
        hidden_ids = sibling_owned_playlist_ids(profiles, runtime, str(store.profile_id)) if kind == "plex" else ()
        with target.exclusive():
            return playlist_detail(target, kind, key, hidden_ids)

    @app.get("/api/playlists/{kind}/{key}/cover")
    def playlist_cover(kind: str, key: str, request: Request, response: Response):
        target = fixed_engine()
        profile_id = str(store.profile_id)
        profile = profiles.get(profile_id)
        hidden_ids = sibling_owned_playlist_ids(profiles, runtime, profile_id) if kind == "plex" else ()
        result = playlist_cover_candidates(target, kind, key, hidden_ids)
        tag = artwork_etag(store, request.cookies.get(COOKIE_NAME), profile_id,
                           profile.get('created_at'), request.url.path,
                           ','.join(result.get('track_ids') or ()), 30)
        if tag and hmac.compare_digest(request.headers.get('if-none-match', ''), tag):
            return Response(status_code=304, headers={'ETag': tag})
        if tag:
            response.headers['ETag'] = tag
        return result

    @app.get("/api/playlists/{kind}/{key}/tracks/{track_id}/audio")
    def playlist_audio(
        kind: str, key: str, track_id: str, request: Request,
        profile_id: str = "", offset: str = "0",
    ):
        selected_profile = str(profile_id or store.profile_id)
        with profiles.fixed_active(selected_profile, enabled_only=True):
            target = runtime.engine(selected_profile)
            hidden_ids = sibling_owned_playlist_ids(profiles, runtime, selected_profile) if kind == "plex" else ()
            with target.exclusive():
                return stream_playlist_audio(
                    target, kind, key, track_id,
                    str(request.headers.get("range") or ""),
                    str(request.cookies.get(COOKIE_NAME) or ""),
                    offset, hidden_ids,
                )

    @app.get("/api/playlists/{kind}/{key}/tracks/{track_id}/artwork")
    def playlist_artwork(
        kind: str, key: str, track_id: str, request: Request, profile_id: str = "",
    ):
        selected_profile = str(profile_id or store.profile_id)
        with profiles.fixed_active(selected_profile, enabled_only=True):
            profile = profiles.get(selected_profile)
            target = runtime.engine(selected_profile)
            hidden_ids = sibling_owned_playlist_ids(profiles, runtime, selected_profile) if kind == "plex" else ()
            with target.exclusive():
                track = playlist_artwork_track(target, kind, key, track_id, hidden_ids)
            tag = artwork_etag(store, request.cookies.get(COOKIE_NAME), selected_profile,
                               profile.get('created_at'), request.url.path, track.get('thumb'), 300)
            if tag and hmac.compare_digest(request.headers.get('if-none-match', ''), tag):
                return Response(status_code=304, headers={'ETag': tag})
            result = _stream_artwork(target, track)
            if tag:
                result.headers['ETag'] = tag
            return result

    @app.post("/api/playlists/remove")
    async def remove(request: Request):
        data = await body(request)
        if data.get("confirm") is not True:
            raise SafetyError("请确认删除助手托管的歌单")
        ensure_idle()
        target = fixed_engine()
        kind = str(data.get("kind") or "")
        hidden_ids = sibling_owned_playlist_ids(profiles, runtime, str(store.profile_id)) if kind == "plex" else ()
        return remove_playlist(
            target, kind, data.get("key"), str(data.get("title") or ""), hidden_ids,
        )

    @app.post("/api/playlists/rename")
    async def rename(request: Request):
        data = await body(request)
        if data.get("confirm") is not True:
            raise SafetyError("请确认重命名歌单")
        ensure_idle()
        target = fixed_engine()
        kind = str(data.get("kind") or "")
        hidden_ids = sibling_owned_playlist_ids(profiles, runtime, str(store.profile_id)) if kind == "plex" else ()
        with target.exclusive():
            return rename_playlist(
                target, kind, data.get("key"),
                str(data.get("title") or ""), hidden_ids,
            )

    @app.post("/api/playlists/tracks/edit")
    async def edit_track(request: Request):
        data = await body(request)
        if data.get("confirm") is not True:
            raise SafetyError("请确认修改歌单歌曲")
        ensure_idle()
        target = fixed_engine()
        kind = str(data.get("kind") or "")
        hidden_ids = sibling_owned_playlist_ids(profiles, runtime, str(store.profile_id)) if kind == "plex" else ()
        with target.exclusive():
            return edit_playlist_track(
                target, kind, data.get("key"),
                data.get("track_id"), str(data.get("operation") or ""), hidden_ids,
            )

    @app.post("/api/playlists/tracks/liked")
    async def set_liked(request: Request):
        data = await body(request)
        if data.get("confirm") is not True:
            raise SafetyError("请确认修改喜欢状态")
        ensure_idle()
        profile_id = resolve_favorite_profile_id(
            profiles, str(store.profile_id), data.get("profile_id"),
        )
        target = runtime.engine(profile_id)
        with target.exclusive():
            return {**set_track_liked(target, data.get("track_id"), data.get("liked")),
                    "profile_id": profile_id}
