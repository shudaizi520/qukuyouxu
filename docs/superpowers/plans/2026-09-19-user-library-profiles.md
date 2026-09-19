# Plex User-Library Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow each Plex user to own independent profiles for each accessible music library, keep browser tabs pinned to the intended profile, and keep the last published daily playlist visible when a draft is invalidated.

**Architecture:** Preserve existing profile IDs and scoped state, then add a canonical `(kind, account, server, library)` identity and library-aware creation service. Pin authenticated requests with a per-tab profile header, and split the daily draft from a durable published view so UI state cannot disappear when settings invalidate a draft.

**Tech Stack:** Python 3.11+, FastAPI, SQLite JSON state store, browser JavaScript, `unittest` test suite.

**Spec:** `docs/superpowers/specs/2026-09-19-user-library-profiles-design.md`

## Global Constraints

- Existing `default` and `shared-*` profile IDs and their scoped state remain unchanged.
- No old cache, history, feedback, playlist ID, or snapshot is copied into a newly added library profile.
- A protected profile never changes account, server, or library in place.
- Every target library must be verified through the target user's token on the saved Plex server.
- No GitHub push, image publication, NAS update, Plex mutation, or live database write occurs in this plan.
- Production changes follow strict RED-GREEN TDD and each task ends with a focused commit.

## Review Focus

- A stale per-tab profile ID after another tab archives that profile must recover to an enabled profile instead of breaking every page; Task 3 adds this test.
- Repeated requests to add the same user/library identity must return one profile and never duplicate state; Tasks 1 and 2 add this test.
- A shared user's library list must come from the shared token, not the owner's cached sections; Task 2 adds this test.
- Failure after selecting a different library must leave both the saved identity and visible select value unchanged; Task 4 adds this test.
- Invalidating a daily draft after a successful publish must preserve the published song list and managed safety record; Task 5 adds this test.

---

### Task 1: Canonical profile identity and atomic library-profile creation

**Files:**
- Modify: `src/helper/profiles.py`
- Create: `tests/test_v108_profile_identity.py`

**Interfaces:**
- Produces: `profile_identity(profile) -> tuple[str, str, str, str]`.
- Produces: `ProfileRegistry.find_identity(kind, account_id, machine, library_id, enabled_only=False) -> dict | None`.
- Produces: `ProfileRegistry.create_for_library(source_profile_id, library, profile_id=None) -> dict`.
- Produces: `ProfileRegistry.switch_unmanaged_library(profile_id, library) -> dict`.
- Consumes: existing `connection_is_protected(ScopedStore)` without changing its safety meaning.

- [ ] **Step 1: Write failing registry tests**

Create `tests/test_v108_profile_identity.py` with real `Store`, `ProfileRegistry`, and `ScopedStore` fixtures. The tests must assert these literal outcomes:

```python
def test_existing_profile_identity_is_found_without_renaming_legacy_id(self):
    registry.update("default", kind="owner", account={"id": "10"},
                    server={"machine": "m1", "url": "http://plex"},
                    library={"id": "11", "name": "音乐"}, token="owner-token")
    found = registry.find_identity("owner", "10", "m1", "11")
    self.assertEqual("default", found["id"])

def test_creating_second_library_copies_preferences_but_not_library_state(self):
    source.set_many({
        "daily_settings": {**source.get("daily_settings"), "size": 50, "hour": 7},
        "catalog": [{"id": "song-1"}], "cache": {"qq": {"data": [1]}},
        "feedback": {"tracks": {"song-1": {"value": "avoid"}}, "artists": {}},
        "daily_history": [{"plan_id": "old"}], "daily_managed": {"id": "playlist-1"},
    })
    created = registry.create_for_library("default", {"id": "15", "name": "经典音乐"})
    target = ScopedStore(base, created["id"])
    self.assertEqual(50, target.get("daily_settings")["size"])
    self.assertEqual(7, target.get("daily_settings")["hour"])
    self.assertEqual([], target.get("catalog", []))
    self.assertEqual({}, target.get("cache"))
    self.assertEqual({"tracks": {}, "artists": {}}, target.get("feedback"))
    self.assertEqual([], target.get("daily_history"))
    self.assertIsNone(target.get("daily_managed"))

def test_repeating_same_identity_returns_existing_profile(self):
    first = registry.create_for_library("default", {"id": "15", "name": "经典音乐"})
    second = registry.create_for_library("default", {"id": "15", "name": "经典音乐"})
    self.assertEqual(first["id"], second["id"])
    self.assertEqual(2, len(registry.list_public()))

def test_unmanaged_switch_is_atomic_and_clears_only_library_derived_state(self):
    source.set_many({"catalog": [{"id": "song-1"}], "daily_history": [{"plan_id": "old"}]})
    result = registry.switch_unmanaged_library("default", {"id": "15", "name": "经典音乐"})
    self.assertEqual("15", result["library"]["id"])
    self.assertEqual("15", source.get("settings")["section"])
    self.assertEqual([], source.get("catalog"))
    self.assertEqual([], source.get("daily_history"))

def test_protected_profile_cannot_switch_in_place(self):
    source.set("daily_managed", {"id": "playlist-1"})
    with self.assertRaisesRegex(ValueError, "已有托管歌单"):
        registry.switch_unmanaged_library("default", {"id": "15", "name": "经典音乐"})
```

- [ ] **Step 2: Run the new tests and verify RED**

Run: `PYTHONPATH=src python3 -m unittest tests.test_v108_profile_identity -v`

Expected: FAIL because `find_identity`, `create_for_library`, and `switch_unmanaged_library` do not exist.

- [ ] **Step 3: Implement canonical identity and atomic creation**

In `profiles.py`, add canonical text-only identity helpers and deterministic IDs based on SHA-256 of the four identity fields. Keep legacy IDs untouched when the identity already exists.

```python
def profile_identity(profile):
    return (
        _text(profile.get("kind"), 20),
        _text((profile.get("account") or {}).get("id"), 80),
        _text((profile.get("server") or {}).get("machine"), 160),
        _text((profile.get("library") or {}).get("id"), 40),
    )

def _library_profile_id(identity):
    payload = json.dumps(identity, ensure_ascii=True, separators=(",", ":"))
    return "p-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
```

Refactor `ProfileRegistry.create()` so initial scoped state and the revised registry are written through one `base_store.set_many()` transaction. Implement `find_identity()`, `create_for_library()`, and `switch_unmanaged_library()` using exact identity matching. `create_for_library()` copies only scalar policy preferences (`daily_settings`, `base_settings`, and non-ID automation timing choices) and uses `_initial_state()` for all library-derived state. `switch_unmanaged_library()` calls `connection_is_protected()` before updating registry, `settings.section`, and `plex_saved.library`; reset catalog, cache, metadata, plans, feedback, behavior, histories, snapshots, managed maps, smart-mix state, and library progress in the same transaction.

- [ ] **Step 4: Run focused and existing profile tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_v108_profile_identity tests.test_v040_profiles tests.test_v040_profile_routes tests.test_v040_switch_guards -v`

Expected: PASS with no warnings or leaked token text.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/helper/profiles.py tests/test_v108_profile_identity.py
git commit -m "feat: model Plex profiles by music library"
```

### Task 2: Discover libraries with the correct user's token and expose lifecycle APIs

**Files:**
- Modify: `src/helper/plex_recipients.py`
- Modify: `src/helper/profile_web.py`
- Modify: `src/helper/plex_state_v0316.py`
- Create: `tests/test_v108_profile_libraries.py`

**Interfaces:**
- Consumes: `ProfileRegistry.find_identity`, `create_for_library`, and `switch_unmanaged_library` from Task 1.
- Produces: `PlexRecipientService.list_profile_libraries(profile_id) -> list[dict]`.
- Produces: `PlexRecipientService.list_recipient_libraries(owner_profile_id, kind, user_id) -> dict` containing public account metadata and music libraries, never a token.
- Produces: `PlexRecipientService.select_profile_library(profile_id, library_id) -> dict` returning `{"profile": ..., "mode": "unchanged|switched|created"}`.
- Produces routes: `GET /api/plex/profiles/libraries`, `POST /api/plex/recipients/libraries`, and `POST /api/plex/profiles/library`.

- [ ] **Step 1: Write failing service and route tests**

Create complete fake Plex clients whose `identity()`, `sections()`, and `playlists()` mirror real response fields. Add tests for:

```python
def test_shared_profile_lists_music_libraries_using_its_own_token(self):
    rows = service.list_profile_libraries("shared-248098626")
    self.assertEqual(["11", "15"], [row["id"] for row in rows])
    self.assertEqual(["friend-token"], client_factory.tokens)

def test_non_music_sections_are_not_returned(self):
    rows = service.list_profile_libraries("default")
    self.assertEqual(["11", "15"], [row["id"] for row in rows])

def test_recipient_library_discovery_does_not_expose_token(self):
    result = service.list_recipient_libraries("default", "shared", "248098626")
    self.assertEqual("shudai6", result["account"]["username"])
    self.assertEqual(["11", "15"], [row["id"] for row in result["libraries"]])
    self.assertNotIn("token", repr(result).lower())

def test_protected_library_selection_creates_profile_and_preserves_source(self):
    ScopedStore(base, "default").set("daily_managed", {"id": "playlist-1"})
    result = service.select_profile_library("default", "15")
    self.assertEqual("created", result["mode"])
    self.assertEqual("11", registry.get("default")["library"]["id"])
    self.assertEqual("15", result["profile"]["library"]["id"])

def test_unmanaged_library_selection_switches_existing_profile(self):
    result = service.select_profile_library("shared-248098626", "15")
    self.assertEqual("switched", result["mode"])
    self.assertEqual("shared-248098626", result["profile"]["id"])

def test_library_not_returned_by_target_token_is_rejected(self):
    with self.assertRaisesRegex(ValueError, "无权访问"):
        service.select_profile_library("shared-248098626", "99")
```

Add route contract tests that call registered handlers and assert the three routes return the service payload while the shared operation lock is held.

- [ ] **Step 2: Run Task 2 tests and verify RED**

Run: `PYTHONPATH=src python3 -m unittest tests.test_v108_profile_libraries -v`

Expected: FAIL because library discovery and lifecycle endpoints are absent.

- [ ] **Step 3: Implement token-correct discovery and idempotent lifecycle**

Add one private `_libraries(profile, token)` method that checks the Plex server machine, filters `type == "artist"`, normalizes IDs/names, and calls `playlists()` to validate usable access. Recipient discovery may obtain a token temporarily but must return only account and library metadata. Refactor recipient imports to use library-aware deterministic IDs and `find_identity()`; restore an archived exact identity instead of creating another.

`select_profile_library()` must:

1. Load and validate the target library using the selected profile's own token.
2. Return `unchanged` when it matches the current identity.
3. Return an already-existing exact profile when present.
4. Call `create_for_library()` when the source is protected.
5. Call `switch_unmanaged_library()` otherwise.

Update `save_library()` so the official-login first-library selection uses the same service behavior and never leaves registry/settings split.

- [ ] **Step 4: Run recipient, connection, and lifecycle tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_v108_profile_libraries tests.test_v040_recipients tests.test_v045_shared_behavior tests.test_v040_switch_guards -v`

Expected: PASS; response representations contain no Plex token.

- [ ] **Step 5: Commit Task 2**

```bash
git add src/helper/plex_recipients.py src/helper/profile_web.py src/helper/plex_state_v0316.py tests/test_v108_profile_libraries.py
git commit -m "feat: discover libraries per Plex user"
```

### Task 3: Pin each browser tab and request to one profile

**Files:**
- Modify: `src/helper/profiles.py`
- Modify: `src/helper/web.py`
- Modify: `src/helper/static/auth.js`
- Create: `tests/test_v108_profile_request_scope.py`

**Interfaces:**
- Consumes: enabled profile registry from Task 1.
- Produces: request header `X-Plex-Profile` and `PCHAuth.setProfile(profile_id)`.
- Produces: `ProfileRegistry.fixed_active(profile_id=None, enabled_only=False)` rejecting disabled explicit profiles.
- All existing `ActiveProfileStore` and `ActiveEngineProxy` consumers remain unchanged because middleware pins the context variable.

- [ ] **Step 1: Write failing HTTP scope tests**

Use a real FastAPI `TestClient` with two scoped stores containing literal owner/friend markers. After creating an authenticated session, assert:

```python
def test_profile_header_reads_requested_scope_without_changing_global_active(self):
    response = client.get("/api/status", headers={"X-Plex-Profile": "friend-a"})
    self.assertEqual("friend-marker", response.json()["daily_notice"])
    self.assertEqual("default", registry._saved_active_id())

def test_two_tabs_can_read_different_profiles(self):
    owner = client.get("/api/status", headers={"X-Plex-Profile": "default"}).json()
    friend = client.get("/api/status", headers={"X-Plex-Profile": "friend-a"}).json()
    self.assertEqual("owner-marker", owner["daily_notice"])
    self.assertEqual("friend-marker", friend["daily_notice"])

def test_disabled_explicit_profile_is_rejected(self):
    registry.archive("friend-a")
    response = client.get("/api/status", headers={"X-Plex-Profile": "friend-a"})
    self.assertEqual(400, response.status_code)

def test_unscoped_profile_list_allows_stale_tab_recovery(self):
    response = client.get("/api/plex/profiles")
    self.assertEqual(200, response.status_code)
    self.assertEqual(["default"], [row["id"] for row in response.json()["items"]])
```

Because this repository has no JavaScript runtime or browser-test dependency, keep the JavaScript change limited to the shown `profile()` / `setProfile()` functions and the existing `request()` boundary. Add an HTTP integration test proving that the exact header contract those functions use selects the requested scope, and extend the existing static asset contract test only to verify the exported public names and header name are shipped. The actual per-tab behavior is exercised in Task 6's browser smoke checklist after the Python suite is green.

- [ ] **Step 2: Run the request-scope tests and verify RED**

Run: `PYTHONPATH=src python3 -m unittest tests.test_v108_profile_request_scope -v`

Expected: FAIL because middleware ignores `X-Plex-Profile` and `PCHAuth` has no profile API.

- [ ] **Step 3: Implement request and tab scoping**

In `web.py`, leave `/api/plex/profiles` unpinned for stale-tab recovery; for every other authenticated application API, read `X-Plex-Profile`, validate an enabled profile, and enter `profiles.fixed_active(profile_id, enabled_only=True)` for the full request. Requests without the header retain the saved active profile for backward compatibility.

In `auth.js`:

```javascript
const PROFILE_KEY='pch-profile-id';
function profile(){return sessionStorage.getItem(PROFILE_KEY)||'';}
function setProfile(value){
 const id=String(value||'').trim();
 if(id)sessionStorage.setItem(PROFILE_KEY,id);else sessionStorage.removeItem(PROFILE_KEY);
 window.dispatchEvent(new CustomEvent('pch-profile-change',{detail:{profile_id:id}}));
}
```

Attach the header inside `request()` only. During authenticated boot, fetch `/api/plex/profiles` without the profile header, retain the tab ID when it remains enabled, otherwise replace it with the server's enabled active ID, then dispatch `pch-auth-ready`. Export `profile` and `setProfile` on `PCHAuth`.

- [ ] **Step 4: Run HTTP, auth, and profile isolation tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_v108_profile_request_scope tests.test_auth tests.test_v040_profile_routes tests.test_v040_recommendation_isolation tests.test_v040_runtime -v`

Expected: PASS; two explicit scopes remain independent and no token appears in responses.

- [ ] **Step 5: Commit Task 3**

```bash
git add src/helper/profiles.py src/helper/web.py src/helper/static/auth.js tests/test_v108_profile_request_scope.py
git commit -m "fix: pin each browser tab to one Plex profile"
```

### Task 4: Make account and library selection obvious and failure-safe

**Files:**
- Modify: `src/helper/static/settings.html`
- Modify: `src/helper/static/settings.js`
- Modify: `src/helper/static/product.css`
- Create: `tests/test_v108_profile_library_ui.py`

**Interfaces:**
- Consumes: Task 2 library endpoints and Task 3 `PCHAuth.setProfile()`.
- Produces: one current `user · library` selector and a two-step add-user dialog.
- Produces: UI rollback to the saved option on every failed library request.

- [ ] **Step 1: Write failing UI behavior tests**

The repository has no JavaScript runtime, so cover state-changing behavior through Task 2's real HTTP API tests and cover rendered structure with the existing `HTMLParser` style. Create tests with these literal DOM requirements:

```python
def test_current_account_owns_profile_and_library_selectors(self):
    page = self.parse("settings.html")
    current = page.by_id("currentUser")
    self.assertIn(current, page.by_id("plexProfile")["ancestors"])
    self.assertIn(current, page.by_id("officialSection")["ancestors"])

def test_add_user_dialog_has_separate_people_and_library_steps(self):
    page = self.parse("settings.html")
    dialog = page.by_id("addUserDialog")
    self.assertIn(dialog, page.by_id("profileRecipientList")["ancestors"])
    self.assertIn(dialog, page.by_id("profileRecipientLibraries")["ancestors"])
    self.assertTrue(page.by_id("profileRecipientLibraries")["hidden"])

def test_main_page_does_not_add_explanation_panels(self):
    page = self.parse("settings.html")
    self.assertFalse(any("instruction-card" in node["classes"] for node in page.nodes))
```

Update the existing API-contract test to require `/api/plex/profiles/libraries`, `/api/plex/profiles/library`, and `/api/plex/recipients/libraries`, and to reject the previous `library_id:String(owner.library?.id||'')` import expression. The failure rollback and `用户 · 音乐库` label are mandatory browser smoke assertions in Task 6.

- [ ] **Step 2: Run the UI tests and verify RED**

Run: `PYTHONPATH=src python3 -m unittest tests.test_v108_profile_library_ui -v`

Expected: FAIL because the add-user dialog has no recipient-library region and the old API contract still hard-codes the owner's library.

- [ ] **Step 3: Implement the concise two-step UI**

Change profile labels to `profileDisplayName(row) + ' · ' + libraryName`. Load current libraries from `/api/plex/profiles/libraries`; always render the saved library, and hide only when no Plex connection exists. On selection, retain `previousValue`, call `/api/plex/profiles/library`, set the returned profile with `PCHAuth.setProfile(result.profile.id)`, and refresh. On failure restore `previousValue` before showing the error.

In the add-user dialog, clicking a person loads `/api/plex/recipients/libraries` and replaces the list with that person's music-library choices plus one `添加` button. Import only after a library is selected. After import, call `PCHAuth.setProfile(result.profile.id)`, close the dialog, and refresh. Keep copy terse: user name, library name, `返回`, `添加`.

Update CSS only for the existing card/dialog system; do not add new explanatory panels or paragraph copy.

- [ ] **Step 4: Run settings and onboarding UI tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_v108_profile_library_ui tests.test_v040_profile_ui tests.test_v0423_settings_redesign tests.test_new_user_onboarding tests.test_v103_ui_simplification -v`

Expected: PASS; no test expects the owner's library ID to be sent for a recipient without a recipient-library choice.

- [ ] **Step 5: Commit Task 4**

```bash
git add src/helper/static/settings.html src/helper/static/settings.js src/helper/static/product.css tests/test_v108_profile_library_ui.py tests/test_v040_profile_ui.py
git commit -m "feat: simplify user and library selection"
```

### Task 5: Preserve and render the last published daily playlist

**Files:**
- Modify: `src/helper/daily.py`
- Modify: `src/helper/extra_web.py`
- Modify: `src/helper/static/daily.html`
- Modify: `src/helper/static/daily.js`
- Create: `tests/test_v108_daily_published_view.py`

**Interfaces:**
- Produces state key: `daily_published_view` with public song summaries and verified managed identifiers.
- Produces status field: `active_profile` with public `id`, display name, and library.
- Produces status field: `daily_published` from `daily_published_view`, or a legacy managed/history summary when full rows do not exist.
- Consumes: Task 3's request-pinned profile scope.

- [ ] **Step 1: Write failing published-view tests**

Add engine-level tests around real store mutations and public status projection:

```python
def test_successful_publish_saves_a_durable_public_view(self):
    result = engine._publish_daily("plan-1", NOW)
    view = store.get("daily_published_view")
    self.assertEqual("playlist-1", view["playlist_id"])
    self.assertEqual(["song-1", "song-2"], [row["id"] for row in view["items"]])
    self.assertNotIn("signature", view)
    self.assertEqual(2, result["written"])

def test_invalidating_draft_through_policy_route_does_not_remove_published_view(self):
    store.set_many({"daily_plan": {"id": "draft"}, "daily_published_view": PUBLISHED})
    routes = _Routes()
    payload = {"size": 40, "rediscovery_days": 90, "daily_avoid_days": 21,
               "favorite_percent": 20, "artist_cap": 2, "hour": 6}
    async def body(_request):
        return payload
    attach_policy_routes(routes, store, engine, body, lambda: None)
    asyncio.run(routes.handlers[("POST", "/api/daily/policy")](object()))
    self.assertIsNone(store.get("daily_plan"))
    self.assertEqual(PUBLISHED, store.get("daily_published_view"))

def test_legacy_managed_playlist_is_reported_as_published_when_plan_is_missing(self):
    store.set_many({"daily_plan": None, "daily_managed": {"id": "99239", "title": "每日推荐", "published_at": NOW},
                    "daily_history": [{"ids": ["1", "2"], "created_at": NOW}]})
    status = extensions_status(store)
    self.assertEqual("99239", status["daily_published"]["playlist_id"])
    self.assertEqual(2, status["daily_published"]["count"])
    self.assertEqual([], status["daily_published"]["items"])

def test_draft_for_one_profile_never_replaces_another_profiles_published_view(self):
    self.assertEqual("owner-song", extensions_status(owner)["daily_published"]["items"][0]["title"])
    self.assertEqual("friend-song", extensions_status(friend)["daily_published"]["items"][0]["title"])
```

Add an HTML structure test requiring a compact `activeProfileLabel` beside the daily heading and no new instruction card. The dynamic outcomes `已发布`, the published songs, and `生成新一批` are mandatory browser smoke assertions in Task 6 because the repository has no JavaScript runtime.

- [ ] **Step 2: Run daily view tests and verify RED**

Run: `PYTHONPATH=src python3 -m unittest tests.test_v108_daily_published_view -v`

Expected: FAIL because `daily_published_view` is never written and status/UI ignore managed history when `daily_plan` is null.

- [ ] **Step 3: Implement durable published projection**

Add a helper that copies only these fields after verified publish: `plan_id`, `playlist_id`, `title`, `date`, `published_at`, `count`, and item fields `id`, `title`, `artist`, `album`, `bucket`, `reasons`. Write it in the same `set_many()` transaction as `daily_managed`, history, and the applied plan.

`public_daily_published(store)` must prefer the durable view. For pre-upgrade databases with `daily_managed` but no view, return a legacy summary whose count comes from the latest history row and whose items are empty. Do not write fabricated titles or contact Plex from status reads.

Update `daily.js` so the display source is a pending draft when present, otherwise `daily_published`. A published source sets state `已发布`, shows its available songs, keeps `发布到 Plexamp` hidden, and labels the primary action `生成新一批`. Add a compact current-profile label from `active_profile`; listen for `pch-profile-change` and refresh immediately.

- [ ] **Step 4: Run all daily recommendation tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_v108_daily_published_view tests.test_daily_fixed_playlist tests.test_daily_v0326 tests.test_v0424_daily_ui tests.test_v0424_rolling_daily tests.test_v046_ui_consistency -v`

Expected: PASS; legacy status shows published summary without making Plex calls.

- [ ] **Step 5: Commit Task 5**

```bash
git add src/helper/daily.py src/helper/extra_web.py src/helper/static/daily.html src/helper/static/daily.js tests/test_v108_daily_published_view.py
git commit -m "fix: keep published daily playlists visible"
```

### Task 6: Complete-flow regression and repository verification

**Files:**
- Create: `tests/test_v108_user_library_flow.py`
- Modify: `README.md`
- Modify: `docs/install/docker.md`

**Interfaces:**
- Consumes all Tasks 1-5.
- Produces one executable fresh-install and upgrade compatibility contract.

- [ ] **Step 1: Write failing end-to-end regression tests**

Use a temporary real SQLite store and FastAPI app, mock only external Plex HTTP at the client boundary, and exercise these literal flows through HTTP:

```text
fresh admin setup
→ connect owner
→ choose 音乐 · 11
→ discover shudai6
→ choose 经典音乐 · 15 for shudai6
→ generate owner draft
→ publish owner daily playlist
→ switch this tab to shudai6 and observe its empty independent state
→ open owner scope in another request and observe the published owner playlist
→ add 经典音乐 · 15 for protected owner and observe a new empty profile
→ return to owner 音乐 · 11 and observe playlist ID/history unchanged
```

Also add an upgrade fixture with legacy `default` and `shared-248098626` IDs, then assert IDs, token-presence flags, managed playlist IDs, history lengths, cache lengths, and snapshot lengths are identical before and after registry initialization.

- [ ] **Step 2: Run the complete-flow test and inspect every boundary result**

Run: `PYTHONPATH=src python3 -m unittest tests.test_v108_user_library_flow -v`

Expected: PASS when Tasks 1-5 are integrated. If it fails, use systematic debugging at the first failing boundary; do not weaken the end-to-end assertions.

- [ ] **Step 3: Make only the integration corrections required by the failing test**

Correct route payload wiring, profile headers, or public projections without introducing new behavior. Update README and Docker installation documentation with the concise rule: each enabled entry is one Plex user plus one music library; adding another library creates an independent entry when the current entry owns playlists.

- [ ] **Step 4: Run complete project verification**

Run:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m compileall -q src tests
python3 tools/check_repository.py
git diff --check
```

Expected: every test passes, compilation exits 0, repository check reports no private/runtime artifacts, and `git diff --check` prints nothing.

After the automated checks, start the app with a temporary empty data directory and use the browser to verify these exact UI outcomes: profile options read `用户 · 音乐库`; a second tab stays on its own profile; selecting an unavailable library restores the old option; the add-user dialog requires a recipient library; and a published daily list remains visible after changing a recommendation setting. Record screenshots in the plan workspace, not the repository.

- [ ] **Step 5: Commit Task 6**

```bash
git add tests/test_v108_user_library_flow.py README.md docs/install/docker.md
git add src tests
git commit -m "test: cover multi-library Plex user flow"
```

- [ ] **Step 6: Perform whole-branch review and one gated fix pass**

Build the executing-plans review package from the merge base through `HEAD`. Give the reviewer this plan, the linked spec, the Review Focus section, and ledger rulings. Re-grade all findings by user effect. For every Critical or Important finding, first add a regression test and observe RED, then implement the minimal fix, observe GREEN, run the full suite, and commit the fix. Record Minor findings without opportunistic changes.
