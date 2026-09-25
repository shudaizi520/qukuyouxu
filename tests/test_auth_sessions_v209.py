import hashlib
import json
import sqlite3
import threading
import time


def _digest(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _legacy_database(root, rows):
    path = root / "helper.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE state (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
        db.execute(
            "INSERT INTO state(k, v) VALUES (?, ?)",
            ("auth_sessions", json.dumps(rows)),
        )
    return path


def _synchronize_legacy_session_reads(auth, parties=2):
    if not hasattr(auth, "_sessions"):
        return lambda: None
    original = auth._sessions
    barrier = threading.Barrier(parties)

    def synchronized(now=None):
        rows = original(now)
        barrier.wait(timeout=3)
        return rows

    auth._sessions = synchronized
    return lambda: setattr(auth, "_sessions", original)


def test_legacy_sessions_migrate_once_and_keep_only_valid_newest_digest(tmp_path):
    from helper.auth import AuthManager
    from helper.store import Store

    now = time.time()
    token = "valid-token"
    expired_token = "expired-token"
    digest = _digest(token)
    path = _legacy_database(tmp_path, [
        {"hash": digest, "username": "older", "created_at": now - 30, "expires_at": now + 600},
        {"hash": digest, "username": "admin", "created_at": now - 10, "expires_at": now + 1200},
        {"hash": _digest(expired_token), "username": "admin", "created_at": now - 2000, "expires_at": now - 1},
        {"hash": "not-a-sha256", "username": "admin", "created_at": now, "expires_at": now + 1200},
        {"broken": True},
    ])

    store = Store(tmp_path)
    auth = AuthManager(store)

    assert auth.session_user(token, now=now) == "admin"
    assert auth.session_user(expired_token, now=now) is None
    with sqlite3.connect(path) as db:
        rows = db.execute(
            "SELECT digest, username, expires_at FROM auth_session"
        ).fetchall()
        marker = db.execute(
            "SELECT v FROM state WHERE k='auth_sessions_sqlite_v1'"
        ).fetchone()
        legacy = db.execute("SELECT v FROM state WHERE k='auth_sessions'").fetchone()
    assert rows == [(digest, "admin", now + 1200)]
    assert json.loads(marker[0]) is True
    assert legacy is not None

    Store(tmp_path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM auth_session").fetchone()[0] == 1


def test_two_concurrent_session_creations_both_remain_valid(tmp_path):
    from helper.auth import AuthManager
    from helper.store import Store

    auth = AuthManager(Store(tmp_path))
    auth.create_account("admin", "correct-horse")
    restore = _synchronize_legacy_session_reads(auth)
    tokens = []
    errors = []

    def login():
        try:
            tokens.append(auth.create_session("admin")[0])
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=login) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    restore()

    assert errors == []
    assert len(tokens) == 2
    assert [auth.session_user(token) for token in tokens] == ["admin", "admin"]


def test_concurrent_logout_cannot_restore_the_revoked_session(tmp_path):
    from helper.auth import AuthManager
    from helper.store import Store

    auth = AuthManager(Store(tmp_path))
    auth.create_account("admin", "correct-horse")
    old_token, _ = auth.create_session("admin")
    restore = _synchronize_legacy_session_reads(auth)
    new_tokens = []

    create = threading.Thread(target=lambda: new_tokens.append(auth.create_session("admin")[0]))
    revoke = threading.Thread(target=lambda: auth.revoke_session(old_token))
    create.start()
    revoke.start()
    create.join(timeout=5)
    revoke.join(timeout=5)
    restore()

    assert auth.session_user(old_token) is None
    assert len(new_tokens) == 1
    assert auth.session_user(new_tokens[0]) == "admin"


def test_password_change_atomically_replaces_every_session(tmp_path):
    from helper.auth import AuthManager
    from helper.store import Store

    auth = AuthManager(Store(tmp_path))
    auth.create_account("admin", "correct-horse")
    old_tokens = [auth.create_session("admin")[0] for _ in range(2)]

    replacement, expires_at = auth.change_password_with_session(
        "admin", "correct-horse", "new-correct-horse", now=1000
    )

    assert expires_at > 1000
    assert [auth.session_user(token, now=1001) for token in old_tokens] == [None, None]
    assert auth.session_user(replacement, now=1001) == "admin"
    assert not auth.verify("admin", "correct-horse")
    assert auth.verify("admin", "new-correct-horse")


def test_session_cleanup_enforces_expiry_and_twenty_session_limit(tmp_path):
    from helper.auth import AuthManager, SESSION_SECONDS
    from helper.store import Store

    store = Store(tmp_path)
    auth = AuthManager(store)
    auth.create_account("admin", "correct-horse")
    expired, _ = auth.create_session("admin", now=100)
    assert auth.session_user(expired, now=100 + SESSION_SECONDS + 1) is None

    tokens = [auth.create_session("admin", now=1000 + index)[0] for index in range(25)]
    assert [auth.session_user(token, now=1100) for token in tokens[:5]] == [None] * 5
    assert all(auth.session_user(token, now=1100) == "admin" for token in tokens[5:])
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM auth_session").fetchone()[0] == 20
