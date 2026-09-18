"""Plex Pass webhook ingestion with profile isolation and no idle polling."""
from __future__ import annotations

from email import policy
from email.parser import BytesParser
import hashlib
import hmac
import json
import math
import secrets
import time
from starlette.responses import JSONResponse

from .behavior import MAX_EVENT_AGE, MAX_EVENTS, recent_behavior_events
from .scoped_store import ScopedStore


MAX_WEBHOOK_BYTES = 1024 * 1024
WEBHOOK_INGRESS_KEY = "plex_webhook_ingress_v1"
WEBHOOK_SECRET_KEY = "plex_webhook_secret_v1"
WEBHOOK_SECRET_CREATED_KEY = "plex_webhook_secret_created_at_v1"
ACTIVE_SESSION_TTL = 6 * 3600
TERMINAL_SESSION_TTL = 3600
UNKNOWN_DURATION_PLAYING_TTL = 5 * 60
PLAYBACK_END_GRACE = 30
MAX_BEHAVIOR_SESSIONS = 256
SUPPORTED_EVENTS = frozenset({
    "media.play", "media.pause", "media.resume", "media.stop", "media.scrobble", "media.rate"
})


def _number(value, default=None):
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else default
    except (TypeError, ValueError, OverflowError):
        return default


def active_session_count(sessions, now=None):
    """Return sessions that can still truthfully be described as playing."""
    now = time.time() if now is None else float(now)
    active = 0
    for row in (sessions or {}).values():
        if not isinstance(row, dict) or row.get("terminal_event"):
            continue
        if row.get("state") not in ("media.play", "media.resume"):
            continue
        updated_at = _number(row.get("updated_at"), None)
        if updated_at is None:
            continue
        duration = _number(row.get("duration_seconds"), 0) or 0
        offset = _number(row.get("offset_seconds"), 0) or 0
        ttl = (
            max(PLAYBACK_END_GRACE, duration - min(offset, duration) + PLAYBACK_END_GRACE)
            if duration > 0 else UNKNOWN_DURATION_PLAYING_TTL
        )
        if max(0.0, now - updated_at) <= ttl:
            active += 1
    return active


def parse_multipart_payload(content_type: str, raw: bytes) -> dict:
    if not isinstance(raw, bytes) or len(raw) > MAX_WEBHOOK_BYTES:
        raise ValueError("Plex Webhook 请求超过1MB")
    if not str(content_type or "").lower().startswith("multipart/form-data;"):
        raise ValueError("Plex Webhook 需要 multipart/form-data")
    message = BytesParser(policy=policy.default).parsebytes(
        b"MIME-Version: 1.0\r\nContent-Type: " + content_type.encode("ascii", "strict") + b"\r\n\r\n" + raw
    )
    if not message.is_multipart():
        raise ValueError("Plex Webhook 表单格式无效")
    for part in message.iter_parts():
        if part.get_content_disposition() != "form-data":
            continue
        if part.get_param("name", header="content-disposition") != "payload":
            continue
        data = part.get_payload(decode=True) or b""
        if len(data) > MAX_WEBHOOK_BYTES:
            raise ValueError("Plex Webhook payload 过大")
        try:
            value = json.loads(data.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise ValueError("Plex Webhook payload 不是有效 JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("Plex Webhook payload 需要 JSON 对象")
        return value
    raise ValueError("Plex Webhook 缺少 payload 字段")


def parse_webhook_payload(payload: dict):
    if not isinstance(payload, dict):
        raise ValueError("Plex Webhook payload 无效")
    event = str(payload.get("event") or "")
    if event not in SUPPORTED_EVENTS:
        return None
    metadata = payload.get("Metadata") if isinstance(payload.get("Metadata"), dict) else {}
    if str(metadata.get("type") or "") != "track":
        return None
    track_id = str(metadata.get("ratingKey") or "")
    account = payload.get("Account") if isinstance(payload.get("Account"), dict) else {}
    server = payload.get("Server") if isinstance(payload.get("Server"), dict) else {}
    player = payload.get("Player") if isinstance(payload.get("Player"), dict) else {}
    account_id = str(account.get("id") or "")
    machine = str(server.get("uuid") or server.get("machineIdentifier") or "")
    if not track_id.isdigit() or not account_id or not machine:
        return None
    signal = {
        "event": event,
        "track_id": track_id,
        "account_id": account_id,
        "machine": machine,
        "user": str(account.get("title") or account.get("username") or "")[:120],
        "player_id": str(player.get("uuid") or player.get("machineIdentifier") or "")[:160],
        "title": str(metadata.get("title") or "")[:300],
        "duration_seconds": (_number(metadata.get("duration"), 0) or 0) / 1000,
        "offset_seconds": (_number(metadata.get("viewOffset"), 0) or 0) / 1000,
        "value": 0.0,
        "kind": "neutral",
    }
    if event == "media.scrobble":
        signal.update(value=1.0, kind="completed")
    elif event == "media.rate":
        rating = _number(metadata.get("userRating"), _number(payload.get("rating")))
        if rating is not None and rating >= 8:
            signal.update(value=1.0, kind="high_rating")
        elif rating is not None and rating <= 4:
            signal.update(value=-1.0, kind="low_rating")
    elif event == "media.stop":
        duration = signal["duration_seconds"]
        offset = signal["offset_seconds"]
        if duration > 0 and 0 <= offset <= duration * 1.25:
            ratio = min(1.0, offset / duration)
            if ratio >= 0.80:
                signal.update(value=0.75, kind="substantial_listen")
            elif ratio < 0.65:
                # A stop is ambiguous (skip, interruption, or closing Plex), so
                # keep it as soft evidence. Earlier stops are gradually stronger;
                # the behavior profile only changes long-term taste after repeats.
                strength = 0.15 + 0.35 * (0.65 - ratio) / 0.65
                signal.update(value=-round(strength, 3), kind="observed_skip")
    return signal


def _matching_profiles(registry, machine, account_id):
    profiles = [
        row for row in registry.list_public()
        if row.get("enabled") is not False
        and str((row.get("server") or {}).get("machine") or "") == machine
    ]
    exact = [
        row["id"] for row in profiles
        if str((row.get("account") or {}).get("id") or "") == account_id
    ]
    if exact or account_id != "1":
        return exact
    return [row["id"] for row in profiles if row.get("kind") == "owner"]


def _event_key(signal, playback_id):
    raw = "|".join((
        signal["machine"], signal["account_id"], signal["track_id"], signal["event"],
        signal.get("player_id") or "",
        str(round(signal.get("offset_seconds") or 0, 3)),
        str(round(signal.get("value") or 0, 3)), str(playback_id or ""),
    ))
    return hashlib.sha256(raw.encode()).hexdigest()


def _record_ingress(base_store, signal, result, now):
    """Keep one privacy-safe delivery receipt; never persist the raw payload."""
    previous = base_store.get(WEBHOOK_INGRESS_KEY, {}) or {}
    profiles = dict(previous.get("profiles") or {})
    receipt = {
        "received_at": now,
        "event": str((signal or {}).get("event") or "")[:40],
        "status": str(result.get("status") or "")[:40],
        "reason": str(result.get("reason") or "")[:80],
        "profile_id": str(result.get("profile_id") or "")[:48],
    }
    if receipt["profile_id"] and receipt["status"] in ("accepted", "recorded", "duplicate"):
        profiles[receipt["profile_id"]] = dict(receipt)
    base_store.set(WEBHOOK_INGRESS_KEY, {**receipt, "profiles": profiles})
    return result


def webhook_secret(base_store, now=None):
    now = time.time() if now is None else float(now)
    value = base_store.get(WEBHOOK_SECRET_KEY, "")
    if isinstance(value, str) and len(value) >= 40:
        if _number(base_store.get(WEBHOOK_SECRET_CREATED_KEY), None) is None:
            base_store.set(WEBHOOK_SECRET_CREATED_KEY, now)
        return value
    value = secrets.token_urlsafe(32)
    base_store.set_many({
        WEBHOOK_SECRET_KEY: value,
        WEBHOOK_SECRET_CREATED_KEY: now,
    })
    return value


def webhook_endpoint_path(base_store, now=None):
    return "/api/plex/webhook?secret=" + webhook_secret(base_store, now=now)


def webhook_health(base_store, profile_store, now=None):
    """Describe connection only when the current secret received an event."""
    now = time.time() if now is None else float(now)
    endpoint_path = webhook_endpoint_path(base_store, now=now)
    secret_created_at = _number(base_store.get(WEBHOOK_SECRET_CREATED_KEY), None)
    if secret_created_at is None:
        secret_created_at = now
    latest = base_store.get(WEBHOOK_INGRESS_KEY, {}) or {}
    events = recent_behavior_events(profile_store.get("behavior_events", []) or [], now)
    product = profile_store.get("product_settings", {}) or {}
    profile_id = str(getattr(profile_store, "profile_id", "") or "")
    ingress = (latest.get("profiles") or {}).get(profile_id) or latest
    matched = bool(
        ingress.get("received_at")
        and (_number(ingress.get("received_at"), 0) or 0) >= secret_created_at
        and ingress.get("profile_id") == profile_id
        and ingress.get("status") in ("accepted", "recorded", "duplicate")
    )
    enabled = product.get("behavior_enabled", True) is not False
    if not enabled:
        state = "disabled"
    elif not matched:
        state = "not_connected"
    elif events:
        state = "learning"
    else:
        state = "connected_waiting"
    return {
        "enabled": enabled,
        "connected": matched,
        "state": state,
        "event_count": len(events),
        "last_received_at": ingress.get("received_at") if matched else None,
        "last_event": ingress.get("event", "") if matched else "",
        "last_status": ingress.get("status", ""),
        "last_reason": ingress.get("reason", ""),
        "last_behavior_at": max((_number(row.get("at"), 0) or 0 for row in events), default=None),
        "endpoint_path": endpoint_path,
    }


def apply_webhook_event(base_store, registry, payload, now=None):
    now = time.time() if now is None else float(now)
    signal = parse_webhook_payload(payload)
    if not signal:
        return _record_ingress(base_store, signal, {"status": "ignored", "reason": "unsupported_or_incomplete"}, now)
    matches = _matching_profiles(registry, signal["machine"], signal["account_id"])
    if len(matches) != 1:
        return _record_ingress(base_store, signal, {"status": "ignored", "reason": "identity_not_unique"}, now)
    profile_id = matches[0]
    store = ScopedStore(base_store, profile_id)
    config = store.get("product_settings", {}) or {}
    if config.get("behavior_enabled", True) is False:
        return _record_ingress(base_store, signal, {"status": "ignored", "reason": "disabled", "profile_id": profile_id}, now)

    sessions = dict(store.get("behavior_sessions", {}) or {})
    sessions = {
        key: row for key, row in sessions.items()
        if isinstance(row, dict) and now - (_number(row.get("updated_at"), 0) or 0) <= (
            TERMINAL_SESSION_TTL if row.get("terminal_event") else ACTIVE_SESSION_TTL
        )
    }
    generation = int(_number(store.get("webhook_playback_generation", 0), 0) or 0)
    session_id = signal["account_id"] + ":" + (signal.get("player_id") or "unknown")
    session = sessions.get(session_id)
    matches_session = bool(session and session.get("track_id") == signal["track_id"])

    def new_playback(started_at=None):
        nonlocal generation
        generation += 1
        started_at = now if started_at is None else started_at
        raw = "|".join((signal["machine"], session_id, signal["track_id"], str(generation), str(started_at)))
        return {
            "track_id": signal["track_id"], "started_at": started_at,
            "playback_id": hashlib.sha256(raw.encode()).hexdigest()[:24],
            "terminal_event": "",
        }

    if signal["event"] == "media.play":
        if matches_session and session.get("terminal_event"):
            # Plex's webhook payload has no documented playback/session id.  A
            # play after a terminal event is therefore held as a candidate
            # until its next progress event proves whether this is a genuine
            # replay or a delayed redelivery of the old play notification.
            session = dict(session)
            session.update(pending_play_at=now, pending_play_offset=signal["offset_seconds"])
            sessions[session_id] = session
        else:
            session = new_playback()
    elif signal["event"] in ("media.pause", "media.resume"):
        if matches_session and session.get("pending_play_at") is not None:
            session = new_playback(session.get("pending_play_at"))
        elif not matches_session or session.get("terminal_event"):
            session = new_playback()
    elif signal["event"] in ("media.stop", "media.scrobble"):
        if not matches_session:
            session = new_playback()
        elif session.get("pending_play_at") is not None:
            pending_at = _number(session.get("pending_play_at"), now) or now
            elapsed = max(0.0, now - pending_at)
            duration = signal["duration_seconds"]
            offset = signal["offset_seconds"]
            ratio = offset / duration if duration > 0 else 1.0
            # A genuinely replayed track needs time to reach its reported
            # offset, except for an obvious early skip.  Short, impossible
            # gaps are treated as out-of-order redelivery of the old play.
            progressed = elapsed >= max(30.0, min(duration or offset, offset) * 0.5)
            if (signal["event"] == "media.stop" and ratio < 0.65) or progressed:
                session = new_playback(pending_at)
            else:
                session = dict(session)
                session.pop("pending_play_at", None)
                session.pop("pending_play_offset", None)
    playback_id = (session or {}).get("playback_id") or "rating:" + signal["track_id"]

    seen = dict(store.get("webhook_seen", {}) or {})
    cutoff_seen = now - 3600
    seen = {key: value for key, value in seen.items() if _number(value, 0) >= cutoff_seen}
    key = _event_key(signal, playback_id)
    if key in seen:
        if signal["event"] == "media.play" and session is not None:
            # Persist the ambiguous replay marker even though this exact play
            # delivery was seen before; the next event resolves its meaning.
            sessions[session_id] = session
            store.set_many({
                "webhook_playback_generation": generation,
                "behavior_sessions": sessions,
            })
        return _record_ingress(base_store, signal, {"status": "duplicate", "profile_id": profile_id}, now)
    seen[key] = now
    if len(seen) > 2000:
        seen = dict(sorted(seen.items(), key=lambda item: item[1])[-2000:])

    if session is not None and signal["event"] != "media.rate":
        session.update({
            "offset_seconds": signal["offset_seconds"],
            "duration_seconds": signal["duration_seconds"],
            "state": signal["event"], "user": signal["user"], "updated_at": now,
        })
        if signal["event"] in ("media.stop", "media.scrobble"):
            session["terminal_event"] = signal["event"]
        sessions[session_id] = session
    if len(sessions) > MAX_BEHAVIOR_SESSIONS:
        sessions = dict(sorted(
            sessions.items(),
            key=lambda item: _number(item[1].get("updated_at"), 0) or 0,
        )[-MAX_BEHAVIOR_SESSIONS:])

    events = list(store.get("behavior_events", []) or [])
    def same_playback(row):
        return bool(playback_id and row.get("playback_id") == playback_id)
    if signal["kind"] == "completed":
        events = [
            row for row in events
            if not (
                same_playback(row) and row.get("kind") == "substantial_listen"
            )
        ]
    if signal["kind"] == "substantial_listen" and any(
        same_playback(row) and row.get("kind") == "completed"
        for row in events
    ):
        signal.update(value=0.0, kind="neutral")
    if signal["value"]:
        events.append({
            "track_id": signal["track_id"], "value": signal["value"], "kind": signal["kind"],
            "at": now, "offset_seconds": round(signal["offset_seconds"], 3),
            "duration_seconds": round(signal["duration_seconds"], 3), "user": signal["user"],
            "source": "plex_webhook", "account_id": signal["account_id"],
            "machine": signal["machine"], "player_id": signal.get("player_id") or "",
            "playback_id": playback_id,
        })
    cutoff = now - MAX_EVENT_AGE
    events = [row for row in events if (_number(row.get("at"), 0) or 0) >= cutoff][-MAX_EVENTS:]
    store.set_many({
        "webhook_seen": seen,
        "webhook_playback_generation": generation,
        "behavior_sessions": sessions,
        "behavior_events": events,
        "behavior_status": {
            "updated_at": now,
            "active": active_session_count(sessions, now=now),
            "event_count": len(events),
            "new_events": 1 if signal["value"] else 0, "source": "plex_webhook",
        },
    })
    return _record_ingress(base_store, signal, {
        "status": "recorded" if signal["value"] else "accepted",
        "profile_id": profile_id,
        "kind": signal["kind"],
    }, now)


def attach_webhook_route(app, base_store, registry):
    from fastapi import Request

    expected_secret = webhook_secret(base_store)

    async def plex_webhook(request):
        supplied = str(request.query_params.get("secret") or "")
        if not hmac.compare_digest(supplied, expected_secret):
            return JSONResponse({"error": "Webhook 地址无效"}, status_code=403)
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_WEBHOOK_BYTES:
                raise ValueError("Plex Webhook 请求超过1MB")
        payload = parse_multipart_payload(request.headers.get("content-type", ""), bytes(data))
        return apply_webhook_event(base_store, registry, payload)
    plex_webhook.__annotations__["request"] = Request
    app.post("/api/plex/webhook")(plex_webhook)
