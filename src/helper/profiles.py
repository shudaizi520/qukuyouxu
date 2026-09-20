"""Plex profile registry and copy-only migration from the v0.3 state."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import time
import uuid

from .scoped_store import GLOBAL_KEYS, ScopedStore, validate_profile_id
from .store import DEFAULT_SETTINGS
from .behavior_store import delete_profile_rows
from .external_store import ExternalRepository, delete_external_profile_rows


REGISTRY_KEY = "plex_profiles_v1"
MIGRATION_KEY = "profiles_migration_v1"
PROFILE_KINDS = frozenset({"owner", "home", "shared"})


def _text(value, limit=160):
    return str(value or "").strip()[:limit]


def profile_identity(profile):
    """Return the stable Plex user/server/library identity for one profile."""
    profile = profile or {}
    return (
        _text(profile.get("kind"), 20),
        _text((profile.get("account") or {}).get("id"), 80),
        _text((profile.get("server") or {}).get("machine"), 160),
        _text((profile.get("library") or {}).get("id"), 40),
    )


def _library_profile_id(identity):
    payload = json.dumps(identity, ensure_ascii=True, separators=(",", ":"))
    return "p-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


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
        "product_settings": {"behavior_enabled": True},
    }


def _scoped_changes(profile, state):
    profile_id = validate_profile_id(profile["id"])
    return {
        f"profile:{profile_id}:{key}": value
        for key, value in state.items()
        if key not in GLOBAL_KEYS
    }


def _library_reset_state(profile, source=None):
    """Build clean library state while retaining only portable preferences."""
    clean = _initial_state(profile)
    if source is not None:
        clean["daily_settings"] = dict(source.get("daily_settings", {}) or clean["daily_settings"])
        clean["base_settings"] = dict(source.get("base_settings", {}) or clean["base_settings"])
        previous = dict(source.get("settings", {}) or {})
        for key in ("interval_minutes", "source_hours", "min_tracks"):
            if key in previous:
                clean["settings"][key] = previous[key]

    saved = {
        "account": dict(profile.get("account") or {}),
        "server": dict(profile.get("server") or {}),
        "library": dict(profile.get("library") or {}),
    }
    clean.update({
        "plex_saved": saved,
        "plex_reconnect_identity": {},
        "catalog": [],
        "metadata_audit": [],
        "qq_playlist_index": {},
        "qq_tags": [],
        "plan": None,
        "base_plan": None,
        "daily_plan": None,
        "daily_previous_plan": None,
        "daily_managed": None,
        "daily_detached_playlists": [],
        "retired_managed": {},
        "smart_mix_settings": {},
        "smart_mix_plans": {},
        "smart_mix_managed": {},
        "smart_mix_removed": {},
        "plex_history_cache": {},
        "workflow_pending": None,
        "library_auto_next_at": None,
        "library_progress": None,
        "daily_published_view": None,
    })
    return clean


class ProfileRegistry:
    def __init__(self, store):
        self.store = store
        self._request_profile = ContextVar(f"plex_profile_{id(self)}", default="")
        migrate_default_profile(store)

    def _load(self):
        value = self.store.get(REGISTRY_KEY, {}) or {}
        if value.get("schema") != 1 or not isinstance(value.get("profiles"), dict):
            raise ValueError("Plex 档案注册表损坏")
        return value

    def _save(self, value):
        self.store.set(REGISTRY_KEY, value)

    def _replace_scoped_state(self, profile, state, registry):
        """Atomically replace one complete profile namespace with clean state."""
        profile_id = validate_profile_id(profile["id"])
        prefix = f"profile:{profile_id}:"
        values = _scoped_changes(profile, state)
        values[REGISTRY_KEY] = registry
        encoded = [
            (key, json.dumps(value, ensure_ascii=False))
            for key, value in values.items()
        ]
        with self.store.lock, self.store._db() as db:
            db.execute(
                "DELETE FROM state WHERE substr(k,1,?)=?",
                (len(prefix), prefix),
            )
            delete_profile_rows(db, profile_id)
            delete_external_profile_rows(db, profile_id)
            db.executemany(
                "INSERT INTO state(k,v) VALUES (?,?) "
                "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                encoded,
            )

    def _saved_active_id(self):
        value = self._load()
        active = str(value.get("active_profile_id") or "default")
        return active if active in value["profiles"] else "default"

    def active_id(self):
        pinned = self._request_profile.get()
        if pinned:
            return pinned
        return self._saved_active_id()

    @contextmanager
    def fixed_active(self, profile_id=None, enabled_only=False):
        """Keep dynamic stores and proxies on one profile for an entire request."""
        selected = validate_profile_id(profile_id or self._saved_active_id())
        profile = self.get(selected)
        if enabled_only and profile.get("enabled") is False:
            raise ValueError("Plex 档案已停用")
        token = self._request_profile.set(selected)
        try:
            yield selected
        finally:
            self._request_profile.reset(token)

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

    def find_identity(self, kind, account_id, machine, library_id, enabled_only=False):
        expected = (
            _text(kind, 20),
            _text(account_id, 80),
            _text(machine, 160),
            _text(library_id, 40),
        )
        for profile in self._load()["profiles"].values():
            if enabled_only and profile.get("enabled") is False:
                continue
            if profile_identity(profile) == expected:
                return _public(profile)
        return None

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
        value["profiles"][profile_id] = profile
        changes = _scoped_changes(profile, _initial_state(profile))
        changes[REGISTRY_KEY] = value
        self.store.set_many(changes)
        return _public(profile)

    def create_for_library(self, source_profile_id, library, profile_id=None):
        source_profile_id = validate_profile_id(source_profile_id)
        source_profile = self.get(source_profile_id)
        private_source = self._load()["profiles"][source_profile_id]
        library = {
            "id": _text((library or {}).get("id"), 40),
            "name": _text((library or {}).get("name"), 160),
        }
        if not library["id"]:
            raise ValueError("请选择音乐资料库")
        identity = (
            private_source.get("kind"),
            (private_source.get("account") or {}).get("id"),
            (private_source.get("server") or {}).get("machine"),
            library["id"],
        )
        existing = self.find_identity(*identity)
        if existing:
            if existing.get("enabled") is False:
                return self.restore(existing["id"])
            return existing

        value = self._load()
        profile_id = validate_profile_id(profile_id or _library_profile_id(tuple(map(str, identity))))
        if profile_id in value["profiles"]:
            raise ValueError("Plex 档案标识已经存在")
        profile = {
            "id": profile_id,
            "name": source_profile.get("name") or library["name"] or "我的 Plex",
            "kind": private_source.get("kind"),
            "created_at": time.time(),
            "account": dict(private_source.get("account") or {}),
            "server": dict(private_source.get("server") or {}),
            "library": library,
            "token": _text(private_source.get("token"), 512),
            "enabled": True,
        }
        source = ScopedStore(self.store, source_profile_id)
        clean = _library_reset_state(profile, source)
        value["profiles"][profile_id] = profile
        changes = _scoped_changes(profile, clean)
        changes[REGISTRY_KEY] = value
        self.store.set_many(changes)
        return _public(profile)

    def switch_unmanaged_library(self, profile_id, library):
        profile_id = validate_profile_id(profile_id)
        scoped = ScopedStore(self.store, profile_id)
        from .profile_web import connection_is_protected

        if connection_is_protected(scoped):
            raise ValueError("该档案已有托管歌单，不能直接切换音乐资料库")
        library = {
            "id": _text((library or {}).get("id"), 40),
            "name": _text((library or {}).get("name"), 160),
        }
        if not library["id"]:
            raise ValueError("请选择音乐资料库")

        value = self._load()
        profile = value["profiles"].get(profile_id)
        if not profile:
            raise ValueError("Plex 档案不存在")
        candidate = {**profile, "library": library}
        existing = self.find_identity(*profile_identity(candidate))
        if existing and existing["id"] != profile_id:
            raise ValueError("该音乐资料库已有独立档案")

        profile["library"] = library
        clean = _library_reset_state(profile, scoped)
        self._replace_scoped_state(profile, clean, value)
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

    def refresh_access(self, profile_id, token, enabled=None):
        """Atomically keep registry identity and runtime connection credentials aligned."""
        profile_id = validate_profile_id(profile_id)
        token = _text(token, 512)
        if token and (len(token) < 8 or not token.isascii()):
            raise ValueError("Plex Token 格式不正确")
        value = self._load()
        profile = value["profiles"].get(profile_id)
        if not profile:
            raise ValueError("Plex 档案不存在")
        profile["token"] = token
        if enabled is not None:
            profile["enabled"] = bool(enabled)
        scoped = ScopedStore(self.store, profile_id)
        settings = dict(scoped.get("settings", {}) or DEFAULT_SETTINGS)
        settings.update({
            "plex_token": token,
            "plex_url": str((profile.get("server") or {}).get("url") or settings.get("plex_url") or ""),
            "section": str((profile.get("library") or {}).get("id") or settings.get("section") or ""),
            "account_label": str((profile.get("account") or {}).get("username") or profile.get("name") or "")[:80],
        })
        self.store.set_many({
            REGISTRY_KEY: value,
            f"profile:{profile_id}:settings": settings,
        })
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
        if (scoped.get("managed", {}) or scoped.get("daily_managed")
                or ExternalRepository(self.store).has_managed(profile_id)):
            raise ValueError("该档案仍有托管歌单，不能删除")
        prefix = f"profile:{profile_id}:"
        with self.store.lock, self.store._db() as db:
            db.execute("DELETE FROM state WHERE substr(k,1,?)=?", (len(prefix), prefix))
            delete_profile_rows(db, profile_id)
            delete_external_profile_rows(db, profile_id)
            del value["profiles"][profile_id]
            if value.get("active_profile_id") == profile_id:
                value["active_profile_id"] = "default"
            db.execute(
                "INSERT INTO state(k,v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (REGISTRY_KEY, json.dumps(value, ensure_ascii=False)),
            )
        return {"removed": profile_id}
