"""Decay-aware playback preference model with reversible skip evidence."""
from __future__ import annotations

import math


DAY = 86400.0
POSITIVE_HALF_LIFE = 180 * DAY
SKIP_HALF_LIFE = 60 * DAY
FATIGUE_HALF_LIFE = 7 * DAY
DAILY_SKIP_CAP = 0.60


def _number(value, default=0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else float(default)
    except (TypeError, ValueError, OverflowError):
        return float(default)


def _decay(value: float, elapsed: float, half_life: float) -> float:
    if value <= 0 or elapsed <= 0:
        return max(0.0, value)
    return value * math.pow(0.5, elapsed / half_life)


def personal_skip_multiplier(user: dict) -> float:
    valid = int(_number((user or {}).get("valid_outcomes")))
    if valid < 30:
        return 1.0
    rate = _number((user or {}).get("skip_outcomes")) / max(1, valid)
    return max(0.7, min(1.3, 1.25 - rate))


def cooldown_days(value: float) -> int:
    value = max(0.0, _number(value))
    if value < 0.25:
        return 0
    if value < 0.60:
        return 1
    if value < 1.20:
        return 7
    if value < 2.00:
        return 30
    if value < 3.00:
        return 90
    return 180


def materialize_track_state(state: dict, now: float) -> dict:
    now = float(now)
    current = dict(state or {})
    updated_at = _number(current.get("updated_at"), now)
    elapsed = max(0.0, now - updated_at)
    positive = _decay(_number(current.get("positive_evidence")), elapsed, POSITIVE_HALF_LIFE)
    skip = _decay(_number(current.get("skip_evidence")), elapsed, SKIP_HALF_LIFE)
    fatigue = _decay(_number(current.get("fatigue")), elapsed, FATIGUE_HALF_LIFE)
    probability = (1.0 + positive) / (2.0 + positive + skip)
    confidence = 1.0 - math.exp(-(positive + skip) / 3.0)
    affinity = (probability - 0.5) * 2.0 * confidence
    current.update(
        positive_evidence=positive,
        skip_evidence=skip,
        fatigue=fatigue,
        affinity=-1.0 if current.get("hard_avoid") else affinity,
        confidence=confidence,
        cooldown_until=max(0.0, _number(current.get("cooldown_until"))),
        hard_avoid=bool(current.get("hard_avoid")),
        last_event=_number(current.get("last_event")),
        updated_at=now,
    )
    return current


def _refresh_scores(state: dict, now: float) -> dict:
    # Values are already materialized at ``now``; this only derives public scores.
    state = dict(state)
    state["updated_at"] = float(now)
    return materialize_track_state(state, now)


def apply_evidence(track_state: dict, user_state: dict, evidence: dict,
                   now: float) -> tuple[dict, dict]:
    now = float(now)
    track = materialize_track_state(track_state, now)
    user = dict(user_state or {})
    user.setdefault("valid_outcomes", 0)
    user.setdefault("skip_outcomes", 0)
    kind = str((evidence or {}).get("kind") or "")
    value = max(0.0, abs(_number((evidence or {}).get("value"))))
    track["last_event"] = now

    if kind == "confirmed_skip":
        user["valid_outcomes"] = int(user["valid_outcomes"]) + 1
        user["skip_outcomes"] = int(user["skip_outcomes"]) + 1
        day_key = int(now // DAY)
        already = _number(track.get("skip_day_amount")) if track.get("skip_day") == day_key else 0.0
        amount = min(value * personal_skip_multiplier(user_state or {}), max(0.0, DAILY_SKIP_CAP - already))
        track["skip_day"] = day_key
        track["skip_day_amount"] = already + amount
        track["skip_evidence"] += amount
        track["fatigue"] += amount
        desired = cooldown_days(track["skip_evidence"]) * DAY
        remaining = max(0.0, track["cooldown_until"] - now)
        if desired > remaining:
            track["cooldown_until"] = now + desired
    elif kind == "late_exit":
        user["valid_outcomes"] = int(user["valid_outcomes"]) + 1
        track["fatigue"] += value or 0.10
    elif kind in ("completed", "substantial_listen"):
        user["valid_outcomes"] = int(user["valid_outcomes"]) + 1
        positive = value or (1.0 if kind == "completed" else 0.75)
        track["positive_evidence"] += positive
        if kind == "completed":
            track["skip_evidence"] *= 0.5
            track["fatigue"] *= 0.5
            track["cooldown_until"] = now + cooldown_days(track["skip_evidence"]) * DAY
    elif kind in ("explicit_like", "high_rating"):
        track["positive_evidence"] += max(2.0, value)
        track["skip_evidence"] = 0.0
        track["fatigue"] = 0.0
        track["cooldown_until"] = 0.0
        track["hard_avoid"] = False
    elif kind in ("explicit_avoid", "low_rating"):
        track["hard_avoid"] = True
        track["skip_evidence"] = max(3.0, track["skip_evidence"])

    if (evidence or {}).get("discovery") and kind in (
        "confirmed_skip", "late_exit", "substantial_listen", "completed",
    ):
        user["discovery_valid"] = int(_number(user.get("discovery_valid"))) + 1
        if kind in ("substantial_listen", "completed"):
            user["discovery_completed"] = int(_number(user.get("discovery_completed"))) + 1
        if kind == "confirmed_skip" and _number((evidence or {}).get("progress"), 1.0) < 0.20:
            user["discovery_early_skips"] = int(_number(user.get("discovery_early_skips"))) + 1

    user["updated_at"] = now
    track = _refresh_scores(track, now)
    return track, user
