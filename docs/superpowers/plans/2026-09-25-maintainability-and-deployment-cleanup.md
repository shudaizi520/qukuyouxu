# Maintainability and Deployment Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove proven dead code, create stable ownership boundaries for web and Plex lifecycle code, reduce CSS patch-layer coupling, and deploy an image-only TrueNAS workload without changing product behavior or data.

**Architecture:** Make small compatibility-preserving moves behind existing public routes and imports. Prove each boundary with focused tests, then run the complete suite and visual checks before publishing v2.0.8. The TrueNAS edit preserves `/data` and every runtime setting while removing only the 44 code overlay mounts.

**Tech Stack:** Python 3.11/3.12, FastAPI/Starlette, plain JavaScript/CSS, pytest, Node test runner, Playwright, Docker/GHCR, TrueNAS SCALE custom apps.

**Spec:** `docs/superpowers/specs/2026-09-25-maintainability-and-deployment-cleanup-design.md`

## Global Constraints

- Do not delete or rewrite `/data`, Plex playlists, credentials, playback history, profiles, metadata or existing database backups.
- Do not change endpoint URLs, persisted keys, recommendation results or visible layout.
- Keep active migration readers and the legacy Plex bootstrap route.
- Add no runtime dependency or front-end build tool.
- Preserve Python 3.11/3.12 and amd64/arm64 images.
- Every behavioral change uses RED-GREEN TDD; every phase ends with the full suite.
- Capture the TrueNAS custom-app YAML before changing mounts.

## Review Focus

- A page or module that loaded an apparently dead asset indirectly must still boot after cleanup; Task 1 exercises every shipped HTML asset reference and application import.
- Authentication middleware must still protect the same API routes after page/static routes move; Task 2 compares the full route method/path table.
- Existing imports of `helper.daily_mix_v036` must remain valid while internal imports use the stable module; Task 3 tests symbol identity and the route table.
- CSS file order must remain identical across all three themes and embedded settings; Task 4 runs computed-style and visual-contract checks.
- Removing code overlays must not lose runtime settings or data ownership; Task 6 verifies mounts, limits, health, version and rendered pages on TrueNAS.

---

### Task 1: Remove proven dead code and expired status aliases

**Files:**
- Modify: `tests/test_dead_code_cleanup.py`
- Modify: `tests/test_v0421_runtime_integrity.py`
- Modify: `tests/test_v120_status_ui.py`
- Modify: `src/helper/web.py`
- Modify: `src/helper/extra_web.py`
- Delete: `src/helper/theme_mixin.py`
- Delete: `src/helper/static/home.css`

**Interfaces:**
- Consumes: current static allow-list in `helper.web`; `extensions_status(store) -> dict`.
- Produces: a static allow-list with no orphan stylesheet; status behavior containing `preferred_tracks` and `cooled_tracks` only.

- [ ] **Step 1: Write failing cleanup tests**

Add `theme_mixin.py` and `static/home.css` to the obsolete-path assertion. Extend the static-asset test to collect all shipped HTML pages and assert that every allowed `.css` file is referenced. Extend the status test with literal assertions:

```python
self.assertNotIn("positive_tracks", behavior)
self.assertNotIn("negative_tracks", behavior)
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_dead_code_cleanup.py tests/test_v0421_runtime_integrity.py tests/test_v120_status_ui.py`

Expected: FAIL naming `theme_mixin.py`, `home.css`, and the two response aliases.

- [ ] **Step 3: Implement the cleanup**

Delete the two unused files, remove `home.css` from the static allow-list, and remove only these two assignments from `extensions_status`:

```python
'positive_tracks': preferred,
'negative_tracks': cooled,
```

- [ ] **Step 4: Verify GREEN and the full suite**

Run: `.venv/bin/python -m pytest -q tests/test_dead_code_cleanup.py tests/test_v0421_runtime_integrity.py tests/test_v120_status_ui.py`

Expected: PASS.

Run: `.venv/bin/python -m pytest -q`

Expected: `1066+ passed`, zero failures.

- [ ] **Step 5: Commit**

```bash
git add tests src/helper
git commit -m "refactor: remove obsolete runtime assets"
```

### Task 2: Extract the public web surface from application construction

**Files:**
- Create: `src/helper/web_surface.py`
- Create: `tests/test_web_surface.py`
- Modify: `src/helper/web.py`
- Modify: `tests/test_v0421_runtime_integrity.py`

**Interfaces:**
- Consumes: `FastAPI`, static directory `Path`, application version string.
- Produces: `attach_web_surface(app, static_root: Path, version: str) -> None`, `STATIC_ASSETS: frozenset[str]`, and the unchanged public page/static/health route table.

- [ ] **Step 1: Write the failing boundary test**

Create a test that imports `attach_web_surface`, attaches it to a fresh `FastAPI`, and asserts the literal route set includes `/`, `/daily`, `/library`, `/status`, `/mixes`, `/external`, `/settings`, `/appearance`, `/advanced`, `/healthz`, and `/static/{name}`. It must also call the `/healthz` endpoint and assert `{"ok": True, "version": "9.8.7"}`.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_web_surface.py`

Expected: FAIL because `helper.web_surface` does not exist.

- [ ] **Step 3: Implement the focused module**

Move only the page, redirect, health and static route registrations from
`create_app` into `attach_web_surface(app, static_root: Path, version: str) ->
None`; the function registers those existing handlers directly on `app`.

Keep rendering through `render_versioned_html`/`render_library_html`, keep the allow-list closed, and have `web.py` call the function once. Do not move authentication or API routes.

- [ ] **Step 4: Verify GREEN, route uniqueness and full suite**

Run: `.venv/bin/python -m pytest -q tests/test_web_surface.py tests/test_v0421_runtime_integrity.py tests/test_release_blockers.py`

Expected: PASS and no duplicate method/path pair.

Run: `.venv/bin/python -m pytest -q`

Expected: zero failures.

- [ ] **Step 5: Commit**

```bash
git add src/helper/web.py src/helper/web_surface.py tests/test_web_surface.py tests/test_v0421_runtime_integrity.py
git commit -m "refactor: isolate public web surface"
```

### Task 3: Give Plex lifecycle code a stable module boundary

**Files:**
- Create: `src/helper/plex_lifecycle.py`
- Modify: `src/helper/daily_mix_v036.py`
- Modify: `src/helper/extra_web.py`
- Modify: `src/helper/playlist_hub.py`
- Modify: `tests/test_recovery_regressions.py`
- Modify: `tests/test_ui_interaction_polish.py`

**Interfaces:**
- Consumes: all current public functions from `daily_mix_v036.py`.
- Produces: the same functions from `helper.plex_lifecycle`; `helper.daily_mix_v036` remains a compatibility facade for external imports.

- [ ] **Step 1: Write a failing stable-import test**

Add a test importing `helper.plex_lifecycle` and asserting that
`remove_managed_playlist`, `reconcile_managed_playlist`,
`managed_playlist_rows`, and `attach_v036_routes` are callable.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_recovery_regressions.py -k stable_lifecycle`

Expected: FAIL with `ModuleNotFoundError: helper.plex_lifecycle`.

- [ ] **Step 3: Move implementation and preserve compatibility**

Move the existing implementation unchanged to `plex_lifecycle.py`. Replace
`daily_mix_v036.py` with explicit re-exports and an `__all__` list. Update only
internal application imports to `plex_lifecycle`; leave tests that validate the
old path so compatibility is exercised.

- [ ] **Step 4: Verify GREEN, recovery behavior and full suite**

Run: `.venv/bin/python -m pytest -q tests/test_recovery_regressions.py tests/test_v045_shared_behavior.py tests/test_v149_library_sharing.py tests/test_ui_interaction_polish.py`

Expected: PASS.

Run: `.venv/bin/python -m pytest -q`

Expected: zero failures.

- [ ] **Step 5: Commit**

```bash
git add src/helper/plex_lifecycle.py src/helper/daily_mix_v036.py src/helper/extra_web.py src/helper/playlist_hub.py tests
git commit -m "refactor: name Plex lifecycle boundary"
```

### Task 4: Separate the authoritative CSS refinement layer

**Files:**
- Create: `src/helper/static/product-refinements.css`
- Create: `tests/ui_css.py`
- Modify: `src/helper/static/product.css`
- Modify: all shipped `src/helper/static/*.html` pages that load `product.css`
- Modify: `src/helper/web_surface.py`
- Modify: `tools/check_theme_contract.py`
- Modify: CSS-reading UI tests that exercise rules moved from the tail layer

**Interfaces:**
- Consumes: the rules beginning at `/* 2026 visual system */` in `product.css` and the existing HTML stylesheet order.
- Produces: `product.css` for accumulated product rules plus `product-refinements.css` loaded immediately afterward; identical computed styles in all themes.

- [ ] **Step 1: Write failing stylesheet-order and combined-CSS tests**

Add a test asserting every HTML page that loads `product.css` immediately loads
`product-refinements.css` next. Add `tests/ui_css.py` with:

```python
def product_css() -> str:
    return (STATIC / "product.css").read_text(encoding="utf-8") + "\n" + (
        STATIC / "product-refinements.css"
    ).read_text(encoding="utf-8")
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_unified_design_system.py tests/test_player_visual_refresh.py`

Expected: FAIL because `product-refinements.css` is missing.

- [ ] **Step 3: Move the refinement layer without changing order**

Move the complete tail beginning with `/* 2026 visual system */` into the new
file. Insert its link immediately after `product.css` in every affected page,
add it to `STATIC_ASSETS` and the theme-contract protected list, and update
behavioral CSS tests to read the combined stylesheet helper.

- [ ] **Step 4: Verify computed styles, visuals and full suite**

Run: `.venv/bin/python tools/check_theme_contract.py`

Expected: `Theme contract passed`.

Run: `.venv/bin/python -m pytest -q tests/test_player_visual_refresh.py tests/test_unified_design_system.py tests/test_settings_visual_refresh.py tests/test_ui_layout_polish.py`

Expected: PASS, including Playwright computed alignment/contrast checks.

Run: `.venv/bin/python -m pytest -q`

Expected: zero failures.

- [ ] **Step 5: Commit**

```bash
git add src/helper/static src/helper/web_surface.py tools/check_theme_contract.py tests
git commit -m "refactor: isolate CSS refinement layer"
```

### Task 5: Document retention and prepare release 2.0.8

**Files:**
- Modify: `docs/install/truenas.md`
- Modify: `docs/install/docker.md`
- Modify: `CHANGELOG.md`
- Modify: `src/helper/__init__.py`
- Modify: version-bearing HTML/JS files
- Modify: release-version tests

**Interfaces:**
- Consumes: release version `2.0.7` and current backup instructions.
- Produces: application/tag version `2.0.8`; explicit no-code-overlay and backup-retention guidance.

- [ ] **Step 1: Change version assertions to 2.0.8 and verify RED**

Update literal release assertions in `tests/test_project_entrypoint.py`,
`tests/test_release_version.py`, and `tests/test_v130_upgrade_integration.py`.

Run: `.venv/bin/python -m pytest -q tests/test_project_entrypoint.py tests/test_release_version.py tests/test_v130_upgrade_integration.py`

Expected: FAIL showing the application still reports `2.0.7`.

- [ ] **Step 2: Implement release metadata and documentation**

Set all shipped release markers to `2.0.8`, add the changelog entry, document
that TrueNAS must mount only `/data`, and document retention as newest three
automatic backups plus one named milestone per major release. State that old
backups require explicit confirmation before deletion.

- [ ] **Step 3: Verify release consistency and full local gates**

Run: `PYTHONPATH=src .venv/bin/python tools/check_release_version.py v2.0.8`

Expected: `发布版本已核对: v2.0.8`.

Run: `.venv/bin/python tools/check_repository.py && .venv/bin/python -m compileall -q src tools && .venv/bin/python -m pytest -q`

Expected: zero errors/failures.

Run: `node --test tests/appearance.test.js tests/playlist_artwork.test.js tests/playlist_sections.test.js tests/playlist_player_track.test.js && node tests/settings_interactions.test.js`

Expected: all JavaScript tests pass.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md docs src tests
git commit -m "chore: prepare 2.0.8 cleanup release"
```

### Task 6: Publish and deploy the reproducible image

**Files:**
- External rollback artifact: current TrueNAS custom-app YAML and mount inventory
- Git tag: `v2.0.8`
- GitHub/GHCR release artifacts

**Interfaces:**
- Consumes: tested `v2.0.8` commit and existing TrueNAS `/data`, port, environment, security and resource settings.
- Produces: `ghcr.io/shudaizi520/qukuyouxu:2.0.8`/`latest`; live TrueNAS app with exactly one `/data` mount.

- [ ] **Step 1: Capture rollback state**

Read the current custom-app YAML through the TrueNAS API and save it outside the
repository with mode `0600`. Record the 45-volume inventory and current image
digest. Do not print secrets.

- [ ] **Step 2: Build and smoke-test locally**

Run: `docker build -t qukuyouxu:2.0.8 .`

Expected: successful build.

Run the image on an unused local port with a temporary data directory, wait for
healthy, assert `/healthz` is `2.0.8`, then stop it.

- [ ] **Step 3: Push commit/tag and wait for release workflow**

Push the implementation branch/commit to `main` using the user-approved release
workflow, create and push tag `v2.0.8`, then verify GitHub Actions tests and the
multi-architecture GHCR manifest succeed.

- [ ] **Step 4: Replace TrueNAS overlays with the release image**

Update the existing `plex-music-preview` custom app without changing `/data`,
ports, environment, security or resource limits. Remove exactly the 44 bind
mounts whose destinations start with
`/usr/local/lib/python3.12/site-packages/helper/` and retain the `/data` mount.

- [ ] **Step 5: Verify live behavior and resource bounds**

Assert `RUNNING`, `/healthz` version `2.0.8`, rendered settings version `v2.0.8`,
all page and static requests return success, the volume inventory contains only
`/data`, and cgroup limits remain `805306368`, `100000 100000`, and `128`.
Sample CPU/memory for at least 10 seconds and assert no OOM event.

- [ ] **Step 6: Commit final deployment evidence if repository policy requires it**

Do not commit the rollback YAML, secrets, runtime database or logs. Record only
non-sensitive verification commands/results in the execution ledger.
