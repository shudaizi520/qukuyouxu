"""Verified Plex identity, official sign-in, playlist retirement and overlap tools for v0.3.6."""
from __future__ import annotations

import re
import time
import uuid

import requests
from fastapi import Request
from .plex_identity import (
    APP_VERSION as VERSION,
    PRODUCT as PLEX_PRODUCT,
    build_plex_auth_url,
    client_id as _client_id,
    plex_headers as _headers,
)

PLEX_TV = "https://plex.tv"
PROTECTED_TITLES = {"我的最爱", "我喜欢", "My Favorites", "Favorites"}


def valid_plex_pin(code):
    return bool(re.fullmatch(r"[A-Za-z0-9-]{4,128}", str(code or "")))


def parse_plex_resources(rows, fallback_token=""):
    result = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or "server" not in str(row.get("provides") or "").split(","):
            continue
        machine = str(row.get("clientIdentifier") or "").strip()
        name = str(row.get("name") or "Plex").strip()[:120]
        token = str(row.get("accessToken") or fallback_token or "").strip()
        connections = []
        for item in row.get("connections") or []:
            if not isinstance(item, dict):
                continue
            uri = str(item.get("uri") or "").strip().rstrip("/")
            if re.fullmatch(r"https?://[^\s/@]+(?::\d+)?(?:/[^\s]*)?", uri):
                connections.append({
                    "uri": uri,
                    "local": bool(item.get("local")),
                    "relay": bool(item.get("relay")),
                    "protocol": str(item.get("protocol") or ""),
                })
        if machine and token and connections:
            connections.sort(key=lambda item: (item["relay"], not item["local"], item["protocol"] != "https"))
            result.append({
                "machine": machine,
                "name": name,
                "owned": bool(row.get("owned")),
                "token": token,
                "connections": connections,
            })
    return result


def calculate_category_overlaps(plan, min_tracks=10, jaccard_limit=0.70, containment_limit=0.80):
    groups = []
    for row in (plan or {}).get("groups", []):
        ids = {str(value) for value in row.get("desired", []) if str(value).isdigit()}
        if len(ids) >= min_tracks and not row.get("blocked"):
            groups.append((row, ids))
    result = []
    for index, (left, left_ids) in enumerate(groups):
        for right, right_ids in groups[index + 1:]:
            shared = len(left_ids & right_ids)
            if shared < min_tracks:
                continue
            union = len(left_ids | right_ids)
            jaccard = shared / union if union else 0
            containment = shared / min(len(left_ids), len(right_ids))
            if jaccard < jaccard_limit and containment < containment_limit:
                continue
            keep = left if len(left_ids) >= len(right_ids) else right
            result.append({
                "left_id": left["id"], "left_title": left.get("title", ""),
                "left_count": len(left_ids),
                "right_id": right["id"], "right_title": right.get("title", ""),
                "right_count": len(right_ids),
                "shared": shared,
                "jaccard": round(jaccard, 4),
                "containment": round(containment, 4),
                "recommend_keep_id": keep["id"],
                "recommend_keep_title": keep.get("title", ""),
            })
    result.sort(key=lambda row: (-row["containment"], -row["jaccard"], row["left_title"], row["right_title"]))
    return result


def _managed_source(store, category_id):
    return next((row for row in store.get("sources", []) if row.get("id") == category_id), None)


def _disabled_categories(store):
    return {
        str(value) for value in (store.get("managed_disabled_categories", []) or [])
        if str(value)
    }


def _expected_managed_state(engine, category_id, record, current):
    """Return the last proven state and removed IDs for a removal-only external edit."""
    from .engine import SafetyError, fingerprint, state_ids

    if (str(current.get("id") or "") != str(record.get("id") or "")
            or str(current.get("title") or "") != str(record.get("title") or "")
            or engine.marker(category_id) not in str(current.get("summary") or "")):
        raise SafetyError("歌单名称、标识或助手管理标记已经变化，不能自动处理")
    snapshot_id = str(record.get("snapshot_id") or "")
    snapshot = next(
        (row for row in (engine.store.get("snapshots", []) or [])
         if str(row.get("id") or "") == snapshot_id),
        None,
    )
    expected = (snapshot or {}).get("after") or {}
    if (not expected or fingerprint(expected) != record.get("fingerprint")
            or str(expected.get("id") or "") != str(record.get("id") or "")
            or str(expected.get("title") or "") != str(record.get("title") or "")
            or engine.marker(category_id) not in str(expected.get("summary") or "")):
        raise SafetyError("找不到该歌单最后一次可信快照，不能自动处理")
    expected_ids = state_ids(expected)
    current_ids = state_ids(current)
    if (len(set(expected_ids)) != len(expected_ids)
            or len(set(current_ids)) != len(current_ids)):
        raise SafetyError("歌单含有重复歌曲，不能自动处理")
    iterator = iter(expected_ids)
    if not all(any(value == candidate for candidate in iterator) for value in current_ids):
        raise SafetyError("只能处理在 Plex 外部删除歌曲的情况；检测到新增或顺序变化")
    current_set = set(current_ids)
    removed = [value for value in expected_ids if value not in current_set]
    if not removed:
        raise SafetyError("没有检测到可接受或恢复的外部删除")
    return snapshot, expected, removed


def _managed_scope_error(engine, record, current, identity):
    if str(current.get("id") or "") != str(record.get("id") or ""):
        return "Plex 歌单标识不一致"
    if record.get("title") and str(current.get("title") or "") != str(record.get("title") or ""):
        return "歌单名称与托管记录不一致"
    snapshot = next((row for row in engine.store.get("snapshots", [])
                     if str(row.get("id") or "") == str(record.get("snapshot_id") or "")), {})
    expected_machine = str(record.get("machine") or snapshot.get("machine") or "")
    if expected_machine and expected_machine != str(identity.get("machine") or ""):
        return "Plex 服务器身份与托管记录不一致"
    expected_scope = str(record.get("scope") or snapshot.get("scope") or "")
    if expected_scope and expected_scope != str(engine.daily_scope() or ""):
        return "音乐资料库身份与托管记录不一致"
    return ""


def managed_playlist_rows(engine):
    from .engine import fingerprint, safe_error
    from .managed_cleanup_v0317 import is_confirmed_missing
    store = engine.store
    managed = store.get("managed", {}) or {}
    sources = {row.get("id"): row for row in store.get("sources", [])}
    disabled = _disabled_categories(store)
    if not managed:
        return []
    plex = engine.plex_factory(store.get("settings"))
    identity = plex.identity()
    rows = []
    for category_id, record in managed.items():
        source = sources.get(category_id, {})
        row = {
            "category_id": category_id,
            "playlist_id": str(record.get("id") or ""),
            "title": str(record.get("title") or source.get("name") or ""),
            "enabled": category_id not in disabled and source.get("enabled", True) is not False,
            "count": None,
            "safe_to_remove": False,
            "safe_to_forget": False,
            "can_accept_changes": False,
            "can_restore_changes": False,
            "status": "需要核对",
            "machine": identity.get("machine", ""),
        }
        try:
            current = plex.playlist_state(record["id"])
            marker_ok = engine.marker(category_id) in current.get("summary", "")
            unchanged = fingerprint(current) == record.get("fingerprint")
            scope_error = _managed_scope_error(engine, record, current, identity)
            removable = marker_ok and not scope_error and current.get("title") not in PROTECTED_TITLES
            row.update(
                title=current.get("title", row["title"]),
                count=len(current.get("items", [])),
                safe_to_remove=bool(removable),
                status=("正常" if marker_ok and unchanged and not scope_error else
                        "已手动修改，可删除或核对" if removable else
                        scope_error or "助手管理标记缺失，需要核对"),
            )
            if marker_ok and not scope_error and not unchanged:
                try:
                    _expected_managed_state(engine, category_id, record, current)
                    row["can_accept_changes"] = True
                    row["can_restore_changes"] = True
                except Exception:
                    pass
        except Exception as exc:
            row["safe_to_forget"] = is_confirmed_missing(exc)
            row["status"] = "Plex 中已不存在，可清除本地记录" if row["safe_to_forget"] else safe_error(exc)
        rows.append(row)
    return sorted(rows, key=lambda row: row["title"])


def disable_managed_playlist(engine, category_id):
    store = engine.store
    if category_id not in (store.get("managed", {}) or {}):
        from .engine import SafetyError
        raise SafetyError("这不是助手托管的分类歌单")
    sources = store.get("sources", [])
    for source in sources:
        if source.get("id") == category_id:
            source["enabled"] = False
            source["approved"] = False
    disabled = _disabled_categories(store)
    disabled.add(category_id)
    plan = store.get("plan")
    base_plan = store.get("base_plan")
    if plan and not plan.get("applied"):
        plan = {**plan, "invalidated_reason": "歌单维护状态已变化，请重新整理预览。"}
    if base_plan and not base_plan.get("applied"):
        base_plan = {**base_plan, "invalidated_reason": "歌单维护状态已变化，请重新整理预览。"}
    store.set_many({
        "sources": sources,
        "managed_disabled_categories": sorted(disabled),
        "plan": plan,
        "base_plan": base_plan,
    })
    store.log("已停止维护分类歌单：" + str((store.get("managed") or {})[category_id].get("title") or category_id))
    return {"message": "已停止维护；Plex 中的歌单和歌曲保持不变。"}


def enable_managed_playlist(engine, category_id):
    store = engine.store
    managed = store.get("managed", {}) or {}
    if category_id not in managed:
        from .engine import SafetyError
        raise SafetyError("这不是助手托管的分类歌单")
    sources = store.get("sources", [])
    for source in sources:
        if source.get("id") == category_id:
            source["enabled"] = True
            source["approved"] = False
    disabled = _disabled_categories(store)
    disabled.discard(category_id)
    plan = store.get("plan")
    base_plan = store.get("base_plan")
    if plan and not plan.get("applied"):
        plan = {**plan, "invalidated_reason": "歌单维护状态已变化，请重新整理预览。"}
    if base_plan and not base_plan.get("applied"):
        base_plan = {**base_plan, "invalidated_reason": "歌单维护状态已变化，请重新整理预览。"}
    store.set_many({
        "sources": sources,
        "managed_disabled_categories": sorted(disabled),
        "plan": plan,
        "base_plan": base_plan,
    })
    title = str(managed[category_id].get("title") or category_id)
    store.log("已恢复维护分类歌单：" + title)
    return {"message": "已恢复维护；Plex 中现有歌单保持不变，后续整理会继续维护。"}


def reconcile_managed_playlist(engine, category_id, action):
    """Accept or restore a proven removal-only edit made outside this application."""
    from .engine import SafetyError, fingerprint, safe_error

    category_id, action = str(category_id or ""), str(action or "")
    if action not in {"accept", "restore"}:
        raise SafetyError("处理方式无效")
    with engine.exclusive():
        store = engine.store
        managed = dict(store.get("managed", {}) or {})
        record = managed.get(category_id)
        if not record:
            raise SafetyError("这不是助手托管的分类歌单")
        plex = engine.plex_factory(store.get("settings"))
        current = plex.playlist_state(record["id"])
        _source_snapshot, expected, removed = _expected_managed_state(
            engine, category_id, record, current,
        )
        snapshot = {
            "id": uuid.uuid4().hex,
            "kind": "managed_external_" + action,
            "category_id": category_id,
            "title": current["title"],
            "created_at": time.time(),
            "status": "prepared",
            "before": current,
            "after": None,
            "add": list(removed) if action == "restore" else [],
            "marker": engine.marker(category_id),
            "plan_id": "",
        }
        engine._save_snapshot(snapshot)
        try:
            if action == "restore":
                plex.append(record["id"], removed)
                after = plex.playlist_state(record["id"])
                actual_ids = [str(row.get("id")) for row in after.get("items", [])]
                expected_ids = [str(row.get("id")) for row in expected.get("items", [])]
                if (str(after.get("id")) != str(record.get("id"))
                        or str(after.get("title")) != str(record.get("title"))
                        or engine.marker(category_id) not in str(after.get("summary") or "")
                        or len(actual_ids) != len(expected_ids)
                        or set(actual_ids) != set(expected_ids)):
                    raise SafetyError("Plex 没有确认恢复结果，停止后续操作")
                message = "已恢复被外部删除的歌曲；歌单和音乐文件均未重建。"
            else:
                after = current
                edits_all = dict(store.get("playlist_manual_edits", {}) or {})
                edit_key = "category:" + category_id
                edits = dict(edits_all.get(edit_key, {}) or {})
                excluded = [
                    str(value) for value in edits.get("exclude", [])
                    if str(value).isdigit()
                ]
                for value in removed:
                    if value not in excluded:
                        excluded.append(value)
                edits_all[edit_key] = {
                    "include": [str(value) for value in edits.get("include", []) if str(value).isdigit()],
                    "exclude": excluded,
                    "updated_at": time.time(),
                }
                store.set("playlist_manual_edits", edits_all)
                message = "已接受并保留当前改动；被删除的歌曲会保持排除，不会被自动加回。"
            snapshot.update(status="applied", after=after)
            engine._save_snapshot(snapshot)
            managed[category_id] = {
                **record,
                "fingerprint": fingerprint(after),
                "snapshot_id": snapshot["id"],
                "count": len(after.get("items", [])),
            }
            store.set_many({"managed": managed, "plan": None, "base_plan": None})
            store.log(("已接受外部歌单改动：" if action == "accept" else "已恢复外部删除歌曲：") + current["title"])
            return {"message": message, "count": len(after.get("items", []))}
        except Exception as exc:
            snapshot.update(status="uncertain" if action == "restore" else "failed", error=safe_error(exc))
            engine._save_snapshot(snapshot)
            if isinstance(exc, SafetyError):
                raise
            raise SafetyError("处理结果需要人工核对：" + safe_error(exc)) from None


def remove_managed_playlist(engine, category_id, confirm_title):
    from .engine import SafetyError, safe_error
    with engine.exclusive():
        store = engine.store
        managed = dict(store.get("managed", {}) or {})
        record = managed.get(category_id)
        if not record:
            raise SafetyError("这不是助手托管的分类歌单；不会按名称删除")
        plex = engine.plex_factory(store.get("settings"))
        identity = plex.identity()
        current = plex.playlist_state(record["id"])
        title = str(current.get("title") or "")
        if title in PROTECTED_TITLES:
            raise SafetyError("“我的最爱”“我喜欢”等收藏歌单受到永久保护")
        if str(confirm_title or "") != title:
            raise SafetyError("歌单名称已经变化，请刷新后重新确认")
        if str(current.get("id")) != str(record.get("id")):
            raise SafetyError("Plex 歌单标识不一致，拒绝删除")
        if engine.marker(category_id) not in current.get("summary", ""):
            raise SafetyError("助手管理标记缺失，拒绝删除")
        scope_error = _managed_scope_error(engine, record, current, identity)
        if scope_error:
            raise SafetyError(scope_error + "，拒绝删除")
        snapshot = {
            "id": uuid.uuid4().hex,
            "kind": "managed_remove",
            "category_id": category_id,
            "title": title,
            "created_at": time.time(),
            "status": "prepared",
            "before": current,
            "after": None,
            "add": [],
            "marker": engine.marker(category_id),
            "plan_id": "",
            "machine": identity.get("machine", ""),
            "scope": engine.daily_scope(),
            "before_managed": record,
        }
        engine._save_snapshot(snapshot)
        runtime = getattr(engine, "profile_runtime", None)
        owner_removal = bool(runtime is not None and runtime.registry.get(store.profile_id).get("kind") == "owner"
                             and not record.get("shared_from"))
        if owner_removal:
            from .library_sharing import queue_owner_revision
            queue_owner_revision(runtime, store.profile_id, category_id, "delete", record)
        try:
            plex.delete_playlist(current["id"])
            from .clients import PlexNotFound
            try:
                plex.playlist_state(current["id"])
            except PlexNotFound:
                pass
            else:
                raise SafetyError("Plex 尚未确认歌单删除")
            if owner_removal:
                from .library_sharing import confirm_owner_revision
                confirm_owner_revision(runtime, store.profile_id, category_id)
            snapshot["status"] = "applied"
            engine._save_snapshot(snapshot)
            managed.pop(category_id, None)
            sources = store.get("sources", [])
            for source in sources:
                if source.get("id") == category_id:
                    source["enabled"] = False
                    source["approved"] = False
            retired = dict(store.get("retired_managed", {}) or {})
            retired[category_id] = {
                "snapshot_id": snapshot["id"], "title": title,
                "machine": snapshot["machine"], "scope": snapshot["scope"],
            }
            changes = {
                "managed": managed, "sources": sources, "retired_managed": retired,
                "plan": None,
            }
            store.set_many(changes)
            store.log("已移除助手托管分类歌单：" + title)
            return {
                "message": "已从 Plex 移除该分类歌单；音乐文件未删除，可从快照恢复。",
                "snapshot_id": snapshot["id"],
            }
        except Exception as exc:
            snapshot.update(status="uncertain", error=safe_error(exc))
            engine._save_snapshot(snapshot)
            raise SafetyError("删除结果需要人工核对；助手不会自动重试：" + safe_error(exc)) from None


def restore_removed_playlist(engine, snapshot_id):
    from .engine import SafetyError, fingerprint, safe_error
    from .playlist_sync import has_exact_members
    with engine.exclusive():
        store = engine.store
        snapshots = store.get("snapshots", [])
        snapshot = next((row for row in snapshots if row.get("id") == snapshot_id), None)
        if not snapshot or snapshot.get("kind") != "managed_remove" or snapshot.get("status") != "applied":
            raise SafetyError("只能恢复最近安全移除的助手分类歌单")
        category_id = snapshot["category_id"]
        managed = dict(store.get("managed", {}) or {})
        if category_id in managed:
            raise SafetyError("该分类已有正在托管的歌单，拒绝重复创建")
        retired = dict(store.get("retired_managed", {}) or {})
        retired_record = retired.get(category_id) or {}
        if retired_record.get("snapshot_id") != snapshot.get("id"):
            raise SafetyError("只能恢复该分类最近移除的歌单")
        scope = engine.daily_scope()
        if not snapshot.get("scope") or not retired_record.get("scope"):
            raise SafetyError("旧版本的恢复记录缺少资料库身份，无法安全自动恢复")
        if snapshot.get("scope") != scope or retired_record.get("scope") != scope:
            raise SafetyError("账户或资料库已经变化，不能恢复旧快照")
        before = snapshot.get("before") or {}
        if before.get("title") in PROTECTED_TITLES:
            raise SafetyError("受保护歌单不能通过分类恢复流程处理")
        plex = engine.plex_factory(store.get("settings"))
        identity = plex.identity()
        if snapshot.get("machine") and snapshot["machine"] != identity.get("machine"):
            raise SafetyError("Plex 服务器已经变化，不能恢复")
        if any(str(row.get("title") or "") == str(before.get("title") or "") for row in plex.playlists()):
            raise SafetyError("Plex 中已经存在同名歌单，不能重复恢复")
        ids = [str(row.get("id")) for row in before.get("items", [])]
        catalog = store.get("catalog", []) or []
        if catalog:
            usable = {str(row.get("id")) for row in catalog if row.get("available", True)}
            if any(value not in usable for value in ids):
                raise SafetyError("原歌单包含当前曲库已不可用的歌曲，未执行恢复")
        snapshot["status"] = "restoring"
        engine._save_snapshot(snapshot)
        try:
            after = plex.create(before["title"], ids, snapshot["marker"], description="从助手安全快照恢复；默认停止维护。")
            if (after.get("title") != before.get("title")
                    or snapshot["marker"] not in after.get("summary", "")
                    or not has_exact_members(after, ids)):
                raise SafetyError("恢复后的歌单回读不一致")
            snapshot["status"] = "restored"
            engine._save_snapshot(snapshot)
            managed[category_id] = {
                "id": after["id"], "title": after["title"], "fingerprint": fingerprint(after),
                "snapshot_id": snapshot["id"], "machine": snapshot.get("machine", ""),
                "scope": snapshot["scope"],
            }
            retired.pop(category_id, None)
            sources = store.get("sources", [])
            for source in sources:
                if source.get("id") == category_id:
                    source["enabled"] = False
                    source["approved"] = False
            store.set_many({
                "managed": managed, "retired_managed": retired, "sources": sources, "plan": None,
            })
            store.log("已从快照恢复分类歌单：" + before["title"])
            return {"message": "分类歌单已恢复，当前保持停止维护。", "playlist_id": str(after["id"])}
        except Exception as exc:
            snapshot.update(status="uncertain", error=safe_error(exc))
            engine._save_snapshot(snapshot)
            raise SafetyError("恢复结果需要人工核对：" + safe_error(exc)) from None


def _plex_json(store, method, path, token="", data=None, params=None):
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.request(
            method, PLEX_TV + path, headers=_headers(store, token),
            data=data, params=params, timeout=(8, 25), allow_redirects=False,
        )
        if response.status_code not in (200, 201):
            raise RuntimeError("Plex 官方登录返回 HTTP " + str(response.status_code))
        if len(response.content) > 2 * 1024 * 1024:
            raise RuntimeError("Plex 官方登录响应过大")
        value = response.json()
        if not isinstance(value, (dict, list)):
            raise RuntimeError("Plex 官方登录返回格式异常")
        return value
    except (requests.RequestException, ValueError) as exc:
        raise RuntimeError("无法连接 Plex 官方登录服务") from exc
    finally:
        session.close()


def _public_servers(resources):
    return [{
        "machine": row["machine"], "name": row["name"], "owned": row["owned"],
        "connections": len(row["connections"]),
    } for row in resources]


def attach_v036_routes(app, store, engine, body, ensure_idle):
    from .engine import SafetyError, digest, safe_error
    from .plex_state_v0316 import attach_routes as attach_plex_state_routes, official_saved
    attach_plex_state_routes(app, store, engine, body, ensure_idle)
    from .workflow_v0317 import attach_routes as attach_workflow_routes
    attach_workflow_routes(app, store, engine, body, ensure_idle)

    @app.post("/api/plex/login/start")
    def plex_login_start():
        ensure_idle()
        value = _plex_json(store, "POST", "/api/v2/pins", data={"strong": "true"})
        pin_id = str(value.get("id") or "")
        code = str(value.get("code") or "")
        if not pin_id.isdigit() or not valid_plex_pin(code):
            raise SafetyError("Plex 官方登录没有返回有效授权码")
        now = time.time()
        pending = {
            "id": pin_id, "code": code, "created_at": now,
            "expires_at": now + min(600, max(60, int(value.get("expiresIn") or 300))),
            "status": "pending",
        }
        store.set("plex_login_pending", pending)
        return {
            "pin_id": pin_id,
            "auth_url": build_plex_auth_url(_client_id(store), code),
            "expires_at": pending["expires_at"],
            "message": "已打开 Plex 官方登录；完成后本页会自动识别服务器。",
        }

    @app.get("/api/plex/login/status")
    def plex_login_status(pin_id: str):
        pending = store.get("plex_login_pending", {}) or {}
        if str(pending.get("id")) != str(pin_id) or pending.get("status") not in ("pending", "authorized"):
            raise ValueError("Plex 授权请求不存在或已经失效")
        if time.time() > float(pending.get("expires_at") or 0):
            return {"status": "expired", "message": "授权已过期，请重新连接 Plex。"}
        if pending.get("status") == "authorized":
            return {"status": "authorized", "servers": _public_servers(pending.get("resources", [])), "user": pending.get("user", {})}
        value = _plex_json(store, "GET", "/api/v2/pins/" + str(pin_id), params={"code": pending["code"]})
        token = str(value.get("authToken") or "").strip()
        if not token:
            return {"status": "pending", "message": "等待你在 Plex 官方页面确认…"}
        user = {}
        try:
            raw_user = _plex_json(store, "GET", "/api/v2/user", token=token)
            user = {
                "id": str(raw_user.get("id") or ""),
                "username": str(raw_user.get("username") or raw_user.get("title") or "").strip()[:120],
            }
        except Exception:
            user = {}
        raw_resources = _plex_json(
            store, "GET", "/api/v2/resources", token=token,
            params={"includeHttps": 1, "includeRelay": 1, "includeIPv6": 0},
        )
        resources = parse_plex_resources(raw_resources, token)
        if not resources:
            raise SafetyError("这个 Plex 账号没有返回可访问的服务器")
        pending.update(status="authorized", token=token, resources=resources, user=user)
        store.set("plex_login_pending", pending)
        return {"status": "authorized", "servers": _public_servers(resources), "user": user}

    def connect_authorized_plex(pin_id, machine):
        with engine.exclusive():
            pending = store.get("plex_login_pending", {}) or {}
            if str(pending.get("id")) != pin_id or pending.get("status") != "authorized":
                raise SafetyError("请先完成 Plex 官方登录")
            resource = next((row for row in pending.get("resources", []) if row["machine"] == machine), None)
            if not resource:
                raise ValueError("请选择登录后实际返回的 Plex 服务器")
            old = store.get("settings")
            candidates = [str(old.get("plex_url") or "").rstrip("/")]
            candidates += [row["uri"] for row in resource["connections"]]
            seen = set()
            connected = None
            last_error = ""
            for uri in candidates:
                if not uri or uri in seen:
                    continue
                seen.add(uri)
                try:
                    cfg = {**old, "plex_url": uri, "plex_token": resource["token"]}
                    plex = engine.plex_factory(cfg)
                    identity = plex.identity()
                    if identity.get("machine") != machine:
                        continue
                    connected = (uri, identity, plex.sections())
                    break
                except Exception as exc:
                    last_error = safe_error(exc)
            if not connected:
                raise SafetyError("登录成功，但助手无法访问所选服务器：" + last_error)
            uri, identity, sections = connected
            managed = store.get("managed", {}) or {}
            daily_managed = store.get("daily_managed")
            smart_managed = store.get("smart_mix_managed", {}) or {}
            smart_removed = store.get("smart_mix_removed", {}) or {}
            from .profile_web import connection_identity, guard_connection_change
            previous_identity = connection_identity(store)
            if managed or daily_managed or smart_managed or smart_removed:
                if not all(previous_identity):
                    raise SafetyError("已有托管歌单且旧连接无法核对，拒绝切换服务器") from None
                if previous_identity[1] != identity.get("machine"):
                    raise SafetyError("已有托管歌单，不能切换到另一台 Plex 服务器")
            section_ids = {str(row.get("id")) for row in sections}
            section = str(old.get("section") or "")
            if not section and previous_identity[2] in section_ids:
                section = previous_identity[2]
            if section not in section_ids:
                if managed or daily_managed or smart_managed or smart_removed:
                    raise SafetyError("当前音乐资料库在登录后的服务器中不存在，拒绝切换")
                section = next(iter(section_ids)) if len(section_ids) == 1 else ""
            guard_connection_change(
                store,
                (pending.get("user", {}) or {}).get("id"),
                machine,
                section,
            )
            revised = {
                **old, "plex_url": uri, "plex_token": resource["token"], "section": section,
                "account_label": (pending.get("user", {}) or {}).get("username") or resource["name"],
                "auto_enabled": False,
            }
            connection_result = {
                **identity, "sections": sections, "checked_at": time.time(),
                "existing_playlists": [],
            }
            values = {
                "settings": revised,
                "plex_login_user": pending.get("user", {}) or {},
                "plex_connection": {
                    "scope": digest([revised.get("plex_url", ""), revised.get("plex_token", "")]),
                    "result": connection_result,
                    "source": "official_login",
                },
                "plex_saved": official_saved(revised, pending.get("user", {}) or {}, resource, identity, sections),
                "daily_plan": None,
                "plan": None,
                "plex_reconnect_identity": {},
            }
            if not managed and not daily_managed and not smart_managed and not smart_removed and any(old.get(key) != revised.get(key) for key in ("plex_url", "plex_token", "section")):
                values.update({
                    "feedback": {"tracks": {}, "artists": {}}, "metadata_overrides": {},
                    "daily_history": [], "catalog": [], "metadata_audit": [],
                })
            safe_pending = {
                "id": pin_id, "created_at": pending.get("created_at"), "expires_at": pending.get("expires_at"),
                "status": "connected", "user": pending.get("user", {}),
            }
            values["plex_login_pending"] = safe_pending
            store.set_many(values)
            registry = getattr(store, "registry", None)
            if registry is not None:
                chosen_library = next(
                    ({"id": str(row.get("id")), "name": str(row.get("title") or "")[:160]}
                     for row in sections if str(row.get("id")) == section),
                    {"id": section, "name": ""},
                )
                registry.update(
                    store.profile_id,
                    account=pending.get("user", {}) or {},
                    server={
                        "machine": machine,
                        "name": str(identity.get("server") or resource.get("name") or "Plex")[:160],
                        "url": uri,
                    },
                    library=chosen_library,
                    token=resource["token"],
                )
            from .connection_scope import migrate_managed_scopes
            migrate_managed_scopes(store)
        return {
            "message": "Plex 已连接。请选择音乐资料库并保存。",
            "server": identity.get("server", "Plex"), "machine": machine,
            "sections": sections, "section": section, "user": pending.get("user", {}),
        }

    @app.post("/api/plex/login/connect")
    async def plex_login_connect(req: Request):
        data = await body(req)
        ensure_idle()
        if data.get("confirm") is not True:
            raise SafetyError("请确认连接所选 Plex 服务器")
        return connect_authorized_plex(
            str(data.get("pin_id") or ""),
            str(data.get("machine") or ""),
        )

    @app.post("/api/plex/login/resume")
    def plex_login_resume():
        """Resume the official login after a popup closes or the page reloads."""
        ensure_idle()
        pending = store.get("plex_login_pending", {}) or {}
        status = str(pending.get("status") or "")
        if status == "pending":
            if time.time() > float(pending.get("expires_at") or 0):
                return {"status": "expired", "message": "授权已过期，请重新连接 Plex。"}
            return {
                "status": "pending", "pin_id": str(pending.get("id") or ""),
                "expires_at": pending.get("expires_at"),
                "message": "等待你在 Plex 官方页面确认…",
            }
        if status != "authorized":
            return {"status": "idle"}
        resources = list(pending.get("resources") or [])
        if len(resources) != 1:
            return {
                "status": "selection_required", "pin_id": str(pending.get("id") or ""),
                "servers": _public_servers(resources),
                "message": "请选择要使用的 Plex 服务器。",
            }
        result = connect_authorized_plex(str(pending.get("id") or ""), resources[0]["machine"])
        return {**result, "status": "connected"}

    @app.post("/api/plex/disconnect")
    async def plex_disconnect(req: Request):
        data = await body(req)
        ensure_idle()
        if data.get("confirm") is not True:
            raise SafetyError("请确认断开 Plex；不会删除 Plex 中已有歌单")
        from .profile_web import disconnect_profile_connection
        with engine.exclusive():
            return disconnect_profile_connection(store)

    @app.get("/api/themes/overlaps")
    def theme_overlaps():
        plan = store.get("plan") or {}
        rows = calculate_category_overlaps(plan)
        return {
            "items": rows,
            "message": "按本次预览中真实匹配的本地歌曲计算；只提示，不会自动合并或删除。",
        }

    @app.get("/api/managed/playlists")
    def managed_playlists():
        ensure_idle()
        with engine.exclusive():
            rows = managed_playlist_rows(engine)
        return {"items": rows, "retired": list((store.get("retired_managed", {}) or {}).values())}

    @app.post("/api/managed/disable")
    async def managed_disable(req: Request):
        data = await body(req)
        ensure_idle()
        if data.get("confirm") is not True:
            raise SafetyError("请确认停止维护")
        with engine.exclusive():
            return disable_managed_playlist(engine, str(data.get("category_id") or ""))

    @app.post("/api/managed/enable")
    async def managed_enable(req: Request):
        data = await body(req)
        ensure_idle()
        if data.get("confirm") is not True:
            raise SafetyError("请确认恢复维护")
        with engine.exclusive():
            return enable_managed_playlist(engine, str(data.get("category_id") or ""))

    @app.post("/api/managed/remove")
    async def managed_remove(req: Request):
        data = await body(req)
        ensure_idle()
        if data.get("confirm") is not True:
            raise SafetyError("请确认移除助手分类歌单")
        return remove_managed_playlist(
            engine, str(data.get("category_id") or ""), str(data.get("title") or "")
        )

    @app.post("/api/managed/reconcile")
    async def managed_reconcile(req: Request):
        data = await body(req)
        ensure_idle()
        if data.get("confirm") is not True:
            raise SafetyError("请确认处理外部歌单改动")
        return reconcile_managed_playlist(
            engine, str(data.get("category_id") or ""), str(data.get("action") or "")
        )

    @app.post("/api/managed/restore")
    async def managed_restore(req: Request):
        data = await body(req)
        ensure_idle()
        if data.get("confirm") is not True:
            raise SafetyError("请确认从快照恢复")
        return restore_removed_playlist(engine, str(data.get("snapshot_id") or ""))
