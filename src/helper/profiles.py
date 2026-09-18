"""Plex profile registry and copy-only migration from the v0.3 state."""
from __future__ import annotations

import json
import time
import uuid

from .scoped_store import GLOBAL_KEYS, ScopedStore, validate_profile_id
from .store import DEFAULT_SETTINGS


REGISTRY_KEY = "plex_profiles_v1"
MIGRATION_KEY = "profiles_migration_v1"
PROFILE_KINDS = frozenset({"owner", "home", "shared"})


def _text(value, limit=160):
    return str(value or "").strip()[:limit]


def _profile_from_legacy(store, now):
    settings = store.get("settings", {}) or {}
    saved = store.get("plex_saved", {}) or {}
    account = saved.get("account") if isinstance(saved.get("account"), dict) else {}
    server = saved.get("server") if isinstance(saved.get("server"), dict) else {}
    library = saved.get("library") if isinstance(saved.get("library"), dict) else {}
    return {
        "id": "default",
        "name": _text(settings.get("account_label") or account.get("username") or "我的 Plex", 80),
        "kind": "owner",
        "created_at": now,
        "account": {
            "id": _text(account.get("id"), 80),
            "username": _text(account.get("username") or account.get("title"), 120),
        },
        "server": {
            "machine": _text(server.get("machine"), 160),
            "name": _text(server.get("name") or settings.get("account_label"), 160),
            "url": _text(settings.get("plex_url") or server.get("url"), 1000),
        },
        "library": {
            "id": _text(settings.get("section") or library.get("id"), 40),
            "name": _text(library.get("name"), 160),
        },
        "token": _text(settings.get("plex_token"), 512),
        "enabled": True,
    }


def migrate_default_profile(store, now=None):
    """Copy legacy business keys into the default namespace exactly once."""
    timestamp = time.time() if now is None else now
    with store.lock, store._db() as db:
        existing = db.execute("SELECT v FROM state WHERE k=?", (MIGRATION_KEY,)).fetchone()
        if existing:
            return {"migrated": False, **json.loads(existing[0])}
        rows = list(db.execute("SELECT k,v FROM state"))
        copied = 0
        for key, value in rows:
            if key in GLOBAL_KEYS or key.startswith("profile:") or key in (REGISTRY_KEY, MIGRATION_KEY):
                continue
            db.execute(
                "INSERT OR IGNORE INTO state(k,v) VALUES (?,?)",
                (f"profile:default:{key}", value),
            )
            copied += 1
        profile = _profile_from_legacy(store, timestamp)
        registry = {
            "schema": 1,
            "active_profile_id": "default",
            "profiles": {"default": profile},
        }
        marker = {"schema": 1, "at": timestamp, "copied": copied}
        db.execute("INSERT INTO state(k,v) VALUES (?,?)", (REGISTRY_KEY, json.dumps(registry, ensure_ascii=False)))
        db.execute("INSERT INTO state(k,v) VALUES (?,?)", (MIGRATION_KEY, json.dumps(marker, ensure_ascii=False)))
    return {"migrated": True, **marker}


def _public(profile):
    return {
        "id": profile["id"],
        "name": profile["name"],
        "kind": profile["kind"],
        "created_at": profile.get("created_at"),
        "account": dict(profile.get("account") or {}),
        "server": dict(profile.get("server") or {}),
        "library": dict(profile.get("library") or {}),
        "enabled": profile.get("enabled") is not False,
        "token_present": bool(profile.get("token")),
    }


def _initial_state(profile):
    from .base_mixin import DEFAULT_BASE
    from .recommend import DEFAULT_DAILY

    settings = dict(DEFAULT_SETTINGS)
    settings.update({
        "plex_url": profile.get("server", {}).get("url", ""),
        "plex_token": profile.get("token", ""),
        "section": profile.get("library", {}).get("id", ""),
        "account_label": profile.get("name", ""),
    })
    return {
        "settings": settings,
        "sources": [], "snapshots": [], "events": [],
        "cache": {}, "managed": {}, "overrides": {}, "metadata_overrides": {},
        "daily_settings": dict(DEFAULT_DAILY), "base_settings": dict(DEFAULT_BASE),
        "feedback": {"tracks": {}, "artists": {}}, "daily_history": [],
        "behavior_events": [], "behavior_sessions": {}, "behavior_status": {},
        "product_settings": {"behavior_enabled": True, "behavior_user": ""},
    }


class ProfileRegistry:
    def __init__(self, store):
        self.store = store
        migrate_default_profile(store)

    def _load(self):
        value = self.store.get(REGISTRY_KEY, {}) or {}
        if value.get("schema") != 1 or not isinstance(value.get("profiles"), dict):
            raise ValueError("Plex 档案注册表损坏")
        return value

    def _save(self, value):
        self.store.set(REGISTRY_KEY, value)

    def active_id(self):
        value = self._load()
        active = str(value.get("active_profile_id") or "default")
        return active if active in value["profiles"] else "default"

    def get(self, profile_id):
        profile_id = validate_profile_id(profile_id)
        profile = self._load()["profiles"].get(profile_id)
        if not profile:
            raise ValueError("Plex 档案不存在")
        return dict(profile)

    def list_public(self, enabled_only=False):
        value = self._load()
        active = self.active_id()
        return [{**_public(row), "active": key == active}
                for key, row in value["profiles"].items()
                if not enabled_only or row.get("enabled") is not False]

    def select(self, profile_id):
        profile_id = validate_profile_id(profile_id)
        value = self._load()
        if profile_id not in value["profiles"]:
            raise ValueError("Plex 档案不存在")
        value["active_profile_id"] = profile_id
        self._save(value)
        return {**_public(value["profiles"][profile_id]), "active": True}

    def create(self, name, kind, profile_id=None, token="", account=None, server=None, library=None):
        name = _text(name, 80)
        if not name:
            raise ValueError("Plex 档案名称不能为空")
        if kind not in PROFILE_KINDS:
            raise ValueError("Plex 档案类型无效")
        profile_id = validate_profile_id(profile_id or ("p-" + uuid.uuid4().hex[:12]))
        token = _text(token, 512)
        if token and (len(token) < 8 or not token.isascii()):
            raise ValueError("Plex Token 格式不正确")
        value = self._load()
        if profile_id in value["profiles"]:
            raise ValueError("Plex 档案标识已经存在")
        profile = {
            "id": profile_id, "name": name, "kind": kind, "created_at": time.time(),
            "account": dict(account or {}), "server": dict(server or {}),
            "library": dict(library or {}), "token": token, "enabled": True,
        }
        scoped = ScopedStore(self.store, profile_id)
        changes = _initial_state(profile)
        changes[REGISTRY_KEY] = None
        scoped.set_many({key: item for key, item in changes.items() if key != REGISTRY_KEY})
        value["profiles"][profile_id] = profile
        self._save(value)
        return _public(profile)

    def update(self, profile_id, **fields):
        profile_id = validate_profile_id(profile_id)
        value = self._load()
        if profile_id not in value["profiles"]:
            raise ValueError("Plex 档案不存在")
        profile = value["profiles"][profile_id]
        for key in ("name", "kind", "account", "server", "library", "token", "enabled"):
            if key in fields:
                profile[key] = fields[key]
        self._save(value)
        return _public(profile)

    def archive(self, profile_id):
        """Hide a recipient from active use while preserving its managed Plex state."""
        profile_id = validate_profile_id(profile_id)
        if profile_id == "default":
            raise ValueError("默认 Plex 档案不能移除")
        value = self._load()
        profile = value["profiles"].get(profile_id)
        if not profile:
            raise ValueError("Plex 档案不存在")
        profile["enabled"] = False
        if value.get("active_profile_id") == profile_id:
            value["active_profile_id"] = "default"
        self._save(value)
        return _public(profile)

    def restore(self, profile_id):
        profile_id = validate_profile_id(profile_id)
        value = self._load()
        profile = value["profiles"].get(profile_id)
        if not profile:
            raise ValueError("Plex 档案不存在")
        profile["enabled"] = True
        self._save(value)
        return _public(profile)

    def refresh_default_from_legacy(self):
        value = self._load()
        revised = _profile_from_legacy(self.store, value["profiles"]["default"].get("created_at") or time.time())
        value["profiles"]["default"] = revised
        self._save(value)
        ScopedStore(self.store, "default").set("settings", dict(self.store.get("settings", {}) or {}))
        return _public(revised)

    def remove(self, profile_id):
        profile_id = validate_profile_id(profile_id)
        if profile_id == "default":
            raise ValueError("默认 Plex 档案不能删除")
        value = self._load()
        if profile_id not in value["profiles"]:
            raise ValueError("Plex 档案不存在")
        scoped = ScopedStore(self.store, profile_id)
        if scoped.get("managed", {}) or scoped.get("daily_managed"):
            raise ValueError("该档案仍有托管歌单，不能删除")
        prefix = f"profile:{profile_id}:"
        with self.store.lock, self.store._db() as db:
            db.execute("DELETE FROM state WHERE substr(k,1,?)=?", (len(prefix), prefix))
            del value["profiles"][profile_id]
            if value.get("active_profile_id") == profile_id:
                value["active_profile_id"] = "default"
            db.execute(
                "INSERT INTO state(k,v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (REGISTRY_KEY, json.dumps(value, ensure_ascii=False)),
            )
        return {"removed": profile_id}
