"""Copy owner-managed category playlists into the same Plex library for recipients.

QQ sources and matching stay with the owner. Each connected same-library
profile receives a verified app-owned copy; it has no independent opt-out.
"""
from __future__ import annotations

import time

from .clients import PlexNotFound
from .engine import fingerprint, state_ids


STATE_KEY = "library_share_v1"
REVISIONS_KEY = "library_share_revisions_v2"


def same_server_library(owner, recipient):
    machine = str((owner.get("server") or {}).get("machine") or "")
    library = str((owner.get("library") or {}).get("id") or "")
    return bool(machine and library
                and machine == str((recipient.get("server") or {}).get("machine") or "")
                and library == str((recipient.get("library") or {}).get("id") or ""))


def queue_owner_revision(runtime, owner_id, category_id, action, source_record):
    """Persist a source generation before its targets can be reconciled."""
    if action not in {"publish", "delete"} or not isinstance(source_record, dict) or not source_record.get("id"):
        raise ValueError("分类同步动作无效")
    owner = runtime.registry.get(owner_id)
    if owner.get("kind") != "owner":
        raise ValueError("只有曲库主账户可发布分类结果")
    store = runtime.engine(owner_id).store
    all_revisions = dict(store.get(REVISIONS_KEY, {}) or {})
    history = list(all_revisions.get(category_id) or [])
    generation = max([int(row.get("generation") or 0) for row in history] or [0]) + 1
    targets = {
        row["id"]: "pending"
        for row in runtime.registry.list_public(enabled_only=True)
        if row["id"] != owner_id and same_server_library(owner, row)
    }
    revision = {
        "generation": generation, "action": action,
        "source_id": str(source_record["id"]),
        "confirmed": action == "publish", "targets": targets,
    }
    if action == "publish":
        plex = runtime.engine(owner_id).plex_factory(store.get("settings"))
        current = plex.playlist_state(revision["source_id"])
        if (runtime.engine(owner_id).marker(category_id) not in current.get("summary", "")
                or fingerprint(current) != source_record.get("fingerprint")):
            raise ValueError("主账户分类歌单尚未确认写入")
    history.append(revision)
    all_revisions[category_id] = history
    store.set(REVISIONS_KEY, all_revisions)
    runtime.wake.set()
    return revision


def confirm_owner_revision(runtime, owner_id, category_id):
    store = runtime.engine(owner_id).store
    revisions = dict(store.get(REVISIONS_KEY, {}) or {})
    history = list(revisions.get(category_id) or [])
    pending_index = next((index for index, row in enumerate(history)
                          if row.get("action") == "delete" and not row.get("confirmed")), None)
    if pending_index is None:
        raise ValueError("没有待确认的来源删除")
    revision = dict(history[pending_index])
    plex = runtime.engine(owner_id).plex_factory(store.get("settings"))
    try:
        plex.playlist_state(revision["source_id"])
    except PlexNotFound:
        revision["confirmed"] = True
    else:
        raise ValueError("主账户歌单尚未确认删除")
    history[pending_index] = revision
    revisions[category_id] = history
    store.set(REVISIONS_KEY, revisions)
    runtime.wake.set()
    return revision


def recover_owner_revisions(runtime, owner_id):
    """Confirm prepared deletes by exact Plex ID after an interrupted write."""
    store = runtime.engine(owner_id).store
    revisions = store.get(REVISIONS_KEY, {}) or {}
    for category_id, history in revisions.items():
        if not any(row.get("action") == "delete" and not row.get("confirmed") for row in history):
            continue
        try:
            revision = confirm_owner_revision(runtime, owner_id, category_id)
        except (ValueError, PlexNotFound):
            continue
        managed = dict(store.get("managed", {}) or {})
        if str((managed.get(category_id) or {}).get("id") or "") == revision["source_id"]:
            managed.pop(category_id, None)
            store.set("managed", managed)


def _confirmed_delete_absent(plex, playlist_id):
    try:
        plex.playlist_state(playlist_id)
    except PlexNotFound:
        return True
    return False


def _pending_revisions(owner_store, category_id, recipient_id):
    revisions = owner_store.get(REVISIONS_KEY, {}) or {}
    return [row for row in revisions.get(category_id, [])
            if (row.get("targets") or {}).get(recipient_id) == "pending"]


def _mark_revision_done(owner_store, category_id, generation, recipient_id):
    revisions = dict(owner_store.get(REVISIONS_KEY, {}) or {})
    history = list(revisions.get(category_id) or [])
    for row in history:
        if int(row.get("generation") or 0) == generation:
            row["targets"][recipient_id] = "done"
            break
    revisions[category_id] = history
    owner_store.set(REVISIONS_KEY, revisions)


def reconcile_owner_revision(runtime, owner_id):
    """Retry only unfinished same-library targets; others continue independently."""
    store = runtime.engine(owner_id).store
    revisions = store.get(REVISIONS_KEY, {}) or {}
    targets = {target for history in revisions.values() for row in history
               if row.get("confirmed") for target, status in (row.get("targets") or {}).items()
               if status == "pending"}
    outcomes = {}
    for target in sorted(targets):
        try:
            outcomes[target] = sync_recipient(runtime, owner_id, target)
        except Exception as exc:
            from .engine import safe_error
            outcomes[target] = {"errors": [safe_error(exc)]}
    return outcomes


def _profiles(runtime, owner_id, recipient_id):
    owner = runtime.registry.get(owner_id)
    recipient = runtime.registry.get(recipient_id)
    same_library = (
        owner.get("kind") == "owner"
        and owner_id != recipient_id
        and owner.get("enabled") is not False
        and recipient.get("enabled") is not False
        and same_server_library(owner, recipient)
    )
    if not same_library or not (owner.get("server") or {}).get("machine") or not (owner.get("library") or {}).get("id"):
        raise ValueError("只同步同一曲库的 Plex 分享用户")
    if not owner.get("token") or not recipient.get("token"):
        raise ValueError("Plex 用户授权已失效，停止同步")
    return owner, recipient


def owner_for_recipient(runtime, recipient_id):
    recipient = runtime.registry.get(recipient_id)
    matches = []
    for row in runtime.registry.list_public(enabled_only=True):
        if row.get("kind") != "owner":
            continue
        try:
            _profiles(runtime, row["id"], recipient_id)
        except ValueError:
            continue
        matches.append(row)
    if recipient.get("kind") == "owner":
        # The oldest owner for a server+library is its one category source.
        all_owners = [row for row in runtime.registry.list_public(enabled_only=True)
                      if row.get("kind") == "owner" and same_server_library(row, recipient)]
        if all_owners and min(all_owners, key=lambda row: (row.get("created_at") or 0, row["id"]))["id"] == recipient_id:
            return None
    return min(matches, key=lambda row: (row.get("created_at") or 0, row["id"]))["id"] if matches else None


def share_status(runtime, recipient_id):
    owner_id = owner_for_recipient(runtime, recipient_id)
    if not owner_id:
        return {"recipient": False, "items": []}
    owner_store = runtime.engine(owner_id).store
    child_store = runtime.engine(recipient_id).store
    share = child_store.get(STATE_KEY, {}) or {}
    child_managed = child_store.get("managed", {}) or {}
    items = []
    for category_id, source in (owner_store.get("managed", {}) or {}).items():
        if not isinstance(source, dict) or not source.get("id"):
            continue
        record = child_managed.get(category_id) or {}
        status = "已同步" if record.get("shared_from") == owner_id else "等待同步"
        items.append({"id": category_id, "title": source.get("title") or category_id, "status": status})
    return {"recipient": True, "owner_id": owner_id, "items": items,
            "checked_at": share.get("checked_at"),
            "last_result": share.get("last_result") or {}}


def restore_category(runtime, recipient_id, category_id):
    raise ValueError("同曲库分类歌单会自动同步，无需单独恢复")


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
        state = {"owner_id": owner_id}
    state.pop("excluded", None)
    managed = dict(child_store.get("managed", {}) or {})
    result = {"created": 0, "updated": 0, "removed": 0, "unchanged": 0, "opted_out": 0,
              "skipped": 0, "errors": []}

    owner_managed = owner_store.get("managed", {}) or {}
    blocked_categories = set()
    revisions = owner_store.get(REVISIONS_KEY, {}) or {}
    for category_id in revisions:
        for revision in _pending_revisions(owner_store, category_id, recipient_id):
            if not revision.get("confirmed"):
                blocked_categories.add(category_id)
                break
            if revision.get("action") != "delete":
                continue
            record = managed.get(category_id) or {}
            if (record.get("shared_from") != owner_id
                    or (record.get("shared_source_id") and
                        record.get("shared_source_id") != revision["source_id"])):
                _mark_revision_done(owner_store, category_id, revision["generation"], recipient_id)
                continue
            try:
                current = child_plex.playlist_state(record["id"])
            except PlexNotFound:
                current = None
            except Exception as exc:
                from .engine import safe_error
                result["errors"].append(category_id + "：" + safe_error(exc))
                blocked_categories.add(category_id)
                break
            if current and child_engine.marker(category_id) not in current.get("summary", ""):
                result["skipped"] += 1
                blocked_categories.add(category_id)
                break
            if current:
                try:
                    child_plex.delete_playlist(record["id"])
                    if not _confirmed_delete_absent(child_plex, record["id"]):
                        raise ValueError("Plex 尚未确认旧歌单删除")
                    result["removed"] += 1
                except Exception as exc:
                    from .engine import safe_error
                    result["errors"].append(category_id + "：" + safe_error(exc))
                    blocked_categories.add(category_id)
                    break
            managed.pop(category_id, None)
            child_store.set("managed", managed)
            _mark_revision_done(owner_store, category_id, revision["generation"], recipient_id)
    existing = {str(row.get("title") or "") for row in child_plex.playlists()}
    for category_id, record in list(managed.items()):
        if category_id in blocked_categories:
            continue
        if category_id in owner_managed or not isinstance(record, dict) or record.get("shared_from") != owner_id:
            continue
        try:
            current = child_plex.playlist_state(record["id"])
        except PlexNotFound:
            managed.pop(category_id, None)
            child_store.set("managed", managed)
            continue
        except Exception as exc:
            from .engine import safe_error
            result["errors"].append(str(record.get("title") or category_id) + "：" + safe_error(exc))
            continue
        if child_engine.marker(category_id) not in current.get("summary", ""):
            result["skipped"] += 1
            continue
        try:
            child_plex.delete_playlist(current["id"])
            try:
                child_plex.playlist_state(current["id"])
            except PlexNotFound:
                pass
            else:
                raise ValueError("Plex 没有确认歌单删除，停止自动重试")
            managed.pop(category_id, None)
            child_store.set("managed", managed)
            result["removed"] += 1
        except Exception as exc:
            from .engine import safe_error
            result["errors"].append(str(record.get("title") or category_id) + "：" + safe_error(exc))

    for category_id, source in owner_managed.items():
        if category_id in blocked_categories:
            continue
        if not isinstance(source, dict) or not source.get("id") or not source.get("title"):
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
                    child_store.set("managed", managed)
                    record = None
                if record and child_engine.marker(category_id) not in before.get("summary", ""):
                    result["skipped"] += 1
                    continue
            if record:
                if state_ids(before) == desired:
                    if before.get("title") == original["title"]:
                        result["unchanged"] += 1
                        for revision in _pending_revisions(owner_store, category_id, recipient_id):
                            if revision.get("action") == "publish" and revision.get("confirmed") and str(source["id"]) == revision["source_id"]:
                                _mark_revision_done(owner_store, category_id, revision["generation"], recipient_id)
                        continue
                current = before
                desired_set = set(desired)

                def remember(fresh):
                    managed[category_id] = {
                        "id": fresh["id"], "title": fresh["title"],
                        "fingerprint": fingerprint(fresh), "count": len(fresh.get("items", [])),
                        "shared_from": owner_id, "shared_source_id": str(source["id"]),
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
                if current.get("title") != original["title"]:
                    child_plex.rename(current["id"], original["title"])
                    current = child_plex.playlist_state(current["id"])
                after = current
                result["updated"] += 1
            if not record:
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
                "shared_from": owner_id, "shared_source_id": str(source["id"]),
            }
            child_store.set("managed", managed)
            for revision in _pending_revisions(owner_store, category_id, recipient_id):
                if revision.get("action") == "publish" and revision.get("confirmed"):
                    if str(source["id"]) == revision["source_id"]:
                        _mark_revision_done(owner_store, category_id, revision["generation"], recipient_id)
        except Exception as exc:
            from .engine import safe_error
            result["errors"].append(str(source.get("title") or category_id) + "：" + safe_error(exc))

    state.update(checked_at=time.time() if now is None else float(now))
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
