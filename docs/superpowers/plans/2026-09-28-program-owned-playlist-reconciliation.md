# Program-Owned Playlist Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every enabled program-owned playlist converge to the program's current title, ownership marker, and exact member set, while keeping library organization shared per library and recommendation results isolated per profile.

**Architecture:** Add one small reconciliation module that owns Plex discovery, update, recreate, verification, and result classification. Daily recommendation, smart mixes, base/library categories, and recipient sharing continue to calculate their own targets but delegate all Plex state convergence to that module; scheduling retains bounded retry and clears obsolete unresolved snapshots only after a verified success.

**Tech Stack:** Python 3, existing SQLite-backed stores, Plex HTTP client, `unittest`/`pytest`, existing Node contract tests.

**Spec:** `docs/superpowers/specs/2026-09-28-program-owned-playlist-reconciliation-design.md`

## Global Constraints

- Do not change selection, recommendation, QQ matching, or categorization algorithms.
- Do not add dependencies or a second synchronization framework.
- Automatic maintenance runs only when the corresponding switch and any per-category maintenance switch are enabled.
- Never adopt an unowned same-name playlist, and never interpret timeout, permission, 429, or 5xx failures as deletion.
- An empty or untrustworthy target must preserve the existing playlist.
- Exact member set, title, and ownership marker are required; Plex member ordering is best effort.
- Library organization is calculated once by the owner and copied to each recipient account; daily and preference-based smart playlists remain profile-local.
- No deployment, GitHub release, or change to port 9512 is part of implementation until tests and preview verification pass.

## Review Focus

- A read timeout after a playlist was deleted must wait for retry rather than create a duplicate; Task 2 covers error classification and Task 3 covers reconcile behavior.
- A successful Plex create whose response is lost must be rediscovered by ownership marker before another create; Task 3 covers ambiguous-create recovery.
- A recipient manually deleting a shared category must not trigger a second classification calculation; Task 6 covers owner-target reuse and recreation.
- Old `uncertain` snapshots must be superseded only after verified remote success, not merely after sending writes; Task 4 covers cleanup timing.
- Disabling automatic maintenance during a retry window must prevent the retry from changing Plex; Task 5 covers switch gating at execution time.

---

### Task 1: Lock the common target and result contracts

**Files:**
- Create: `src/helper/managed_playlist_sync.py`
- Create: `tests/test_managed_playlist_sync.py`

**Interfaces:**
- Consumes: existing Plex state dictionaries, `engine.marker(category_id)`, and `engine.daily_scope()`.
- Produces: `ManagedPlaylistTarget`, `ManagedPlaylistResult`, `ReconcileConflict`, and `validate_target(target)`.

- [ ] **Step 1: Write failing contract tests** for non-empty unique numeric member IDs, stable scope/category identity, normalized titles, and the result statuses `unchanged`, `updated`, and `created`.
- [ ] **Step 2: Run** `pytest -q tests/test_managed_playlist_sync.py` and verify import/contract failures.
- [ ] **Step 3: Implement the immutable target/result types and validation** without any Plex mutation code.
- [ ] **Step 4: Re-run** `pytest -q tests/test_managed_playlist_sync.py` and verify the contract tests pass.
- [ ] **Step 5: Commit** `test: define managed playlist reconciliation contracts`.

### Task 2: Make Plex lookup failures structurally distinguishable

**Files:**
- Modify: `src/helper/clients.py`
- Modify: `tests/test_v149_direct_plex.py`
- Modify: `tests/test_managed_playlist_sync.py`

**Interfaces:**
- Consumes: existing `PlexNotFound`, `PlexError`, playlist listing and state methods.
- Produces: `PlexClient.owned_playlists(marker: str) -> list[dict]`, with `PlexNotFound` reserved for a confirmed missing resource.

- [ ] **Step 1: Add failing tests** proving 404 becomes `PlexNotFound`, while connection failure, 401/403, 429, and 5xx remain retryable/non-not-found errors; add marker lookup tests with zero, one, and multiple matches.
- [ ] **Step 2: Run** `pytest -q tests/test_v149_direct_plex.py tests/test_managed_playlist_sync.py` and verify the new cases fail.
- [ ] **Step 3: Implement exact error classification and bounded ownership-marker discovery** using the existing playlist APIs.
- [ ] **Step 4: Re-run the two test files** and verify all cases pass.
- [ ] **Step 5: Commit** `fix: distinguish missing Plex playlists from transient failures`.

### Task 3: Implement idempotent playlist convergence

**Files:**
- Modify: `src/helper/managed_playlist_sync.py`
- Modify: `tests/test_managed_playlist_sync.py`

**Interfaces:**
- Consumes: `ManagedPlaylistTarget`, current managed record, Plex client, and ownership marker.
- Produces: `reconcile_managed_playlist(plex, target, managed_record) -> ManagedPlaylistResult`.

- [ ] **Step 1: Add failing reconciliation tests** for artificial additions/removals, rename/summary drift, confirmed deletion, transient read failure, same-name unowned collision, duplicate ownership markers, lost-create response recovery, concurrent pre-write change, duplicate source members, and repeated unchanged runs.
- [ ] **Step 2: Run** `pytest -q tests/test_managed_playlist_sync.py` and verify the new tests fail for missing behavior.
- [ ] **Step 3: Implement read-discover-create/update flow** so each mutation is followed by read-back verification and a pre-write change causes one fresh read/recalculation rather than a permanent pause.
- [ ] **Step 4: Implement exact member convergence** by removing stale playlist item IDs before appending missing rating keys; do not require MOVE ordering for success.
- [ ] **Step 5: Re-run** `pytest -q tests/test_managed_playlist_sync.py` and verify all cases pass with no duplicate create or duplicate member calls.
- [ ] **Step 6: Commit** `feat: add idempotent managed playlist reconciler`.

### Task 4: Integrate daily recommendation and smart playlists

**Files:**
- Modify: `src/helper/daily.py`
- Modify: `src/helper/smart_mix_web.py`
- Modify: `src/helper/playlist_sync.py`
- Modify: `tests/test_daily_fixed_playlist.py`
- Modify: `tests/test_v0420_smart_mix_controls.py`
- Modify: `tests/test_v0424_smart_mix_auto.py`

**Interfaces:**
- Consumes: `reconcile_managed_playlist(...)` from Task 3 and each feature's existing target calculation.
- Produces: daily and smart managed records updated from verified reconciliation results; `supersede_unresolved_snapshots(store, category_id, success_snapshot_id)`.

- [ ] **Step 1: Replace old expected failures with failing behavior tests** proving manual member/title/summary drift is repaired, confirmed deletion is recreated, transient errors remain retryable, and current per-profile target calculations are unchanged.
- [ ] **Step 2: Add a failing regression test** for the real stale weekly `uncertain` pattern: a later verified success marks it `superseded` and clears the matching pause/retry fields.
- [ ] **Step 3: Run** `pytest -q tests/test_daily_fixed_playlist.py tests/test_v0420_smart_mix_controls.py tests/test_v0424_smart_mix_auto.py` and verify the new expectations fail.
- [ ] **Step 4: Route daily and smart publication through the common reconciler**, removing fingerprint/manual-drift blocks but retaining preview expiry, library identity, and candidate freshness checks.
- [ ] **Step 5: Implement verified-success cleanup** for old `prepared`, `uncertain`, and `restoring` snapshots and compatibility pause fields.
- [ ] **Step 6: Re-run the focused tests** and verify all pass.
- [ ] **Step 7: Commit** `feat: reconcile daily and smart playlists automatically`.

### Task 5: Integrate library organization and scheduler semantics

**Files:**
- Modify: `src/helper/base_mixin.py`
- Modify: `src/helper/engine.py`
- Modify: `src/helper/automation.py`
- Modify: `src/helper/scheduler_retry.py`
- Modify: `tests/test_base_system_authority.py`
- Modify: `tests/test_v0415_library_maintenance.py`
- Modify: `tests/test_v114_global_automation.py`
- Modify: `tests/test_scheduler_retry.py`

**Interfaces:**
- Consumes: common reconciler and existing base/theme target calculation.
- Produces: exact-set base/theme category maintenance and retry/attention classification shared by all three automatic task families.

- [ ] **Step 1: Add failing tests** proving manual additions are removed, missing intended songs are restored, renamed/deleted categories are repaired, and empty/insufficient evidence never clears a playlist.
- [ ] **Step 2: Add failing scheduler tests** proving a switch disabled before retry causes no Plex mutation, transient errors become `waiting_retry`, and only ownership/scope ambiguity becomes `needs_attention`.
- [ ] **Step 3: Run the four focused test files** and verify the new tests fail.
- [ ] **Step 4: Replace append-only category writes and drift blocks with common reconciliation**, preserving per-category `enabled`/`approved` gates and existing evidence thresholds.
- [ ] **Step 5: Normalize automation outcomes and clear stale pause fields only after verified success.**
- [ ] **Step 6: Re-run the focused tests** and verify all pass.
- [ ] **Step 7: Commit** `feat: converge managed library categories`.

### Task 6: Reconcile shared library copies without recomputation

**Files:**
- Modify: `src/helper/library_sharing.py`
- Modify: `tests/test_v149_library_sharing.py`
- Modify: `tests/test_v040_recommendation_isolation.py`

**Interfaces:**
- Consumes: owner category's verified target and common reconciler.
- Produces: recipient-local managed copies with owner-identical members, plus unchanged profile-local daily/smart isolation.

- [ ] **Step 1: Add failing tests** proving one owner target feeds all recipient copies, recipient drift/deletion is repaired, a transient recipient failure creates no duplicate, and recipient sync performs no QQ/category calculation.
- [ ] **Step 2: Extend isolation tests** proving daily, weekly, and time-capsule histories never cross profile boundaries, while recent additions may independently produce the same IDs.
- [ ] **Step 3: Run** `pytest -q tests/test_v149_library_sharing.py tests/test_v040_recommendation_isolation.py` and verify the new cases fail.
- [ ] **Step 4: Route recipient copy maintenance through the common reconciler** using the owner's verified member target and recipient-specific marker/scope.
- [ ] **Step 5: Re-run the two test files** and verify all cases pass.
- [ ] **Step 6: Commit** `feat: reconcile shared library playlist copies`.

### Task 7: Remove obsolete drift-blocking paths and verify the product

**Files:**
- Modify: affected tests that explicitly assert the replaced manual-drift behavior.
- Modify: `README.md` only if the user-visible automation semantics are currently documented incorrectly.

**Interfaces:**
- Consumes: all prior task interfaces.
- Produces: one consistent product contract and a release-ready, but not deployed, branch.

- [ ] **Step 1: Search for obsolete behavior** with `rg -n "待核对|不会覆盖|禁止重复写入|manual.*drift|uncertain" src/helper tests` and classify every remaining occurrence as destructive-operation safety, external-import safety, or obsolete managed-playlist drift handling.
- [ ] **Step 2: Remove only obsolete program-owned playlist branches and update their tests**; retain rename/delete/external-import protections outside this feature.
- [ ] **Step 3: Run focused Python tests** from Tasks 1-6 and verify they all pass.
- [ ] **Step 4: Run** `pytest -q` and verify the complete Python suite passes.
- [ ] **Step 5: Run the repository's Node test command and repository/release checks** discovered from project metadata; verify zero failures.
- [ ] **Step 6: Review `git diff --check`, changed-file diff, and status** to ensure no unrelated user file is staged and no dependency or release version changed.
- [ ] **Step 7: Commit** `test: verify unified managed playlist maintenance`.
