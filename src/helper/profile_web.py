"""Profile APIs and connection-switch safety shared by every Plex flow."""
from __future__ import annotations

from contextlib import nullcontext
from fastapi import Request

from .profiles import ProfileRegistry
from .scoped_store import ScopedStore


def _identity_document(store):
    saved = store.get("plex_saved", {}) or {}
    if not saved:
        saved = store.get("plex_reconnect_identity", {}) or {}
    saved = {
        key: dict(saved.get(key) or {})
        for key in ("account", "server", "library")
    }
    registry = getattr(store, "registry", None)
    profile_id = str(getattr(store, "profile_id", "") or "")
    if registry is not None and profile_id:
        try:
            profile = registry.get(profile_id)
        except (KeyError, ValueError):
            profile = {}
        for key in ("account", "server", "library"):
            for field, value in (profile.get(key) or {}).items():
                if value and not saved[key].get(field):
                    saved[key][field] = value
    return saved


def _identity(store):
    saved = _identity_document(store)
    account = saved["account"]
    server = saved["server"]
    library = saved["library"]
    return (
        str(account.get("id") or ""),
        str(server.get("machine") or ""),
        str(library.get("id") or ""),
    )


def connection_identity(store):
    """Return the verified current or preserved reconnect identity."""
    return _identity(store)


def connection_is_protected(store):
    unresolved = any(
        row.get("status") in ("prepared", "uncertain", "restoring")
        for row in (store.get("snapshots", []) or [])
        if isinstance(row, dict)
    )
    return bool(
        store.get("managed", {}) or store.get("daily_managed")
        or store.get("retired_managed", {}) or store.get("smart_mix_managed", {})
        or store.get("smart_mix_removed", {}) or unresolved
    )


def guard_connection_change(store, account_id, machine, library_id):
    if not connection_is_protected(store):
        return
    before = _identity(store)
    after = (str(account_id or ""), str(machine or ""), str(library_id or ""))
    labels = ("Plex 账户", "Plex 服务器", "音乐资料库")
    for old, new, label in zip(before, after, labels):
        if not old or not new or old != new:
            raise ValueError(f"该档案已有托管歌单，不能直接切换{label}")


def sync_profile_connection(store, account, server, library, token):
    """Save one verified connection to both the active scope and registry."""
    account = dict(account or {})
    server = dict(server or {})
    library = dict(library or {})
    token = str(token or "").strip()
    guard_connection_change(
        store, account.get("id"), server.get("machine"), library.get("id")
    )
    settings = dict(store.get("settings", {}) or {})
    settings.update({
        "plex_url": str(server.get("url") or "").rstrip("/"),
        "plex_token": token,
        "section": str(library.get("id") or ""),
        "account_label": str(account.get("username") or server.get("name") or "")[:80],
    })
    store.set_many({"settings": settings, "plex_reconnect_identity": {}})
    registry = getattr(store, "registry", None)
    if registry is None:
        raise ValueError("当前存储没有 Plex 档案注册表")
    registry.update(
        store.profile_id,
        account=account,
        server=server,
        library=library,
        token=token,
    )
    from .connection_scope import migrate_managed_scopes
    migrate_managed_scopes(store)
    return registry.get(store.profile_id)


def disconnect_profile_connection(store):
    """Drop the active profile's Plex authorization without deleting local work."""
    reconnect_identity = {}
    if connection_is_protected(store):
        saved = _identity_document(store)
        if all(_identity(store)):
            reconnect_identity = {
                "account": dict(saved.get("account") or {}),
                "server": dict(saved.get("server") or {}),
                "library": dict(saved.get("library") or {}),
            }
    settings = dict(store.get("settings", {}) or {})
    settings.update({
        "plex_url": "",
        "plex_token": "",
        "section": "",
        "account_label": "",
        "auto_enabled": False,
    })
    daily_settings = dict(store.get("daily_settings", {}) or {})
    daily_settings["enabled"] = False
    smart_settings = dict(store.get("smart_mix_settings", {}) or {})
    smart_settings.update({
        "auto_enabled": False,
        "weekly_auto_enabled": False,
        "auto_paused_reasons": {},
        "auto_retry_state": {},
    })
    store.set_many({
        "settings": settings,
        "daily_settings": daily_settings,
        "smart_mix_settings": smart_settings,
        "library_auto_next_at": 0,
        "plex_login_user": {},
        "plex_login_pending": {},
        "plex_connection": None,
        "plex_saved": {},
        "plex_reconnect_identity": reconnect_identity,
        "daily_plan": None,
        "plan": None,
    })
    registry = getattr(store, "registry", None)
    if registry is None:
        raise ValueError("当前存储没有 Plex 档案注册表")
    registry.update(
        store.profile_id,
        account={},
        server={},
        library={},
        token="",
    )
    return {"message": "Plex 已断开"}


def resolve_owner_profile_id(registry, profile_id=None):
    """Resolve the current project's Plex owner without crossing servers."""
    current = registry.get(profile_id or registry.active_id())
    if current.get("kind") == "owner" and current.get("token_present"):
        return current["id"]
    machine = str((current.get("server") or {}).get("machine") or "")
    owners = [
        row for row in registry.list_public(enabled_only=True)
        if row.get("kind") == "owner"
        and row.get("token_present")
        and str((row.get("server") or {}).get("machine") or "") == machine
    ]
    if len(owners) == 1:
        return owners[0]["id"]
    library_id = str((current.get("library") or {}).get("id") or "")
    same_library = [
        row for row in owners
        if library_id and str((row.get("library") or {}).get("id") or "") == library_id
    ]
    if len(same_library) == 1:
        return same_library[0]["id"]
    candidates = same_library or owners
    account_ids = {
        str((row.get("account") or {}).get("id") or "")
        for row in candidates
    }
    if candidates and len(account_ids) == 1 and "" not in account_ids:
        return sorted(candidates, key=lambda row: row["id"])[0]["id"]
    raise ValueError("请先打开这个服务器对应的 Plex 所有者账户")


def attach_profile_routes(app, base_store, registry: ProfileRegistry, body, ensure_idle, engine=None):
    from .plex_recipients import PlexRecipientService
    from .profile_controls import migrate_controls, read_controls, write_control
    from .profile_runtime import ProfileRuntime
    recipients = PlexRecipientService(base_store, registry)

    def ensure_controls_migrated():
        # Do this before the first control read/write, not while registering routes.
        runtime = getattr(getattr(app, "state", None), "profile_runtime", None)
        migrate_controls(runtime or ProfileRuntime(base_store, registry))

    def operation():
        return engine.exclusive() if engine is not None else nullcontext()

    def public_profiles():
        ensure_controls_migrated()
        rows = registry.list_public(enabled_only=True)
        for row in rows:
            row["controls"] = read_controls(ScopedStore(base_store, row["id"]))
            row["behavior_enabled"] = row["controls"]["learning"]
        return rows

    def enabled_store(profile_id):
        ensure_controls_migrated()
        profile = registry.get(profile_id)
        if profile.get("enabled") is False:
            raise ValueError("该用户已停用")
        return ScopedStore(base_store, profile_id, registry=registry)

    @app.get("/api/plex/profiles")
    async def list_profiles():
        return {"active_profile_id": registry.active_id(), "items": public_profiles()}

    @app.get("/api/plex/profiles/controls")
    def get_profile_controls(profile_id: str):
        return {"profile_id": profile_id, "controls": read_controls(enabled_store(profile_id))}

    @app.post("/api/plex/profiles/control")
    async def set_profile_control(request: Request):
        data = await body(request)
        profile_id = str(data.get("profile_id") or "").strip()
        if not profile_id:
            raise ValueError("请选择用户")
        ensure_idle()
        with operation():
            controls = write_control(enabled_store(profile_id), data.get("key"), data.get("enabled"))
        return {"profile_id": profile_id, "controls": controls}

    @app.post("/api/plex/profiles/learning")
    async def set_profile_learning(request: Request):
        data = await body(request)
        profile_id = str(data.get("profile_id") or "").strip()
        if not profile_id:
            raise ValueError("请选择用户")
        if not isinstance(data.get("enabled"), bool):
            raise ValueError("播放学习开关状态无效")
        ensure_idle()
        with operation():
            write_control(enabled_store(profile_id), "learning", data["enabled"])
        return {
            "profile_id": profile_id,
            "behavior_enabled": data["enabled"],
        }

    @app.get("/api/plex/profiles/libraries")
    def list_profile_libraries(profile_id: str = "default"):
        with operation():
            return {"items": recipients.list_profile_libraries(profile_id)}

    @app.post("/api/plex/profiles/select")
    async def select_profile(request: Request):
        data = await body(request)
        ensure_idle()
        with operation():
            return {"profile": registry.select(data.get("profile_id"))}

    @app.post("/api/plex/profiles/create")
    async def create_profile(request: Request):
        data = await body(request)
        ensure_idle()
        with operation():
            profile = registry.create(
                name=data.get("name"), kind="owner", profile_id=data.get("profile_id") or None
            )
            registry.select(profile["id"])
        return {"profile": profile, "message": "Plex 档案已建立，请完成官方授权。"}

    @app.post("/api/plex/profiles/remove")
    async def remove_profile(request: Request):
        data = await body(request)
        ensure_idle()
        if data.get("confirm") is not True:
            raise ValueError("请确认删除 Plex 档案；不会删除 Plex 歌单")
        with operation():
            profile = registry.archive(data.get("profile_id"))
        return {"profile": profile, "message": "已停止为这位用户生成推荐；Plex 歌单保留。"}

    @app.post("/api/plex/profiles/restore")
    async def restore_profile(request: Request):
        data = await body(request)
        ensure_idle()
        with operation():
            profile = registry.restore(data.get("profile_id"))
        return {"profile": profile, "message": "用户已重新加入每日推荐。"}

    @app.get("/api/plex/recipients/home")
    def list_home_recipients(owner_profile_id: str = "default"):
        return {"items": recipients.list_home_users(owner_profile_id)}

    @app.get("/api/plex/recipients")
    def list_people(owner_profile_id: str = ""):
        owner_profile_id = owner_profile_id or resolve_owner_profile_id(registry)
        return {
            **recipients.list_people(owner_profile_id),
            "owner_profile_id": owner_profile_id,
        }

    @app.post("/api/plex/recipients/libraries")
    async def list_recipient_libraries(request: Request):
        data = await body(request)
        ensure_idle()
        with operation():
            return recipients.list_recipient_libraries(
                data.get("owner_profile_id", "default"),
                data.get("kind"),
                data.get("user_id"),
            )

    @app.post("/api/plex/profiles/library")
    async def select_profile_library(request: Request):
        data = await body(request)
        ensure_idle()
        with operation():
            return recipients.select_profile_library(
                data.get("profile_id"), data.get("library_id")
            )

    @app.post("/api/plex/recipients/home/import")
    async def import_home_recipient(request: Request):
        data = await body(request)
        ensure_idle()
        with operation():
            profile = recipients.import_home_user(
                data.get("owner_profile_id", "default"), data.get("user_id"), data.get("library_id")
            )
        return {"profile": profile, "message": "Plex Home 用户已建立独立推荐档案。"}

    @app.get("/api/plex/recipients/shared")
    def list_shared_recipients(owner_profile_id: str = "default"):
        return {"items": recipients.list_shared_users(owner_profile_id)}

    @app.post("/api/plex/recipients/shared/import")
    async def import_shared_recipient(request: Request):
        data = await body(request)
        ensure_idle()
        with operation():
            profile = recipients.import_shared_user(
                data.get("owner_profile_id", "default"), data.get("user_id"), data.get("library_id")
            )
        return {"profile": profile, "message": "共享用户已建立独立推荐档案。"}
