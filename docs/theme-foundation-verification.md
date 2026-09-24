# Theme Foundation Verification

## Revision under test

`eeb3bac9c5efb9cee48f9879891e0bec2bd206cd`

The visual baseline was served from baseline checkpoint `ceb38eb` on an isolated
temporary data root. The current branch was served separately from the revision
above with an equivalent isolated data root. Both captures waited for
`networkidle`, used the same disposable temporary account, and emulated reduced
motion. Credential-bearing fields, including `#webhookUrl`, were masked before
pixels were written. The temporary servers, data roots, and credential file were
removed afterwards.

## Automated verification

| Command | Exit | Observed result |
| --- | ---: | --- |
| `PYTHONPATH=. .venv/bin/python tools/check_theme_contract.py` | 0 | `Theme contract passed: zero unregistered UI colors.` |
| `PYTHONPATH=src:. .venv/bin/python -m pytest tests/test_theme_contract.py tests/test_icon_state_contract.py tests/test_theme_background_contract.py tests/test_management_workspace.py tests/test_unified_design_system.py tests/test_appearance_palette_v152.py tests/test_appearance_pages_v152.py tests/test_page_version_v0331.py tests/test_visual_capture_tool.py tests/test_playwright_runtime.py -q` | 0 | `51 passed in 0.20s` |
| `node --test tests/*.test.js` | 127 | The shell had no `node` entry on `PATH`; no tests ran in this invocation. |
| `.venv/lib/python3.14/site-packages/playwright/driver/node --test tests/*.test.js` | 0 | `8` test files passed, `0` failed. |
| `PYTHONPATH=src .venv/bin/python -m pytest -q` | 0 | `1042 passed, 115 subtests passed in 16.11s` |
| `PLAYWRIGHT_BROWSERS_PATH=.playwright PCH_VISUAL_BASE_URL=http://127.0.0.1:29511 .venv/bin/python tools/capture_theme_matrix.py --mode baseline` | 0 | `captured=37 mode=baseline` from checkpoint `ceb38eb`. |
| `PLAYWRIGHT_BROWSERS_PATH=.playwright PCH_VISUAL_BASE_URL=http://127.0.0.1:29512 .venv/bin/python tools/capture_theme_matrix.py --mode current --compare baseline` | 0 | `captured=37 mode=current`; ratios recorded below. |
| `PLAYWRIGHT_BROWSERS_PATH=.playwright PCH_VISUAL_BASE_URL=http://127.0.0.1:29512 .venv/bin/python tools/verify_theme_background.py` | 0 | Five background, pointer, computed-motion, and embedding checks passed. |

Visual matrix: 37/37 captured.

## Visual inspection

Every nonzero diff image was opened and inspected. The two zero-diff images were
`1366x768/light/external.png` and `1920x1080/light/external.png`.

| Changed ratio | Capture | Inspected result |
| ---: | --- | --- |
| 0.00319706 | `1366x768/light/settings.png` | Shared control/surface tokens changed; structure, labels, controls, and spacing remain intact. |
| 0.00199460 | `1920x1080/light/settings.png` | Same inspected settings token normalization; no clipping or missing control. |
| 0.00883243 | `1366x768/warm/settings.png` | Warm controls and surfaces now consistently use the warm palette; no layout change. |
| 0.01897569 | `1920x1080/warm/settings.png` | Same warm-palette normalization across the larger visible settings area. |
| 0.00580028 | `1366x768/night/settings.png` | Night controls/surfaces and semantic states are consistent; no white orphan surface. |
| 0.00368972 | `1920x1080/night/settings.png` | Same inspected night token normalization; no missing field or action. |
| 0.03266742 | `1366x768/warm/external.png` | Import control surface now follows the warm palette; geometry and actions are unchanged. |
| 0.01652730 | `1920x1080/warm/external.png` | Same warm import-surface normalization at the larger viewport. |
| 0.00236491 | `1366x768/night/external.png` | Night text/control semantic colors normalized; no clipping. |
| 0.00119647 | `1920x1080/night/external.png` | Same night import normalization at the larger viewport. |
| 0.00086551 | `1366x768/light/mixes.png` | Minor semantic token differences only; the light layout is unchanged. |
| 0.00047550 | `1920x1080/light/mixes.png` | Light smart-playlist card/state colors normalized; controls remain aligned and readable. |
| 0.43986205 | `1366x768/warm/mixes.png` | Previously white list/card surfaces now intentionally use the warm theme surface; no field or interaction changed. |
| 0.25569010 | `1920x1080/warm/mixes.png` | Same intentional warm surface correction over the larger visible list area. |
| 0.33818993 | `1366x768/night/mixes.png` | Previously white list/card surfaces now intentionally use the night surface; no white orphan panel remains. |
| 0.20472078 | `1920x1080/night/mixes.png` | Same intentional night surface correction over the larger visible list area. |
| 0.00324949 | `1366x768/light/library.png` | Library summary/status colors normalized; structure unchanged. |
| 0.00164400 | `1920x1080/light/library.png` | Same light library normalization at the larger viewport. |
| 0.15212070 | `1366x768/warm/library.png` | Library summary surface now follows the warm palette; no missing data or action. |
| 0.07696181 | `1920x1080/warm/library.png` | Same intentional warm library surface correction. |
| 0.00537800 | `1366x768/night/library.png` | Night library surface and semantic colors normalized; no clipped control. |
| 0.00272087 | `1920x1080/night/library.png` | Same night library normalization at the larger viewport. |
| 0.00195598 | `1366x768/light/status.png` | Status cards and semantic states use shared tokens; layout and values remain present. |
| 0.00098958 | `1920x1080/light/status.png` | Same light status normalization at the larger viewport. |
| 0.00588797 | `1366x768/warm/status.png` | Warm status surfaces and state colors normalized; no clipping. |
| 0.00297888 | `1920x1080/warm/status.png` | Same warm status normalization at the larger viewport. |
| 0.00625305 | `1366x768/night/status.png` | Night status cards and state colors normalized; no orphan light surface. |
| 0.00316358 | `1920x1080/night/status.png` | Same night status normalization at the larger viewport. |
| 0.00046898 | `1366x768/light/appearance.png` | Appearance card border/accent tokens normalized; cards remain aligned and selectable. |
| 0.00023727 | `1920x1080/light/appearance.png` | Same light appearance normalization at the larger viewport. |
| 0.00208085 | `1366x768/warm/appearance.png` | Warm navigation/card token normalization; selection remains clear. |
| 0.00105276 | `1920x1080/warm/appearance.png` | Same warm appearance normalization at the larger viewport. |
| 0.00316179 | `1366x768/night/appearance.png` | Night navigation/card token normalization; selection remains clear. |
| 0.00159963 | `1920x1080/night/appearance.png` | Same night appearance normalization at the larger viewport. |
| 0.00526042 | `1920x1080/light/embedded-settings.png` | Embedded settings use shared component tokens; iframe credentials are masked, and the sidebar/player remain visible and unobscured. |

The ratios above `0.005` were not dismissed automatically. Inspection showed
that the large smart-playlist and library differences are the intended removal
of hard-coded white surfaces from warm/night themes; the remaining differences
are the corresponding semantic control, border, accent, and status colors. No
page field, action, or layout region disappeared.

## Interaction and background checks

The current branch was exercised in Chromium with `reduced_motion="reduce"`:

- ordinary, hover, active, and danger icons resolved to their registered
  semantic icon tokens;
- keyboard focus rendered a solid `2px` outline;
- disabled controls rendered at `0.42` opacity with `not-allowed` cursor;
- the standalone page mounted exactly one non-intercepting background layer;
- embedded settings mounted no second background layer while the parent kept
  exactly one;
- the playlist sidebar and bottom player remained visible in embedded settings.

The background check injected a red synthetic background plus a green stacking
probe. Pixel assertions confirmed the background remained visible behind normal
content, and hit-testing confirmed that the background never captured input. It
also injected inline animation/transition declarations and confirmed the layer
plus both pseudo-elements computed to `animation-name:none` and `0s` transition
duration under reduced motion.

Observed interaction result: `interaction-states=PASS`.

## Gate

Settings redesign gate: GO
