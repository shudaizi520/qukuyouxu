"""Indexed, profile-scoped playback evidence stored in the app database."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .scoped_store import validate_profile_id


EVENT_MAX_AGE = 180 * 86400
EVENT_LIMIT = 20_000
_EVENT_COLUMNS = (
    "event_key",
    "track_id",
    "kind",
    "value",
    "at",
    "progress",
    "duration",
    "player_id",
    "playback_id",
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def ensure_behavior_schema(db) -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS behavior_event (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id TEXT NOT NULL,
            event_key TEXT NOT NULL,
            track_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            value REAL NOT NULL,
            at REAL NOT NULL,
            progress REAL,
            duration REAL,
            player_id TEXT NOT NULL DEFAULT '',
            playback_id TEXT NOT NULL DEFAULT '',
            payload TEXT NOT NULL DEFAULT '{}',
            UNIQUE(profile_id, event_key)
        )"""
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS behavior_event_profile_at "
        "ON behavior_event(profile_id, at DESC)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS behavior_event_profile_track "
        "ON behavior_event(profile_id, track_id, at DESC)"
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS behavior_track_state (
            profile_id TEXT NOT NULL,
            track_id TEXT NOT NULL,
            state TEXT NOT NULL,
            PRIMARY KEY(profile_id, track_id)
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS behavior_user_state (
            profile_id TEXT PRIMARY KEY,
            state TEXT NOT NULL
        )"""
    )


def delete_profile_rows(db, profile_id: str) -> None:
    """Delete one validated profile's V2 learning rows in the caller's transaction."""
    profile_id = validate_profile_id(profile_id)
    for table in ("behavior_event", "behavior_track_state", "behavior_user_state"):
        db.execute(f"DELETE FROM {table} WHERE profile_id=?", (profile_id,))


class BehaviorRepository:
    def __init__(self, base_store):
        self.store = getattr(base_store, "base", base_store)
        self._event_limit = EVENT_LIMIT

    @staticmethod
    def _profile(profile_id: str) -> str:
        return validate_profile_id(profile_id)

    @staticmethod
    def _event_values(event: dict) -> tuple:
        extras = {key: value for key, value in event.items() if key not in _EVENT_COLUMNS}
        return (
            str(event.get("event_key") or ""),
            str(event.get("track_id") or ""),
            str(event.get("kind") or ""),
            float(event.get("value") or 0.0),
            float(event.get("at") or 0.0),
            _optional_float(event.get("progress")),
            _optional_float(event.get("duration")),
            str(event.get("player_id") or ""),
            str(event.get("playback_id") or ""),
            _json(extras),
        )

    def append_event(self, profile_id: str, event: dict) -> bool:
        profile_id = self._profile(profile_id)
        values = self._event_values(event)
        if not values[0] or not values[1] or not values[2]:
            raise ValueError("播放行为缺少事件、歌曲或类型标识")
        with self.store.lock, self.store._db() as db:
            result = db.execute(
                """INSERT OR IGNORE INTO behavior_event (
                    profile_id, event_key, track_id, kind, value, at,
                    progress, duration, player_id, playback_id, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (profile_id, *values),
            )
            return result.rowcount == 1

    def prune(self, profile_id: str, now: float) -> None:
        profile_id = self._profile(profile_id)
        cutoff = float(now) - EVENT_MAX_AGE
        with self.store.lock, self.store._db() as db:
            db.execute(
                "DELETE FROM behavior_event WHERE profile_id=? AND at<?",
                (profile_id, cutoff),
            )
            db.execute(
                """DELETE FROM behavior_event
                   WHERE profile_id=? AND id NOT IN (
                       SELECT id FROM behavior_event WHERE profile_id=?
                       ORDER BY at DESC, id DESC LIMIT ?
                   )""",
                (profile_id, profile_id, int(self._event_limit)),
            )

    def list_events(self, profile_id: str, now: float, limit: int = EVENT_LIMIT) -> list[dict]:
        profile_id = self._profile(profile_id)
        cutoff = float(now) - EVENT_MAX_AGE
        safe_limit = max(0, min(int(limit), int(self._event_limit)))
        with self.store.lock, self.store._db() as db:
            rows = db.execute(
                """SELECT event_key, track_id, kind, value, at, progress,
                          duration, player_id, playback_id, payload
                   FROM behavior_event
                   WHERE profile_id=? AND at>=? AND at<=?
                   ORDER BY at DESC, id DESC LIMIT ?""",
                (profile_id, cutoff, float(now), safe_limit),
            ).fetchall()
        return [_event_dict(row) for row in rows]

    def load_track_state(self, profile_id: str, track_id: str) -> dict:
        profile_id = self._profile(profile_id)
        with self.store.lock, self.store._db() as db:
            row = db.execute(
                "SELECT state FROM behavior_track_state WHERE profile_id=? AND track_id=?",
                (profile_id, str(track_id)),
            ).fetchone()
        return json.loads(row[0]) if row else {}

    def load_track_states(self, profile_id: str) -> dict[str, dict]:
        profile_id = self._profile(profile_id)
        with self.store.lock, self.store._db() as db:
            rows = db.execute(
                "SELECT track_id, state FROM behavior_track_state WHERE profile_id=?",
                (profile_id,),
            ).fetchall()
        return {track_id: json.loads(state) for track_id, state in rows}

    def save_track_state(self, profile_id: str, track_id: str, state: dict) -> None:
        profile_id = self._profile(profile_id)
        with self.store.lock, self.store._db() as db:
            db.execute(
                """INSERT INTO behavior_track_state(profile_id, track_id, state)
                   VALUES (?, ?, ?)
                   ON CONFLICT(profile_id, track_id) DO UPDATE SET state=excluded.state""",
                (profile_id, str(track_id), _json(dict(state or {}))),
            )

    def load_user_state(self, profile_id: str) -> dict:
        profile_id = self._profile(profile_id)
        with self.store.lock, self.store._db() as db:
            row = db.execute(
                "SELECT state FROM behavior_user_state WHERE profile_id=?",
                (profile_id,),
            ).fetchone()
        return json.loads(row[0]) if row else {}

    def save_user_state(self, profile_id: str, state: dict) -> None:
        profile_id = self._profile(profile_id)
        with self.store.lock, self.store._db() as db:
            db.execute(
                """INSERT INTO behavior_user_state(profile_id, state) VALUES (?, ?)
                   ON CONFLICT(profile_id) DO UPDATE SET state=excluded.state""",
                (profile_id, _json(dict(state or {}))),
            )

    def migrate_profile(self, profile_id: str, legacy_events: list[dict], now: float) -> dict:
        profile_id = self._profile(profile_id)
        marker_key = f"profile:{profile_id}:behavior_v2_migration"
        inserted = 0
        with self.store.lock, self.store._db() as db:
            marker = db.execute("SELECT v FROM state WHERE k=?", (marker_key,)).fetchone()
            if marker and (json.loads(marker[0]) or {}).get("complete"):
                return {"inserted": 0, "already_complete": True}
            for index, original in enumerate(legacy_events or []):
                event = dict(original or {})
                track_id = str(event.get("track_id") or event.get("rating_key") or "")
                kind = str(event.get("kind") or event.get("event") or "legacy")
                if not track_id:
                    continue
                at = float(event.get("at") or event.get("time") or now)
                value = float(event.get("value") or 0.0)
                if value < 0 and "explicit" not in kind and "rating" not in kind:
                    value = -min(abs(value), 0.30)
                raw_key = f"{profile_id}\0{track_id}\0{kind}\0{at:.6f}\0{index}"
                event.update(
                    event_key="legacy:" + hashlib.sha256(raw_key.encode("utf-8")).hexdigest(),
                    track_id=track_id,
                    kind=kind,
                    value=value,
                    at=at,
                )
                result = db.execute(
                    """INSERT OR IGNORE INTO behavior_event (
                        profile_id, event_key, track_id, kind, value, at,
                        progress, duration, player_id, playback_id, payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (profile_id, *self._event_values(event)),
                )
                inserted += max(0, result.rowcount)
            db.execute(
                "INSERT INTO state(k,v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (marker_key, _json({"complete": True, "at": float(now), "inserted": inserted})),
            )
        return {"inserted": inserted, "already_complete": False}

    def delete_profile(self, profile_id: str) -> None:
        profile_id = self._profile(profile_id)
        with self.store.lock, self.store._db() as db:
            delete_profile_rows(db, profile_id)


def _optional_float(value):
    if value in (None, ""):
        return None
    return float(value)


def _event_dict(row) -> dict:
    event = {
        "event_key": row[0],
        "track_id": row[1],
        "kind": row[2],
        "value": row[3],
        "at": row[4],
        "progress": row[5],
        "duration": row[6],
        "player_id": row[7],
        "playback_id": row[8],
    }
    event.update(json.loads(row[9] or "{}"))
    return event
