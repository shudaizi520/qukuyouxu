"""Stable Plex library identity that never depends on a renewable access token."""
from __future__ import annotations


def _saved_identity(store):
    saved = store.get("plex_saved", {}) or store.get("plex_reconnect_identity", {}) or {}
    result = {key: dict(saved.get(key) or {}) for key in ("account", "server", "library")}
    registry = getattr(store, "registry", None)
    profile_id = str(getattr(store, "profile_id", "") or "")
    if registry is not None and profile_id:
        try:
            profile = registry.get(profile_id)
        except (KeyError, ValueError):
            profile = {}
        for key in result:
            for field, value in (profile.get(key) or {}).items():
                if value and not result[key].get(field):
                    result[key][field] = value
    return result


def stable_library_identity(store):
    saved = _saved_identity(store)
    settings = store.get("settings", {}) or {}
    saved_server = str(saved["server"].get("url") or "").rstrip("/")
    configured_server = str(settings.get("plex_url") or "").rstrip("/")
    saved_library = str(saved["library"].get("id") or "")
    configured_library = str(settings.get("section") or "")
    server = str(saved["server"].get("machine") or configured_server or "")
    library = saved_library or configured_library
    if saved_server and configured_server and saved_server != configured_server:
        server = "unverified:" + configured_server
    if saved_library and configured_library and saved_library != configured_library:
        library = "unverified:" + configured_library
    return (
        str(saved["account"].get("id") or settings.get("account_label") or ""),
        server,
        library,
    )


def stable_library_scope(store):
    from .engine import digest
    return digest(stable_library_identity(store))


def migrate_managed_scopes(store):
    """Adopt records from the same verified server/library after an auth refresh."""
    scope = stable_library_scope(store)
    identity = _saved_identity(store)
    settings = store.get("settings", {}) or {}
    account = str(identity["account"].get("id") or "")
    machine = str(identity["server"].get("machine") or "")
    library = str(identity["library"].get("id") or "")
    saved_url = str(identity["server"].get("url") or "").rstrip("/")
    configured_url = str(settings.get("plex_url") or "").rstrip("/")
    configured_library = str(settings.get("section") or "")
    if (not account or not machine or not library
            or (saved_url and configured_url and saved_url != configured_url)
            or (configured_library and library != configured_library)):
        return False
    changed = False
    values = {}

    def adopt(row):
        nonlocal changed
        if not isinstance(row, dict):
            return row
        record_machine = str(row.get("machine") or "")
        if record_machine and record_machine != machine:
            return row
        if row.get("scope") == scope:
            return row
        changed = True
        return {**row, "scope": scope}

    daily = store.get("daily_managed")
    if daily:
        values["daily_managed"] = adopt(daily)
    smart = store.get("smart_mix_managed", {}) or {}
    if smart:
        values["smart_mix_managed"] = {key: adopt(row) for key, row in smart.items()}
    referenced = set()
    for row in (store.get("managed", {}) or {}).values():
        if isinstance(row, dict) and row.get("snapshot_id"):
            referenced.add(str(row["snapshot_id"]))
    if isinstance(daily, dict) and daily.get("snapshot_id"):
        referenced.add(str(daily["snapshot_id"]))
    for row in smart.values():
        if isinstance(row, dict) and row.get("snapshot_id"):
            referenced.add(str(row["snapshot_id"]))
    for row in (store.get("smart_mix_removed", {}) or {}).values():
        if isinstance(row, dict) and row.get("snapshot_id"):
            referenced.add(str(row["snapshot_id"]))
    snapshots = store.get("snapshots", []) or []
    if snapshots:
        values["snapshots"] = [
            adopt(row) if str(row.get("id") or "") in referenced else row
            for row in snapshots
        ]
    if changed:
        store.set_many(values)
    return changed
