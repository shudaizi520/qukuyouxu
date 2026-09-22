# User-Library Lifecycle and Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make each Plex user+library a cleanly addable/removable profile, synchronize owner-managed category playlists to every same-library profile, and replace conflicting settings controls with a professional three-switch user table.

**Architecture:** Keep the existing `ProfileRegistry`, `ScopedStore`, `ProfileRuntime`, and Plex ownership markers. Add focused control, onboarding, and cleanup services; make owner category reconciliation durable and idempotent. Routes only validate/request work, while the services own state transitions. The UI reads one per-profile controls endpoint and shows normal work quietly.

**Tech Stack:** Python 3, FastAPI, SQLite-backed `Store`/`ScopedStore`, pytest, vanilla JavaScript and CSS.

**Spec:** `docs/superpowers/specs/2026-09-22-profile-lifecycle-library-sync-settings-design.md`

## Global Constraints

- Identity is `(Plex user type and ID, server machine identifier, library ID)`, never display name.
- New profiles default to playback learning, daily, and smart automation on. Existing profiles migrate their actually effective state without blanket enablement.
- Smart automation covers weekly, time capsule, and recent additions. Category organization has no recipient switch and only an owner may run the expensive scan.
- A disabled automation leaves existing playlists untouched. Removing a profile deletes only playlists verified as app-created for that exact profile, never native playlists or audio files.
- A deleted owner category must be removed from every connected same-library recipient; recreating a title must not leave two app copies. Other libraries and profiles must remain untouched.
- Settings body text targets 16px, secondary text stays at least 14px, and touch targets are at least 44px high. Do not reintroduce repeated headings, batch daily tools, or duplicate global enable switches.
- No real Plex mutation, production-data migration, push, version release, or NAS deployment in implementation tests. Use isolated stores and fake Plex clients; release remains a separate verified step.

## Review Focus

1. A stale Plex list after remote delete must not create a second same-title app playlist. Task 3 tests direct-ID confirmation before recreate.
2. A native Plex playlist with the same title must neither be overwritten nor deleted. Tasks 3 and 4 test it.
3. A user-edited app-created copy with its verified ownership marker must still be removable, while a copy with a missing marker must pause safely. Tasks 3 and 4 test both.
4. A process restart during profile onboarding or removal must resume the same job, not publish duplicates or report a false success. Tasks 2 and 4 test restart.
5. The same account on two music libraries must retain separate controls and playlist IDs; owner category fan-out reaches only matching server+library. Tasks 1, 3, and 6 test the two-library matrix.

## File Map

- Create `src/helper/profile_controls.py`: normalized per-profile switch reads/writes and one-time effective-state migration.
- Create `src/helper/profile_onboarding.py`: durable, per-profile first-publish job and compact status.
- Create `src/helper/profile_cleanup.py`: verified inventory and resumable two-phase profile removal.
- Modify `src/helper/profiles.py`, `src/helper/profile_web.py`, `src/helper/profile_runtime.py`, `src/helper/automation.py`, `src/helper/smart_mix_web.py`: lifecycle integration, routes, and scheduler gates.
- Modify `src/helper/library_sharing.py` and category mutation entrypoints in `src/helper/daily_mix_v036.py`/`src/helper/playlist_hub.py`: owner/recipient linkage, deletion intent, immediate reconciliation and retry.
- Modify `src/helper/static/settings.html`, `settings.js`, `product.css`, and any business-page auto checkbox still writing an independent state: compact settings layout, single switch source, responsive styling.
- Add `tests/test_profile_controls_v2.py`, `test_profile_onboarding_v2.py`, `test_profile_cleanup_v2.py`, `test_settings_v2.py`; extend `test_v149_library_sharing.py` and update legacy tests whose opt-out/archive/global-switch assumptions this spec supersedes.

---

### Task 1: Per-Profile Controls and Scheduler Gates

**Files:** Create `src/helper/profile_controls.py`, `tests/test_profile_controls_v2.py`; modify `src/helper/profiles.py`, `src/helper/profile_runtime.py`, `src/helper/automation.py`, `src/helper/profile_web.py`, `src/helper/smart_mix_web.py`.

**Interfaces:** `read_controls(store) -> dict[str,bool]`; `write_control(store, key: str, enabled: bool) -> dict[str,bool]`; `migrate_controls(runtime) -> None`. Later tasks call `read_controls` and `write_control`, not legacy global `enabled` fields.

- [ ] **Step 1: Write failing tests.** Use isolated `Store(Path(tmp_path))`, two profiles with library IDs `11` and `15`, and assert default controls on only for newly created profiles, independent edits, and old effective-state migration.

```python
def test_two_libraries_keep_independent_switches(tmp_path):
    from helper.profile_controls import read_controls, write_control
    from helper.profile_runtime import ProfileRuntime
    from helper.profiles import ProfileRegistry
    from helper.store import Store
    base = Store(tmp_path)
    registry = ProfileRegistry(base)
    for name, library in (("music", "11"), ("classic", "15")):
        registry.create(name=name, kind="owner", profile_id=name,
                        account={"id": "owner"}, server={"machine": "server-a"},
                        library={"id": library, "name": name}, token="owner-token")
    runtime = ProfileRuntime(base, registry)
    music = runtime.engine("music").store
    classic = runtime.engine("classic").store
    write_control(music, "smart", False)
    assert read_controls(music)["smart"] is False
    assert read_controls(classic)["smart"] is True
```

- [ ] **Step 2: Confirm red.** Run `pytest -q tests/test_profile_controls_v2.py`; expect the missing module/API and old global gate assertions to fail.
- [ ] **Step 3: Implement controls and migration.** Use the current scoped keys as the only persisted state (`product_settings.behavior_enabled`, `daily_settings.enabled`, `smart_mix_settings.auto_enabled`), with `weekly_auto_enabled` written only as a compatibility alias. Mark migrated old profiles once; calculate old effective daily/smart enablement from the old global schedule plus their old profile state before retiring the global gate. Set all three true in fresh-profile initialization, not during old-profile migration.

```python
CONTROL_KEYS = {"learning", "daily", "smart"}

def read_controls(store):
    return {
        "learning": (store.get("product_settings", {}) or {}).get("behavior_enabled", True) is not False,
        "daily": bool((store.get("daily_settings", {}) or {}).get("enabled")),
        "smart": bool((store.get("smart_mix_settings", {}) or {}).get("auto_enabled")),
    }

def write_control(store, key, enabled):
    if key not in CONTROL_KEYS or type(enabled) is not bool:
        raise ValueError("用户开关无效")
    field, name = {
        "learning": ("product_settings", "behavior_enabled"),
        "daily": ("daily_settings", "enabled"),
        "smart": ("smart_mix_settings", "auto_enabled"),
    }[key]
    value = dict(store.get(field, {}) or {})
    value[name] = enabled
    if key == "smart":
        value["weekly_auto_enabled"] = enabled
    store.set(field, value)
    return read_controls(store)
```

- [ ] **Step 4: Route and schedule.** Expose a scoped controls read/write route that verifies an enabled profile ID; make daily/smart scheduler eligibility use `read_controls`, not the global enable fields. Keep hour/interval configuration but remove global daily/smart enablement as a second gate. Keep the library scan owner-only. Remove any `due_kinds` path that bypasses the scoped smart switch.
- [ ] **Step 5: Green and commit.** Run `pytest -q tests/test_profile_controls_v2.py tests/test_v114_global_automation.py tests/test_v0424_smart_mix_auto.py tests/test_v108_profile_identity.py`; adjust superseded expectations, then `git add` these files and `git commit -m "feat: scope automation controls to user libraries"`.

### Task 2: New-Profile First Publish Without Batch Work

**Files:** Create `src/helper/profile_onboarding.py`, `tests/test_profile_onboarding_v2.py`; modify `src/helper/profile_web.py`, `src/helper/profile_runtime.py`, `src/helper/smart_mix_web.py`.

**Interfaces:** `queue_new_profile(runtime, profile_id: str) -> dict`; `prepare_new_profile(runtime, profile_id: str) -> dict`. `profile_web` queues only after a genuinely new identity is persisted; a repeated add returns the existing profile without queueing unrelated users.

- [ ] **Step 1: Write failing tests.** A fake Plex client records calls by profile. Add the third profile and assert only its daily and three smart publishes run, plus existing owner-category copy; the two old profiles have zero calls. Add data-insufficient and restart/resume cases.

```python
def test_new_profile_queue_does_not_change_existing_users(tmp_path):
    from helper.profile_onboarding import queue_new_profile
    from helper.profile_runtime import ProfileRuntime
    from helper.profiles import ProfileRegistry
    from helper.store import Store
    base = Store(tmp_path)
    registry = ProfileRegistry(base)
    for name in ("old-owner", "old-friend", "new-user"):
        registry.create(name=name, kind="shared", profile_id=name,
                        account={"id": name}, server={"machine": "server-a"},
                        library={"id": "11", "name": "音乐"}, token="shared-token")
    runtime = ProfileRuntime(base, registry)
    queue_new_profile(runtime, "new-user")
    assert runtime.engine("new-user").store.get("profile_prepare_v1")["status"] == "pending"
    assert runtime.engine("old-owner").store.get("profile_prepare_v1") is None
    assert runtime.engine("old-friend").store.get("profile_prepare_v1") is None
```

- [ ] **Step 2: Confirm red.** Run `pytest -q tests/test_profile_onboarding_v2.py`; expect missing service / no initial smart publication.
- [ ] **Step 3: Implement a durable per-profile state machine.** Persist `profile_prepare_v1` with `pending`, `running`, `done`, or `needs_attention` per playlist kind. Under the shared job/operation gates, call `engine.preview_daily()` then `engine.publish_daily(plan["id"])`; for each built-in smart kind call `preview_smart_mix(engine, kind, defaults[kind])` then `publish_smart_mix(engine, plan["id"])`. If preview has insufficient inputs, record `waiting_for_data` and do not publish an empty list. Re-read the app-owned record before each publish so a restart cannot create a duplicate.

```python
SMART_DEFAULTS = {
    "weekly": {"size": 30, "recent_days": 30},
    "time_capsule": {"size": 30, "stale_days": 180},
    "recent_additions": {"size": 30, "added_days": 90},
}

def queue_new_profile(runtime, profile_id):
    store = runtime.engine(profile_id).store
    if store.get("profile_prepare_v1"):
        return store.get("profile_prepare_v1")
    state = {"status": "pending", "completed": [], "errors": {}}
    store.set("profile_prepare_v1", state)
    return state

def prepare_new_profile(runtime, profile_id):
    from helper.smart_mix_web import preview_smart_mix, publish_smart_mix
    from helper.engine import safe_error
    engine = runtime.engine(profile_id)
    state = dict(engine.store.get("profile_prepare_v1") or queue_new_profile(runtime, profile_id))
    if engine.store.get("profile_removal_v1"):
        return state
    for kind in ("daily", *SMART_DEFAULTS):
        if kind in state["completed"]:
            continue
        try:
            plan = (engine.preview_daily() if kind == "daily" else
                    preview_smart_mix(engine, kind, SMART_DEFAULTS[kind]))
            if plan.get("blocked") or not plan.get("items"):
                state["errors"][kind] = "waiting_for_data" if not plan.get("items") else "needs_attention"
            else:
                (engine.publish_daily(plan["id"]) if kind == "daily" else
                 publish_smart_mix(engine, plan["id"]))
                state["completed"].append(kind)
                state["errors"].pop(kind, None)
        except Exception as exc:
            state["errors"][kind] = safe_error(exc)
        engine.store.set("profile_prepare_v1", state)
    state["status"] = ("done" if not state["errors"] else
                       "waiting_for_data" if set(state["errors"].values()) == {"waiting_for_data"}
                       else "needs_attention")
    engine.store.set("profile_prepare_v1", state)
    return state
```

- [ ] **Step 4: Wire the create/import routes and startup recovery.** After persistence, use FastAPI background work to prepare that profile only. On scheduler startup, discover pending preparation states and resume them through the same serial gate. Call same-library `sync_recipient` only if a verified owner has published category records; never call category scan/`refresh_new_tracks` during add. Return one compact per-profile status endpoint for the settings row.
- [ ] **Step 5: Green and commit.** Run `pytest -q tests/test_profile_onboarding_v2.py tests/test_new_user_onboarding.py tests/test_v047_smart_mix_web.py`; commit as `feat: prepare only newly added Plex profile`.

### Task 3: Durable Same-Library Category Fan-Out

**Files:** Modify `src/helper/library_sharing.py`, `src/helper/profile_runtime.py`, category publish/delete paths in `src/helper/daily_mix_v036.py` and `src/helper/playlist_hub.py`; extend `tests/test_v149_library_sharing.py` with the new no-opt-out and revision cases.

**Interfaces:** `queue_owner_revision(runtime, owner_id: str, category_id: str, action: str, source_record: dict) -> dict`; `confirm_owner_revision(runtime, owner_id: str, category_id: str) -> dict`; `reconcile_owner_revision(runtime, owner_id: str) -> dict`; `same_server_library(owner: dict, recipient: dict) -> bool`. The existing `sync_recipient(runtime, owner_id, recipient_id)` remains the per-target primitive, now reading durable source revisions/tombstones.

- [ ] **Step 1: Write failing tests.** Cover create/update/delete/recreate with identical title, same account in two libraries, recipient-edited app copy with marker, missing marker, native same-title collision, and delayed Plex listings. Assert no cross-library calls and no duplicate create after a delete that cannot be confirmed.

```python
def test_same_title_rebuild_waits_for_old_copy_removal(shared_library):
    from helper.engine import fingerprint
    from helper.library_sharing import confirm_owner_revision, queue_owner_revision, sync_recipient
    from helper.scoped_store import ScopedStore
    base, registry, runtime, data = shared_library
    sync_recipient(runtime, "default", "friend")
    old_id = next(iter(data["friend-token"]["playlists"]))
    owner = ScopedStore(base, "default")
    old_record = owner.get("managed")["qq:pop"]
    queue_owner_revision(runtime, "default", "qq:pop", "delete", old_record)
    owner.set("managed", {})
    confirm_owner_revision(runtime, "default", "qq:pop")
    sync_recipient(runtime, "default", "friend")
    replacement = FakePlex("owner-token", data).create(
        "流行精选", ["2"], runtime.engine("default").marker("qq:pop"))
    new_record = {"id": replacement["id"], "title": replacement["title"],
                  "fingerprint": fingerprint(replacement), "count": 1}
    owner.set("managed", {"qq:pop": new_record})
    queue_owner_revision(runtime, "default", "qq:pop", "publish", new_record)
    result = sync_recipient(runtime, "default", "friend")
    assert old_id not in data["friend-token"]["playlists"]
    assert len(data["friend-token"]["playlists"]) == 1
    assert result["errors"] == []
```

- [ ] **Step 2: Confirm red.** Run `pytest -q tests/test_v149_library_sharing.py`; expect old `excluded`/fingerprint or same-title logic to fail.
- [ ] **Step 3: Record owner revisions and delete intent.** Before an owner removal discards a managed record, persist a *prepared* tombstone containing category ID, source Plex ID, generation and target profile IDs. Only mark it confirmed after the owner Plex deletion is read back; a crash between these stages is recovered by checking that exact source ID, not by assuming deletion. A successful owner publish increments and confirms its generation after readback. Reconciliation processes confirmed revisions only, serializes each same-library target, verifies program marker and saved Plex ID, updates the existing copy, or deletes the old copy and confirms its ID absent before a same-title replacement. Do not treat a recipient-deleted app copy as a permanent opt-out; recreate only after direct-ID `PlexNotFound` confirmation. Preserve a missing-marker target for review.

```python
def same_server_library(owner, recipient):
    machine = str((owner.get("server") or {}).get("machine") or "")
    library = str((owner.get("library") or {}).get("id") or "")
    return bool(machine and library
                and machine == str((recipient.get("server") or {}).get("machine") or "")
                and library == str((recipient.get("library") or {}).get("id") or ""))

def queue_owner_revision(runtime, owner_id, category_id, action, source_record):
    if action not in {"publish", "delete"}:
        raise ValueError("分类同步动作无效")
    store = runtime.engine(owner_id).store
    revisions = dict(store.get("library_share_revisions_v2", {}) or {})
    previous = revisions.get(category_id, {})
    revisions[category_id] = {
        "generation": int(previous.get("generation") or 0) + 1,
        "action": action,
        "source_id": str(source_record["id"]),
        "confirmed": action == "publish",
        "targets": [row["id"] for row in runtime.registry.list_public(enabled_only=True)
                    if same_server_library(runtime.registry.get(owner_id), row)
                    and row["id"] != owner_id],
    }
    store.set("library_share_revisions_v2", revisions)
    return revisions[category_id]

def confirm_owner_revision(runtime, owner_id, category_id):
    store = runtime.engine(owner_id).store
    revisions = dict(store.get("library_share_revisions_v2", {}) or {})
    revision = dict(revisions[category_id])
    revision["confirmed"] = True
    revisions[category_id] = revision
    store.set("library_share_revisions_v2", revisions)
    return revision
```

- [ ] **Step 4: Trigger and recover.** On delete, queue prepared intent before remote write, confirm it only after verified delete; on publish, queue a confirmed revision only after verified write. Immediately attempt `reconcile_owner_revision` without holding a route-level lock across unbounded fan-out. Keep per-target completion state for retry on the existing scheduler. Ensure the owner resolver includes every enabled other profile on the same server+library, not only `home/shared` kind; no recipient switch or QQ credential is required.
- [ ] **Step 5: Green and commit.** Run `pytest -q tests/test_v149_library_sharing.py tests/test_v0415_library_maintenance.py`; commit as `feat: reconcile owner categories across same-library profiles`.

### Task 4: Verified, Resumable Removal

**Files:** Create `src/helper/profile_cleanup.py`, `tests/test_profile_cleanup_v2.py`; modify `src/helper/profiles.py`, `src/helper/profile_web.py`, `src/helper/playlist_hub.py`, `src/helper/external_store.py` only where necessary to enumerate saved app-owned IDs.

**Interfaces:** `begin_profile_removal(runtime, profile_id: str) -> dict`; `resume_profile_removal(runtime, profile_id: str) -> dict`; `owned_playlist_inventory(runtime, profile_id: str) -> list[dict]`; `verify_owned_playlist(engine, item: dict, current: dict) -> None`; `verify_favorite_owned_rule(engine, current: dict) -> None`; `forget_owned_record(runtime, profile_id: str, item: dict) -> None`. The route returns one profile-level removal status; a profile in `removing` state cannot be re-added as fresh.

- [ ] **Step 1: Write failing tests.** Inventory daily, smart, category, favorite, and external imported playlists for one profile; place a native same-title playlist alongside them. Assert all verified app IDs deleted, native untouched, other library untouched, and local scoped keys purged only after remote confirmation. Test network error, missing marker, already-absent ID and restart/resume.

```python
def test_failed_delete_keeps_profile_for_retry(tmp_path):
    from helper.engine import fingerprint
    from helper.profile_cleanup import begin_profile_removal, resume_profile_removal
    from helper.profile_runtime import ProfileRuntime
    from helper.profiles import ProfileRegistry
    from helper.store import Store
    from helper.clients import PlexNotFound
    class FakePlex:
        fail_delete_once = True
        def __init__(self):
            self.rows = {"101": {"id": "101", "title": "每日推荐",
                                 "summary": "", "items": []}}
        def identity(self):
            return {"machine": "server-a"}
        def playlist_state(self, playlist_id):
            if str(playlist_id) not in self.rows:
                raise PlexNotFound("missing")
            return dict(self.rows[str(playlist_id)])
        def delete_playlist(self, playlist_id):
            if self.fail_delete_once:
                self.fail_delete_once = False
                raise ConnectionError("temporary")
            self.rows.pop(str(playlist_id), None)
    base = Store(tmp_path)
    registry = ProfileRegistry(base)
    registry.create(name="朋友", kind="shared", profile_id="friend-music",
                    account={"id": "friend"}, server={"machine": "server-a"},
                    library={"id": "11", "name": "音乐"}, token="friend-token")
    runtime = ProfileRuntime(base, registry)
    plex = FakePlex()
    engine = runtime.engine("friend-music")
    plex.rows["101"]["summary"] = engine.marker("daily")
    engine.plex_factory = lambda _settings: plex
    engine.store.set("daily_managed", {"id": "101", "title": "每日推荐",
                                       "fingerprint": fingerprint(plex.rows["101"])})
    plex.fail_delete_once = True
    begin_profile_removal(runtime, "friend-music")
    first = resume_profile_removal(runtime, "friend-music")
    assert first["status"] == "needs_attention"
    assert runtime.registry.get("friend-music")["id"] == "friend-music"
    second = resume_profile_removal(runtime, "friend-music")
    assert second["status"] == "removed"
```

- [ ] **Step 2: Confirm red.** Run `pytest -q tests/test_profile_cleanup_v2.py`; expect archive-preserves-playlists behavior to fail.
- [ ] **Step 3: Implement two-phase cleanup.** Persist `profile_removal_v1` before any remote write, mark the profile unavailable to new work, then enumerate app-owned records. For each ID, verify server/library and marker or equivalent strong app provenance. A changed song list alone does not negate verified ownership. Delete and read back absence; if direct read says already absent, mark complete. If ownership or deletion is uncertain, retain a retryable state without clearing the registry. After every item is confirmed, call `ProfileRegistry.remove(profile_id)` to purge scoped state and relation rows.

```python
def begin_profile_removal(runtime, profile_id):
    runtime.registry.get(profile_id)
    store = runtime.engine(profile_id).store
    state = store.get("profile_removal_v1") or {
        "status": "pending", "completed_ids": [], "error": ""
    }
    store.set("profile_removal_v1", state)
    return state

def owned_playlist_inventory(runtime, profile_id):
    from helper.playlist_hub import assistant_playlist_rows
    store = runtime.engine(profile_id).store
    return [row for row in assistant_playlist_rows(store)
            if row.get("kind") in {"daily", "smart", "category", "favorite", "external"}
            and str(row.get("playlist_id") or "").isdigit()]

def verify_favorite_owned_rule(engine, current):
    from helper.favorite_smart import _favorite_title, _rule_kind
    saved = engine.store.get("favorite_smart_v2", {}) or {}
    cfg = engine.store.get("settings", {}) or {}
    plex = engine.plex_factory(cfg)
    info = plex.smart_playlist_info(str(current["id"]))
    machine = str(plex.identity().get("machine") or "")
    if (str(saved.get("playlist_id") or "") != str(current["id"])
            or str(saved.get("section") or "") != str(cfg.get("section") or "")
            or not _favorite_title(info.get("title"))
            or _rule_kind(info.get("content"), str(cfg.get("section") or ""), machine) != "current"):
        raise ValueError("无法确认我喜欢歌单归属")

def verify_owned_playlist(engine, item, current):
    from helper.external_playlist_sync import external_marker
    profile = engine.store.registry.get(engine.store.profile_id)
    cfg = engine.store.get("settings", {}) or {}
    plex = engine.plex_factory(cfg)
    if (str(plex.identity().get("machine") or "")
            != str((profile.get("server") or {}).get("machine") or "")
            or str(cfg.get("section") or "")
            != str((profile.get("library") or {}).get("id") or "")):
        raise ValueError("服务器或曲库身份不一致")
    if str(current.get("id")) != str(item["playlist_id"]):
        raise ValueError("歌单编号不一致")
    kind, key = item["kind"], str(item["key"])
    if kind == "favorite":
        verify_favorite_owned_rule(engine, current)
        return
    marker = (external_marker(engine.store.get("installation_id"), key)
              if kind == "external" else
              engine.marker({"daily": "daily", "smart": "smart:" + key}.get(kind, key)))
    if marker not in str(current.get("summary") or ""):
        raise ValueError("程序所有权标记缺失")

def forget_owned_record(runtime, profile_id, item):
    from helper.external_store import ExternalRepository
    store = runtime.engine(profile_id).store
    kind, key = item["kind"], str(item["key"])
    if kind == "daily":
        store.set("daily_managed", None)
    elif kind in {"smart", "category"}:
        state_key = "smart_mix_managed" if kind == "smart" else "managed"
        records = dict(store.get(state_key, {}) or {})
        records.pop(key, None)
        store.set(state_key, records)
    elif kind == "favorite":
        store.set("favorite_smart_v2", {})
    else:
        ExternalRepository(store).save_managed(profile_id, key, None)

def resume_profile_removal(runtime, profile_id):
    from helper.clients import PlexNotFound
    from helper.engine import safe_error
    engine = runtime.engine(profile_id)
    state = dict(engine.store.get("profile_removal_v1") or begin_profile_removal(runtime, profile_id))
    plex = engine.plex_factory(engine.store.get("settings"))
    for item in owned_playlist_inventory(runtime, profile_id):
        playlist_id = str(item["playlist_id"])
        if playlist_id in state["completed_ids"]:
            continue
        try:
            current = plex.playlist_state(playlist_id)
            verify_owned_playlist(engine, item, current)
            plex.delete_playlist(playlist_id)
            try:
                plex.playlist_state(playlist_id)
            except PlexNotFound:
                pass
            else:
                raise ValueError("Plex 尚未确认删除")
        except PlexNotFound:
            pass
        except Exception as exc:
            state.update(status="needs_attention", error=safe_error(exc))
            engine.store.set("profile_removal_v1", state)
            return state
        state["completed_ids"].append(playlist_id)
        engine.store.set("profile_removal_v1", state)
        forget_owned_record(runtime, profile_id, item)
    runtime.registry.remove(profile_id)
    return {"status": "removed", "profile_id": profile_id}
```

- [ ] **Step 4: Integrate routes and cleanup guards.** Replace `/api/plex/profiles/remove` archive behavior with begin/resume cleanup. Reject normal profile switching, background onboarding, scheduling and category fan-out for `removing` profiles. Reject fresh add of the same identity until cleanup reaches `removed`. Existing disabled/archived profiles with retained managed records are migrated to a visible `legacy_cleanup_required` state; never silently restore their old IDs or delete them without the user's explicit removal confirmation. The confirmation text must say app-created Plex playlists will be deleted. Do not repurpose Plex account disconnect as profile removal.
- [ ] **Step 5: Green and commit.** Run `pytest -q tests/test_profile_cleanup_v2.py tests/test_v040_profiles.py tests/test_v040_profile_routes.py tests/test_v130_external_service.py tests/test_v150_favorite_ensure.py`; update explicit old archive assertions and commit as `feat: remove profile with verified app playlist cleanup`.

### Task 5: Professional Settings Page and One Control Surface

**Files:** Modify `src/helper/static/settings.html`, `settings.js`, `product.css` and business-page checkboxes that still write an independent automation value; create `tests/test_settings_v2.py`; update `tests/test_v147_settings.py`, `test_v114_automation_ui.py`, `test_v047_batch_daily.py` as needed.

**Interfaces:** The page consumes `/api/plex/profiles`, the new scoped controls/status routes, and the schedule-only `/api/automation` fields. No page may render a second independently persisted daily/smart switch.

- [ ] **Step 1: Write failing static/UI tests.** Assert the page contains `Plex 连接`, `用户管理`, three unique switch headers, `复制地址`, `打开 Webhook`; assert no `批量每日推荐`, `每日推荐（全部账户）`, redundant `Plex 用户` switcher, or global daily/smart enable inputs. DOM tests cover toggle rollback after failed request and per-row status.

```python
def test_settings_has_one_user_control_surface():
    from pathlib import Path
    html = Path("src/helper/static/settings.html").read_text()
    assert "用户管理" in html
    assert "批量每日推荐" not in html
    assert 'id="plexProfile"' not in html
    assert 'id="dailyAutomationEnabled"' not in html
    assert 'id="smartAutomationEnabled"' not in html
    assert "打开 Webhook" in html
```

- [ ] **Step 2: Confirm red.** Run `pytest -q tests/test_settings_v2.py`; expect current duplicate controls and batch UI to fail.
- [ ] **Step 3: Replace the markup and render path.** Keep a compact connection card, the first-connection owner+library chooser in its setup flow, a user+library add control for later additions, one table-header row, aligned toggles per profile, and a reserved action cell. Load per-profile controls once; each toggle posts `{profile_id, key, enabled}` and restores its prior value on failure. Remove batch preview/publish JS and status polling; retain Webhook copy/open actions. Scheduling controls save hours/interval only, with owner-only library maintenance clearly labeled.

```javascript
function controlsCell(profile, key, checked) {
  const input = document.createElement('input');
  input.type = 'checkbox'; input.className = 'toggle'; input.checked = checked;
  input.setAttribute('aria-label', `${profileLabel(profile)} ${key}`);
  input.onchange = async () => {
    const next = input.checked; input.disabled = true;
    try { await post('/api/plex/profiles/control', {profile_id:profile.id, key, enabled:next}); }
    catch (error) { input.checked = !next; note(error.message, true); }
    finally { input.disabled = false; }
  };
  return input;
}
```

- [ ] **Step 4: Apply the responsive visual contract.** Use one grid template for header and rows, and card layout below the narrow-screen breakpoint. Enforce body/secondary/touch-target sizes; eliminate duplicate labels and normal-operation toasts. Keep a single short error badge per user with details on demand. Review 1280px and 375px captures, long Chinese names, keyboard tab order, and focus visibility.

```css
.settings-user-header,.settings-user-row{display:grid;grid-template-columns:minmax(16rem,1fr) repeat(3,8.5rem) 5.5rem;align-items:center;gap:1rem}
.settings-user-row{min-height:4.5rem}
.settings-user-row button,.settings-user-row input{min-height:44px}
.settings-secondary{font-size:14px}
@media(max-width:760px){.settings-user-header{display:none}.settings-user-row{grid-template-columns:1fr 1fr}.settings-person{grid-column:1/-1}}
```

- [ ] **Step 5: Green and commit.** Run `pytest -q tests/test_settings_v2.py tests/test_v147_settings.py tests/test_v114_automation_ui.py tests/test_v1417_embedded_settings.py`; visually inspect both widths and commit as `feat: simplify and align profile settings`.

### Task 6: Cross-Feature Verification and Handoff

**Files:** Extend `tests/test_v149_library_sharing.py` and `tests/test_profile_cleanup_v2.py` with cross-feature cases; modify only test expectations/documentation necessary to reflect the approved design. Do not change product code as an unreviewed last-minute add-on.

**Interfaces:** Use public API routes and the existing `FakePlex`/`shared_library` test fixture to exercise the whole lifecycle, including restart by reconstructing `Store`/`ProfileRuntime` against the same temporary database.

- [ ] **Step 1: Write integration tests.** Cover owner music + owner classic + two recipients, new profile default controls and first publish, owner category delete/rebuild, independent second-library removal, native same-title survival, partial cleanup recovery, and scheduling after toggles off/on.

```python
def test_owner_deletion_keeps_native_same_title(shared_library):
    from helper.library_sharing import confirm_owner_revision, queue_owner_revision, sync_recipient
    from helper.scoped_store import ScopedStore
    base, registry, runtime, data = shared_library
    sync_recipient(runtime, "default", "friend")
    native = FakePlex("friend-token", data).create("流行精选", ["3"], "")
    owner = ScopedStore(base, "default")
    record = owner.get("managed")["qq:pop"]
    queue_owner_revision(runtime, "default", "qq:pop", "delete", record)
    owner.set("managed", {})
    confirm_owner_revision(runtime, "default", "qq:pop")
    sync_recipient(runtime, "default", "friend")
    assert native["id"] in data["friend-token"]["playlists"]
    assert len(data["friend-token"]["playlists"]) == 1
```

- [ ] **Step 2: Confirm red/green for the combined journey.** Run `pytest -q tests/test_v149_library_sharing.py tests/test_profile_cleanup_v2.py tests/test_profile_onboarding_v2.py`; fix only failures traceable to Tasks 1–5, re-run each owning task's tests, and commit the integration tests.
- [ ] **Step 3: Run repository verification.** Run `pytest -q`, `python -m compileall -q src/helper`, `git diff --check`, repository privacy/healthcheck tests, and inspect the changed settings page at desktop and narrow widths. Review all failures; do not claim success from a partial run.
- [ ] **Step 4: Prepare release evidence, not deployment.** Record test results, migration dry-run results on a copied database, known unresolved cases, screenshots, and a verified rollback note. No version bump, image push, real Plex write or NAS deploy until an explicit release decision follows this implementation review.

## Execution Gate

After the user reviews this plan and chooses execution method, implement tasks in order with TDD, focused commits, and review gates. The written spec and this plan authorize local implementation only; real Plex mutation or deployment requires separate verification and release approval.
