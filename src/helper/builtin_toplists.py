"""Idempotent installation and upgrade of the two public QQ charts."""
from __future__ import annotations

import time

from .external_store import ExternalRepository


STATE_KEY = "builtin_qq_toplists_v1"
POLICY = "v1"
RETRY_SECONDS = 300
TOPLISTS = (
    ("26", "热歌榜"),
    ("62", "飙升榜"),
)


def qq_authorized(store):
    saved = store.get("qq_auth_credentials", {}) or {}
    credential = saved.get("credential", {}) if isinstance(saved, dict) else {}
    music_id = credential.get("musicid") or credential.get("str_musicid") or credential.get("strMusicid")
    return bool(str(music_id or "").isdigit() and credential.get("musickey"))


def plex_connected(store):
    settings = store.get("settings", {}) or {}
    return bool(settings.get("plex_url") and settings.get("plex_token") and settings.get("section"))


def _sources(repository, profile_id):
    return {
        str(row.get("external_id") or ""): row
        for row in repository.list_sources(profile_id)
        if row.get("provider") == "qq"
    }


def builtin_toplists_ready(engine):
    profile_id = str(engine.store.profile_id)
    repository = ExternalRepository(engine.store)
    sources = _sources(repository, profile_id)
    from .library_sharing import MIRRORED_TOPLISTS_KEY
    mirrored = engine.store.get(MIRRORED_TOPLISTS_KEY, {}) or {}
    for external_id, title in TOPLISTS:
        source = sources.get(external_id)
        managed = repository.get_managed(profile_id, source["id"]) if source else None
        follows_canonical = source and source["id"] in mirrored
        if (not source or not managed or managed.get("title") != title
                or (not follows_canonical and not source.get("follow_updates"))):
            return False
    return True


def builtin_toplists_due(runtime, profile_id, now=None):
    profile = runtime.registry.get(profile_id)
    if profile.get("kind") != "owner" or profile.get("enabled") is False:
        return False
    from .library_sharing import owner_for_recipient
    if owner_for_recipient(runtime, profile_id):
        return False
    engine = runtime.engine(profile_id)
    if not qq_authorized(engine.store) or not plex_connected(engine.store):
        return False
    if builtin_toplists_ready(engine):
        return False
    state = engine.store.get(STATE_KEY, {}) or {}
    return float(state.get("next_retry_at") or 0) <= (time.time() if now is None else float(now))


def ensure_builtin_toplists(runtime, profile_id, now=None):
    """Create or normalize the built-ins with existing external-playlist code."""
    from .library_sharing import (
        MIRRORED_TOPLISTS_KEY,
        owner_for_recipient,
        sync_qq_toplists_across_libraries,
    )
    from .playlist_hub import rename_playlist
    from .qq_auth_v0320 import QQAuthManager

    now = time.time() if now is None else float(now)
    profile = runtime.registry.get(profile_id)
    engine = runtime.engine(profile_id)
    result = {"created": 0, "renamed": 0, "unchanged": 0, "errors": []}
    if (profile.get("kind") != "owner" or owner_for_recipient(runtime, profile_id)
            or not qq_authorized(engine.store) or not plex_connected(engine.store)):
        return {**result, "status": "waiting_for_authorization"}

    # A restarted process has the saved credential but its QQ client has not
    # necessarily received the cookies yet. This applies it without starting
    # another QR-login thread.
    QQAuthManager(engine.store, engine.qq, start_background=False)
    repository = ExternalRepository(engine.store)
    mirrored = engine.store.get(MIRRORED_TOPLISTS_KEY, {}) or {}
    sources = _sources(repository, profile_id)

    for external_id, title in TOPLISTS:
        source = sources.get(external_id)
        if source and source["id"] in mirrored:
            continue
        try:
            if source is None:
                public = engine.external.import_source(
                    value=f"https://y.qq.com/n/ryqq/toplist/{external_id}",
                )
                source = repository.get_source(profile_id, public["id"])
                sources[external_id] = source
            engine.external.set_follow_updates(source["id"], True)
            managed = repository.get_managed(profile_id, source["id"])
            if managed:
                if managed.get("title") != title:
                    rename_playlist(engine, "external", source["id"], title)
                    result["renamed"] += 1
                else:
                    result["unchanged"] += 1
                continue
            public = engine.external.match(source["id"])
            if int((public.get("counts") or {}).get("matched") or 0) == 0:
                raise ValueError("榜单暂时没有匹配到本地歌曲")
            engine.external.publish(source["id"], title, source["revision"])
            result["created"] += 1
        except Exception as exc:
            result["errors"].append({"external_id": external_id, "error": type(exc).__name__})

    canonical_profiles = sorted({
        str((record or {}).get("source_profile_id") or "")
        for record in mirrored.values() if isinstance(record, dict)
    } - {"", profile_id})
    if canonical_profiles:
        # A previous cross-library publish may have matched successfully but
        # stopped before creating the Plex playlist. Retry from the canonical
        # owner, because mirrored owners deliberately never fetch QQ directly.
        result["libraries"] = [
            sync_qq_toplists_across_libraries(runtime, source_profile_id)
            for source_profile_id in canonical_profiles
        ]
    elif any(repository.get_managed(profile_id, row["id"]) for row in sources.values()):
        result["libraries"] = sync_qq_toplists_across_libraries(runtime, profile_id)
    complete = builtin_toplists_ready(engine)
    state = {
        "policy": POLICY,
        "status": "completed" if complete else "waiting_retry",
        "checked_at": now,
        "next_retry_at": 0 if complete else now + RETRY_SECONDS,
        "errors": list(result["errors"]),
    }
    engine.store.set(STATE_KEY, state)
    result["status"] = state["status"]
    return result
