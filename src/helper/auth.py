"""Local administrator account/password authentication.

Passwords are never stored in plaintext. Browser sessions use opaque random
HttpOnly cookies whose SHA-256 digests are stored in the application-owned DB.
The legacy ADMIN_TOKEN is accepted only as an upgrade/bootstrap proof when no
administrator account exists yet.
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from .auth_store import AuthSessionRepository

PBKDF2_ITERATIONS = 310_000
SESSION_SECONDS = 30 * 24 * 3600
COOKIE_NAME = 'pch_session'


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode('ascii').rstrip('=')


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def validate_username(value: str) -> str:
    value = str(value or '').strip()
    if not 2 <= len(value) <= 40 or any(ord(c) < 33 for c in value):
        raise ValueError('用户名需为2—40个可见字符')
    return value


def validate_password(value: str) -> str:
    value = str(value or '')
    if not 8 <= len(value) <= 128 or any(ord(c) < 32 for c in value):
        raise ValueError('密码需为8—128个字符')
    return value


def derive_password(password: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS) -> bytes:
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations, dklen=32)


class AuthManager:
    def __init__(self, store):
        self.store = store
        self.sessions = AuthSessionRepository(store)

    @staticmethod
    def _credential_value(raw):
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) and value.get('username') and value.get('password_hash') else None

    @classmethod
    def _credentials_in(cls, db):
        row = db.execute("SELECT v FROM state WHERE k='auth_credentials'").fetchone()
        return cls._credential_value(row[0]) if row else None

    @staticmethod
    def _store_credentials(db, credentials):
        db.execute(
            "INSERT INTO state(k,v) VALUES ('auth_credentials',?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (json.dumps(credentials, ensure_ascii=False),),
        )

    @staticmethod
    def _clear_legacy_sessions(db):
        db.execute(
            "INSERT INTO state(k,v) VALUES ('auth_sessions','[]') "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v"
        )

    def credentials(self):
        return self._credential_value(self.store.get('auth_credentials'))

    def setup_required(self) -> bool:
        return self.credentials() is None

    def create_account(self, username: str, password: str):
        username = validate_username(username); password = validate_password(password)
        salt = os.urandom(16); digest = derive_password(password, salt)
        now = time.time()
        # Keep the first-registration check and write under one process-wide store
        # lock so two simultaneous browsers cannot both become administrator.
        with self.store.lock, self.store._db() as db:
            if self._credentials_in(db) is not None:
                raise ValueError('管理员账户已经建立')
            self._store_credentials(db, {
                'username': username,
                'salt': _b64(salt),
                'password_hash': _b64(digest),
                'iterations': PBKDF2_ITERATIONS,
                'created_at': now,
                'password_changed_at': now,
            })
            db.execute("DELETE FROM auth_session")
            self._clear_legacy_sessions(db)
        return username

    @staticmethod
    def _verify_credentials(cred, username: str, password: str) -> bool:
        if not cred or not hmac.compare_digest(str(username or ''), str(cred.get('username') or '')):
            # Do a fixed-cost derivation to reduce username timing signal.
            derive_password(str(password or ''), b'\0' * 16, PBKDF2_ITERATIONS)
            return False
        try:
            salt = _unb64(cred['salt']); expected = _unb64(cred['password_hash']); iterations = int(cred.get('iterations') or PBKDF2_ITERATIONS)
            actual = derive_password(str(password or ''), salt, iterations)
            return hmac.compare_digest(actual, expected)
        except Exception:
            return False

    def verify(self, username: str, password: str) -> bool:
        return self._verify_credentials(self.credentials(), username, password)

    def _change_password(self, username, current_password, new_password, now, issue_session):
        new_password = validate_password(new_password)
        now = time.time() if now is None else float(now)
        token = secrets.token_urlsafe(32) if issue_session else None
        expires_at = now + SESSION_SECONDS if issue_session else None
        with self.store.lock, self.store._db() as db:
            cred = self._credentials_in(db)
            if not self._verify_credentials(cred, username, current_password):
                raise ValueError('当前密码不正确')
            salt = os.urandom(16)
            updated = {
                **cred,
                'salt': _b64(salt),
                'password_hash': _b64(derive_password(new_password, salt)),
                'iterations': PBKDF2_ITERATIONS,
                'password_changed_at': now,
            }
            self._store_credentials(db, updated)
            db.execute("DELETE FROM auth_session")
            self._clear_legacy_sessions(db)
            if issue_session:
                AuthSessionRepository._create_in(
                    db, updated['username'], self._session_hash(token), now, expires_at
                )
        return updated['username'], token, expires_at

    def change_password(self, username: str, current_password: str, new_password: str):
        changed, _token, _expires_at = self._change_password(
            username, current_password, new_password, None, False
        )
        return changed

    def change_password_with_session(self, username: str, current_password: str,
                                     new_password: str, now=None):
        _changed, token, expires_at = self._change_password(
            username, current_password, new_password, now, True
        )
        return token, expires_at

    @staticmethod
    def _session_hash(token: str) -> str:
        return hashlib.sha256(token.encode('ascii', 'ignore')).hexdigest()

    def create_session(self, username: str, now=None):
        now = time.time() if now is None else now
        token = secrets.token_urlsafe(32)
        expires_at = now + SESSION_SECONDS
        self.sessions.create(username, self._session_hash(token), now, expires_at)
        return token, expires_at

    def session_user(self, token: str | None, now=None):
        if not token:return None
        now = time.time() if now is None else float(now)
        return self.sessions.username_for(self._session_hash(str(token)), now)

    def revoke_session(self, token: str | None):
        if not token:return
        self.sessions.revoke(self._session_hash(str(token)))

    def revoke_all(self):
        self.sessions.revoke_all()
