# QQ-Inspired Management Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved compact, QQ-inspired visual system across all six management pages while preserving every route, control, and business interaction.

**Architecture:** Keep existing page JavaScript and IDs, move management macro layout into `management-shell.css`, and keep interaction states in `ui-components.css`. Use shared compact primitives plus page-scoped widths and fixed grids; remove or narrow conflicting legacy rules instead of adding another override layer.

**Tech Stack:** Static HTML, vanilla JavaScript, CSS custom properties, Python 3.14, pytest, Node test runner, Playwright.

**Spec:** `docs/superpowers/specs/2026-09-24-qq-inspired-management-redesign.md`

## Global Constraints

- Preserve every route, field, form ID, JavaScript hook, dialog, API call, and live feedback region.
- Keep the left rail, search, player, and six management routes.
- Use only semantic `--app-*` tokens; introduce no raw production colors.
- Green represents persistent state, not ordinary action buttons.
- Settings use whitespace and fixed grids, not card borders or separator lattices.
- Page widths are content-driven and left-aligned; pages need not share one width.
- Automatic task labels, recurrence, time, and switches stay in fixed columns.
- `下载缺失歌曲` sits beside the missing count with no persistent background.
- Do not deploy, push, or merge; provide an isolated preview.

## Review Focus

- Long Chinese labels must not shift time or switch columns or cause overflow; Task 2 owns this.
- Dynamic user rows must inherit the compact hover-only layout; Tasks 1–2 own this.
- TXT, CSV, image, and copy exports must remain wired after relocation; Task 3 owns this.
- Hidden panels, dialogs, disabled controls, and review toolbars must remain usable; Tasks 2–4 own this.
- Light, warm, night, and embedded pages must preserve readable focus, danger, disabled, and state styling; Task 5 owns this.

---

### Task 1: Shared Compact Shell and Neutral Actions

**Files:**
- Create: `tests/test_qq_management_layout.py`
- Modify: `src/helper/static/management-shell.css`
- Modify: `src/helper/static/ui-components.css`
- Modify: `tests/test_management_workspace.py`
- Modify: `tests/test_icon_state_contract.py`

**Interfaces:**
- Consumes: `management-shell.js` decoration with `.management-action`
- Produces: 30px neutral actions, 32px fields, hover-only fill, danger semantics, and shared content variables for Tasks 2–4

- [ ] **Step 1: Write failing shared contracts**

Create `tests/test_qq_management_layout.py` with the `_rule()` helper from `tests/test_management_workspace.py` and tests asserting:

```python
action = _rule(_text("management-shell.css"), "body[data-management-page] .management-action")
primary = _rule(_text("ui-components.css"), "body[data-management-page] .management-action.primary")
hover = _rule(_text("ui-components.css"), "body[data-management-page] .management-action:hover:not(:disabled)")
assert action["height"] == "30px"
assert action["min-width"] == "0"
assert action["border-radius"] == "4px"
assert primary["background"] == "transparent"
assert hover["background"] == "var(--app-hover)"
```

Also assert fields use `min-height:32px`, `border-radius:5px`, and `.management-stage` remains left aligned.

- [ ] **Step 2: Verify RED**

Run `PYTHONPATH=src:. .venv/bin/python -m pytest tests/test_qq_management_layout.py tests/test_management_workspace.py tests/test_icon_state_contract.py -q`.

Expected: FAIL on current 40px actions/fields, 9px radius, and green primary fill.

- [ ] **Step 3: Implement shared primitives**

Set `.management-stage` to `width:100%; margin:0 auto 0 0; padding:0 30px 48px`. Set `.management-action` to `min-width:0`, `height/min-height:30px`, `padding:0 10px`, `border-radius:4px`, and `font-weight:500`. Set text fields/selects to 32px height, a 1px semantic line border, 5px radius, and transparent background.

In `ui-components.css`, make management `.primary` use `border-color:var(--app-line); background:transparent; color:var(--app-text)` and use `var(--app-hover)` on hover. Keep danger red with transparent rest state and `var(--app-danger-soft)` hover. Preserve focus-visible, disabled, and `currentColor` icon behavior.

Update obsolete geometry/state assertions in existing tests.

- [ ] **Step 4: Verify GREEN and commit**

Run the Step 2 command, then:

```bash
git add src/helper/static/management-shell.css src/helper/static/ui-components.css tests/test_qq_management_layout.py tests/test_management_workspace.py tests/test_icon_state_contract.py
git diff --cached --check
git commit -m "style: establish compact management primitives"
```

---

### Task 2: System Settings and Fixed Column Alignment

**Files:**
- Modify: `src/helper/static/settings.html`
- Modify: `src/helper/static/management-shell.css`
- Modify: `src/helper/static/product.css`
- Modify: `tests/test_qq_management_layout.py`
- Modify: `tests/test_settings_visual_refresh.py`

**Interfaces:**
- Consumes: Task 1 action/field primitives
- Produces: `.settings-user-section`, `.settings-automation-section`, capped user rows, and fixed automatic-task columns

- [ ] **Step 1: Write failing settings contracts**

Assert that `openAddUser` remains inside the first heading div of `#people`, `.settings-user-section` has `max-width:760px`, and user header/rows use `grid-template-columns:minmax(220px,1fr) repeat(3,92px) 92px`. Assert automatic rows use `grid-template-columns:120px minmax(0,1fr)` and controls use `grid-template-columns:72px 72px 30px` with `justify-content:start`. Assert settings cards/user rows have transparent backgrounds and only user-row hover uses `var(--management-hover)`.

- [ ] **Step 2: Verify RED**

Run `PYTHONPATH=src:. .venv/bin/python -m pytest tests/test_qq_management_layout.py tests/test_settings_visual_refresh.py tests/test_settings_v2.py tests/test_v042_settings.py -q`.

Expected: FAIL because settings uses a 1040px card layout and a three-card automatic-task grid.

- [ ] **Step 3: Implement settings structure and ownership**

Add `settings-user-section` to `#people` and `settings-automation-section` to the automatic-task section without renaming IDs. Keep `openAddUser` beside the `用户管理` heading.

In `management-shell.css`, cap `.settings-workspace` and `.settings-user-section` at 760px; make cards transparent/borderless with 30px whitespace gaps; define the user grid above; make user rows 40px high with hover-only fill; make `.automation-grid` a single column capped at 520px; and use the fixed row/control grids above.

Remove or narrow duplicated `.settings-user-header`, `#managedUserList .settings-user-row`, `.automation-row`, and `.automation-controls` layout ownership in `product.css`; retain dialog, avatar, and non-layout component rules.

Update `tests/test_settings_visual_refresh.py` from the obsolete card-grid contract to the approved row contract.

- [ ] **Step 4: Verify GREEN and commit**

Run the Step 2 command, then:

```bash
git add src/helper/static/settings.html src/helper/static/management-shell.css src/helper/static/product.css tests/test_qq_management_layout.py tests/test_settings_visual_refresh.py
git diff --cached --check
git commit -m "style: align and compact system settings"
```

---

### Task 3: Import and Smart Playlist Workspaces

**Files:**
- Modify: `src/helper/static/external.html`
- Modify: `src/helper/static/external-workspace.css`
- Modify: `src/helper/static/management-shell.css`
- Modify: `src/helper/static/mixes.html`
- Modify: `tests/test_qq_management_layout.py`
- Modify: `tests/test_management_workspace.py`
- Modify: `tests/test_v1416_ui_audit.py`

**Interfaces:**
- Consumes: Task 1 action grammar and existing export IDs
- Produces: compact source/Plex rows, download control beside missing count, result table, and compact smart rules/list

- [ ] **Step 1: Write failing import/smart contracts**

Assert `.external-import-form` and `.external-command-bar` have `max-width:680px`, `.external-detail-card` has `max-width:940px`, and `replenishmentCard` plus `downloadMenu` occur inside `.external-counts`. Assert `.external-missing-download` is transparent. Assert smart settings/list have `max-width:820px`, no border, and `.mix-row` has no persistent background/border.

- [ ] **Step 2: Verify RED**

Run `PYTHONPATH=src:. .venv/bin/python -m pytest tests/test_qq_management_layout.py tests/test_management_workspace.py tests/test_v1412_ui_feedback.py tests/test_v1413_layout_contract.py tests/test_v1416_ui_audit.py tests/test_v130_external_ui.py tests/test_v0429_mixes_simplification.py -q`.

Expected: FAIL because export actions are a separate full-width command bar and smart rows remain ruled/full-width.

- [ ] **Step 3: Relocate exports without changing hooks**

Place this immediately after the missing-count button inside `.external-counts`:

```html
<span id="replenishmentCard" class="external-missing-download" hidden>
 <button id="copyMissing" class="external-text-action" type="button">复制清单</button>
 <details id="downloadMenu" class="external-more"><summary class="external-text-action">下载缺失歌曲</summary><div><a id="downloadText" class="link" href="#">TXT</a><a id="downloadCsv" class="link" href="#">CSV</a><button id="downloadImage" class="external-text-action" type="button">长图</button></div></details>
</span>
```

Delete the old separate replenishment section. Preserve all IDs and `external.js` wiring.

- [ ] **Step 4: Implement compact macro layouts**

In `management-shell.css`, cap import/source settings at 680px, results at 940px, smart settings/list at 820px, and remove separator borders. Use fixed 92px labels, compact controls, transparent rows, and hover-only row fill.

In `external-workspace.css`, retain track/table internals but remove/narrow old full-width import/card/command ownership and command-bar separator pseudo-elements. Add only semantic section classes to `mixes.html` if needed; do not alter IDs.

Update obsolete full-width/ruled assertions while keeping all event-hook assertions.

- [ ] **Step 5: Verify GREEN, interactions, and commit**

Run the Step 2 command and `node --test tests/settings_interactions.test.js`, then:

```bash
git add src/helper/static/external.html src/helper/static/external-workspace.css src/helper/static/management-shell.css src/helper/static/mixes.html tests/test_qq_management_layout.py tests/test_management_workspace.py tests/test_v1416_ui_audit.py
git diff --cached --check
git commit -m "style: compact import and smart playlist pages"
```

---

### Task 4: Library, Status, and Appearance Pages

**Files:**
- Modify: `src/helper/static/management-shell.css`
- Modify: `src/helper/static/home.html`
- Modify: `src/helper/static/status.html`
- Modify: `src/helper/static/appearance.html`
- Modify: `tests/test_qq_management_layout.py`
- Modify: `tests/test_management_workspace.py`
- Modify: `tests/test_appearance_pages_v152.py`

**Interfaces:**
- Consumes: Task 1 action/status grammar
- Produces: unboxed library metrics, hover-only playlist rows, inline status groups, vertical status sections, and borderless theme tiles

- [ ] **Step 1: Write failing page contracts**

Assert library overview has `max-width:850px` and `border:0`; status health has `max-width:680px`, `border:0`, and `gap:30px`; and appearance cards have `border:0`, transparent rest background, and `var(--management-hover)` selected background.

- [ ] **Step 2: Verify RED**

Run `PYTHONPATH=src:. .venv/bin/python -m pytest tests/test_qq_management_layout.py tests/test_management_workspace.py tests/test_appearance_pages_v152.py tests/test_v120_status_ui.py tests/test_v0415_library_maintenance.py -q`.

Expected: FAIL because library/status summaries and theme cards still have persistent frames.

- [ ] **Step 3: Implement remaining layouts**

In `management-shell.css`, cap library sections at 850px, render metrics as a flex group without outer/internal borders, make managed rows transparent with hover-only fill, cap status groups at 680px without borders, stack status sections vertically, cap disclosure rows at 620px, and remove unselected theme-card borders/backgrounds. Add only semantic classes to HTML if selectors require them; preserve IDs and element order used by scripts.

Update obsolete ruled/card assertions.

- [ ] **Step 4: Verify GREEN and commit**

Run the Step 2 command, then:

```bash
git add src/helper/static/management-shell.css src/helper/static/home.html src/helper/static/status.html src/helper/static/appearance.html tests/test_qq_management_layout.py tests/test_management_workspace.py tests/test_appearance_pages_v152.py
git diff --cached --check
git commit -m "style: compact library status and appearance pages"
```

---

### Task 5: Regression, Screenshot Matrix, and Preview

**Files:**
- Create: `docs/management-redesign-verification.md`
- Modify: only files required by a failing regression, using RED→GREEN
- Test: full Python suite, full JavaScript suite, Playwright matrix

**Interfaces:**
- Consumes: Tasks 1–4 and `tools/capture_theme_matrix.py`
- Produces: green suites, 37 screenshots, verification record, and an isolated preview URL

- [ ] **Step 1: Run full suites**

Run:

```bash
PYTHONPATH=src:. .venv/bin/python -m pytest -q
node --test tests/*.test.js
```

Expected: zero failures. Any regression gets a focused failing test before its fix.

- [ ] **Step 2: Start isolated preview**

Run `PYTHONPATH=src .venv/bin/python -m helper.web --host 0.0.0.0 --port 19513` from the worktree.

Expected: the worktree app responds on port 19513 without replacing the deployed service.

- [ ] **Step 3: Capture and inspect the matrix**

Run:

```bash
PCH_VISUAL_BASE_URL=http://127.0.0.1:19513 PYTHONPATH=src:. .venv/bin/python tools/capture_theme_matrix.py --mode management-redesign --output .artifacts/management-redesign
```

Expected: `captured=37 mode=management-redesign`.

Inspect night-theme 1920×1080 screenshots for all six pages plus light embedded settings. Reject horizontal overflow, clipped actions, persistent row fills, separator lattices, stretched modules, or misaligned automatic-task columns.

- [ ] **Step 4: Record and commit evidence**

Create `docs/management-redesign-verification.md` with exact suite counts, capture count, preview URL, inspected paths, and accepted limitations; include no credentials. Then:

```bash
git add -f docs/management-redesign-verification.md
git diff --cached --check
git commit -m "docs: record management redesign verification"
```

- [ ] **Step 5: Final hygiene**

Run `git diff --check $(git merge-base main HEAD)..HEAD` and `git status --short`.

Expected: no whitespace errors and no tracked uncommitted changes.

## Self-Review

- Spec coverage: all six pages, shared action grammar, fixed-column alignment, download placement, responsive behavior, semantic theme constraints, and preview capture have owning tasks.
- Placeholder scan: every implementation and verification step is concrete.
- Type/ID consistency: Task 3 preserves every export ID consumed by `external.js`; Tasks 2–4 consume Task 1's shared action contract.
- Review Focus mapping: label overflow belongs to Task 2, dynamic users to Tasks 1–2, exports to Task 3, hidden states to Tasks 2–4, and theme/embedded verification to Task 5.
