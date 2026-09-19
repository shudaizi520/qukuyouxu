"""Small, local-only Plex connection state helpers.

The Plex token remains in the legacy ``settings`` record because the rest of the
application already reads it there.  This module stores only display metadata
and connection health, so refreshing a page never needs to contact Plex.
"""
from __future__ import annotations

import time

from fastapi import Request


EMPTY_PUBLIC = {
    "configured": False,
    "state": "not_configured",
    "source": "",
    "account": {},
    "server": {},
    "library": {},
    "configured_at": None,
    "last_check": {},
}


def _text(value, limit=240):
    return str(value or "").strip()[:limit]


def _account(value):
    value = value if isinstance(value, dict) else {}
    username = _text(value.get("username") or value.get("title"), 120)
    return {
        "id": _text(value.get("id"), 80),
        "username": username,
        "title": _text(value.get("title") or username, 120),
        "thumb": _text(value.get("thumb"), 500),
    }


def _section(sections, section_id):
    wanted = _text(section_id, 40)
    for row in sections if isinstance(sections, list) else []:
        if _text(row.get("id") if isinstance(row, dict) else "", 40) == wanted:
            return {"id": wanted, "name": _text(row.get("title") or row.get("name"), 160)}
    return {"id": wanted, "name": ""} if wanted else {}


def _normalise(saved):
    saved = saved if isinstance(saved, dict) else {}
    last = saved.get("last_check") if isinstance(saved.get("last_check"), dict) else {}
    status = _text(last.get("status"), 30)
    if status not in ("online", "unreachable", "auth_invalid"):
        last = {}
    else:
        last = {
            "status": status,
            "at": last.get("at"),
            "message": _text(last.get("message"), 300),
        }
    server = saved.get("server") if isinstance(saved.get("server"), dict) else {}
    library = saved.get("library") if isinstance(saved.get("library"), dict) else {}
    return {
        "schema": 1,
        "source": "official" if saved.get("source") == "official" else "manual",
        "account": _account(saved.get("account")),
        "server": {
            "name": _text(server.get("name"), 160),
            "machine": _text(server.get("machine"), 160),
            "url": _text(server.get("url"), 1000),
        },
        "library": {
            "id": _text(library.get("id"), 40),
            "name": _text(library.get("name"), 160),
        },
        "configured_at": saved.get("configured_at"),
        "last_check": last,
    }


def _with_profile_labels(store, saved):
    """Fill display labels from the profile registry without changing identity."""
    registry = getattr(store, "registry", None)
    profile_id = getattr(store, "profile_id", "")
    if registry is None or not profile_id:
        return saved
    try:
        profile = registry.get(profile_id)
    except (KeyError, ValueError):
        return saved
    result = dict(saved)
    for key in ("account", "server", "library"):
        current = dict(result.get(key) or {})
        profile_value = profile.get(key) if isinstance(profile.get(key), dict) else {}
        for field, value in profile_value.items():
            if not current.get(field) and value:
                current[field] = value
        result[key] = current
    return _normalise(result)


def migrate_saved(store, now=None):
    current = store.get("plex_saved")
    if isinstance(current, dict) and current.get("schema") == 1:
        saved = _with_profile_labels(store, _normalise(current))
        if saved != _normalise(current):
            store.set("plex_saved", saved)
        return saved
    settings = store.get("settings", {}) or {}
    if not (_text(settings.get("plex_url")) and _text(settings.get("plex_token"))):
        return None
    connection = store.get("plex_connection", {}) or {}
    result = connection.get("result") if isinstance(connection.get("result"), dict) else {}
    checked_at = result.get("checked_at")
    saved = {
        "schema": 1,
        "source": "official" if connection.get("source") == "official_login" else "manual",
        "account": _account(store.get("plex_login_user", {}) or {}),
        "server": {
            "name": _text(result.get("server") or settings.get("account_label"), 160),
            "machine": _text(result.get("machine"), 160),
            "url": _text(settings.get("plex_url"), 1000),
        },
        "library": _section(result.get("sections", []), settings.get("section")),
        "configured_at": checked_at or (time.time() if now is None else now),
        "last_check": ({"status": "online", "at": checked_at, "message": ""}
                       if checked_at and (result.get("machine") or result.get("server")) else {}),
    }
    saved = _with_profile_labels(store, _normalise(saved))
    store.set("plex_saved", saved)
    return saved


def get_public_saved(store, now=None):
    saved = migrate_saved(store, now=now)
    if not saved:
        return dict(EMPTY_PUBLIC)
    status = saved.get("last_check", {}).get("status") or "saved"
    return {
        "configured": True,
        "state": status,
        "source": saved["source"],
        "account": dict(saved["account"]),
        "server": dict(saved["server"]),
        "library": dict(saved["library"]),
        "configured_at": saved.get("configured_at"),
        "last_check": dict(saved.get("last_check") or {}),
    }


def record_check(store, status, message="", now=None):
    if status not in ("online", "unreachable", "auth_invalid"):
        raise ValueError("unknown Plex check status")
    saved = migrate_saved(store, now=now)
    if not saved:
        return dict(EMPTY_PUBLIC)
    saved["last_check"] = {
        "status": status,
        "at": time.time() if now is None else now,
        "message": _text(message, 300),
    }
    store.set("plex_saved", saved)
    return get_public_saved(store, now=now)


def save_library(store, section_id, name="", now=None):
    section_id = _text(section_id, 40)
    if not section_id.isdigit():
        raise ValueError("音乐资料库无效")
    saved = migrate_saved(store, now=now)
    if not saved:
        raise ValueError("请先连接 Plex")
    from .profile_web import connection_is_protected, guard_connection_change
    guard_connection_change(
        store,
        (saved.get("account") or {}).get("id"),
        (saved.get("server") or {}).get("machine"),
        section_id,
    )
    registry = getattr(store, "registry", None)
    profile_id = str(getattr(store, "profile_id", "") or "")
    if registry is not None and profile_id and not connection_is_protected(store):
        from .plex_recipients import PlexRecipientService

        base_store = getattr(store, "base", store)
        selected = PlexRecipientService(base_store, registry).select_profile_library(
            profile_id, section_id
        )
        if selected["profile"]["id"] != profile_id:
            registry.select(selected["profile"]["id"])
        return get_public_saved(store, now=now)

    settings = dict(store.get("settings", {}) or {})
    settings["section"] = section_id
    saved["library"] = {"id": section_id, "name": _text(name, 160)}
    store.set_many({"settings": settings, "plex_saved": saved, "daily_plan": None, "plan": None})
    if registry is not None:
        registry.update(store.profile_id, library={"id": section_id, "name": _text(name, 160)})
    return get_public_saved(store, now=now)


def official_saved(settings, user, resource, identity, sections, now=None):
    section = _section(sections, settings.get("section"))
    account = _account(user)
    timestamp = time.time() if now is None else now
    return _normalise({
        "schema": 1,
        "source": "official",
        "account": account,
        "server": {
            "name": _text(identity.get("server") or resource.get("name"), 160),
            "machine": _text(identity.get("machine") or resource.get("machine"), 160),
            "url": _text(settings.get("plex_url"), 1000),
        },
        "library": section,
        "configured_at": timestamp,
        "last_check": {"status": "online", "at": timestamp, "message": ""},
    })


def _is_auth_failure(exc):
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if status in (401, 403):
        return True
    message = str(exc).casefold()
    return any(token in message for token in ("unauthorized", "forbidden", "invalid token"))


def attach_routes(app, store, engine, body, ensure_idle):
    """Attach the small connection-state API independently of `/api/status`."""

    @app.get("/api/plex/saved")
    def plex_saved():
        return get_public_saved(store)

    @app.post("/api/plex/library/select")
    async def plex_library_select(request: Request):
        data = await body(request)
        ensure_idle()
        with engine.exclusive():
            return save_library(store, data.get("section"), data.get("name", ""))

    @app.post("/api/plex/check")
    def plex_check():
        ensure_idle()
        timestamp = time.time()
        try:
            with engine.exclusive():
                settings = store.get("settings", {}) or {}
                if not (_text(settings.get("plex_url")) and _text(settings.get("plex_token"))):
                    raise ValueError("请先连接 Plex")
                plex = engine.plex_factory(settings)
                identity = plex.identity()
                sections = plex.sections()
                saved = migrate_saved(store, now=timestamp)
                saved["server"] = {
                    "name": _text(identity.get("server") or saved.get("server", {}).get("name"), 160),
                    "machine": _text(identity.get("machine") or saved.get("server", {}).get("machine"), 160),
                    "url": _text(settings.get("plex_url"), 1000),
                }
                selected = _section(sections, settings.get("section"))
                if selected:
                    saved["library"] = selected
                saved["last_check"] = {"status": "online", "at": timestamp, "message": ""}
                result = {
                    "server": saved["server"]["name"],
                    "machine": saved["server"]["machine"],
                    "version": _text(identity.get("version"), 80),
                    "sections": sections,
                    "checked_at": timestamp,
                }
                store.set_many({
                    "plex_saved": saved,
                    "plex_connection": {"result": result, "source": saved.get("source", "manual")},
                })
                return {**get_public_saved(store), "sections": sections}
        except ValueError:
            raise
        except Exception as exc:
            status = "auth_invalid" if _is_auth_failure(exc) else "unreachable"
            record_check(store, status, str(exc), now=timestamp)
            if status == "auth_invalid":
                raise ValueError("Plex 授权已失效，请重新连接") from None
            raise ValueError("暂时无法访问 Plex，已保留原连接") from None
