# Profile-Scoped Page Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make repeat navigation show recent read-only data immediately without ever reusing data across Plex users, servers, or libraries.

**Architecture:** Add one bounded, top-window in-memory JSON cache shared by same-origin management iframes. Every entry is keyed by the authenticated web user plus the complete public Plex profile identity; child pages use an explicit allow-listed cached GET helper, while writes invalidate only the active profile's tagged entries. Existing `X-Plex-Profile`, request-generation checks, one-iframe workspace, and server-side `fixed_active()` remain authoritative.

**Tech Stack:** Browser JavaScript (classic scripts and ES modules), Node `node:test`, Python `pytest`, existing FastAPI/static frontend.

**Spec:** `docs/superpowers/specs/2026-09-26-profile-scoped-page-cache-design.md`

## Global Constraints

- Never render or store one Plex profile's response under another profile's identity.
- Cache only explicitly allow-listed GET JSON responses; unknown endpoints and all writes bypass the cache.
- Do not cache auth, live task status/progress, authorization flows, searches, track details, lyrics, audio, or media binaries.
- Do not persist shared page data to `localStorage`; release it when the browser tab closes.
- Keep one management iframe and do not add a frontend framework.
- Keep entries below 512 KiB each, 16 entries per profile scope, 48 entries total, and 8 MiB estimated total.
- Discard responses whose authenticated user, profile identity, scope generation, or page lifetime changed before completion.
- Deploy only to preview port 9513 until the user approves publication and production port 9512.

## Review Focus

- A response started for profile A finishes after selecting profile B: it must neither render nor enter B's cache (Task 1 and Task 2 tests).
- The same `profile_id` is rebound to another account/server/library while unmanaged: the previous identity must not be reusable (Task 1 tests).
- An authenticated web session logs out and another user logs in in the same tab: all old cache entries and in-flight callbacks must be inaccessible (Task 1 tests).
- A cached entry exceeds a limit or repeated navigation reaches the LRU ceiling: memory must remain bounded and the newest valid entries remain available (Task 1 tests).
- A write changes only profile A: A's related tags must reload while profile B's warm entries remain intact (Task 3 tests).

---

### Task 1: Bounded multi-profile cache core

**Files:**
- Create: `src/helper/static/page-cache.js`
- Create: `tests/page_cache.test.js`
- Modify: `src/helper/static/auth.js`
- Modify: `src/helper/static/playlists.html`
- Modify: `src/helper/static/home.html`
- Modify: `src/helper/static/mixes.html`
- Modify: `src/helper/static/daily.html`
- Modify: `src/helper/static/status.html`
- Modify: `src/helper/static/settings.html`
- Modify: `src/helper/static/external.html`
- Modify: `src/helper/static/appearance.html`
- Test: `tests/test_v1416_ui_audit.py`

**Interfaces:**
- Produces: `PCHPageCache.setProfiles(username: string, profiles: object[]): void`
- Produces: `PCHPageCache.activate(profileId: string): string` returning the active immutable scope key or `""` when identity is incomplete.
- Produces: `PCHPageCache.cachedJson(path: string, options: {tag: string, freshMs: number, retainMs: number, load: () => Promise<object>, onUpdate?: (value: object) => void}): Promise<{value: object, source: "network"|"fresh"|"stale"}>`
- Produces: `PCHPageCache.invalidate(tags?: string[], profileId?: string): void`, `PCHPageCache.clear(): void`, and `PCHPageCache.stats(): object`.
- Produces: `PCHAuth.cachedJson(path, options)` and `PCHAuth.invalidateCache(tags, profileId)` facades that preserve existing authenticated request behavior.

- [ ] **Step 1: Write failing cache isolation and lifecycle tests**

Add Node tests named `profile identities never share entries`, `late response cannot populate a changed scope`, `logout clears entries and suppresses callbacks`, `incomplete identities bypass cache`, `same profile id with a changed library misses`, `fresh stale and expired entries follow time boundaries`, `concurrent loads share one request`, and `entry count and byte limits evict least recently used data`. Assert the exact limits from Global Constraints and that cached values are cloned rather than shared mutable objects.

- [ ] **Step 2: Run the focused tests and confirm the cache module is missing**

Run: `node --test tests/page_cache.test.js`

Expected: FAIL because `page-cache.js` or its exported factory does not exist.

- [ ] **Step 3: Implement the cache as one focused module**

Implement `createProfilePageCache({now, maxEntryBytes, maxEntriesPerScope, maxEntries, maxBytes})` for tests and expose one top-window `PCHPageCache` instance in browsers. Use full profile identity fields (`id`, `account.id`, `server.machine`, `library.id`, `created_at`), request generations, deep-cloned JSON values, in-flight deduplication, and LRU eviction. A child iframe must reuse `window.top.PCHPageCache` only when it is same-origin; otherwise it creates a tab-local standalone instance.

- [ ] **Step 4: Add the authenticated facade and load order**

Load `page-cache.js` before `auth.js` on every listed page. Extend `syncProfile()` to provide public profiles and authenticated username to the cache, extend `setProfile()` to activate the selected scope, clear everything on logout/session expiry, and implement `PCHAuth.cachedJson()` by delegating its network load to the existing `request()` path. Do not change `PCHAuth.request()` cache headers or error recovery semantics.

- [ ] **Step 5: Run focused JavaScript and static-contract tests**

Run: `node --test tests/page_cache.test.js tests/playlist_artwork.test.js`

Run: `pytest -q tests/test_v1416_ui_audit.py`

Expected: all tests PASS.

- [ ] **Step 6: Commit the cache core**

```bash
git add src/helper/static/page-cache.js src/helper/static/auth.js src/helper/static/*.html tests/page_cache.test.js tests/test_v1416_ui_audit.py
git commit -m "feat: add profile-scoped page cache"
```

### Task 2: Warm playlist navigation without cross-user results

**Files:**
- Modify: `src/helper/static/playlists.js`
- Modify: `src/helper/static/playlist-workspace.js`
- Test: `tests/page_cache.test.js`
- Test: `tests/test_v132_playlist_hub.py`
- Test: `tests/test_v1416_ui_audit.py`

**Interfaces:**
- Consumes: `PCHPageCache.setProfiles()`, `activate()`, and `PCHAuth.cachedJson()` from Task 1.
- Produces: profile-scoped warm reads for `/api/playlists` under the `playlists` tag.
- Produces: an incremented page/profile generation before any selected profile's cached or network value may render.

- [ ] **Step 1: Write failing profile-switch and warm-return tests**

Add tests proving that `loadProfiles()` binds complete public profile identities, `switchProfile()` activates its cache scope before loading playlists, an A response arriving after B selection is ignored, switching A → B → A can return A's cached `/api/playlists`, and `workspace.reset()` still clears the visible old iframe instead of preserving another profile's DOM.

- [ ] **Step 2: Run the focused tests and confirm failure**

Run: `node --test tests/page_cache.test.js`

Run: `pytest -q tests/test_v132_playlist_hub.py tests/test_v1416_ui_audit.py`

Expected: new assertions FAIL because playlist loading still always uses `PCHAuth.request()` directly.

- [ ] **Step 3: Integrate the playlist summary cache**

Route only `/api/playlists` through `PCHAuth.cachedJson()` with tag `playlists`, `freshMs: 60_000`, and `retainMs: 900_000`. Cached data may render immediately; a stale background update may render only when its captured `profileRequest`, complete scope key, and `loadedProfileId` still match. Keep playlist detail, search, likes, playback, track edits, artwork, and media requests uncached.

- [ ] **Step 4: Preserve visible isolation during navigation**

Keep the one-iframe reset on actual profile changes, close dialogs, stop playback, clear tracks, and show the selected profile's loading state before accepting its cached result. Do not display A's old playlist list as a placeholder for B.

- [ ] **Step 5: Run focused tests**

Run: `node --test tests/page_cache.test.js tests/playlist_artwork.test.js tests/playlist_player_track.test.js`

Run: `pytest -q tests/test_v132_playlist_hub.py tests/test_v1416_ui_audit.py`

Expected: all tests PASS.

- [ ] **Step 6: Commit warm playlist navigation**

```bash
git add src/helper/static/playlists.js src/helper/static/playlist-workspace.js tests/page_cache.test.js tests/test_v132_playlist_hub.py tests/test_v1416_ui_audit.py
git commit -m "feat: reuse playlist summaries per profile"
```

### Task 3: Cache safe management summaries and invalidate writes precisely

**Files:**
- Modify: `src/helper/static/mixes.js`
- Modify: `src/helper/static/external.js`
- Modify: `src/helper/static/settings.js`
- Modify: `src/helper/static/home.js`
- Modify: `src/helper/static/playlists.js`
- Test: `tests/page_cache.test.js`
- Test: `tests/test_external_workspace_refresh.py`
- Test: `tests/test_v0420_smart_mix_controls.py`
- Test: `tests/test_v1416_ui_audit.py`

**Interfaces:**
- Consumes: `PCHAuth.cachedJson()` and `PCHAuth.invalidateCache()` from Task 1.
- Produces: cached `/api/mixes/status` tagged `smart-mixes`, cached `/api/external/sources` tagged `external-sources`, and cached non-sensitive settings summaries tagged `settings`.
- Produces: explicit invalidation messages for `playlists`, `library-summary`, `smart-mixes`, `external-sources`, and `settings`.

- [ ] **Step 1: Write failing allow-list and precise-invalidation tests**

Add tests asserting that only the exact safe summary endpoints use cached reads; `/api/status`, `/api/workflow/status`, auth/PIN/QR endpoints, source track-detail endpoints, searches, and all writes remain uncached. Assert that a successful A write invalidates only A's declared tags and leaves B entries available.

- [ ] **Step 2: Run focused tests and confirm failure**

Run: `node --test tests/page_cache.test.js`

Run: `pytest -q tests/test_external_workspace_refresh.py tests/test_v0420_smart_mix_controls.py tests/test_v1416_ui_audit.py`

Expected: new assertions FAIL because management pages do not yet use the cache facade or tag invalidation.

- [ ] **Step 3: Integrate safe summary reads**

Use `freshMs: 60_000` and `retainMs: 900_000` for mix and external-source summaries. Cache only settings responses already filtered by the server and not involved in login, recipient discovery, task progress, or connection authorization; use `freshMs: 120_000` and `retainMs: 1_800_000`. Each page's background callback must verify its current profile and page generation before rendering.

- [ ] **Step 4: Add write-driven invalidation**

After confirmed successful mutations, invalidate only the current profile's affected tags. Preserve the existing `pch-playlists-changed` message and have the parent invalidate `playlists` before refreshing. When a library workflow completes, invalidate `library-summary` and `playlists`; never cache the live workflow/status response itself.

- [ ] **Step 5: Handle profile lifecycle mutations**

When settings changes account/server/library identity, refresh public profiles before any subsequent cached read so the complete scope key changes. Removing or disabling a profile deletes that profile's entries. Logging out clears all scopes. Authorization failures use existing error handling and never fall back to cached settings as if current.

- [ ] **Step 6: Run focused tests**

Run: `node --test tests/page_cache.test.js tests/*.test.js`

Run: `pytest -q tests/test_external_workspace_refresh.py tests/test_v0420_smart_mix_controls.py tests/test_v1416_ui_audit.py tests/test_v108_profile_request_scope.py`

Expected: all tests PASS.

- [ ] **Step 7: Commit management integration**

```bash
git add src/helper/static/mixes.js src/helper/static/external.js src/helper/static/settings.js src/helper/static/home.js src/helper/static/playlists.js tests/page_cache.test.js tests/test_external_workspace_refresh.py tests/test_v0420_smart_mix_controls.py tests/test_v1416_ui_audit.py
git commit -m "feat: reuse safe management summaries"
```

### Task 4: Regression, memory bounds, and preview deployment

**Files:**
- Create: `tools/verify_profile_page_cache.py`
- Modify: `CHANGELOG.md`
- Test: full existing Python and Node suites

**Interfaces:**
- Consumes: `PCHPageCache.stats()` from Task 1.
- Produces: a read-only verification report for cache count/bytes, profile isolation, repeat navigation, and server memory comparison.

- [ ] **Step 1: Write the read-only verification script**

Implement a Playwright-based script that logs in only with supplied preview credentials, records initial and post-navigation browser cache stats, switches between two available Plex profiles when present, and verifies that scope keys and visible labels match after A → B → A navigation. It must skip the two-profile check with a clear message when preview data has only one enabled profile and must never invoke a write endpoint.

- [ ] **Step 2: Run all automated tests**

Run: `pytest -q`

Run: `node --test tests/*.test.js`

Expected: all tests PASS with no regressions.

- [ ] **Step 3: Inspect the final diff and static assets**

Run: `git diff --check HEAD~3..HEAD`

Run: `git status --short`

Expected: no whitespace errors; pre-existing accepted work remains present and no unrelated files are modified by this feature.

- [ ] **Step 4: Back up and deploy only to 9513**

Create a timestamped runtime backup on the NAS preview backup path, deploy the verified working tree to the preview service, and confirm 9512 remains untouched. Do not push GitHub, tag a release, publish an image, or alter the production service.

- [ ] **Step 5: Verify preview behavior and resources**

Run the read-only verification script against `http://192.168.50.99:9513/`. Record repeat-navigation timings, cache entry/byte plateaus, console errors, network errors, and preview container memory before and after repeated A → B → A navigation. Confirm task status always issues live requests and that no cached page displays another profile's identity.

- [ ] **Step 6: Update the changelog and commit verification tooling**

```bash
git add tools/verify_profile_page_cache.py CHANGELOG.md
git commit -m "test: verify profile-scoped page cache"
```

- [ ] **Step 7: Request user preview approval**

Report the 9513 URL, isolation results, cache/memory measurements, and any limitations. Stop before GitHub, image, or 9512 publication.
