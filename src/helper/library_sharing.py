"""Copy owner-managed category playlists into the same Plex library for recipients.

QQ sources and matching stay with the owner. Each recipient owns their Plex copy;
deleting that copy opts out, while a modified copy is left untouched.
"""
from __future__ import annotations

import time

from .clients import PlexNotFound
from .engine import fingerprint, state_ids


STATE_KEY = "library_share_v1"


def _profiles(runtime, owner_id, recipient_id):
    owner = runtime.registry.get(owner_id)
    recipient = runtime.registry.get(recipient_id)
    same_library = (
        owner.get("kind") == "owner"
        and recipient.get("kind") in {"home", "shared"}
        and owner.get("enabled") is not False
        and recipient.get("enabled") is not False
        and str((owner.get("server") or {}).get("machine") or "")
        == str((recipient.get("server") or {}).get("machine") or "")
        and str((owner.get("library") or {}).get("id") or "")
        == str((recipient.get("library") or {}).get("id") or "")
        and str((owner.get("account") or {}).get("id") or "")
        != str((recipient.get("account") or {}).get("id") or "")
    )
    if not same_library or not (owner.get("server") or {}).get("machine") or not (owner.get("library") or {}).get("id"):
        raise ValueError("只同步同一曲库的 Plex 分享用户")
    if not owner.get("token") or not recipient.get("token"):
        raise ValueError("Plex 用户授权已失效，停止同步")
    return owner, recipient


def owner_for_recipient(runtime, recipient_id):
    recipient = runtime.registry.get(recipient_id)
    if recipient.get("kind") not in {"home", "shared"}:
        return None
    matches = []
    for row in runtime.registry.list_public(enabled_only=True):
        if row.get("kind") != "owner":
            continue
        try:
            _profiles(runtime, row["id"], recipient_id)
        except ValueError:
            continue
        matches.append(row["id"])
    return matches[0] if len(matches) == 1 else None


def share_status(runtime, recipient_id):
    owner_id = owner_for_recipient(runtime, recipient_id)
    if not owner_id:
        return {"recipient": False, "items": []}
    owner_store = runtime.engine(owner_id).store
    child_store = runtime.engine(recipient_id).store
    share = child_store.get(STATE_KEY, {}) or {}
    excluded = set(share.get("excluded") or []) if share.get("owner_id") == owner_id else set()
    child_managed = child_store.get("managed", {}) or {}
    items = []
    for category_id, source in (owner_store.get("managed", {}) or {}).items():
        if not isinstance(source, dict) or not source.get("id"):
            continue
        record = child_managed.get(category_id) or {}
        status = "已退出" if category_id in excluded else (
            "已同步" if record.get("shared_from") == owner_id else "等待同步"
        )
        items.append({"id": category_id, "title": source.get("title") or category_id, "status": status})
    return {"recipient": True, "owner_id": owner_id, "items": items,
            "checked_at": share.get("checked_at"),
            "last_result": share.get("last_result") or {}}


def restore_category(runtime, recipient_id, category_id):
    owner_id = owner_for_recipient(runtime, recipient_id)
    if not owner_id:
        raise ValueError("当前用户没有可继承的主账户曲库")
    category_id = str(category_id or "")
    if category_id not in (runtime.engine(owner_id).store.get("managed", {}) or {}):
        raise ValueError("主账户已没有这张分类歌单")
    store = runtime.engine(recipient_id).store
    state = dict(store.get(STATE_KEY, {}) or {})
    if state.get("owner_id") != owner_id or category_id not in (state.get("excluded") or []):
        raise ValueError("这张歌单没有退出同步")
    state["excluded"] = [value for value in state["excluded"] if value != category_id]
    state["checked_at"] = 0
    store.set(STATE_KEY, state)
    return {"message": "已恢复同步；下一次检查会重新建立这张歌单。"}


def _confirmed_state(plex, playlist_id, expected):
    reader = getattr(plex, "read_playlist_until", None)
    state = reader(playlist_id, lambda row: state_ids(row) == expected) if callable(reader) else plex.playlist_state(playlist_id)
    if state_ids(state) != expected:
        raise ValueError("分享歌单写入后回读不一致，停止自动重试")
    return state


def sync_recipient(runtime, owner_id, recipient_id, *, now=None):
    """Synchronize only verified owner categories; never replace personal playlists."""
    owner, recipient = _profiles(runtime, owner_id, recipient_id)
    owner_engine = runtime.engine(owner_id)
    child_engine = runtime.engine(recipient_id)
    owner_store, child_store = owner_engine.store, child_engine.store
    owner_cfg = owner_store.get("settings", {}) or {}
    child_cfg = child_store.get("settings", {}) or {}
    section = str((owner.get("library") or {}).get("id") or "")
    if (str(owner_cfg.get("plex_token") or "") != str(owner.get("token"))
            or str(child_cfg.get("plex_token") or "") != str(recipient.get("token"))
            or str(owner_cfg.get("section") or "") != section
            or str(child_cfg.get("section") or "") != section):
        raise ValueError("Plex 档案连接与曲库身份不一致，停止同步")
    owner_plex = owner_engine.plex_factory(owner_cfg)
    child_plex = child_engine.plex_factory(child_cfg)
    machine = str((owner.get("server") or {}).get("machine") or "")
    if owner_plex.identity().get("machine") != machine or child_plex.identity().get("machine") != machine:
        raise ValueError("Plex 服务器身份发生变化，停止同步")
    if section not in {str(row.get("id")) for row in child_plex.sections()}:
        raise ValueError("分享用户已无权访问该音乐曲库")
    accessible = {str(row.get("id")) for row in child_plex.tracks(section) if row.get("available", True)}
    if not accessible:
        raise ValueError("分享用户当前看不到该曲库歌曲，停止同步")

    state = dict(child_store.get(STATE_KEY, {}) or {})
    if state.get("owner_id") != owner_id:
        state = {"owner_id": owner_id, "excluded": []}
    excluded = set(state.get("excluded") or [])
    managed = dict(child_store.get("managed", {}) or {})
    existing = {str(row.get("title") or "") for row in child_plex.playlists()}
    source_settings = {str(row.get("id")): row for row in owner_store.get("sources", []) or []}
    result = {"created": 0, "updated": 0, "removed": 0, "unchanged": 0, "opted_out": 0,
              "skipped": 0, "errors": []}

    owner_managed = owner_store.get("managed", {}) or {}
    for category_id, record in list(managed.items()):
        if category_id in owner_managed or not isinstance(record, dict) or record.get("shared_from") != owner_id:
            continue
        try:
            current = child_plex.playlist_state(record["id"])
        except PlexNotFound:
            managed.pop(category_id, None)
            excluded.add(category_id)
            result["opted_out"] += 1
            child_store.set_many({"managed": managed, STATE_KEY: {**state, "excluded": sorted(excluded)}})
            continue
        except Exception as exc:
            from .engine import safe_error
            result["errors"].append(str(record.get("title") or category_id) + "：" + safe_error(exc))
            continue
        if (child_engine.marker(category_id) not in current.get("summary", "")
                or fingerprint(current) != record.get("fingerprint")):
            result["skipped"] += 1
            continue
        try:
            child_plex.delete_playlist(current["id"])
            reader = getattr(child_plex, "read_playlists_until", None)
            playlists = reader(lambda rows: all(str(row.get("ratingKey") or row.get("id")) != str(current["id"]) for row in rows)) if callable(reader) else child_plex.playlists()
            if any(str(row.get("ratingKey") or row.get("id")) == str(current["id"]) for row in playlists):
                raise ValueError("Plex 没有确认歌单删除，停止自动重试")
            managed.pop(category_id, None)
            child_store.set("managed", managed)
            result["removed"] += 1
        except Exception as exc:
            from .engine import safe_error
            result["errors"].append(str(record.get("title") or category_id) + "：" + safe_error(exc))

    for category_id, source in owner_managed.items():
        if (not isinstance(source, dict) or not source.get("id") or not source.get("title")
                or source_settings.get(category_id, {}).get("enabled", True) is False):
            continue
        if category_id in excluded:
            continue
        record = managed.get(category_id)
        if record and record.get("shared_from") != owner_id:
            result["skipped"] += 1
            continue
        try:
            original = owner_plex.playlist_state(source["id"])
            if (owner_engine.marker(category_id) not in original.get("summary", "")
                    or fingerprint(original) != source.get("fingerprint")):
                result["skipped"] += 1
                continue
            desired = [value for value in state_ids(original) if value in accessible]
            if not desired:
                result["skipped"] += 1
                continue
            if record:
                try:
                    before = child_plex.playlist_state(record["id"])
                except PlexNotFound:
                    managed.pop(category_id, None)
                    excluded.add(category_id)
                    result["opted_out"] += 1
                    child_store.set_many({
                        "managed": managed,
                        STATE_KEY: {**state, "excluded": sorted(excluded)},
                    })
                    continue
                if (child_engine.marker(category_id) not in before.get("summary", "")
                        or fingerprint(before) != record.get("fingerprint")):
                    result["skipped"] += 1
                    continue
                if state_ids(before) == desired:
                    result["unchanged"] += 1
                    continue
                current = before
                desired_set = set(desired)

                def remember(fresh):
                    managed[category_id] = {
                        "id": fresh["id"], "title": fresh["title"],
                        "fingerprint": fingerprint(fresh), "count": len(fresh.get("items", [])),
                        "shared_from": owner_id,
                    }
                    child_store.set("managed", managed)

                if not desired_set.intersection(state_ids(current)):
                    anchor = desired[0]
                    child_plex.append(current["id"], [anchor])
                    current = _confirmed_state(child_plex, current["id"], state_ids(current) + [anchor])
                    remember(current)
                for item in list(current.get("items", [])):
                    if str(item.get("id")) in desired_set:
                        continue
                    item_id = str(item.get("item_id") or "")
                    if not item_id.isdigit():
                        raise ValueError("分享歌单条目标识异常，停止修改")
                    expected = [row["id"] for row in current["items"] if str(row.get("item_id")) != item_id]
                    child_plex.remove_items(current["id"], [item_id])
                    current = _confirmed_state(child_plex, current["id"], expected)
                    remember(current)
                missing = [value for value in desired if value not in set(state_ids(current))]
                if missing:
                    child_plex.append(current["id"], missing)
                    current = _confirmed_state(child_plex, current["id"], state_ids(current) + missing)
                    remember(current)
                after = current
                result["updated"] += 1
            else:
                if original["title"] in existing:
                    result["skipped"] += 1
                    continue
                after = child_plex.create(original["title"], desired, child_engine.marker(category_id))
                if (after.get("title") != original["title"] or state_ids(after) != desired
                        or child_engine.marker(category_id) not in after.get("summary", "")):
                    raise ValueError("分享歌单创建后回读不一致，停止自动重试")
                existing.add(original["title"])
                result["created"] += 1
            managed[category_id] = {
                "id": after["id"], "title": after["title"],
                "fingerprint": fingerprint(after), "count": len(after.get("items", [])),
                "shared_from": owner_id,
            }
            child_store.set("managed", managed)
        except Exception as exc:
            from .engine import safe_error
            result["errors"].append(str(source.get("title") or category_id) + "：" + safe_error(exc))

    state.update(excluded=sorted(excluded), checked_at=time.time() if now is None else float(now))
    child_store.set(STATE_KEY, state)
    return result


def attach_library_share_routes(app, store, runtime, body, ensure_idle):
    from fastapi import Request

    @app.get("/api/library-share/status")
    def library_share_status():
        return share_status(runtime, str(store.profile_id))

    @app.post("/api/library-share/restore")
    async def library_share_restore(request: Request):
        data = await body(request)
        ensure_idle()
        profile_id = str(store.profile_id)
        with runtime.engine(profile_id).exclusive():
            return restore_category(runtime, profile_id, data.get("category_id"))
