"""Transactional storage and one-time migration for administrator sessions."""
from __future__ import annotations

import json
import math
import re
import time


MIGRATION_KEY = "auth_sessions_sqlite_v1"
_DIGEST = re.compile(r"[0-9a-fA-F]{64}")


def _valid_legacy_row(row, now):
    if not isinstance(row, dict):
        return None
    digest = str(row.get("hash") or "")
    username = str(row.get("username") or "")
    if not _DIGEST.fullmatch(digest) or not 2 <= len(username) <= 40:
        return None
    if any(ord(character) < 33 for character in username):
        return None
    try:
        created_at = float(row.get("created_at"))
        expires_at = float(row.get("expires_at"))
    except (TypeError, ValueError, OverflowError):
        return None
    if not (math.isfinite(created_at) and math.isfinite(expires_at)):
        return None
    if created_at < 0 or expires_at <= float(now):
        return None
    return digest.lower(), username, created_at, expires_at


def ensure_auth_schema(db, now=None) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS auth_session (
            digest TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL
        )
    """)
    db.execute(
        "CREATE INDEX IF NOT EXISTS auth_session_expires ON auth_session(expires_at)"
    )
    migrated = db.execute(
        "SELECT 1 FROM state WHERE k=?", (MIGRATION_KEY,)
    ).fetchone()
    if migrated:
        return

    now = time.time() if now is None else float(now)
    legacy = db.execute("SELECT v FROM state WHERE k='auth_sessions'").fetchone()
    try:
        rows = json.loads(legacy[0]) if legacy else []
    except (TypeError, ValueError, json.JSONDecodeError):
        rows = []
    newest = {}
    for raw in rows if isinstance(rows, list) else []:
        row = _valid_legacy_row(raw, now)
        if row is None:
            continue
        previous = newest.get(row[0])
        if previous is None or (row[3], row[2]) > (previous[3], previous[2]):
            newest[row[0]] = row
    db.executemany(
        "INSERT OR REPLACE INTO auth_session(digest,username,created_at,expires_at) "
        "VALUES (?,?,?,?)",
        newest.values(),
    )
    db.execute("DELETE FROM auth_session WHERE expires_at<=?", (now,))
    db.execute("""
        DELETE FROM auth_session
        WHERE digest NOT IN (
            SELECT digest FROM auth_session
            ORDER BY created_at DESC, digest DESC LIMIT 20
        )
    """)
    db.execute(
        "INSERT INTO state(k,v) VALUES (?,?) "
        "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        (MIGRATION_KEY, json.dumps(True)),
    )


class AuthSessionRepository:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _create_in(db, username, digest, created_at, expires_at):
        db.execute("DELETE FROM auth_session WHERE expires_at<=?", (created_at,))
        db.execute(
            "INSERT OR REPLACE INTO auth_session(digest,username,created_at,expires_at) "
            "VALUES (?,?,?,?)",
            (digest, username, created_at, expires_at),
        )
        db.execute("""
            DELETE FROM auth_session
            WHERE digest NOT IN (
                SELECT digest FROM auth_session
                ORDER BY created_at DESC, digest DESC LIMIT 20
            )
        """)

    def create(self, username: str, digest: str, created_at: float, expires_at: float) -> None:
        with self.store.lock, self.store._db() as db:
            self._create_in(db, username, digest, created_at, expires_at)

    def username_for(self, digest: str, now: float) -> str | None:
        with self.store.lock, self.store._db() as db:
            db.execute("DELETE FROM auth_session WHERE expires_at<=?", (now,))
            row = db.execute(
                "SELECT username FROM auth_session WHERE digest=? AND expires_at>?",
                (digest, now),
            ).fetchone()
            return str(row[0]) if row and row[0] else None

    def revoke(self, digest: str) -> None:
        with self.store.lock, self.store._db() as db:
            db.execute("DELETE FROM auth_session WHERE digest=?", (digest,))

    def revoke_all(self) -> None:
        with self.store.lock, self.store._db() as db:
            db.execute("DELETE FROM auth_session")
