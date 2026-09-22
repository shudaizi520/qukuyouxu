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
from .behavior_store import BehaviorRepository
from .playback_learning import advance_playback
from .preference_model import materialize_track_state
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
        if not isinstance(row, dict):
            continue
        pending_replay_at = _number(row.get("pending_replay_at"), None)
        if pending_replay_at is not None:
            duration = _number(row.get("duration_seconds") or row.get("duration"), 0) or 0
            offset = _number(row.get("pending_replay_offset"), 0) or 0
            ttl = (
                max(PLAYBACK_END_GRACE, duration - min(offset, duration) + PLAYBACK_END_GRACE)
                if duration > 0 else UNKNOWN_DURATION_PLAYING_TTL
            )
            if max(0.0, now - pending_replay_at) <= ttl:
                active += 1
            continue
        if row.get("terminal_event"):
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
        "library_id": str(metadata.get("librarySectionID") or "").strip(),
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
        elif rating is not None and 0 < rating <= 4:
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


def _matching_profiles(registry, machine, account_id, library_id="", base_store=None,
                       track_id=""):
    profiles = [
        row for row in registry.list_public()
        if row.get("enabled") is not False
        and str((row.get("server") or {}).get("machine") or "") == machine
    ]
    exact = [
        row for row in profiles
        if str((row.get("account") or {}).get("id") or "") == account_id
    ]
    candidates = exact if exact or account_id != "1" else [
        row for row in profiles if row.get("kind") == "owner"
    ]
    if library_id:
        candidates = [
            row for row in candidates
            if str((row.get("library") or {}).get("id") or "") == library_id
        ]
    elif base_store is not None and str(track_id).isdigit():
        track_id = str(track_id)
        catalog_matches = []
        known_catalog = False
        for row in candidates:
            catalog = ScopedStore(base_store, row["id"]).get("catalog", []) or []
            known_catalog = known_catalog or bool(catalog)
            if any(str(track.get("id") or "") == track_id
                   for track in catalog if isinstance(track, dict)):
                catalog_matches.append(row)
        if known_catalog:
            candidates = catalog_matches
    return [row["id"] for row in candidates]


def _event_key(signal, playback_id):
    raw = "|".join((
        signal["machine"], signal["account_id"], signal["track_id"], signal["event"],
        signal.get("player_id") or "",
        str(round(signal.get("offset_seconds") or 0, 3)),
        str(round(signal.get("value") or 0, 3)), str(playback_id or ""),
    ))
    return hashlib.sha256(raw.encode()).hexdigest()


def catalog_duration(store, track_id):
    track_id = str(track_id or "")
    for row in store.get("catalog", []) or []:
        if isinstance(row, dict) and str(row.get("id") or "") == track_id:
            return _number(row.get("duration"), 0) or 0
    return 0


def load_behavior_snapshot(store, now=None):
    """Return materialized V2 track states for exactly one profile."""
    now = time.time() if now is None else float(now)
    base_store = getattr(store, "base", store)
    profile_id = str(getattr(store, "profile_id", "") or "")
    if not profile_id or not hasattr(base_store, "_db"):
        return {}
    repo = BehaviorRepository(base_store)
    migration = repo.migrate_profile(
        profile_id,
        list(store.get("behavior_events", []) or []),
        now,
    )
    if migration.get("needs_rebuild"):
        repo.rebuild_aggregates(profile_id, now)
    snapshot = {}
    for track_id, saved in repo.load_track_states(profile_id).items():
        state = materialize_track_state(saved, now)
        state.update({
            "score": state["affinity"] - 0.20 * state["fatigue"],
            "long_term_score": state["affinity"],
            "short_term_score": -state["fatigue"],
            "positive": int(state["positive_evidence"] > 0),
            "negative": int(state["skip_evidence"] > 0 or state["hard_avoid"]),
            "recent_positive": int(state["positive_evidence"] > 0),
        })
        snapshot[track_id] = state
    return snapshot


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
    valid_transport = (
        receipt["status"] in ("accepted", "recorded", "duplicate")
        or (receipt["status"] == "ignored" and receipt["reason"] == "disabled")
    )
    if receipt["profile_id"] and valid_transport:
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
    """Keep a current-secret Webhook verified until that secret is replaced."""
    now = time.time() if now is None else float(now)
    endpoint_path = webhook_endpoint_path(base_store, now=now)
    secret_created_at = _number(base_store.get(WEBHOOK_SECRET_CREATED_KEY), None)
    if secret_created_at is None:
        secret_created_at = now
    latest = base_store.get(WEBHOOK_INGRESS_KEY, {}) or {}
    profile_receipts = latest.get("profiles") or {}
    if not isinstance(profile_receipts, dict):
        profile_receipts = {}
    global_receipts = [latest, *profile_receipts.values()]
    global_receipts = [
        row for row in global_receipts
        if isinstance(row, dict)
        and (_number(row.get("received_at"), 0) or 0) >= secret_created_at
        and (
            row.get("status") in ("accepted", "recorded", "duplicate")
            or (row.get("status") == "ignored" and row.get("reason") == "disabled")
        )
    ]
    global_ingress = max(
        global_receipts,
        key=lambda row: _number(row.get("received_at"), 0) or 0,
        default={},
    )
    # A quiet Plex server is not a disconnected Plex server. Webhooks do not
    # have a heartbeat, so elapsed time since the last delivery cannot prove a
    # failure. Secret creation time is the durable validation boundary: after
    # an event reaches the current endpoint, it remains verified until that
    # endpoint's secret is replaced.
    global_connected = bool(global_ingress)
    base_for_behavior = getattr(profile_store, "base", base_store)
    profile_id = str(getattr(profile_store, "profile_id", "") or "")
    if profile_id and hasattr(base_for_behavior, "_db"):
        repo = BehaviorRepository(base_for_behavior)
        migration = repo.migrate_profile(
            profile_id,
            list(profile_store.get("behavior_events", []) or []),
            now,
        )
        if migration.get("needs_rebuild"):
            repo.rebuild_aggregates(profile_id, now)
        stats = repo.event_stats(profile_id, now)
    else:
        events = recent_behavior_events(profile_store.get("behavior_events", []) or [], now)
        stats = {
            "count": len(events),
            "last_at": max((_number(row.get("at"), 0) or 0 for row in events), default=None),
        }
    product = profile_store.get("product_settings", {}) or {}
    active_sessions = active_session_count(
        profile_store.get("behavior_sessions", {}) or {}, now=now,
    )
    ingress = profile_receipts.get(profile_id) or latest
    matched_before = bool(
        ingress.get("received_at")
        and (_number(ingress.get("received_at"), 0) or 0) >= secret_created_at
        and ingress.get("profile_id") == profile_id
        and ingress.get("status") in ("accepted", "recorded", "duplicate")
    )
    matched = matched_before
    enabled = product.get("behavior_enabled", True) is not False
    if not enabled:
        state = "disabled"
    elif not matched_before:
        state = "not_connected"
    elif active_sessions:
        state = "learning"
    else:
        state = "connected_waiting"
    return {
        "enabled": enabled,
        "connected": matched,
        "global_connected": global_connected,
        "state": state,
        "event_count": stats["count"],
        "last_received_at": ingress.get("received_at") if matched_before else None,
        "last_event": ingress.get("event", "") if matched_before else "",
        "last_status": ingress.get("status", ""),
        "last_reason": ingress.get("reason", ""),
        "last_behavior_at": stats["last_at"],
        "global_last_received_at": global_ingress.get("received_at"),
        "global_last_event": global_ingress.get("event", ""),
        "endpoint_path": endpoint_path,
    }


def apply_webhook_event(base_store, registry, payload, now=None):
    now = time.time() if now is None else float(now)
    signal = parse_webhook_payload(payload)
    if not signal:
        return _record_ingress(base_store, signal, {"status": "ignored", "reason": "unsupported_or_incomplete"}, now)
    matches = _matching_profiles(
        registry, signal["machine"], signal["account_id"],
        signal.get("library_id") or "", base_store=base_store,
        track_id=signal["track_id"],
    )
    if len(matches) != 1:
        return _record_ingress(base_store, signal, {"status": "ignored", "reason": "identity_not_unique"}, now)
    profile_id = matches[0]
    if not signal.get("library_id"):
        # Profile matching may safely recover a missing Plex librarySectionID
        # from unique catalog ownership. Use that resolved identity for the
        # playback session too, otherwise play/stop events land in two sessions.
        profile = registry.get(profile_id)
        resolved_library = str((profile.get("library") or {}).get("id") or "")
        if resolved_library:
            signal = dict(signal, library_id=resolved_library)
    store = ScopedStore(base_store, profile_id)
    config = store.get("product_settings", {}) or {}
    if config.get("behavior_enabled", True) is False:
        return _record_ingress(base_store, signal, {"status": "ignored", "reason": "disabled", "profile_id": profile_id}, now)

    sessions = {
        key: dict(row) for key, row in (store.get("behavior_sessions", {}) or {}).items()
        if isinstance(row, dict)
        and now - (_number(row.get("updated_at", row.get("last_at")), 0) or 0)
        <= (TERMINAL_SESSION_TTL if row.get("terminal_event") else ACTIVE_SESSION_TTL)
    }
    identity = "\x1f".join(
        str(signal.get(key) or "")
        for key in ("machine", "account_id", "library_id", "player_id")
    )
    previous = sessions.get(identity) or {}
    pending_replay_at = _number(previous.get("pending_replay_at"), None)
    if (
        pending_replay_at is not None
        and signal["event"] in ("media.stop", "media.scrobble")
        and previous.get("track_id") == signal["track_id"]
    ):
        elapsed = max(0.0, now - pending_replay_at)
        duration_for_replay = signal.get("duration_seconds") or previous.get("duration") or 0
        offset = signal.get("offset_seconds") or 0
        ratio = offset / duration_for_replay if duration_for_replay else 1.0
        progressed = elapsed >= max(30.0, min(duration_for_replay or offset, offset) * 0.5)
        if (signal["event"] == "media.stop" and ratio < 0.65) or progressed:
            replay_signal = dict(signal, event="media.play", offset_seconds=0.0)
            sessions, _ = advance_playback(
                sessions, replay_signal, pending_replay_at,
                catalog_duration(store, signal["track_id"]),
            )
        else:
            row = dict(previous)
            row.pop("pending_replay_at", None)
            row.pop("pending_replay_offset", None)
            sessions[identity] = row
        previous = sessions.get(identity) or {}
    delivery_playback_id = previous.get("playback_id") or "direct:" + signal["track_id"]
    seen = {
        key: value for key, value in (store.get("webhook_seen", {}) or {}).items()
        if (_number(value, 0) or 0) >= now - 3600
    }
    delivery_key = _event_key(signal, delivery_playback_id)
    if delivery_key in seen:
        if (
            signal["event"] == "media.play"
            and previous.get("track_id") == signal["track_id"]
            and previous.get("completed")
        ):
            previous = dict(previous)
            previous["pending_replay_at"] = now
            previous["pending_replay_offset"] = signal.get("offset_seconds") or 0
            sessions[identity] = previous
            store.set("behavior_sessions", sessions)
        return _record_ingress(
            base_store, signal,
            {"status": "duplicate", "profile_id": profile_id}, now,
        )
    seen[delivery_key] = now
    if len(seen) > 2000:
        seen = dict(sorted(seen.items(), key=lambda item: item[1])[-2000:])

    duration = catalog_duration(store, signal["track_id"])
    evidence_rows = []
    if signal["event"] == "media.rate":
        if signal.get("value"):
            evidence_rows.append({
                "event_key": delivery_key,
                "track_id": signal["track_id"],
                "kind": signal["kind"],
                "value": signal["value"],
                "at": now,
                "progress": None,
                "duration": signal.get("duration_seconds") or duration or None,
                "player_id": signal.get("player_id") or "",
                "playback_id": delivery_playback_id,
            })
    else:
        sessions, evidence_rows = advance_playback(sessions, signal, now, duration)

    published = store.get("daily_published_view", {}) or {}
    published_at = _number(published.get("published_at"), 0) or 0
    discovery_ids = {
        str(row.get("id") or "")
        for row in published.get("items", []) or []
        if isinstance(row, dict)
        and row.get("bucket") in ("新鲜发现", "跨口味探索")
    }
    if now >= published_at:
        for evidence in evidence_rows:
            if str(evidence.get("track_id") or "") in discovery_ids:
                evidence["discovery"] = True

    resulting = sessions.get(identity) or {}
    resulting_playback_id = resulting.get("playback_id")
    if resulting_playback_id:
        seen[_event_key(signal, resulting_playback_id)] = now

    if len(sessions) > MAX_BEHAVIOR_SESSIONS:
        sessions = dict(sorted(
            sessions.items(),
            key=lambda item: _number(item[1].get("updated_at", item[1].get("last_at")), 0) or 0,
        )[-MAX_BEHAVIOR_SESSIONS:])

    repo = BehaviorRepository(base_store)
    recorded = 0
    for evidence in evidence_rows:
        recorded += int(repo.record_evidence(profile_id, evidence, now))
    repo.prune(profile_id, now)
    event_count = repo.event_stats(profile_id, now)["count"]
    store.set_many({
        "webhook_seen": seen,
        "behavior_sessions": sessions,
        "behavior_status": {
            "updated_at": now,
            "active": active_session_count(sessions, now=now),
            "event_count": event_count,
            "new_events": recorded,
            "source": "plex_webhook",
        },
    })
    return _record_ingress(base_store, signal, {
        "status": "recorded" if recorded else "accepted",
        "profile_id": profile_id,
        "kind": evidence_rows[-1]["kind"] if evidence_rows else "neutral",
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
