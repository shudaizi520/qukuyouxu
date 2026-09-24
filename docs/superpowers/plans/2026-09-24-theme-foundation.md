# Theme Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing application safe for many static and background-only dynamic themes without changing current business behavior or intentionally redesigning the six management pages.

**Architecture:** Preserve the current HTML and business JavaScript while separating theme tokens, shared component states, management-shell layout, and background rendering into explicit owners. Migrate existing raw UI colors and duplicate theme selectors instead of layering more overrides, then gate completion on automated contracts and a real-browser screenshot matrix.

**Tech Stack:** Python 3.14, FastAPI-rendered static HTML, vanilla JavaScript, CSS custom properties, pytest/unittest, Playwright Python 1.63.0, Pillow 12.3.0.

**Spec:** `docs/superpowers/specs/2026-09-24-theme-foundation-design.md`

## Global Constraints

- Preserve every existing business function, route, page field, and interaction.
- Do not redesign settings-page content or change the six management-page information architectures.
- Do not add a production theme or a production animation in this plan.
- Dynamic-theme infrastructure may affect only the background layer; controls and icons remain static.
- Every SVG icon consumes `currentColor`; pages may not define their own icon hover color.
- Migrating a rule requires removing or narrowing its old definition in the same task; no end-of-file override patches.
- Invalid or deleted theme IDs fall back to `light`.
- Embedded management pages do not render a second animated background.
- `prefers-reduced-motion: reduce` disables continuous background motion.
- No task may be declared complete without its stated tests and reviewer check.
- The current dirty worktree is the intended baseline. Do not reset, clean, stash, or discard it.

## Review Focus

- A saved unknown theme ID must render `light`, update storage safely, and never leave an unthemed first frame; Task 2 owns this test.
- A blocked `localStorage` or inaccessible iframe must not prevent local theme application; Task 2 owns this test.
- Default, hover, focus-visible, active, disabled, and danger icon states must remain readable in all three palettes; Tasks 3 and 4 own these tests.
- The background layer must not intercept pointer input, duplicate inside embedded pages, or animate under reduced-motion; Task 6 owns these tests.
- Cache-busted asset URLs must resolve to one release version even though source HTML uses one neutral placeholder; Task 5 owns this test.

---

### Task 0: Preserve the Current Dirty Baseline

**Files:**
- Create: `/tmp/qukuyouxu-theme-foundation-baseline/` (local safety copy, not committed)
- Modify: none
- Test: current Python and JavaScript suites

**Interfaces:**
- Consumes: the current working tree, including every tracked modification and untracked source/test file
- Produces: branch `theme-foundation`, a baseline commit, a test log, and a recoverable filesystem snapshot

- [ ] **Step 1: Record the exact starting state**

Run:

```bash
git status --short > /tmp/qukuyouxu-theme-foundation-status.txt
git diff --binary > /tmp/qukuyouxu-theme-foundation-tracked.patch
git ls-files --others --exclude-standard > /tmp/qukuyouxu-theme-foundation-untracked.txt
```

Expected: all three commands succeed; no working-tree file changes.

- [ ] **Step 2: Create a recoverable copy of every currently changed path**

Run from the repository root:

```bash
mkdir -p /tmp/qukuyouxu-theme-foundation-baseline
while IFS= read -r path; do
  [ -e "$path" ] || continue
  mkdir -p "/tmp/qukuyouxu-theme-foundation-baseline/$(dirname "$path")"
  cp -a "$path" "/tmp/qukuyouxu-theme-foundation-baseline/$path"
done < <(git status --short | sed 's/^...//')
```

Expected: copied file count equals the number of existing paths in `git status --short`.

- [ ] **Step 3: Run the baseline Python suite**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
```

Expected: PASS. If a pre-existing failure appears, record the exact test and stop before changing product code.

- [ ] **Step 4: Run the baseline JavaScript suite in the project-supported Node environment**

Run:

```bash
node --test tests/appearance.test.js tests/settings_interactions.test.js tests/playlist_artwork.test.js tests/playlist_sections.test.js tests/playlist_player_track.test.js tests/playlist_now_playing.test.js tests/playlist_playback_mode.test.js tests/playlist_visualizer.test.js
```

Expected: PASS. The current Ubuntu shell has no Node binary, so execute this through the same Node-capable CI/container environment used by `.github/workflows/test.yml`; do not silently skip it.

- [ ] **Step 5: Create the feature branch and baseline commit**

Run:

```bash
git switch -c theme-foundation
git add -A
git diff --cached --check
git commit -m "chore: checkpoint current UI baseline"
```

Expected: the branch contains the exact user-visible starting state, and `git status --short` is empty.

### Task 1: Establish Repeatable Real-Browser Visual Baselines

**Files:**
- Create: `requirements-visual.txt`
- Create: `tools/capture_theme_matrix.py`
- Create: `tests/test_visual_capture_tool.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: environment variables `PCH_VISUAL_BASE_URL`, `PCH_VISUAL_USERNAME`, `PCH_VISUAL_PASSWORD`
- Produces: `capture_matrix(base_url: str, output: Path, mode: str) -> list[Path]` and `compare_images(baseline: Path, current: Path, diff: Path, threshold: int = 12) -> float`

- [ ] **Step 1: Write the failing visual-tool contract tests**

Add tests that use generated 4×4 PNG files and a fake theme/page matrix:

```python
from pathlib import Path
from PIL import Image
from tools.capture_theme_matrix import compare_images, matrix_cases


def test_matrix_contains_six_pages_three_themes_and_two_viewports():
    cases = matrix_cases()
    assert len(cases) == 36
    assert {case.theme for case in cases} == {"light", "warm", "night"}
    assert {case.page for case in cases} == {
        "settings", "external", "mixes", "library", "status", "appearance"
    }
    assert {case.viewport for case in cases} == {(1366, 768), (1920, 1080)}


def test_pixel_comparison_writes_a_diff_and_reports_changed_ratio(tmp_path: Path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    diff = tmp_path / "diff.png"
    Image.new("RGB", (4, 4), "white").save(first)
    changed = Image.new("RGB", (4, 4), "white")
    changed.putpixel((0, 0), (0, 0, 0))
    changed.save(second)
    assert compare_images(first, second, diff, threshold=12) == 1 / 16
    assert diff.exists()
```

- [ ] **Step 2: Run the tests and verify the missing module failure**

Run:

```bash
PYTHONPATH=src:. .venv/bin/python -m pytest tests/test_visual_capture_tool.py -q
```

Expected: FAIL because `tools.capture_theme_matrix` does not exist.

- [ ] **Step 3: Add pinned visual dependencies and install them**

Create `requirements-visual.txt`:

```text
playwright==1.63.0
Pillow==12.3.0
```

Run:

```bash
.venv/bin/python -m pip install -r requirements-visual.txt
PLAYWRIGHT_BROWSERS_PATH=.playwright .venv/bin/python -m playwright install chromium
```

Expected: Chromium installs into the repository-local ignored `.playwright/` directory. Network/download approval is required at execution time.

- [ ] **Step 4: Implement the matrix and pixel comparator**

Use these exact public structures:

```python
from dataclasses import dataclass
from pathlib import Path
from PIL import Image, ImageChops


@dataclass(frozen=True)
class VisualCase:
    page: str
    route: str
    theme: str
    viewport: tuple[int, int]


def matrix_cases() -> list[VisualCase]:
    routes = {
        "settings": "/settings", "external": "/external",
        "mixes": "/mixes", "library": "/library",
        "status": "/status", "appearance": "/appearance",
    }
    return [
        VisualCase(page, route, theme, viewport)
        for page, route in routes.items()
        for theme in ("light", "warm", "night")
        for viewport in ((1366, 768), (1920, 1080))
    ]


def compare_images(baseline: Path, current: Path, diff: Path, threshold: int = 12) -> float:
    before = Image.open(baseline).convert("RGB")
    after = Image.open(current).convert("RGB")
    if before.size != after.size:
        raise ValueError(f"image sizes differ: {before.size} != {after.size}")
    delta = ImageChops.difference(before, after)
    changed = sum(1 for pixel in delta.getdata() if max(pixel) > threshold)
    diff.parent.mkdir(parents=True, exist_ok=True)
    delta.save(diff)
    return changed / (before.width * before.height)
```

Implement `capture_matrix()` with Playwright sync API. It must:

- open `/`, wait for `body[data-auth-state]`, and log in only when `#loginForm` is visible;
- read credentials only from environment variables and never print them;
- set `pch-appearance-theme` with `page.evaluate()` before visiting each route;
- visit each management route directly for the six-page matrix;
- also capture `/` with `/settings` loaded in `#playlistToolFrame` to verify sidebar/player containment;
- inject `*{animation:none!important;transition:none!important}` before each screenshot;
- mask `#version`, timestamps, progress labels, and live status messages;
- save files under `.artifacts/theme-foundation/` using the concrete path pattern `baseline/1366x768/light/settings.png` (with the selected mode, viewport, theme, and page substituted).

- [ ] **Step 5: Ignore downloaded browsers and generated visual artifacts**

Append exactly:

```gitignore
.playwright/
.artifacts/theme-foundation/
```

- [ ] **Step 6: Run unit tests**

Run:

```bash
PYTHONPATH=src:. .venv/bin/python -m pytest tests/test_visual_capture_tool.py -q
```

Expected: PASS.

- [ ] **Step 7: Capture the immutable before-state**

Run with credentials supplied through the environment, not command-line arguments:

```bash
PLAYWRIGHT_BROWSERS_PATH=.playwright PCH_VISUAL_BASE_URL=http://192.168.50.99:9512 \
  .venv/bin/python tools/capture_theme_matrix.py --mode baseline
```

Expected: 37 PNG files: 36 direct-page cases plus one embedded settings containment case. Inspect a representative image from each theme with the local image viewer before continuing.

- [ ] **Step 8: Commit the visual harness**

```bash
git add .gitignore requirements-visual.txt tools/capture_theme_matrix.py tests/test_visual_capture_tool.py
git commit -m "test: add theme visual regression harness"
```

### Task 2: Make Theme Metadata a Single Source of Truth

**Files:**
- Modify: `src/helper/static/appearance.js`
- Modify: `src/helper/static/appearance.html`
- Modify: `tests/appearance.test.js`
- Modify: `tests/test_appearance_pages_v152.py`

**Interfaces:**
- Consumes: storage key `pch-appearance-theme`, existing `data-appearance-choice` container
- Produces: `window.PCHAppearance.themes() -> Array<{id,label,scheme,background,motion}>`, existing `setTheme()`, `getTheme()`, `receiveTheme()`, and generated appearance cards

- [ ] **Step 1: Write failing JavaScript tests for registry behavior**

Add assertions equivalent to:

```javascript
test('theme registry is immutable metadata and invalid ids fall back to light',()=>{
 const setup=fixture('missing');setup.mount();
 assert.deepEqual(setup.window.PCHAppearance.themes().map(theme=>theme.id),['light','warm','night']);
 assert.deepEqual(setup.window.PCHAppearance.themes().map(theme=>theme.background),['solid','solid','solid']);
 assert.equal(setup.window.PCHAppearance.getTheme(),'light');
});

test('appearance page cards are generated from the registry',()=>{
 const setup=fixture('',{withSettingsPanel:true});setup.mount();
 assert.equal(setup.appearanceChoices.length,3);
 assert.deepEqual(setup.appearanceChoices.map(choice=>choice.dataset.appearanceChoice),['light','warm','night']);
});
```

Update the fake document so `[data-appearance-grid]` returns a container and card creation is observable.

- [ ] **Step 2: Add a failing Python source-contract test**

```python
def test_appearance_page_has_one_generated_theme_grid_without_handwritten_cards():
    source = (STATIC / "appearance.html").read_text(encoding="utf-8")
    assert 'data-appearance-grid' in source
    assert 'data-appearance-choice="light"' not in source
    assert 'data-appearance-choice="warm"' not in source
    assert 'data-appearance-choice="night"' not in source
```

- [ ] **Step 3: Run focused tests and confirm failure**

```bash
node --test tests/appearance.test.js
PYTHONPATH=src .venv/bin/python -m pytest tests/test_appearance_pages_v152.py -q
```

Expected: FAIL on missing registry API and handwritten cards still present.

- [ ] **Step 4: Implement the frozen registry and generated cards**

Replace tuple choices with:

```javascript
const THEMES=Object.freeze([
 Object.freeze({id:'light',label:'清爽浅色',scheme:'light',background:'solid',motion:false}),
 Object.freeze({id:'warm',label:'暖色纸感',scheme:'light',background:'solid',motion:false}),
 Object.freeze({id:'night',label:'深色夜间',scheme:'dark',background:'solid',motion:false}),
]);
const valid=id=>THEMES.some(theme=>theme.id===id);
const themeById=id=>THEMES.find(theme=>theme.id===id)||THEMES[0];
```

Add `renderAppearanceCards()` that creates the existing preview markup from `THEMES`, sets `data-appearance-choice`, and appends to `[data-appearance-grid]`. Export a defensive copy:

```javascript
window.PCHAppearance={
 setTheme,getTheme:()=>current,applyTheme,receiveTheme,
 themes:()=>THEMES.map(theme=>({...theme})),
};
```

Replace the three handwritten buttons in `appearance.html` with:

```html
<div class="appearance-theme-grid" role="group" aria-label="界面主题" data-appearance-grid></div>
```

- [ ] **Step 5: Run focused tests**

Run the same two commands from Step 3.

Expected: PASS, including blocked-storage and iframe-sync tests.

- [ ] **Step 6: Commit**

```bash
git add src/helper/static/appearance.js src/helper/static/appearance.html tests/appearance.test.js tests/test_appearance_pages_v152.py
git commit -m "refactor: centralize appearance metadata"
```

### Task 3: Establish One Semantic Token Layer and Remove Duplicate Theme Overrides

**Files:**
- Create: `src/helper/static/theme-tokens.css`
- Create: `tools/check_theme_contract.py`
- Create: `tests/test_theme_contract.py`
- Modify: `src/helper/static/product.css`
- Modify: `src/helper/static/design-system.css`
- Modify: `src/helper/static/external-workspace.css`
- Modify: `src/helper/static/playlist-now-playing.css`
- Modify: `tests/test_appearance_palette_v152.py`

**Interfaces:**
- Consumes: `html[data-appearance="light"]`, `html[data-appearance="warm"]`, and `html[data-appearance="night"]`
- Produces: the complete `--app-*`, `--icon-*`, `--state-*`, `--focus-*`, and `--background-*` token contract

- [ ] **Step 1: Write failing token-ownership tests**

Test that `theme-tokens.css` defines all required tokens for the default, warm, and night palettes:

```python
REQUIRED = {
    "--app-rail", "--app-main", "--app-surface", "--app-control",
    "--app-hover", "--app-selected", "--app-overlay", "--app-text",
    "--app-muted", "--app-line", "--app-accent", "--app-accent-strong",
    "--app-on-accent", "--app-danger", "--app-danger-soft", "--app-warning",
    "--app-success", "--icon-default", "--icon-muted", "--icon-hover",
    "--icon-active", "--icon-danger", "--focus-ring", "--app-shadow",
    "--background-color", "--background-image", "--background-overlay",
}

def test_each_palette_resolves_the_full_semantic_contract():
    css = TOKENS.read_text(encoding="utf-8")
    assert REQUIRED <= token_names_for_default(css)
    for theme in ("warm", "night"):
        assert REQUIRED <= resolved_token_names(css, theme)
```

Add a contract test that `product.css` no longer contains `html[data-appearance=warm]` or `html[data-appearance=night]` theme selector blocks.

- [ ] **Step 2: Write the failing raw-color checker test**

`tools/check_theme_contract.py` must expose:

```python
RAW_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\)")

def unregistered_colors(path: Path) -> list[tuple[int, str]]:
    """Return raw UI colors outside theme-tokens.css and explicit asset-fallback lines."""
```

The checker permits raw colors only in `theme-tokens.css` or on a line containing `theme-contract-allow: asset-fallback`. Test both a rejected ordinary declaration and an accepted documented asset fallback.

- [ ] **Step 3: Run tests and confirm failure**

```bash
PYTHONPATH=src:. .venv/bin/python -m pytest tests/test_theme_contract.py tests/test_appearance_palette_v152.py -q
```

Expected: FAIL because the token file and checker do not exist and legacy theme blocks remain.

- [ ] **Step 4: Move palette values into `theme-tokens.css`**

Move the current `html`, warm, and night token definitions from `design-system.css`. Add semantic aliases rather than page selectors. The core alias pattern must be:

```css
html{
 --app-surface:var(--app-main);
 --app-success:var(--app-accent);
 --icon-default:var(--app-text);
 --icon-muted:var(--app-muted);
 --icon-hover:var(--app-text);
 --icon-active:var(--app-accent);
 --icon-danger:var(--app-danger);
 --focus-ring:color-mix(in srgb,var(--app-accent) 60%,transparent);
 --app-shadow:0 10px 28px color-mix(in srgb,var(--app-text) 12%,transparent);
 --background-color:var(--app-main);
 --background-image:none;
 --background-overlay:transparent;
 --background-opacity:1;
 color-scheme:light;
}
```

Warm and night override palette values only; they do not contain component or page selectors.

- [ ] **Step 5: Remove legacy theme-selector blocks and convert direct UI colors**

Delete the `product.css` theme override region beginning at the current `html[data-appearance=warm] body` block. Replace remaining UI color literals in the four protected CSS files according to role:

| Existing role | Replacement |
|---|---|
| page/background white or pale canvas | `var(--app-main)` or `var(--app-surface)` |
| field/control fill | `var(--app-control)` |
| primary dark text | `var(--app-text)` |
| gray explanatory text | `var(--app-muted)` |
| green/teal action | `var(--app-accent)` or `var(--app-accent-strong)` |
| separators/borders | `var(--app-line)` |
| red destructive state | `var(--app-danger)` / `var(--app-danger-soft)` |
| amber warning state | `var(--app-warning)` |
| shadows | `var(--app-shadow)` or a `color-mix()` derived from tokens |

Album-derived immersive fallback colors may remain only with `/* theme-contract-allow: asset-fallback */` on the same declaration line.

- [ ] **Step 6: Implement and run the contract checker**

The CLI must scan the four protected CSS files and exit nonzero with `path:line: literal` for every violation:

```bash
PYTHONPATH=. .venv/bin/python tools/check_theme_contract.py
```

Expected: PASS with zero unregistered colors.

- [ ] **Step 7: Run palette and UI contract tests**

```bash
PYTHONPATH=src:. .venv/bin/python -m pytest tests/test_theme_contract.py tests/test_appearance_palette_v152.py tests/test_unified_design_system.py -q
```

Expected: PASS, including WCAG contrast checks already present for warm and night.

- [ ] **Step 8: Commit**

```bash
git add src/helper/static/theme-tokens.css src/helper/static/product.css src/helper/static/design-system.css src/helper/static/external-workspace.css src/helper/static/playlist-now-playing.css tools/check_theme_contract.py tests/test_theme_contract.py tests/test_appearance_palette_v152.py
git commit -m "refactor: establish semantic theme tokens"
```

### Task 4: Unify Shared Component and Icon States

**Files:**
- Create: `src/helper/static/ui-components.css`
- Modify: `src/helper/static/design-system.css`
- Modify: `src/helper/static/product.css`
- Modify: `src/helper/static/playlists.html`
- Modify: `src/helper/static/mixes.html`
- Modify: `src/helper/static/status.html`
- Modify: `tests/test_unified_design_system.py`
- Create: `tests/test_icon_state_contract.py`

**Interfaces:**
- Consumes: semantic tokens from `theme-tokens.css`
- Produces: `.ui-icon`, `.ui-icon--fill`, and shared component state rules

- [ ] **Step 1: Write failing shared-state tests**

Require the shared component file to contain exactly one owner for these states:

```python
def test_shared_controls_and_icons_have_one_state_owner():
    css = COMPONENTS.read_text(encoding="utf-8")
    for selector in (
        ".ui-icon", ":focus-visible", ".primary:hover:not(:disabled)",
        ".secondary:hover:not(:disabled)", ".danger:hover:not(:disabled)",
        ":disabled",
    ):
        assert selector in css
    assert "stroke:currentColor" in css
    assert "--icon-hover" in css
    assert "--icon-active" in css
```

Parse the relevant HTML files and assert that every control SVG has `ui-icon` or `ui-icon--fill`.

- [ ] **Step 2: Run focused tests and verify failure**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_icon_state_contract.py tests/test_unified_design_system.py -q
```

Expected: FAIL because `ui-components.css` and standardized icon classes do not exist.

- [ ] **Step 3: Implement the shared icon contract**

Start with these rules, then move the existing button/form/menu state declarations out of `product.css` and `design-system.css` into this file:

```css
.ui-icon{
 width:1em;
 height:1em;
 flex:none;
 fill:none;
 stroke:currentColor;
 stroke-width:1.8;
 stroke-linecap:round;
 stroke-linejoin:round;
 color:inherit;
}
.ui-icon--fill{fill:currentColor;stroke:none}
:is(button,a){--control-icon-color:var(--icon-default);color:var(--app-text)}
:is(button,a) .ui-icon{color:var(--control-icon-color)}
:is(button,a):hover:not(:disabled){--control-icon-color:var(--icon-hover)}
:is(button,a)[aria-current=page],:is(button,a).active{--control-icon-color:var(--icon-active)}
:is(button,a).danger{--control-icon-color:var(--icon-danger)}
:is(button,a):disabled{opacity:.42;cursor:not-allowed}
:is(button,a,input,select,textarea):focus-visible{outline:2px solid var(--focus-ring);outline-offset:2px}
```

Do not suppress focus outlines globally. Component-specific focus styles may replace the shared outline only when they remain visibly equivalent.

- [ ] **Step 4: Mark existing SVG controls with the shared class**

Add `ui-icon` to menu, import, refresh, player transport, volume, appearance trigger, and mix refresh SVGs. Use `ui-icon--fill` only for intentionally filled play/pause glyphs.

Text-symbol icons such as `✦`, `▦`, `↻`, `P`, and `Q` must consume `color:var(--icon-*)` through their component class and may not receive a literal color.

- [ ] **Step 5: Remove duplicate state owners**

Remove shared button, form, menu, focus, and icon-color declarations from `product.css` and `design-system.css`. Retain only geometry or page layout in those files.

- [ ] **Step 6: Run focused tests and theme checker**

```bash
PYTHONPATH=src:. .venv/bin/python -m pytest tests/test_icon_state_contract.py tests/test_unified_design_system.py -q
PYTHONPATH=. .venv/bin/python tools/check_theme_contract.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/helper/static/ui-components.css src/helper/static/design-system.css src/helper/static/product.css src/helper/static/playlists.html src/helper/static/mixes.html src/helper/static/status.html tests/test_unified_design_system.py tests/test_icon_state_contract.py
git commit -m "refactor: unify component and icon states"
```

### Task 5: Separate the Management Shell and Normalize Asset Loading

**Files:**
- Create: `src/helper/static/management-shell.css`
- Modify: `src/helper/static/design-system.css`
- Modify: `src/helper/static/management-shell.js`
- Modify: `src/helper/static/settings.html`
- Modify: `src/helper/static/external.html`
- Modify: `src/helper/static/mixes.html`
- Modify: `src/helper/static/home.html`
- Modify: `src/helper/static/status.html`
- Modify: `src/helper/static/appearance.html`
- Modify: `src/helper/static/playlists.html`
- Modify: `src/helper/static/daily.html`
- Modify: `src/helper/web.py`
- Modify: `tests/test_management_workspace.py`
- Modify: `tests/test_page_version_v0331.py`
- Modify: `tests/test_unified_design_system.py`

**Interfaces:**
- Consumes: `body[data-management-page]`, `[data-management-shell]`, shared component/token files
- Produces: `management-shell.css`, the existing ordered six-route navigation, and neutral source asset marker `?v=app`

- [ ] **Step 1: Write failing load-order and ownership tests**

For every themed page, require this relative order when applicable:

```python
ORDER = (
    "product.css", "theme-tokens.css", "ui-components.css",
    "design-system.css",
)
```

For the six management pages, additionally require `management-shell.css` after `design-system.css`. Assert that management selectors such as `.management-stage` and `.management-nav` no longer occur in `design-system.css`.

Add a source contract that every `/static/` URL in HTML uses `?v=app`, while `render_versioned_html(source, "2.1.0")` still emits `?v=2.1.0`.

- [ ] **Step 2: Run focused tests and verify failure**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_management_workspace.py tests/test_page_version_v0331.py tests/test_unified_design_system.py -q
```

Expected: FAIL on missing files, inconsistent link order, and numeric source versions.

- [ ] **Step 3: Extract management shell and page layout rules**

Move the complete `body[data-management-page]` section from `design-system.css` to `management-shell.css`, including responsive management rules and the six page-specific layout subsections. Do not duplicate declarations. Preserve these public selectors:

```css
body[data-management-page]
[data-management-shell]
.management-stage
.management-nav
.management-action
.management-section
.management-row
.management-summary
```

Keep theme colors indirect through `--app-*` aliases.

- [ ] **Step 4: Normalize every HTML asset marker and stylesheet order**

Replace numeric source markers such as `?v=2.0.7` with `?v=app`. Insert the new CSS files in the tested order. Specialized layout files such as `external-workspace.css` and `playlist-now-playing.css` remain before `theme-tokens.css`, so the token/component layer owns final shared colors and states.

- [ ] **Step 5: Serve the new static assets**

Add these names to the explicit static allowlist in `src/helper/web.py`:

```python
"theme-tokens.css", "ui-components.css", "management-shell.css", "theme-background.css"
```

Do not broaden the route to arbitrary filesystem access.

- [ ] **Step 6: Preserve the navigation API and test all six routes**

Keep `management-shell.js` route order exactly:

```javascript
['settings','external','mixes','library','status','appearance']
```

Keep links targeted at `_top`, keep one `aria-current="page"`, and keep action decoration separate from route generation.

- [ ] **Step 7: Run focused tests**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_management_workspace.py tests/test_page_version_v0331.py tests/test_unified_design_system.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/helper/static/management-shell.css src/helper/static/design-system.css src/helper/static/management-shell.js src/helper/static/*.html src/helper/web.py tests/test_management_workspace.py tests/test_page_version_v0331.py tests/test_unified_design_system.py
git commit -m "refactor: isolate management presentation shell"
```

### Task 6: Add a Non-Interactive Background Theme Contract

**Files:**
- Create: `src/helper/static/theme-background.css`
- Modify: `src/helper/static/theme-tokens.css`
- Modify: `src/helper/static/appearance.js`
- Modify: all themed HTML pages to load `theme-background.css`
- Modify: `tests/appearance.test.js`
- Create: `tests/test_theme_background_contract.py`

**Interfaces:**
- Consumes: registry fields `background` and `motion`, `.pch-embedded`, `data-appearance`
- Produces: `data-background-kind`, `data-background-motion`, and the non-interactive `.app-theme-background` layer

- [ ] **Step 1: Write failing JavaScript state tests**

```javascript
test('theme application exposes background metadata without animating controls',()=>{
 const setup=fixture();setup.mount();
 setup.window.PCHAppearance.setTheme('night');
 assert.equal(setup.document.documentElement.dataset.backgroundKind,'solid');
 assert.equal(setup.document.documentElement.dataset.backgroundMotion,'off');
});
```

Also assert embedded fixtures do not mount `.app-theme-background`.

- [ ] **Step 2: Write failing CSS contract tests**

Require:

```python
def test_background_layer_never_handles_input_or_duplicates_when_embedded():
    css = BACKGROUND.read_text(encoding="utf-8")
    assert "pointer-events:none" in css
    assert ".pch-embedded .app-theme-background" in css
    assert "display:none" in css
    assert "prefers-reduced-motion:reduce" in css.replace(" ", "")
```

Also assert no selector in `theme-background.css` targets `button`, `input`, `select`, `.management-action`, `.ui-icon`, or `.management-nav a`.

- [ ] **Step 3: Run focused tests and verify failure**

```bash
node --test tests/appearance.test.js
PYTHONPATH=src .venv/bin/python -m pytest tests/test_theme_background_contract.py -q
```

Expected: FAIL on missing metadata and stylesheet.

- [ ] **Step 4: Apply background metadata in `appearance.js`**

Extend `applyTheme()` without changing its return value:

```javascript
function applyTheme(){
 const theme=themeById(current);
 document.documentElement.dataset.appearance=theme.id;
 document.documentElement.dataset.backgroundKind=theme.background;
 document.documentElement.dataset.backgroundMotion=theme.motion?'on':'off';
 updateMenu();
 return current;
}
```

On DOM ready, create exactly one `<div class="app-theme-background" aria-hidden="true"></div>` only when `embedded === false`.

- [ ] **Step 5: Implement the inert background stylesheet**

Use the semantic variables only:

```css
.app-theme-background{
 position:fixed;
 inset:0;
 z-index:-1;
 pointer-events:none;
 overflow:hidden;
 background-color:var(--background-color);
 background-image:linear-gradient(var(--background-overlay),var(--background-overlay)),var(--background-image);
 background-position:center;
 background-size:cover;
 opacity:var(--background-opacity);
}
.pch-embedded .app-theme-background{display:none}
@media(prefers-reduced-motion:reduce){
 .app-theme-background,.app-theme-background::before,.app-theme-background::after{
  animation:none!important;
  transition:none!important;
 }
}
```

Establish a safe stacking context without changing existing fixed/sticky component z-indexes. The first implementation remains visually solid because all current themes use `--background-image:none`.

- [ ] **Step 6: Run focused tests and theme checker**

```bash
node --test tests/appearance.test.js
PYTHONPATH=src:. .venv/bin/python -m pytest tests/test_theme_background_contract.py tests/test_theme_contract.py -q
PYTHONPATH=. .venv/bin/python tools/check_theme_contract.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/helper/static/theme-background.css src/helper/static/theme-tokens.css src/helper/static/appearance.js src/helper/static/*.html tests/appearance.test.js tests/test_theme_background_contract.py
git commit -m "feat: add safe theme background contract"
```

### Task 7: Run Full Structural, Behavioral, and Visual Regression

**Files:**
- Modify only files required to fix regressions discovered by this task
- Generate: `.artifacts/theme-foundation/current/`
- Generate: `.artifacts/theme-foundation/diff/`
- Create: `docs/theme-foundation-verification.md`

**Interfaces:**
- Consumes: Tasks 1–6 and the baseline visual matrix
- Produces: complete test evidence and a go/no-go statement for the later settings redesign

- [ ] **Step 1: Run source and theme contracts**

```bash
PYTHONPATH=. .venv/bin/python tools/check_theme_contract.py
PYTHONPATH=src:. .venv/bin/python -m pytest \
  tests/test_theme_contract.py \
  tests/test_icon_state_contract.py \
  tests/test_theme_background_contract.py \
  tests/test_management_workspace.py \
  tests/test_unified_design_system.py \
  tests/test_appearance_palette_v152.py \
  tests/test_appearance_pages_v152.py \
  tests/test_page_version_v0331.py -q
```

Expected: PASS.

- [ ] **Step 2: Run all JavaScript interaction tests**

```bash
node --test tests/*.test.js
```

Expected: PASS in the Node-capable test environment.

- [ ] **Step 3: Run the full Python suite**

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
```

Expected: PASS.

- [ ] **Step 4: Capture the after-state and compare it**

```bash
PLAYWRIGHT_BROWSERS_PATH=.playwright PCH_VISUAL_BASE_URL=http://192.168.50.99:9512 \
  .venv/bin/python tools/capture_theme_matrix.py --mode current --compare baseline
```

Expected: all 37 screenshots are generated. Because this is a behavior-preserving foundation migration, any changed-pixel ratio above `0.005` is a failure requiring inspection. Ratios below the threshold still require visual inspection of every generated diff.

- [ ] **Step 5: Inspect screenshots and interaction states**

Use the local image viewer on every diff image with nonzero pixels and representative full screenshots for all three themes. In Playwright, additionally exercise:

- pointer hover on menu, ordinary icon, active icon, and danger action;
- keyboard focus on the same controls;
- disabled control state;
- embedded settings page with sidebar and player visible;
- a context emulating `reduced_motion="reduce"`.

Expected: no unexplained visual change, no clipped control, no inconsistent icon color, no duplicate background, and no background interception.

- [ ] **Step 6: Write the verification record**

Create `docs/theme-foundation-verification.md` with the heading `# Theme Foundation Verification`. Record the exact output of `git rev-parse HEAD`, every verification command from Steps 1–4 with its exit code, `Visual matrix: 37/37 captured`, every nonzero changed-pixel ratio with its inspected result, and a final `Settings redesign gate: GO` or `Settings redesign gate: NO-GO`. Write only observed values; do not use placeholder text.

- [ ] **Step 7: Commit verified fixes and evidence**

```bash
git add src/helper/static tests tools docs/theme-foundation-verification.md
git diff --cached --check
git commit -m "test: verify theme foundation migration"
```

- [ ] **Step 8: Run final branch review**

Review the complete range from the baseline checkpoint through `HEAD`. Confirm that no business API, route behavior, page field, or interaction was changed and that every old theme/component rule migrated in Tasks 3–5 was removed rather than shadowed.

Expected: reviewer returns no blocking findings. Only then report that the foundation is safe and begin a separate design cycle for the management menu and settings interface.
