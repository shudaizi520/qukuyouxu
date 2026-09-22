"""Per user-library automation switches have one persisted source of truth."""
from helper.profile_controls import migrate_controls, read_controls, write_control
from helper.profile_runtime import ProfileRuntime
from helper.profiles import ProfileRegistry
from helper.scoped_store import ScopedStore
from helper.store import Store
from datetime import datetime, timedelta, timezone
import threading
import asyncio
from fastapi import FastAPI, Request
from helper.profile_web import attach_profile_routes


def _profile(registry, profile_id, library):
    return registry.create(
        name=profile_id, kind="owner", profile_id=profile_id,
        account={"id": "owner"}, server={"machine": "server-a"},
        library={"id": library, "name": profile_id}, token="owner-token",
    )


def test_new_libraries_start_with_independent_enabled_controls(tmp_path):
    base = Store(tmp_path)
    registry = ProfileRegistry(base)
    _profile(registry, "music", "11")
    _profile(registry, "classic", "15")
    runtime = ProfileRuntime(base, registry)
    music = runtime.engine("music").store
    classic = runtime.engine("classic").store

    assert read_controls(music) == {"learning": True, "daily": True, "smart": True}
    assert read_controls(classic) == {"learning": True, "daily": True, "smart": True}
    write_control(music, "smart", False)
    write_control(classic, "daily", False)
    assert read_controls(music) == {"learning": True, "daily": True, "smart": False}
    assert read_controls(classic) == {"learning": True, "daily": False, "smart": True}


def test_legacy_switches_migrate_effective_state_only_once(tmp_path):
    base = Store(tmp_path)
    registry = ProfileRegistry(base)
    _profile(registry, "new", "15")
    old = ScopedStore(base, "default", registry=registry)
    old.set("daily_settings", {"enabled": True, "hour": 8})
    old.set("smart_mix_settings", {"auto_enabled": True, "weekly_auto_enabled": True})
    base.set("automation_settings_v1", {
        "version": 1, "revision": 4,
        "daily": {"enabled": False, "hour": 8},
        "smart": {"enabled": True, "interval_days": 7, "hour": 3},
        "library": {"enabled": False, "hour": 0},
    })
    runtime = ProfileRuntime(base, registry)

    migrate_controls(runtime)
    assert read_controls(old) == {"learning": True, "daily": False, "smart": True}
    assert read_controls(runtime.engine("new").store) == {
        "learning": True, "daily": True, "smart": True,
    }
    write_control(old, "daily", True)
    migrate_controls(runtime)
    assert read_controls(old)["daily"] is True


def test_invalid_control_update_does_not_change_saved_state(tmp_path):
    base = Store(tmp_path)
    registry = ProfileRegistry(base)
    _profile(registry, "music", "11")
    scoped = ScopedStore(base, "music", registry=registry)
    before = read_controls(scoped)
    for key, value in (("library", False), ("daily", 0)):
        try:
            write_control(scoped, key, value)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid control was accepted")
    assert read_controls(scoped) == before


def test_reenable_does_not_bypass_daily_safety_pause(tmp_path):
    import pytest

    base = Store(tmp_path)
    registry = ProfileRegistry(base)
    _profile(registry, "music", "11")
    store = ScopedStore(base, "music", registry=registry)
    write_control(store, "daily", False)
    store.set("daily_auto_suspension", {"reason": "歌单需要核对"})
    with pytest.raises(ValueError, match="歌单需要核对"):
        write_control(store, "daily", True)
    assert read_controls(store)["daily"] is False
    store.set("daily_auto_suspension", None)
    store.set("daily_auto_opt_out", True)
    with pytest.raises(ValueError, match="先手动预览"):
        write_control(store, "daily", True)
    assert read_controls(store)["daily"] is False


def test_safety_pause_is_visible_even_when_daily_control_is_off(tmp_path):
    base = Store(tmp_path)
    registry = ProfileRegistry(base)
    _profile(registry, "music", "11")
    scoped = ScopedStore(base, "music", registry=registry)
    write_control(scoped, "daily", False)
    scoped.set("daily_auto_suspension", {"reason": "歌单需要核对"})
    app = FastAPI()

    async def body(request):
        return request.state.data

    attach_profile_routes(app, base, registry, body, lambda: None)
    routes = {route.path: route.endpoint for route in app.routes if hasattr(route, "path")}
    result = asyncio.run(routes["/api/plex/profiles"]())
    music = next(row for row in result["items"] if row["id"] == "music")
    assert music["controls"]["daily"] is False
    assert music["daily_status"] == {"status": "needs_attention", "reason": "歌单需要核对"}


def test_disabled_smart_switch_blocks_managed_retry(tmp_path):
    base = Store(tmp_path)
    registry = ProfileRegistry(base)
    _profile(registry, "music", "11")
    runtime = ProfileRuntime(base, registry)
    engine = runtime.engine("music")
    engine.store.set("smart_mix_managed", {"weekly": {"id": "123"}})
    write_control(engine.store, "smart", False)

    assert runtime._eligible_for_task(engine, "smart") is False


def test_old_global_off_does_not_block_new_profiles_daily_schedule(tmp_path):
    class Engine:
        def __init__(self, store):
            self.store = store
            self.job = {"running": False}
            self.status_lock = threading.Lock()

        def daily_auto(self, **_kwargs):
            calls.append(self.store.profile_id)
            return {"status": "published"}

    base = Store(tmp_path)
    registry = ProfileRegistry(base)
    _profile(registry, "music", "11")
    calls = []
    runtime = ProfileRuntime(base, registry, engine_factory=Engine)
    scoped = runtime.engine("music").store
    scoped.set("settings", {
        "plex_url": "http://plex:32400", "plex_token": "owner-token", "section": "11",
    })
    base.set("automation_settings_v1", {
        "version": 1, "revision": 4,
        "daily": {"enabled": False, "hour": 6},
        "smart": {"enabled": False, "interval_days": 7, "hour": 3},
        "library": {"enabled": False, "hour": 0},
    })
    now = datetime(2027, 1, 10, 7, tzinfo=timezone(timedelta(hours=8))).timestamp()

    runtime.run_due(now)

    assert calls == ["music"]


def test_profile_control_route_updates_only_the_selected_library(tmp_path):
    base = Store(tmp_path)
    registry = ProfileRegistry(base)
    _profile(registry, "music", "11")
    _profile(registry, "classic", "15")
    app = FastAPI()

    async def body(request):
        return request.state.data

    attach_profile_routes(app, base, registry, body, lambda: None)
    routes = {route.path: route.endpoint for route in app.routes if hasattr(route, "path")}
    request = Request({"type": "http", "method": "POST", "path": "/api/plex/profiles/control", "headers": []})
    request.state.data = {"profile_id": "classic", "key": "smart", "enabled": False}

    result = asyncio.run(routes["/api/plex/profiles/control"](request))

    assert result["controls"]["smart"] is False
    assert read_controls(ScopedStore(base, "music"))["smart"] is True


def test_first_control_write_is_not_overwritten_by_late_legacy_migration(tmp_path):
    from helper.automation import automation_settings

    base = Store(tmp_path)
    registry = ProfileRegistry(base)
    base.set("automation_settings_v1", {
        "version": 1, "revision": 4,
        "daily": {"enabled": False, "hour": 6},
        "smart": {"enabled": False, "interval_days": 7, "hour": 3},
        "library": {"enabled": False, "hour": 0},
    })
    app = FastAPI()

    async def body(request):
        return request.state.data

    attach_profile_routes(app, base, registry, body, lambda: None)
    routes = {route.path: route.endpoint for route in app.routes if hasattr(route, "path")}
    request = Request({"type": "http", "method": "POST", "path": "/api/plex/profiles/control", "headers": []})
    request.state.data = {"profile_id": "default", "key": "daily", "enabled": True}
    asyncio.run(routes["/api/plex/profiles/control"](request))
    automation_settings(base, registry, ProfileRuntime(base, registry))

    assert read_controls(ScopedStore(base, "default"))["daily"] is True
