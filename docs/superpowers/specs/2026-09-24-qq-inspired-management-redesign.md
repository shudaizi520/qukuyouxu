# QQ-Inspired Management Redesign

## Goal

Redesign all six management pages so they feel like one mature music product: compact, left-aligned, orderly, low-noise, and ready for many future background themes without changing any business behavior.

## Approved Direction

The approved visual direction is based on the density and alignment of QQ Music's settings pages, adapted to the existing 曲库有序 shell:

- Keep the existing left playlist rail, global search, player, and six-item management navigation.
- Use a narrow, left-aligned reading column. Right-side whitespace is intentional.
- Let each page choose the smallest width its content requires; do not force all pages to the same width.
- Arrange settings as aligned label/content/action rows instead of cards or full-width strips.
- Use fixed grid columns so labels, controls, times, switches, and action columns remain aligned even when Chinese labels have different lengths.
- Establish hierarchy with typography, whitespace, hover tone, and alignment. Do not draw a separator lattice.
- Keep selectable visual content such as theme previews as compact tiles; ordinary settings must not become cards.

## Shared Interaction Language

- Green is reserved for selected navigation, enabled switches, success/connected status, and other persistent state.
- Ordinary clickable actions are transparent with a subtle one-pixel neutral border and a compact four-pixel radius.
- Ordinary action hover/focus adds a small neutral gray background; it does not change to a saturated green.
- Dangerous operations keep red text and receive only a restrained danger-tinted hover background.
- Status is represented by a small dot plus text, never by a button-like pill.
- Buttons use a common 30-pixel height, content-driven width, and consistent text weight.
- Inputs and selects use compact 32-pixel height, five-pixel radius, and only the width required by their content.
- Existing accessible focus-visible behavior and disabled states remain intact.

## Shared Layout Contract

- The management stage remains inside the existing right-hand workspace and never covers the left playlist rail or player.
- Navigation remains horizontal and uses the active accent underline.
- Desktop content begins 30 to 34 pixels from the left edge of the management canvas.
- General settings content targets 680 to 850 pixels, depending on the page.
- Responsive layouts collapse at existing mobile breakpoints without horizontal page overflow.
- Themes continue to consume semantic `--app-*` tokens. The redesign must not introduce page-specific raw colors.

## Page Designs

### System Settings

- Use separate whitespace groups for Plex connection, user management, automatic tasks, account security, about, and logout.
- Plex connection uses fixed label and control columns.
- User management is capped near 760 pixels and does not span the entire workspace.
- “添加用户” sits on the same heading row as “用户管理”. It uses the shared neutral action style.
- User rows have no persistent background; a light neutral background appears only on hover/focus.
- User columns remain aligned for user/library, playback learning, daily recommendations, smart playlists, and actions.
- Automatic task rows use one fixed grid: label, recurrence, time, switch. Different label lengths must not shift the controls.
- Exit login remains at the lower left.

### Import Playlist

- Use a compact source section with label/content/action rows.
- Import link input is capped near 380 pixels; file selection and import actions follow it.
- Current source operations stay close to the source metadata.
- Plex sync uses a short playlist-name field, dot status, refresh switch, and update action.
- Match results expand only as required by song/artist/album/duration columns.
- “下载缺失歌曲” sits immediately after the missing count, uses no persistent background, and is visually a secondary action belonging to the missing result.
- Track rows have no rules or permanent cards; hover/focus supplies the only row background.

### Smart Playlists

- Daily recommendation rules use a compact fixed grid.
- Generation, save, publish, adjust, view, and delete actions all use the shared neutral/danger action grammar.
- Other smart playlists render as aligned rows without separator lines or permanent backgrounds.

### Library Organization

- Summary metrics appear as a compact inline group without an outer box or internal divider lines.
- Automation controls use fixed aligned columns.
- Managed playlists render as compact rows with state dots and hover-only row backgrounds.
- Long operational panels that appear only during active analysis retain their functionality but adopt the same spacing and action language.

### Runtime Status

- Plex, QQ Music, and background task state appear as compact inline status groups without a bordered summary strip.
- Learning metrics and assistant management are arranged as left-aligned sections rather than side-by-side cards.
- Runtime details and event history use quiet disclosure rows without full-width card borders.

### Appearance

- The page contains only the theme chooser.
- Theme tiles remain compact and left-aligned because they are visual selections, not ordinary settings rows.
- Unselected tiles have no persistent border/background; selected and hover states use semantic theme tokens.

## Architecture and Ownership

- `management-shell.css` owns management navigation, stage geometry, shared compact actions, shared row primitives, and the six page layouts.
- `ui-components.css` owns interaction states, focus, disabled, hover, and danger semantics.
- `external-workspace.css` owns track/table internals only; management-shell rules own external-page macro layout. Conflicting older macro layout declarations must be removed or narrowed rather than overridden at the end.
- Existing HTML IDs, data attributes, route order, JavaScript hooks, API calls, forms, dialogs, and business state remain unchanged.
- Markup changes are limited to semantic grouping/classes needed for alignment or action placement.

## Testing and Preview

- Contract tests must assert content widths, absence of separator/card framing, fixed column grids, button geometry/state ownership, and the import-download placement.
- Existing Python and JavaScript suites must remain green.
- Capture all six pages in light, warm, and night themes at 1366×768 and 1920×1080, plus the embedded settings view.
- Inspect the night-theme 1920×1080 screenshots for all pages and the embedded settings view before handing the preview to the user.
- The finished branch is previewed independently and is not deployed or merged without the user's follow-up approval.

## Non-Goals

- No business logic, API, persistence, scheduling, playlist behavior, or authorization changes.
- No new production theme or animated background.
- No sidebar, player, search, or playlist playback redesign.
- No explanatory copy reintroduced into the six management pages.
