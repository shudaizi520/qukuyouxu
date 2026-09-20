"""Pure playback state machine that learns only from confirmed transitions."""
from __future__ import annotations

import hashlib
import math


NEXT_TRACK_WINDOW = 30.0
_ACTIVE_EVENTS = frozenset({"media.play", "media.resume"})


def _number(value, default=0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else float(default)
    except (TypeError, ValueError, OverflowError):
        return float(default)


def _identity(signal: dict) -> str:
    return "\x1f".join(
        str(signal.get(key) or "")
        for key in ("machine", "account_id", "library_id", "player_id")
    )


def _playback_id(identity: str, track_id: str, generation: int, now: float) -> str:
    raw = f"{identity}\0{track_id}\0{generation}\0{now:.6f}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _new_session(identity: str, signal: dict, now: float, catalog_duration: float,
                 previous: dict | None = None) -> dict:
    generation = int((previous or {}).get("generation") or 0) + 1
    duration = _number(signal.get("duration_seconds")) or _number(catalog_duration)
    offset = _number(signal.get("offset_seconds"))
    return {
        "identity": identity,
        "track_id": str(signal.get("track_id") or ""),
        "player_id": str(signal.get("player_id") or ""),
        "playback_id": _playback_id(identity, str(signal.get("track_id") or ""), generation, now),
        "generation": generation,
        "started_at": float(now),
        "last_at": float(now),
        "last_offset": offset,
        "active_seconds": 0.0,
        "duration": duration,
        "state": "media.play",
        "pending_at": None,
        "completed": False,
    }


def _advance_time(session: dict, signal: dict, now: float, catalog_duration: float) -> dict:
    row = dict(session)
    last_at = _number(row.get("last_at"), now)
    if row.get("state") in _ACTIVE_EVENTS:
        elapsed = max(0.0, float(now) - last_at)
        # A Webhook that arrives many hours late must not manufacture a listen.
        row["active_seconds"] = _number(row.get("active_seconds")) + min(elapsed, 6 * 3600)
    duration = _number(signal.get("duration_seconds")) or _number(row.get("duration")) or _number(catalog_duration)
    if duration:
        row["duration"] = duration
    row["last_at"] = float(now)
    row["last_offset"] = _number(signal.get("offset_seconds"), row.get("last_offset") or 0)
    return row


def classify_transition(session: dict, duration: float | None = None):
    duration = _number(duration) or _number(session.get("duration"))
    if duration <= 0:
        return None
    active_seconds = min(_number(session.get("active_seconds")), duration)
    progress = min(1.0, max(0.0, active_seconds / duration))
    if progress >= 0.80:
        return "substantial_listen", 0.75, progress
    if progress < 0.20 and active_seconds <= 60:
        return "confirmed_skip", 0.45, progress
    if progress < 0.50:
        return "confirmed_skip", 0.30, progress
    return "late_exit", 0.10, progress


def _evidence(session: dict, kind: str, value: float, now: float, progress: float) -> dict:
    playback_id = str(session.get("playback_id") or "")
    raw = f"{playback_id}\0{kind}"
    return {
        "event_key": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "track_id": str(session.get("track_id") or ""),
        "kind": kind,
        "value": float(value),
        "at": float(now),
        "progress": round(float(progress), 6),
        "duration": _number(session.get("duration")) or None,
        "player_id": str(session.get("player_id") or ""),
        "playback_id": playback_id,
    }


def _transition_evidence(session: dict, now: float) -> list[dict]:
    if session.get("completed"):
        return []
    classified = classify_transition(session)
    if not classified:
        return []
    kind, value, progress = classified
    return [_evidence(session, kind, value, now, progress)]


def advance_playback(sessions: dict, signal: dict, now: float,
                     catalog_duration: float = 0) -> tuple[dict, list[dict]]:
    """Advance one signal and return a copied session map plus new evidence."""
    now = float(now)
    current = {str(key): dict(value) for key, value in (sessions or {}).items() if isinstance(value, dict)}
    for key, row in list(current.items()):
        pending_at = row.get("pending_at")
        if pending_at is not None and now - _number(pending_at, now) > NEXT_TRACK_WINDOW:
            del current[key]

    identity = _identity(signal)
    event = str(signal.get("event") or "")
    track_id = str(signal.get("track_id") or "")
    session = current.get(identity)
    evidence: list[dict] = []

    if event == "media.play":
        if session and str(session.get("track_id") or "") != track_id:
            pending_at = session.get("pending_at")
            if pending_at is None or now - _number(pending_at, now) <= NEXT_TRACK_WINDOW:
                # The new track's metadata must never supply the old track's
                # missing duration or offset.
                previous_signal = {
                    "offset_seconds": session.get("last_offset"),
                    "duration_seconds": session.get("duration"),
                }
                session = _advance_time(session, previous_signal, now, 0)
                evidence = _transition_evidence(session, now)
            current[identity] = _new_session(identity, signal, now, catalog_duration, session)
        elif session and str(session.get("track_id") or "") == track_id:
            if session.get("pending_at") is not None or session.get("completed"):
                current[identity] = _new_session(identity, signal, now, catalog_duration, session)
            else:
                row = _advance_time(session, signal, now, catalog_duration)
                row["state"] = "media.play"
                current[identity] = row
        else:
            current[identity] = _new_session(identity, signal, now, catalog_duration)
        return current, evidence

    if not session or str(session.get("track_id") or "") != track_id:
        return current, []

    if event == "media.pause":
        row = _advance_time(session, signal, now, catalog_duration)
        row["state"] = "media.pause"
        current[identity] = row
        return current, []

    if event == "media.resume":
        row = dict(session)
        row["last_at"] = now
        row["last_offset"] = _number(signal.get("offset_seconds"), row.get("last_offset") or 0)
        row["duration"] = _number(signal.get("duration_seconds")) or _number(row.get("duration")) or _number(catalog_duration)
        row["state"] = "media.resume"
        current[identity] = row
        return current, []

    if event == "media.scrobble":
        if session.get("completed"):
            return current, []
        row = _advance_time(session, signal, now, catalog_duration)
        row.update(completed=True, state="media.scrobble", pending_at=None)
        current[identity] = row
        duration = _number(row.get("duration"))
        progress = min(1.0, _number(row.get("active_seconds")) / duration) if duration else 1.0
        return current, [_evidence(row, "completed", 1.0, now, progress)]

    if event == "media.stop":
        if session.get("completed"):
            return current, []
        row = _advance_time(session, signal, now, catalog_duration)
        row.update(state="media.stop", pending_at=now)
        current[identity] = row
        return current, []

    return current, []
