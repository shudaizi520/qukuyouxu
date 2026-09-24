# Management redesign verification

Verified on 2026-09-24 from the isolated `qq-management-redesign` worktree.

## Automated checks

- Python: `1052 passed, 115 subtests passed in 15.94s`
- JavaScript: `8 passed, 0 failed in 12.06s`
- Focused layout and behavior suites: green
- Whitespace validation: green

## Visual matrix

- Preview origin: `http://127.0.0.1:19513`
- Capture command mode: `management-redesign`
- Captures: 37
- Themes: light, warm, night
- Viewports: 1366×768 and 1920×1080
- Embedded verification: light-theme settings inside the playlist workspace at 1920×1080

The final night-theme 1920×1080 captures were inspected for all six management pages:

- `.artifacts/management-redesign/management-redesign/1920x1080/night/settings.png`
- `.artifacts/management-redesign/management-redesign/1920x1080/night/external.png`
- `.artifacts/management-redesign/management-redesign/1920x1080/night/mixes.png`
- `.artifacts/management-redesign/management-redesign/1920x1080/night/library.png`
- `.artifacts/management-redesign/management-redesign/1920x1080/night/status.png`
- `.artifacts/management-redesign/management-redesign/1920x1080/night/appearance.png`
- `.artifacts/management-redesign/management-redesign/1920x1080/light/embedded-settings.png`

The 1366×768 night captures were also inspected for clipping and horizontal overflow.

## Visual acceptance

- Management content remains left aligned and uses page-specific readable widths.
- System settings stays capped when embedded in the playlist workspace.
- User and automatic-task columns remain aligned without label wrapping.
- Import controls and result tables use independent 680px and 940px limits.
- Missing-song exports remain beside the missing count and keep every JavaScript hook ID.
- Library empty/setup states no longer render as full-width framed cards.
- Smart playlist rows, status rows, and unselected appearance tiles have no persistent frames.
- Ordinary actions are neutral at rest; green is reserved for state, selection, and toggles.

## Accepted limitation

The isolated preview deliberately used an empty temporary data store, so screenshots do not contain a real Plex library or imported playlist. Data-dependent hooks and hidden-state placement are covered by DOM, interaction, and layout contract tests; no production data was copied into the preview.
