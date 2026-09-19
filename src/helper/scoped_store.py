"""Profile-scoped view over the application-owned state store."""
from __future__ import annotations

import re


GLOBAL_KEYS = frozenset({
    "installation_id",
    "auth_credentials",
    "auth_sessions",
    "auth_bootstrap_token",
    "plex_profiles_v1",
    "profiles_migration_v1",
    "automation_settings_v1",
})


def validate_profile_id(value: str) -> str:
    value = str(value or "").strip()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9_-]{0,46}[a-z0-9])?", value):
        raise ValueError("Plex 档案标识无效")
    return value


class ScopedStore:
    def __init__(self, base_store, profile_id: str, registry=None):
        self.base = base_store
        self.profile_id = validate_profile_id(profile_id)
        self.registry = registry
        self.root = base_store.root
        self.path = base_store.path
        self.lock = base_store.lock

    def _key(self, key: str) -> str:
        key = str(key)
        if key in GLOBAL_KEYS:
            return key
        return f"profile:{self.profile_id}:{key}"

    def get(self, key, default=None):
        return self.base.get(self._key(key), default)

    def get_prefix(self, prefix):
        prefix = str(prefix)
        full = f"profile:{self.profile_id}:{prefix}"
        rows = self.base.get_prefix(full)
        root = f"profile:{self.profile_id}:"
        return {key[len(root):]: value for key, value in rows.items()}

    def set(self, key, value):
        self.base.set(self._key(key), value)

    def set_many(self, values):
        self.base.set_many({self._key(key): value for key, value in values.items()})

    def log(self, message, level="info"):
        import time
        with self.lock:
            events = self.get("events", []) or []
            events.append({"time": time.time(), "level": level, "message": str(message)[:500]})
            self.set("events", events[-200:])


class ActiveProfileStore:
    """Dynamic store used by the admin UI for the currently selected profile."""
    def __init__(self, base_store, registry):
        self.base = base_store
        self.registry = registry
        self.root = base_store.root
        self.path = base_store.path
        self.lock = base_store.lock

    @property
    def profile_id(self):
        return self.registry.active_id()

    def fixed(self):
        return ScopedStore(self.base, self.profile_id, registry=self.registry)

    def get(self, key, default=None):
        return self.fixed().get(key, default)

    def get_prefix(self, prefix):
        return self.fixed().get_prefix(prefix)

    def set(self, key, value):
        return self.fixed().set(key, value)

    def set_many(self, values):
        return self.fixed().set_many(values)

    def log(self, message, level="info"):
        return self.fixed().log(message, level)
