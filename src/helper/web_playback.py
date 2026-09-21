"""Authenticated, profile-scoped playback signals from the persistent web player."""
from __future__ import annotations

import hashlib
import math
import re
import time

from .behavior_store import BehaviorRepository
from .playback_learning import advance_playback
from .plex_webhook import (
    ACTIVE_SESSION_TTL,
    MAX_BEHAVIOR_SESSIONS,
    TERMINAL_SESSION_TTL,
    active_session_count,
    catalog_duration,
)
from .scoped_store import ScopedStore, validate_profile_id


WEB_EVENTS = frozenset({"play", "resume", "pause", "progress", "stop", "scrobble"})
_SIGNAL_EVENTS = {
    "play": "media.play",
    "resume": "media.resume",
    "pause": "media.pause",
    "progress": "media.play",
    "stop": "media.stop",
    "scrobble": "media.scrobble",
}
_ID = re.compile(r"[A-Za-z0-9._:-]{1,160}")


def _seconds(value, name):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"网页播放{name}无效") from None
    if not math.isfinite(number) or not 0 <= number <= 86400:
        raise ValueError(f"网页播放{name}无效")
    return number


def _parse_payload(store, profile, payload, plex_factory=None):
    if not isinstance(payload, dict):
        raise ValueError("网页播放事件需要 JSON 对象")
    event = str(payload.get("event") or "")
    if event not in WEB_EVENTS:
        raise ValueError("网页播放事件类型无效")
    track_id = str(payload.get("track_id") or "")
    if not track_id.isdigit():
        raise ValueError("网页播放歌曲标识无效")
    track = next((
        row for row in (store.get("catalog", []) or [])
        if isinstance(row, dict) and str(row.get("id") or "") == track_id
        and row.get("available", True)
    ), None)
    if not track:
        settings = store.get("settings") or {}
        section = str(settings.get("section") or "")
        if (plex_factory is None or not section.isdigit()
                or plex_factory(settings).track_section(track_id) != section):
            raise ValueError("网页播放歌曲不在当前曲库")
    player_id = str(payload.get("player_id") or "")
    event_id = str(payload.get("event_id") or "")
    if not _ID.fullmatch(player_id):
        raise ValueError("网页播放器标识无效")
    if not _ID.fullmatch(event_id):
        raise ValueError("网页播放事件标识无效")
    position = _seconds(payload.get("position", 0), "位置")
    duration = _seconds(payload.get("duration", 0), "时长")
    known_duration = catalog_duration(store, track_id)
    effective_duration = known_duration or duration
    if effective_duration and position > effective_duration + 5:
        raise ValueError("网页播放位置超过歌曲时长")
    return {
        "event": _SIGNAL_EVENTS[event],
        "web_event": event,
        "event_id": event_id,
        "track_id": track_id,
        "player_id": "web:" + player_id,
        "account_id": str((profile.get("account") or {}).get("id") or profile["id"]),
        "machine": str((profile.get("server") or {}).get("machine") or "web-player"),
        "library_id": str((profile.get("library") or {}).get("id") or ""),
        "offset_seconds": position,
        "duration_seconds": effective_duration,
    }


def _seen_key(signal):
    raw = f'{signal["player_id"]}\0{signal["event_id"]}'
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def apply_web_playback_event(base_store, registry, profile_id, payload, now=None, plex_factory=None):
    """Validate and record one browser signal for exactly one selected profile."""
    now = time.time() if now is None else float(now)
    profile_id = validate_profile_id(profile_id)
    profile = registry.get(profile_id)
    if profile.get("enabled") is False:
        raise ValueError("Plex 档案已停用")
    store = ScopedStore(base_store, profile_id, registry=registry)
    signal = _parse_payload(store, profile, payload, plex_factory)
    if (store.get("product_settings", {}) or {}).get("behavior_enabled", True) is False:
        return {"status": "ignored", "reason": "disabled", "profile_id": profile_id, "active": 0}

    sessions = {
        key: dict(row) for key, row in (store.get("behavior_sessions", {}) or {}).items()
        if isinstance(row, dict)
        and now - float(row.get("updated_at", row.get("last_at", 0)) or 0)
        <= (TERMINAL_SESSION_TTL if row.get("terminal_event") else ACTIVE_SESSION_TTL)
    }
    seen = {
        key: value for key, value in (store.get("web_playback_seen", {}) or {}).items()
        if isinstance(value, (int, float)) and value >= now - 3600
    }
    delivery_key = _seen_key(signal)
    if delivery_key in seen:
        return {
            "status": "duplicate", "profile_id": profile_id,
            "active": active_session_count(sessions, now=now),
        }
    seen[delivery_key] = now
    if len(seen) > 2000:
        seen = dict(sorted(seen.items(), key=lambda item: item[1])[-2000:])

    sessions, evidence_rows = advance_playback(
        sessions, signal, now, signal["duration_seconds"],
    )
    published = store.get("daily_published_view", {}) or {}
    published_at = float(published.get("published_at") or 0)
    discovery_ids = {
        str(row.get("id") or "")
        for row in (published.get("items", []) or [])
        if isinstance(row, dict) and row.get("bucket") in ("新鲜发现", "跨口味探索")
    }
    if now >= published_at and signal["track_id"] in discovery_ids:
        for evidence in evidence_rows:
            evidence["discovery"] = True

    if len(sessions) > MAX_BEHAVIOR_SESSIONS:
        sessions = dict(sorted(
            sessions.items(),
            key=lambda item: float(item[1].get("updated_at", item[1].get("last_at", 0)) or 0),
        )[-MAX_BEHAVIOR_SESSIONS:])

    repo = BehaviorRepository(base_store)
    recorded = sum(
        int(repo.record_evidence(profile_id, evidence, now))
        for evidence in evidence_rows
    )
    repo.prune(profile_id, now)
    event_count = repo.event_stats(profile_id, now)["count"]
    active = active_session_count(sessions, now=now)
    store.set_many({
        "web_playback_seen": seen,
        "behavior_sessions": sessions,
        "behavior_status": {
            "updated_at": now,
            "active": active,
            "event_count": event_count,
            "new_events": recorded,
            "source": "web_player",
        },
    })
    return {
        "status": "recorded" if recorded else "accepted",
        "profile_id": profile_id,
        "active": active,
        "kind": evidence_rows[-1]["kind"] if evidence_rows else "neutral",
    }


def attach_web_playback_route(app, base_store, registry, body, runtime=None):
    from fastapi import Request

    async def web_playback_event(request: Request):
        payload = await body(request)
        profile_id = registry.active_id()
        return apply_web_playback_event(
            base_store, registry, profile_id, payload,
            plex_factory=runtime.engine(profile_id).plex_factory if runtime is not None else None,
        )
    web_playback_event.__annotations__["request"] = Request
    app.post("/api/playback/events")(web_playback_event)
