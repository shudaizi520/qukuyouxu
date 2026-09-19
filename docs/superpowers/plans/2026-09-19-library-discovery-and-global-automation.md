# Library Discovery and Global Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace theme-first onboarding with library-derived playlist discovery and consolidate all automatic work into one three-row global scheduler.

**Architecture:** The existing safe preview/apply and managed-playlist lifecycle remain authoritative. A focused discovery policy computes an adaptive eligibility threshold and exposes only eligible preview groups; a new global automation module owns schedule configuration while `ProfileRuntime` remains the single serial executor for isolated profiles.

**Tech Stack:** Python 3.11+, FastAPI, SQLite state store, vanilla JavaScript/HTML/CSS, pytest/unittest.

**Spec:** `docs/superpowers/specs/2026-09-19-library-discovery-onboarding-design.md` and `docs/superpowers/specs/2026-09-19-global-automation-hub-design.md`

## Global Constraints

- Never delete music files or take over hand-created Plex playlists.
- Preserve all existing caches, QQ authorization, profile isolation, playback learning, and managed playlists.
- Use one global serial mutation queue; never run concurrent Plex scans or writes.
- The automatic task page has exactly three compact rows and is the only automatic-task control surface.
- The manual `检查新增歌曲` action remains on the library page.
- All new behavior is covered by tests written and observed failing before production changes.

## Review Focus

- A 3,072-track library uses a 16-track threshold, while small and huge libraries clamp at 10 and 30.
- A fresh profile never shows preselected themes before analysis and never creates an ineligible playlist.
- Existing managed playlists survive migration even if they are below the new discovery threshold.
- Missed schedules run at most once after restart and same-time work runs library → smart → daily.
- One ineligible or failing profile cannot block eligible profiles or leak state across profile scopes.

---

### Task 1: Adaptive discovery policy and workflow contract

**Files:**
- Create: `src/helper/library_discovery.py`
- Modify: `src/helper/engine.py`
- Modify: `src/helper/workflow_v0317.py`
- Test: `tests/test_v114_library_discovery.py`

**Interfaces:**
- Produces: `discovery_min_tracks(library_count: int) -> int` and `eligible_discovery_groups(plan: dict, managed: dict) -> list[dict]`.
- Produces: workflow fields `discovery.phase`, `discovery.threshold`, and `review.groups` containing only eligible or already-managed groups.

- [ ] **Step 1: Write failing policy tests**

```python
def test_adaptive_threshold_is_half_percent_clamped():
    assert discovery_min_tracks(100) == 10
    assert discovery_min_tracks(3072) == 16
    assert discovery_min_tracks(10000) == 30

def test_unmanaged_sparse_groups_are_hidden_but_existing_managed_groups_remain():
    plan = {"library_count": 3072, "groups": [
        {"id": "sparse", "desired": list(range(15)), "blocked": []},
        {"id": "kept", "desired": list(range(3)), "blocked": []},
    ]}
    visible = eligible_discovery_groups(plan, {"kept": {"id": "plex-1"}})
    assert [row["id"] for row in visible] == ["kept"]
```

- [ ] **Step 2: Run the tests and confirm missing-module/API failures**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_v114_library_discovery.py -q`

Expected: FAIL because `helper.library_discovery` does not exist.

- [ ] **Step 3: Implement the minimal policy and connect it to preview/status**

```python
def discovery_min_tracks(library_count):
    return max(10, min(30, math.ceil(max(0, int(library_count or 0)) * 0.005)))

def eligible_discovery_groups(plan, managed):
    threshold = discovery_min_tracks(plan.get("library_count", 0))
    return [row for row in plan.get("groups", [])
            if row.get("id") in managed or (not row.get("blocked") and len(row.get("desired", [])) >= threshold)]
```

Update engine preview blocking to use the adaptive threshold and status serialization to hide ineligible fresh candidates while retaining existing managed groups.

- [ ] **Step 4: Add workflow-state tests**

```python
def test_fresh_status_starts_in_analyze_phase_without_theme_choices():
    workflow = build_workflow_status(store, engine, qq_status)["workflow"]
    assert workflow["discovery"]["phase"] == "before_analysis"
    assert workflow["review"] is None

def test_completed_preview_exposes_only_eligible_candidates():
    store.set("plan", plan_with_15_and_16_track_groups)
    workflow = build_workflow_status(store, engine, qq_status)["workflow"]
    assert [row["id"] for row in workflow["review"]["groups"]] == ["eligible"]
```

- [ ] **Step 5: Run task tests and full suite**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_v114_library_discovery.py tests/test_new_user_onboarding.py tests/test_v0418_workflow_state.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/helper/library_discovery.py src/helper/engine.py src/helper/workflow_v0317.py tests/test_v114_library_discovery.py
git commit -m "feat: derive category playlists from library analysis"
```

### Task 2: Library-first interface and managed playlist lifecycle

**Files:**
- Modify: `src/helper/static/home.html`
- Modify: `src/helper/static/home.js`
- Modify: `src/helper/static/theme_home.js`
- Modify: `src/helper/static/product.css`
- Test: `tests/test_v114_library_discovery_ui.py`
- Test: `tests/test_v103_ui_simplification.py`

**Interfaces:**
- Consumes: Task 1 `workflow.discovery` and filtered `workflow.review.groups`.
- Produces: three UI phases: analyze, choose candidates, managed playlists.

- [ ] **Step 1: Write failing markup and behavior tests**

```python
def test_library_page_has_one_analysis_action_and_no_theme_picker():
    html = HOME.read_text()
    assert 'id="analyzeLibrary"' in html
    assert 'id="themeChoices"' not in html
    assert 'id="autoToggle"' not in html
    assert 'id="incrementalAction"' in html

def test_candidate_rows_show_name_count_and_view_action_only():
    script = HOME_JS.read_text()
    assert "发现可创建的歌单" in script
    assert "创建选中歌单" in script
    assert "查看歌曲" in script
```

- [ ] **Step 2: Run and verify expected failures**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_v114_library_discovery_ui.py tests/test_v103_ui_simplification.py -q`

Expected: FAIL on old theme picker and automatic toggle markup.

- [ ] **Step 3: Implement the three compact phases**

Remove the permanent theme preference card and library automatic switch. Reuse the existing progress, pause, evidence, safe apply, managed delete and restore APIs. Before analysis show `分析曲库`; after analysis show eligible candidates selected by default; after creation show `我的分类歌单` and the manual `检查新增歌曲` action.

- [ ] **Step 4: Run focused UI tests**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_v114_library_discovery_ui.py tests/test_library_controls_v0330.py tests/test_v103_ui_simplification.py tests/test_v0414_ui_hierarchy.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/helper/static/home.html src/helper/static/home.js src/helper/static/theme_home.js src/helper/static/product.css tests/test_v114_library_discovery_ui.py tests/test_v103_ui_simplification.py
git commit -m "feat: simplify library discovery workflow"
```

### Task 3: Global automation schedule model and migration

**Files:**
- Create: `src/helper/automation.py`
- Modify: `src/helper/profile_runtime.py`
- Modify: `src/helper/web.py`
- Modify: `src/helper/workflow_v0317.py`
- Modify: `src/helper/smart_mix_web.py`
- Test: `tests/test_v114_global_automation.py`

**Interfaces:**
- Produces: `automation_settings(base_store, registry, runtime, now=None) -> dict`.
- Produces: `save_automation_settings(base_store, payload, now=None) -> dict`.
- Produces: ordered due work from `ProfileRuntime.run_due()` in `library`, `smart_mixes`, `daily` order.
- HTTP: `GET /api/automation` and `POST /api/automation` with all three rows in one payload.

- [ ] **Step 1: Write failing schedule and migration tests**

```python
def test_default_global_schedule_has_three_jobs():
    result = automation_settings(store, registry, runtime, now)
    assert result["daily"] == {"enabled": False, "hour": 6}
    assert result["smart"] == {"enabled": False, "interval_days": 7, "hour": 3}
    assert result["library"] == {"enabled": False, "hour": 0}

def test_legacy_profile_switches_migrate_to_global_enabled_state():
    scoped.set("daily_settings", {"enabled": True, "hour": 8})
    assert automation_settings(store, registry, runtime, now)["daily"]["enabled"] is True

def test_same_time_jobs_run_library_then_smart_then_daily():
    runtime.run_due(now)
    assert calls == ["library", "smart_mixes", "daily"]

def test_missed_slots_run_once_and_advance_to_future():
    runtime.run_due(now_after_three_missed_days)
    runtime.run_due(now_after_three_missed_days + 1)
    assert calls.count("library") == 1
```

- [ ] **Step 2: Run and verify missing-module/API failures**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_v114_global_automation.py -q`

Expected: FAIL because `helper.automation` and routes do not exist.

- [ ] **Step 3: Implement global settings and deterministic slots**

Store one base key `automation_settings_v1`:

```python
{
  "version": 1,
  "daily": {"enabled": False, "hour": 6},
  "smart": {"enabled": False, "interval_days": 7, "hour": 3},
  "library": {"enabled": False, "hour": 0},
  "migrated": True,
}
```

Validate hours 0–23 and smart intervals in `{3, 5, 7, 10, 14, 20}`. Keep per-profile task timestamps and prerequisites, but read enable/time from the global settings. Advance the stored due slot to the next future occurrence after every attempt so a restart never loops through missed history.

- [ ] **Step 4: Add isolation/failure tests**

```python
def test_ineligible_profile_is_skipped_without_blocking_eligible_profile():
    runtime.run_due(now)
    assert calls == [("eligible", "daily")]

def test_one_profile_failure_does_not_stop_remaining_profiles():
    results = runtime.run_due(now)
    assert any(row.get("error") for row in results)
    assert ("second", "library") in calls
```

- [ ] **Step 5: Run scheduler and route tests**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_v114_global_automation.py tests/test_v040_runtime.py tests/test_v0424_smart_mix_auto.py tests/test_v047_batch_daily.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/helper/automation.py src/helper/profile_runtime.py src/helper/web.py src/helper/workflow_v0317.py src/helper/smart_mix_web.py tests/test_v114_global_automation.py
git commit -m "feat: centralize automatic task scheduling"
```

### Task 4: Three-row automation UI and duplicate control removal

**Files:**
- Modify: `src/helper/static/settings.html`
- Modify: `src/helper/static/settings.js`
- Modify: `src/helper/static/daily.html`
- Modify: `src/helper/static/daily.js`
- Modify: `src/helper/static/mixes.html`
- Modify: `src/helper/static/mixes.js`
- Modify: `src/helper/static/product.css`
- Test: `tests/test_v114_automation_ui.py`
- Test: `tests/test_v0423_settings_redesign.py`

**Interfaces:**
- Consumes: Task 3 `/api/automation` GET/POST.
- Produces: exactly three automation rows and no duplicate automatic switches elsewhere.

- [ ] **Step 1: Write failing UI contract tests**

```python
def test_automation_panel_has_exactly_three_rows():
    page = parse_settings()
    assert page.automation_row_ids == ["dailyAutomation", "smartAutomation", "libraryAutomation"]
    assert 'id="smartIntervalDays"' in page.html
    assert page.html.count('class="automation-hour"') == 3

def test_other_pages_have_no_automatic_switches():
    assert 'id="dailyToggle"' not in DAILY.read_text()
    assert 'id="smartMixAuto"' not in MIXES.read_text()
    assert 'id="autoToggle"' not in HOME.read_text()
    assert 'id="batchDailyAuto"' not in SETTINGS.read_text()
```

- [ ] **Step 2: Run and verify failures against old controls**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_v114_automation_ui.py tests/test_v0423_settings_redesign.py -q`

Expected: FAIL because duplicate controls remain and smart schedule controls are absent.

- [ ] **Step 3: Implement compact three-row controls**

Each row contains one title, its switch and inline select controls. Save the full validated schedule through `/api/automation`. Remove old event handlers and old automatic-toggle markup while preserving all manual action handlers.

- [ ] **Step 4: Run all affected UI tests**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_v114_automation_ui.py tests/test_v0423_settings_redesign.py tests/test_v103_ui_simplification.py tests/test_v0424_daily_ui.py tests/test_v0424_mixes_ui.py tests/test_v0414_ui_hierarchy.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/helper/static/settings.html src/helper/static/settings.js src/helper/static/daily.html src/helper/static/daily.js src/helper/static/mixes.html src/helper/static/mixes.js src/helper/static/product.css tests/test_v114_automation_ui.py tests/test_v0423_settings_redesign.py
git commit -m "feat: add three-row automatic task center"
```

### Task 5: Upgrade, integration and release verification

**Files:**
- Modify: `src/helper/__init__.py`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Test: `tests/test_v114_upgrade_integration.py`
- Test: `tests/test_release_version.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: one coherent upgrade with preserved data and updated public documentation.

- [ ] **Step 1: Write failing upgrade tests**

```python
def test_upgrade_preserves_managed_playlists_cache_and_learning_settings():
    upgraded = open_existing_fixture()
    assert upgraded.get("managed") == original_managed
    assert upgraded.get("cache") == original_cache
    assert upgraded.get("product_settings")["behavior_enabled"] is False

def test_manual_new_song_action_remains_available_after_automation_migration():
    assert 'id="incrementalAction"' in HOME.read_text()
```

- [ ] **Step 2: Run and verify version/documentation failures**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_v114_upgrade_integration.py tests/test_release_version.py -q`

Expected: FAIL until the version and changelog are updated.

- [ ] **Step 3: Update release metadata and user-facing documentation**

Document the library-first workflow, three-row automation center, global scope, Beijing time, serial execution and preserved manual actions. Bump the patch version consistently across the application and static pages.

- [ ] **Step 4: Run the complete suite**

Run: `PYTHONPATH=. .venv/bin/pytest -q`

Expected: all tests pass with no warnings or collection errors.

- [ ] **Step 5: Run repository and container checks**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_repository_hygiene.py tests/test_container_contract.py tests/test_github_workflows.py tests/test_release_blockers.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/helper/__init__.py CHANGELOG.md README.md tests/test_v114_upgrade_integration.py tests/test_release_version.py
git commit -m "release: prepare library discovery and automation update"
```
