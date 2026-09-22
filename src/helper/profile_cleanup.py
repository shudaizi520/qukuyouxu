"""Conservative, restartable cleanup of one user/library profile."""
from __future__ import annotations

import time

from .clients import PlexNotFound
from .engine import safe_error
from .external_playlist_sync import external_marker
from .external_store import ExternalRepository
from .favorite_smart import _favorite_title, _rule_kind


STATE_KEY = "profile_removal_v1"


def owned_playlist_inventory(runtime, profile_id: str) -> list[dict]:
    """Inventory saved IDs, including malformed records that require review."""
    store = runtime.engine(profile_id).store
    items = []

    def add(kind, key, record):
        if record:
            items.append({"kind": kind, "key": str(key),
                          "playlist_id": str((record or {}).get("id") or "")})

    add("daily", "daily", store.get("daily_managed"))
    for kind, record in (store.get("smart_mix_managed", {}) or {}).items():
        add("smart", kind, record)
    for category_id, record in (store.get("managed", {}) or {}).items():
        add("category", category_id, record)
    favorite = store.get("favorite_smart_v2") or {}
    if favorite.get("playlist_id"):
        items.append({"kind": "favorite", "key": "liked",
                      "playlist_id": str(favorite["playlist_id"])})
    repo = ExternalRepository(store)
    for source in repo.list_sources(profile_id):
        add("external", source["id"], repo.get_managed(profile_id, source["id"]))
    return items


def verify_favorite_owned_rule(engine, current: dict) -> None:
    saved = engine.store.get("favorite_smart_v2") or {}
    cfg = engine.store.get("settings") or {}
    plex = engine.plex_factory(cfg)
    info = plex.smart_playlist_info(str(current["id"]))
    machine = str(plex.identity().get("machine") or "")
    if (str(saved.get("playlist_id") or "") != str(current["id"])
            or str(saved.get("section") or "") != str(cfg.get("section") or "")
            or not _favorite_title(info.get("title"))
            or _rule_kind(info.get("content"), str(cfg.get("section") or ""), machine) != "current"):
        raise ValueError("无法确认我喜欢歌单归属")


def verify_owned_playlist(engine, item: dict, current: dict) -> None:
    profile = engine.store.registry.get(engine.store.profile_id)
    cfg = engine.store.get("settings") or {}
    plex = engine.plex_factory(cfg)
    if (str(plex.identity().get("machine") or "")
            != str((profile.get("server") or {}).get("machine") or "")
            or str(cfg.get("section") or "")
            != str((profile.get("library") or {}).get("id") or "")):
        raise ValueError("Plex 服务器或曲库身份不一致")
    if str(current.get("id") or "") != str(item["playlist_id"]):
        raise ValueError("Plex 歌单编号不一致")
    section = current.get("section") or current.get("librarySectionID")
    if section and str(section) != str(cfg.get("section") or ""):
        raise ValueError("Plex 歌单所属曲库不一致")
    kind, key = item["kind"], str(item["key"])
    if kind == "favorite":
        verify_favorite_owned_rule(engine, current)
        return
    marker = (external_marker(engine.store.get("installation_id"), key)
              if kind == "external" else
              engine.marker({"daily": "daily", "smart": "smart:" + key}.get(kind, key)))
    if marker not in str(current.get("summary") or ""):
        raise ValueError("程序歌单归属标记缺失")


def forget_owned_record(runtime, profile_id: str, item: dict) -> None:
    store = runtime.engine(profile_id).store
    kind, key = item["kind"], str(item["key"])
    if kind == "daily":
        store.set("daily_managed", None)
    elif kind in ("smart", "category"):
        state_key = "smart_mix_managed" if kind == "smart" else "managed"
        records = dict(store.get(state_key, {}) or {})
        records.pop(key, None)
        store.set(state_key, records)
    elif kind == "favorite":
        store.set("favorite_smart_v2", {})
    elif kind == "external":
        ExternalRepository(store).save_managed(profile_id, key, None)


def _dependent_category_copies(runtime, profile_id: str) -> bool:
    """Keep another profile's live category copy anchored to its owner."""
    profile = runtime.registry.get(profile_id)
    if profile.get("kind") != "owner":
        return False
    for recipient in runtime.registry.list_public():
        if recipient["id"] == profile_id:
            continue
        managed = runtime.engine(recipient["id"]).store.get("managed", {}) or {}
        if any(isinstance(record, dict) and record.get("shared_from") == profile_id
               for record in managed.values()):
            return True
    return False


def begin_profile_removal(runtime, profile_id: str) -> dict:
    if profile_id == "default":
        raise ValueError("默认 Plex 档案不能移除")
    profile = runtime.registry.get(profile_id)
    if _dependent_category_copies(runtime, profile_id):
        raise ValueError("其他用户仍有此主账户同步的分类歌单；请先删除主账户的分类歌单并等待同步完成")
    store = runtime.engine(profile_id).store
    state = store.get(STATE_KEY)
    if not isinstance(state, dict):
        state = {"status": "pending", "items": owned_playlist_inventory(runtime, profile_id),
                 "completed": [], "error": "", "started_at": time.time(), "next_retry_at": 0}
        store.set(STATE_KEY, state)
    else:
        state = {**state, "status": "pending", "error": "", "next_retry_at": 0}
        store.set(STATE_KEY, state)
    if profile.get("enabled") is not False:
        runtime.registry.archive(profile_id)
    runtime.wake.set()
    return dict(state)


def _unresolved_write(store) -> str:
    favorite = store.get("favorite_smart_v2") or {}
    if favorite.get("status") in ("pending", "needs_review"):
        return str(favorite.get("message") or "我喜欢歌单创建结果待核对")
    for snapshot in store.get("snapshots", []) or []:
        if isinstance(snapshot, dict) and snapshot.get("status") in ("prepared", "uncertain", "restoring"):
            return "有 Plex 歌单写入结果待核对"
    return ""


def resume_profile_removal(runtime, profile_id: str) -> dict:
    engine = runtime.engine(profile_id)
    store = engine.store
    state = dict(store.get(STATE_KEY) or begin_profile_removal(runtime, profile_id))
    if _dependent_category_copies(runtime, profile_id):
        state.update(status="needs_attention", error="其他用户仍有此主账户同步的分类歌单，请先完成分类同步",
                     next_retry_at=time.time() + 60)
        store.set(STATE_KEY, state)
        return dict(state)
    if runtime.registry.get(profile_id).get("enabled") is not False:
        runtime.registry.archive(profile_id)
    unresolved = _unresolved_write(store)
    if unresolved:
        state.update(status="needs_attention", error=unresolved,
                     next_retry_at=time.time() + 60)
        store.set(STATE_KEY, state)
        return dict(state)
    try:
        plex = engine.plex_factory(store.get("settings") or {})
    except Exception as exc:
        state.update(status="needs_attention", error=safe_error(exc),
                     next_retry_at=time.time() + 60)
        store.set(STATE_KEY, state)
        return dict(state)
    completed = set(state.get("completed") or [])
    for item in state.get("items") or []:
        identifier = f"{item['kind']}:{item['key']}:{item['playlist_id']}"
        if identifier in completed:
            # Recovery after a crash between the remote-outcome checkpoint and
            # local bookkeeping must finish the latter before final purge.
            forget_owned_record(runtime, profile_id, item)
            continue
        playlist_id = str(item.get("playlist_id") or "")
        try:
            if not playlist_id:
                raise ValueError("托管歌单记录缺少 Plex 编号")
            try:
                current = (plex.playlist_view(playlist_id) if item["kind"] == "favorite"
                           else plex.playlist_state(playlist_id))
            except PlexNotFound:
                current = None
            if current is not None:
                verify_owned_playlist(engine, item, current)
                plex.delete_playlist(playlist_id)
                try:
                    # Smart favorites cannot be read through playlist_state.
                    if item["kind"] == "favorite":
                        plex.playlist_view(playlist_id)
                    else:
                        plex.playlist_state(playlist_id)
                except PlexNotFound:
                    pass
                else:
                    raise ValueError("Plex 尚未确认歌单删除")
        except Exception as exc:
            state.update(status="needs_attention", error=safe_error(exc),
                         next_retry_at=time.time() + 60)
            store.set(STATE_KEY, state)
            return dict(state)
        # Persist the remote outcome before touching the local record. A crash here is
        # safe because the next run confirms the ID is already absent.
        completed.add(identifier)
        state.update(status="running", completed=sorted(completed), error="")
        store.set(STATE_KEY, state)
        forget_owned_record(runtime, profile_id, item)
    try:
        runtime.registry.remove(profile_id)
    except Exception as exc:
        state.update(status="needs_attention", error=safe_error(exc),
                     next_retry_at=time.time() + 60)
        store.set(STATE_KEY, state)
        return dict(state)
    runtime._engines.pop(profile_id, None)
    return {"status": "removed", "profile_id": profile_id}
