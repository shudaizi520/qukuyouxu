"""First publication belongs to the just-added user/library, never a batch."""
from helper.profile_onboarding import prepare_new_profile, queue_new_profile
from helper.profile_runtime import ProfileRuntime
from helper.profiles import ProfileRegistry
from helper.store import Store
from helper.profile_web import attach_profile_routes
from fastapi import FastAPI, Request
import asyncio
from contextlib import contextmanager


class _Engine:
    def __init__(self, store, calls, data_available=True):
        self.store = store
        self.calls = calls
        self.data_available = data_available
        self.job = {"running": False}

    def preview_daily(self):
        self.calls.append((self.store.profile_id, "daily-preview"))
        return {"id": "daily-plan", "items": [{"id": "1"}] if self.data_available else [], "blocked": []}

    def publish_daily(self, _plan_id):
        self.calls.append((self.store.profile_id, "daily-publish"))
        self.store.set("daily_managed", {"id": "daily-1"})
        return {"playlist_id": "daily-1"}


def _registry(tmp_path):
    base = Store(tmp_path)
    registry = ProfileRegistry(base)
    for name in ("old-owner", "old-friend", "new-user"):
        registry.create(
            name=name, kind="shared", profile_id=name,
            account={"id": name}, server={"machine": "server-a"},
            library={"id": "11", "name": "音乐"}, token="shared-token",
        )
    return base, registry


def test_queue_and_prepare_touch_only_new_profile(tmp_path, monkeypatch):
    import helper.smart_mix_web as smart

    base, registry = _registry(tmp_path)
    calls = []
    runtime = ProfileRuntime(base, registry, engine_factory=lambda store: _Engine(store, calls))

    def preview(engine, kind, _options):
        calls.append((engine.store.profile_id, kind + "-preview"))
        return {"id": kind + "-plan", "items": [{"id": "1"}], "blocked": []}

    def publish(engine, plan_id):
        kind = plan_id.removesuffix("-plan")
        calls.append((engine.store.profile_id, kind + "-publish"))
        managed = dict(engine.store.get("smart_mix_managed", {}) or {})
        managed[kind] = {"id": kind + "-1"}
        engine.store.set("smart_mix_managed", managed)

    monkeypatch.setattr(smart, "preview_smart_mix", preview)
    monkeypatch.setattr(smart, "publish_smart_mix", publish)
    queue_new_profile(runtime, "new-user")
    result = prepare_new_profile(runtime, "new-user")
    assert result["status"] == "done"
    assert set(result["completed"]) == {"daily", "weekly", "time_capsule", "recent_additions"}
    assert {profile_id for profile_id, _action in calls} == {"new-user"}
    assert runtime.engine("old-owner").store.get("profile_prepare_v1") is None
    assert runtime.engine("old-friend").store.get("profile_prepare_v1") is None


def test_waiting_for_data_is_retryable_without_empty_publication(tmp_path, monkeypatch):
    import helper.smart_mix_web as smart

    base, registry = _registry(tmp_path)
    calls = []
    runtime = ProfileRuntime(base, registry, engine_factory=lambda store: _Engine(store, calls, data_available=False))
    monkeypatch.setattr(smart, "preview_smart_mix", lambda _engine, kind, _options: {
        "id": kind + "-plan", "items": [], "blocked": ["没有数据"],
    })
    monkeypatch.setattr(smart, "publish_smart_mix", lambda *_args: calls.append(("published", "smart")))
    queue_new_profile(runtime, "new-user")
    first = prepare_new_profile(runtime, "new-user")
    assert first["status"] == "waiting_for_data"
    assert first["completed"] == []
    assert not any(action.endswith("publish") for _profile_id, action in calls)
    assert not runtime.engine("new-user").store.get("daily_managed")


def test_restart_resumes_only_unfinished_kinds(tmp_path, monkeypatch):
    import helper.smart_mix_web as smart

    base, registry = _registry(tmp_path)
    calls = []
    first = ProfileRuntime(base, registry, engine_factory=lambda store: _Engine(store, calls))
    queue_new_profile(first, "new-user")
    first.engine("new-user").store.set("daily_managed", {"id": "already-daily"})
    first.engine("new-user").store.set("profile_prepare_v1", {
        "status": "running", "completed": ["daily"], "errors": {},
    })

    def preview(_engine, kind, _options):
        return {"id": kind + "-plan", "items": [{"id": "1"}], "blocked": []}

    def publish(engine, plan_id):
        kind = plan_id.removesuffix("-plan")
        calls.append((engine.store.profile_id, kind))
        managed = dict(engine.store.get("smart_mix_managed", {}) or {})
        managed[kind] = {"id": kind + "-1"}
        engine.store.set("smart_mix_managed", managed)

    monkeypatch.setattr(smart, "preview_smart_mix", preview)
    monkeypatch.setattr(smart, "publish_smart_mix", publish)
    restarted = ProfileRuntime(Store(tmp_path), ProfileRegistry(Store(tmp_path)),
                               engine_factory=lambda store: _Engine(store, calls))
    result = prepare_new_profile(restarted, "new-user")
    assert result["status"] == "done"
    assert ("new-user", "daily-publish") not in calls
    assert {kind for _profile_id, kind in calls} == {"weekly", "time_capsule", "recent_additions"}


def test_import_route_queues_only_the_new_identity(tmp_path, monkeypatch):
    from helper.plex_recipients import PlexRecipientService
    import helper.profile_onboarding as onboarding

    base = Store(tmp_path)
    registry = ProfileRegistry(base)
    runtime = ProfileRuntime(base, registry, engine_factory=lambda store: _Engine(store, []))
    app = FastAPI()
    app.state.profile_runtime = runtime

    async def body(request):
        return request.state.data

    def import_user(_self, _owner, _user, _library):
        existing = registry.find_identity("shared", "new-user", "server-a", "11", enabled_only=True)
        return existing or registry.create(
            name="new-user", kind="shared", profile_id="new-user", token="shared-token",
            account={"id": "new-user"}, server={"machine": "server-a"},
            library={"id": "11", "name": "音乐"},
        )

    monkeypatch.setattr(PlexRecipientService, "import_shared_user", import_user)
    locked = [False]
    real_queue = onboarding.queue_new_profile

    def checked_queue(*args):
        assert locked[0] is False, "onboarding must start after the route releases the Plex gate"
        return real_queue(*args)

    class RouteEngine:
        @contextmanager
        def exclusive(self):
            locked[0] = True
            try:
                yield
            finally:
                locked[0] = False

    monkeypatch.setattr(onboarding, "queue_new_profile", checked_queue)
    attach_profile_routes(app, base, registry, body, lambda: None, engine=RouteEngine())
    routes = {route.path: route.endpoint for route in app.routes if hasattr(route, "path")}
    request = Request({"type": "http", "method": "POST", "path": "/api/plex/recipients/shared/import", "headers": []})
    request.state.data = {"owner_profile_id": "default", "user_id": "new-user", "library_id": "11"}

    asyncio.run(routes["/api/plex/recipients/shared/import"](request))
    assert runtime.engine("new-user").store.get("profile_prepare_v1")["status"] == "pending"
    assert runtime.wake.is_set()
    runtime.engine("new-user").store.set("profile_prepare_v1", {"status": "done", "completed": [], "errors": {}})
    asyncio.run(routes["/api/plex/recipients/shared/import"](request))
    assert runtime.engine("new-user").store.get("profile_prepare_v1")["status"] == "done"


def test_failed_preparation_is_visible_and_can_be_retried_for_one_profile(tmp_path):
    base, registry = _registry(tmp_path)
    runtime = ProfileRuntime(base, registry, engine_factory=lambda store: _Engine(store, []))
    app = FastAPI()
    app.state.profile_runtime = runtime

    async def body(request):
        return request.state.data

    attach_profile_routes(app, base, registry, body, lambda: None)
    routes = {route.path: route.endpoint for route in app.routes if hasattr(route, "path")}
    runtime.engine("new-user").store.set("profile_prepare_v1", {
        "status": "needs_attention", "completed": ["daily"],
        "errors": {"weekly": "Plex 暂时离线"}, "next_retry_at": 9999999999,
    })

    listed = asyncio.run(routes["/api/plex/profiles"]())
    new_user = next(row for row in listed["items"] if row["id"] == "new-user")
    assert new_user["preparation"]["status"] == "needs_attention"
    assert new_user["preparation"]["errors"] == {"weekly": "Plex 暂时离线"}

    request = Request({"type": "http", "method": "POST",
                       "path": "/api/plex/profiles/prepare/retry", "headers": []})
    request.state.data = {"profile_id": "new-user"}
    result = asyncio.run(routes["/api/plex/profiles/prepare/retry"](request))
    assert result["preparation"]["status"] == "pending"
    assert result["preparation"]["next_retry_at"] == 0
    assert result["preparation"]["completed"] == ["daily"]
    assert runtime.wake.is_set()


def test_creating_independent_owner_does_not_switch_active_profile(tmp_path):
    base = Store(tmp_path)
    registry = ProfileRegistry(base)
    app = FastAPI()

    async def body(request):
        return request.state.data

    attach_profile_routes(app, base, registry, body, lambda: None)
    routes = {route.path: route.endpoint for route in app.routes if hasattr(route, "path")}
    request = Request({"type": "http", "method": "POST",
                       "path": "/api/plex/profiles/create", "headers": []})
    request.state.data = {"name": "第二个 Plex 账户"}

    result = asyncio.run(routes["/api/plex/profiles/create"](request))
    assert result["profile"]["id"] != "default"
    assert registry.active_id() == "default"


def test_remove_waits_for_new_user_publication_gate(tmp_path):
    import pytest

    base, registry = _registry(tmp_path)
    runtime = ProfileRuntime(base, registry, engine_factory=lambda store: _Engine(store, []))
    app = FastAPI()
    app.state.profile_runtime = runtime

    async def body(request):
        return request.state.data

    attach_profile_routes(app, base, registry, body, lambda: None)
    routes = {route.path: route.endpoint for route in app.routes if hasattr(route, "path")}
    request = Request({"type": "http", "method": "POST",
                       "path": "/api/plex/profiles/remove", "headers": []})
    request.state.data = {"profile_id": "new-user", "confirm": True}

    assert runtime.job_gate.acquire(blocking=False)
    try:
        with pytest.raises(ValueError, match="正在生成或更新"):
            asyncio.run(routes["/api/plex/profiles/remove"](request))
        assert registry.get("new-user")["enabled"] is True
        assert runtime.engine("new-user").store.get("profile_removal_v1") is None
    finally:
        runtime.job_gate.release()

    asyncio.run(routes["/api/plex/profiles/remove"](request))
    assert registry.get("new-user")["enabled"] is False


def test_onboarding_stops_if_profile_is_frozen_during_preview(tmp_path, monkeypatch):
    import helper.smart_mix_web as smart

    base, registry = _registry(tmp_path)
    calls = []
    runtime = ProfileRuntime(base, registry, engine_factory=lambda store: _Engine(store, calls))

    def preview(engine, kind, _options):
        if kind == "weekly":
            registry.archive("new-user")
        return {"id": kind + "-plan", "items": [{"id": "1"}], "blocked": []}

    monkeypatch.setattr(smart, "preview_smart_mix", preview)
    monkeypatch.setattr(smart, "publish_smart_mix", lambda *_args: calls.append(("new-user", "smart-publish")))
    queue_new_profile(runtime, "new-user")
    result = prepare_new_profile(runtime, "new-user")
    assert result["status"] != "done"
    assert ("new-user", "smart-publish") not in calls
