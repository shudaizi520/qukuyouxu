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
import os
import secrets
import time

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

    def credentials(self):
        value = self.store.get('auth_credentials')
        return value if isinstance(value, dict) and value.get('username') and value.get('password_hash') else None

    def setup_required(self) -> bool:
        return self.credentials() is None

    def create_account(self, username: str, password: str):
        username = validate_username(username); password = validate_password(password)
        salt = os.urandom(16); digest = derive_password(password, salt)
        now = time.time()
        # Keep the first-registration check and write under one process-wide store
        # lock so two simultaneous browsers cannot both become administrator.
        with self.store.lock:
            if self.credentials() is not None:
                raise ValueError('管理员账户已经建立')
            self.store.set_many({
                'auth_credentials': {
                    'username': username,
                    'salt': _b64(salt),
                    'password_hash': _b64(digest),
                    'iterations': PBKDF2_ITERATIONS,
                    'created_at': now,
                    'password_changed_at': now,
                },
                'auth_sessions': [],
            })
        return username

    def verify(self, username: str, password: str) -> bool:
        cred = self.credentials()
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

    def change_password(self, username: str, current_password: str, new_password: str):
        if not self.verify(username, current_password):
            raise ValueError('当前密码不正确')
        new_password = validate_password(new_password)
        cred = self.credentials(); salt = os.urandom(16); now = time.time()
        cred = {**cred, 'salt': _b64(salt), 'password_hash': _b64(derive_password(new_password, salt)),
                'iterations': PBKDF2_ITERATIONS, 'password_changed_at': now}
        self.store.set_many({'auth_credentials': cred, 'auth_sessions': []})
        return cred['username']

    @staticmethod
    def _session_hash(token: str) -> str:
        return hashlib.sha256(token.encode('ascii', 'ignore')).hexdigest()

    def _sessions(self, now=None):
        now = time.time() if now is None else now
        rows = self.store.get('auth_sessions', []) or []
        clean = [r for r in rows if isinstance(r, dict) and float(r.get('expires_at') or 0) > now and r.get('hash')]
        if clean != rows:self.store.set('auth_sessions', clean)
        return clean

    def create_session(self, username: str, now=None):
        now = time.time() if now is None else now
        token = secrets.token_urlsafe(32); row = {'hash': self._session_hash(token), 'username': username,
                'created_at': now, 'expires_at': now + SESSION_SECONDS}
        rows = self._sessions(now); rows.append(row); self.store.set('auth_sessions', rows[-20:])
        return token, row['expires_at']

    def session_user(self, token: str | None, now=None):
        if not token:return None
        target = self._session_hash(str(token))
        for row in self._sessions(now):
            if hmac.compare_digest(str(row.get('hash') or ''), target):return str(row.get('username') or '') or None
        return None

    def revoke_session(self, token: str | None):
        if not token:return
        target = self._session_hash(str(token)); rows = self._sessions()
        self.store.set('auth_sessions', [r for r in rows if not hmac.compare_digest(str(r.get('hash') or ''), target)])

    def revoke_all(self):
        self.store.set('auth_sessions', [])
