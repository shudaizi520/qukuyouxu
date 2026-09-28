"""Copy owner-managed category playlists into the same Plex library for recipients.

QQ sources and matching stay with the owner. Each connected same-library
profile receives a verified app-owned copy; it has no independent opt-out.
"""
from __future__ import annotations

import time
from urllib.parse import urlsplit

from .clients import PlexError, PlexNotFound
from .engine import fingerprint, state_ids


STATE_KEY = "library_share_v1"
REVISIONS_KEY = "library_share_revisions_v2"
MIRRORED_TOPLISTS_KEY = "mirrored_qq_toplists_v1"


def _is_qq_toplist(source):
    """Only QQ rankings are shared; ordinary imported playlists stay personal."""
    if source.get("provider") != "qq":
        return False
    parsed = urlsplit(str(source.get("source_url") or ""))
    parts = parsed.path.rstrip("/").split("/")
    return (
        parsed.hostname == "y.qq.com"
        and len(parts) == 5
        and parts[1:4] == ["n", "ryqq", "toplist"]
        and parts[4].isdigit()
    )


def _qq_toplist_title(source, managed=None):
    """Use a clean playlist title while keeping QQ as the internal provider."""
    title = str((managed or {}).get("title") or source.get("title") or "").strip()
    if title[:2].upper() == "QQ":
        title = title[2:].lstrip(" -·：:")
    return title or "排行榜"


def sync_qq_toplists_across_libraries(runtime, source_profile_id):
    """Rematch public QQ charts into every other library on the same Plex server.

    The imported source profile remains the only remote updater. Other profiles
    receive a local snapshot and match it against their own Plex library IDs.
    """
    from .external_store import ExternalRepository
    from .engine import safe_error
    from .playlist_hub import rename_playlist

    source_profile = runtime.registry.get(source_profile_id)
    source_engine = runtime.engine(source_profile_id)
    source_repository = ExternalRepository(source_engine.store)
    mirrored = source_engine.store.get(MIRRORED_TOPLISTS_KEY, {}) or {}
    sources = []
    for source in source_repository.list_sources(source_profile_id):
        if source["id"] in mirrored or not _is_qq_toplist(source):
            continue
        managed = source_repository.get_managed(source_profile_id, source["id"])
        if managed:
            sources.append((source, managed))
    result = {"updated": 0, "skipped": 0, "errors": []}
    if not sources:
        return result

    # Existing installations used names such as “QQ飙升榜”. Rename only a
    # verified app-owned playlist; the guarded helper refuses foreign content.
    normalized = []
    for source, managed in sources:
        title = _qq_toplist_title(source, managed)
        if managed.get("title") != title:
            try:
                rename_playlist(source_engine, "external", source["id"], title)
                managed = source_repository.get_managed(source_profile_id, source["id"])
            except Exception as exc:
                result["errors"].append({
                    "profile_id": source_profile_id,
                    "source_id": source["id"],
                    "error": type(exc).__name__,
                    "message": safe_error(exc),
                })
        normalized.append((source, managed, title))

    source_machine = str((source_profile.get("server") or {}).get("machine") or "")
    source_library = str((source_profile.get("library") or {}).get("id") or "")
    target_libraries = {}
    for target_profile in runtime.registry.list_public(enabled_only=True):
        target_machine = str((target_profile.get("server") or {}).get("machine") or "")
        target_library = str((target_profile.get("library") or {}).get("id") or "")
        if (target_profile["id"] == source_profile_id or target_machine != source_machine
                or not target_library or target_library == source_library):
            continue
        target_libraries.setdefault((target_machine, target_library), []).append(target_profile)

    targets = []
    for profiles in target_libraries.values():
        owners = [profile for profile in profiles if profile.get("kind") == "owner"]
        if owners:
            targets.append(min(owners, key=lambda row: (row.get("created_at") or 0, row["id"])))
        else:
            # Old installations can contain a shared profile without its owner.
            # Keep those profiles usable until their owner is connected again.
            targets.extend(sorted(profiles, key=lambda row: (row.get("created_at") or 0, row["id"])))

    for target_profile in targets:
        target_id = target_profile["id"]
        target_engine = runtime.engine(target_id)
        target_repository = ExternalRepository(target_engine.store)
        target_mirrored = dict(target_engine.store.get(MIRRORED_TOPLISTS_KEY, {}) or {})
        for source, managed, title in normalized:
            try:
                snapshot = {
                    "provider": source["provider"],
                    "external_id": source["external_id"],
                    "url": source["source_url"],
                    "title": source["title"],
                    "revision": source["revision"],
                    "tracks": source_repository.list_tracks(source_profile_id, source["id"]),
                }
                target_source = target_repository.upsert_source(target_id, snapshot, time.time())
                target_repository.set_follow_updates(target_id, target_source["id"], False)
                target_mirrored[target_source["id"]] = {
                    "source_profile_id": source_profile_id,
                    "source_id": source["id"],
                }
                target_engine.store.set(MIRRORED_TOPLISTS_KEY, target_mirrored)
                target_managed = target_repository.get_managed(target_id, target_source["id"])
                if target_managed and target_managed.get("title") != title:
                    rename_playlist(target_engine, "external", target_source["id"], title)
                matched = target_engine.external.match(target_source["id"])
                if int((matched.get("counts") or {}).get("matched") or 0) == 0:
                    result["skipped"] += 1
                    continue
                target_engine.external.publish(
                    target_source["id"], title, target_source["revision"],
                )
                result["updated"] += 1
            except Exception as exc:
                result["errors"].append({
                    "profile_id": target_id,
                    "source_id": source["id"],
                    "error": type(exc).__name__,
                    "message": safe_error(exc),
                })
    return result


def owner_shared_playlists(owner_engine):
    """Return category playlists plus published QQ charts from the owner."""
    shared = {
        key: dict(record)
        for key, record in (owner_engine.store.get("managed", {}) or {}).items()
        if isinstance(record, dict)
    }
    from .external_playlist_sync import external_marker
    from .external_store import ExternalRepository

    profile_id = str(owner_engine.store.profile_id)
    repository = ExternalRepository(owner_engine.store)
    installation_id = owner_engine.store.get("installation_id")
    for source in repository.list_sources(profile_id):
        if not _is_qq_toplist(source):
            continue
        record = repository.get_managed(profile_id, source["id"])
        if not record:
            continue
        category_id = "external:" + source["id"]
        shared[category_id] = {
            **record,
            "marker": external_marker(installation_id, source["id"]),
        }
    return shared


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


def _category_enabled(owner_store, category_id):
    disabled = set(owner_store.get("managed_disabled_categories", []) or [])
    if category_id in disabled:
        return False
    source = next((row for row in owner_store.get("sources", []) or []
                   if str(row.get("id") or "") == str(category_id)), None)
    return source is None or source.get("enabled") is not False


def _record_error(result, label, exc):
    from .engine import safe_error

    message = str(label) + "：" + safe_error(exc)
    result["errors"].append(message)
    bucket = "retryable_errors" if isinstance(exc, PlexError) else "conflicts"
    result[bucket].append(message)


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
    for category_id, source in owner_shared_playlists(runtime.engine(owner_id)).items():
        if not isinstance(source, dict) or not source.get("id"):
            continue
        record = child_managed.get(category_id) or {}
        status = "已同步" if record.get("shared_from") == owner_id else "等待同步"
        items.append({"id": category_id, "title": source.get("title") or category_id, "status": status})
    return {"recipient": True, "owner_id": owner_id, "items": items,
            "checked_at": share.get("checked_at"),
            "status": share.get("status") or "normal",
            "retry_at": share.get("retry_at"),
            "last_result": share.get("last_result") or {}}


def restore_category(runtime, recipient_id, category_id):
    raise ValueError("同曲库分类歌单会自动同步，无需单独恢复")


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
              "skipped": 0, "errors": [], "retryable_errors": [], "conflicts": []}

    owner_managed = owner_shared_playlists(owner_engine)
    blocked_categories = set()
    revisions = owner_store.get(REVISIONS_KEY, {}) or {}
    for category_id in revisions:
        if not _category_enabled(owner_store, category_id):
            continue
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
                _record_error(result, category_id, exc)
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
                    _record_error(result, category_id, exc)
                    blocked_categories.add(category_id)
                    break
            managed.pop(category_id, None)
            child_store.set("managed", managed)
            _mark_revision_done(owner_store, category_id, revision["generation"], recipient_id)
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
            _record_error(result, str(record.get("title") or category_id), exc)
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
            _record_error(result, str(record.get("title") or category_id), exc)

    for category_id, source in owner_managed.items():
        if category_id in blocked_categories or not _category_enabled(owner_store, category_id):
            continue
        if not isinstance(source, dict) or not source.get("id") or not source.get("title"):
            continue
        record = managed.get(category_id)
        if record and record.get("shared_from") != owner_id:
            result["skipped"] += 1
            continue
        try:
            original = owner_plex.playlist_state(source["id"])
            if ((source.get("marker") or owner_engine.marker(category_id)) not in original.get("summary", "")
                    or fingerprint(original) != source.get("fingerprint")):
                result["skipped"] += 1
                continue
            desired = [value for value in state_ids(original) if value in accessible]
            if not desired:
                result["skipped"] += 1
                continue
            from .managed_playlist_sync import ManagedPlaylistTarget, reconcile_managed_playlist
            scope=child_engine.daily_scope();trusted=dict(record or {})
            if trusted:
                trusted.setdefault("machine",machine);trusted.setdefault("scope",scope)
            target=ManagedPlaylistTarget(
                category_id=category_id,title=original["title"],
                marker=child_engine.marker(category_id),member_ids=tuple(desired),
                machine=machine,scope=scope,
                description="由曲库有序管理；内容与主账户已确认的曲库分类保持一致。",
            )
            reconciled=reconcile_managed_playlist(
                child_plex,target,trusted or None,adopt_existing=bool(trusted),
            )
            after=dict(reconciled.playlist)
            if reconciled.status=="created":result["created"]+=1
            elif reconciled.status=="updated":result["updated"]+=1
            else:result["unchanged"]+=1
            managed[category_id] = {
                "id": after["id"], "title": after["title"],
                "fingerprint": fingerprint(after), "count": len(after.get("items", [])),
                "shared_from": owner_id, "shared_source_id": str(source["id"]),
                "machine": machine, "scope": scope,
                "marker": child_engine.marker(category_id),
            }
            child_store.set("managed", managed)
            for revision in _pending_revisions(owner_store, category_id, recipient_id):
                if revision.get("action") == "publish" and revision.get("confirmed"):
                    if str(source["id"]) == revision["source_id"]:
                        _mark_revision_done(owner_store, category_id, revision["generation"], recipient_id)
        except Exception as exc:
            _record_error(result, str(source.get("title") or category_id), exc)

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
