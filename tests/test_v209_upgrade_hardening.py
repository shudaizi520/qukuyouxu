"""End-to-end preservation check for a copied 2.0.8-style data directory."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import time


def _raw_state(path, *keys):
    with sqlite3.connect(path) as database:
        return dict(database.execute(
            f"SELECT k,v FROM state WHERE k IN ({','.join('?' for _ in keys)})",
            keys,
        ))


def _route_payload(app, path):
    endpoint = next(
        route.endpoint for route in app.routes
        if getattr(route, "path", "") == path
    )
    return endpoint()


def test_copied_208_data_survives_hardening_upgrade(tmp_path):
    from helper.auth import AuthManager, SESSION_SECONDS
    from helper.auth_store import MIGRATION_KEY as AUTH_MIGRATION_KEY
    from helper.automation import PROFILE_STATE_KEY
    from helper.behavior_store import BehaviorRepository
    from helper.profiles import ProfileRegistry
    from helper.scoped_store import ScopedStore
    from helper.store import Store
    from helper.web import create_app

    source_root = tmp_path / "source"
    source = Store(source_root)
    registry = ProfileRegistry(source)
    registry.create(
        "家人", "home", profile_id="family", token="family-token",
        account={"id": "account-family", "username": "family"},
        server={"machine": "server-1", "name": "家中 Plex", "url": "http://plex"},
        library={"id": "22", "name": "家庭音乐"},
    )
    registry.select("family")

    auth = AuthManager(source)
    auth.create_account("admin", "safe-password")
    legacy_token = "legacy-session-token"
    now = time.time()

    family = ScopedStore(source, "family", registry=registry)
    settings = family.get("settings")
    settings.update(plex_url="http://plex", plex_token="family-token", section="22")
    managed = {
        "theme:work": {
            "id": "playlist-88", "title": "工作陪伴",
            "fingerprint": "managed-fingerprint", "snapshot_id": "snapshot-1",
        }
    }
    snapshots = [{
        "id": "snapshot-1", "category_id": "theme:work", "status": "applied",
        "before": {"id": "playlist-88", "items": ["1", "2"]},
        "after": {"id": "playlist-88", "items": ["1", "2", "3"]},
    }]
    schedule = {
        "revision": 7,
        "daily_bootstrap_scheduled": True,
        "tasks": {
            "daily": {
                "config": {"enabled": True, "hour": 6},
                "slot": now - 120, "next_at": now + 300,
                "retry_at": now + 300, "retry_slot": now - 120,
                "failure_count": 1,
            },
            "smart": {
                "config": {"enabled": True, "hour": 3, "interval_days": 7},
                "slot": now + 86400, "next_at": now + 86400,
            },
            "library": {
                "config": {"enabled": False, "hour": 0},
                "slot": None, "next_at": None,
            },
        },
    }
    family.set_many({
        "settings": settings,
        "managed": managed,
        "snapshots": snapshots,
        PROFILE_STATE_KEY: schedule,
        "appearance_settings": {"theme": "warm", "contrast": "normal"},
    })

    behavior = BehaviorRepository(source)
    behavior.append_event("family", {
        "event_key": "upgrade-event", "track_id": "track-1",
        "kind": "complete", "value": 1.0, "at": now - 60,
    })
    behavior.save_track_state("family", "track-1", {
        "affinity": 0.8, "cooldown_until": now + 3600,
    })
    behavior.save_user_state("family", {"completed": 9, "skipped": 2})

    # Recreate the legacy session-only layout after all normal fixture setup.
    # Opening the copied directory with Store must perform the one-time migration.
    digest = hashlib.sha256(legacy_token.encode("ascii")).hexdigest()
    source.set("auth_sessions", [{
        "hash": digest, "username": "admin",
        "created_at": now, "expires_at": now + SESSION_SECONDS,
    }])
    with source.lock, source._db() as database:
        database.execute("DROP TABLE auth_session")
        database.execute("DELETE FROM state WHERE k=?", (AUTH_MIGRATION_KEY,))

    preserved_keys = (
        "profile:family:managed",
        "profile:family:snapshots",
        f"profile:family:{PROFILE_STATE_KEY}",
        "profile:family:appearance_settings",
    )
    expected_raw = _raw_state(source.path, *preserved_keys)

    copied_root = tmp_path / "copied"
    shutil.copytree(source_root, copied_root)
    copied = Store(copied_root)
    copied_registry = ProfileRegistry(copied)
    copied_family = ScopedStore(copied, "family", registry=copied_registry)

    upgraded_auth = AuthManager(copied)
    assert upgraded_auth.verify("admin", "safe-password")
    assert upgraded_auth.session_user(legacy_token, now=now + 1) == "admin"
    assert copied_registry.active_id() == "family"
    assert {row["id"] for row in copied_registry.list_public()} == {"default", "family"}
    assert copied_family.get(PROFILE_STATE_KEY) == schedule
    assert copied_family.get("managed") == managed
    assert copied_family.get("snapshots") == snapshots
    assert copied_family.get("appearance_settings") == {"theme": "warm", "contrast": "normal"}
    assert _raw_state(copied.path, *preserved_keys) == expected_raw

    upgraded_behavior = BehaviorRepository(copied)
    assert upgraded_behavior.list_events("family", now + 1)[0]["event_key"] == "upgrade-event"
    assert upgraded_behavior.load_track_state("family", "track-1")["affinity"] == 0.8
    assert upgraded_behavior.load_user_state("family") == {"completed": 9, "skipped": 2}

    app = create_app(store=copied, start_scheduler=False)
    try:
        with copied_registry.fixed_active("family"):
            status = _route_payload(app, "/api/status")
        assert {
            "version", "settings", "sources", "job", "managed", "conversion",
            "last_run", "summary", "plex_connection", "qq_auth", "single",
            "qq_tags", "name_plan", "scheduler",
        } <= set(status)
        assert status["managed"] == managed
        assert status["scheduler"]["state"] == "error"
        assert status["scheduler"]["next_retry_at"] == schedule["tasks"]["daily"]["retry_at"]
    finally:
        app.state.profile_runtime.close()

    with copied.lock, copied._db() as database:
        assert database.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        migrated = database.execute(
            "SELECT username FROM auth_session WHERE digest=?", (digest,)
        ).fetchone()
    assert migrated == ("admin",)
