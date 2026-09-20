# Fixed Playlist Shell and Reliable Player Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fixed music-app shell with whole-library search and a persistent player that starts Plex-transcoded tracks reliably on the first user action.

**Architecture:** Keep the existing FastAPI and vanilla HTML/CSS/JavaScript stack. Replace body-level scrolling with a three-row application shell, model playlist/search/tool/system content as one explicit in-document state so navigation never destroys audio, expose scoped library media routes, and make audio startup a tokenized one-retry state machine.

**Tech Stack:** Python 3.11+, FastAPI/Starlette, vanilla JavaScript, CSS Grid, `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-20-fixed-playlist-shell-and-reliable-player-design.md`

## Global Constraints

- The active Plex profile and library remain the security and data boundary for search, playback, artwork, and playlist edits.
- The bottom player is always present; playlist, search, creation tool, status, and settings switches never destroy playback.
- Exactly one sidebar destination may be active at a time.
- No new frontend framework or runtime dependency.
- Split workspace state, library search, and playback into native ES modules; `playlists.js` remains an orchestrator rather than a shared-state monolith.
- Theme/wallpaper support must remain CSS-variable driven and must not control layout dimensions.
- The default desktop presentation is a full-bleed, theme-neutral music application with dense aligned rows; it must not retain floating dashboard cards or hard-code a dark/light dependency into layout.
- Existing recommendation, learning, import, and playlist ownership behavior must not change.

## Review Focus

- A stale audio error after rapidly choosing another song must not mark the new song failed; Task 3 adds a tokenized-event test.
- A second quick Plex transcode request may return HTTP 400; Task 3 adds single-start and automatic one-retry behavior tests.
- A search result outside the open playlist must still be scoped to the current catalog; Task 2 adds allowed and unavailable-track route tests.
- Switching profile while search results are loading must discard the old response; Task 1 adds a request-generation test.
- Narrow layouts must keep the player and active content usable without body scrolling; Task 4 adds structural CSS assertions and manual viewport checks.

---

### Task 1: One application-shell state and whole-library search

**Files:**
- Create: `src/helper/static/playlist-workspace.js`
- Create: `src/helper/static/playlist-search.js`
- Modify: `src/helper/static/playlists.html`
- Modify: `src/helper/static/playlists.js`
- Modify: `src/helper/web.py`
- Test: `tests/test_v132_playlist_hub.py`

**Interfaces:**
- Consumes: existing `GET /api/playlists/search?q=` response `{items: Track[]}`.
- Produces: `createPlaylistWorkspace(options)` from `playlist-workspace.js`, `createLibrarySearch(options)` from `playlist-search.js`, and a search result queue callback consumed by Task 3.

- [ ] **Step 1: Write failing page behavior tests**

Add tests that assert the top bar owns `librarySearchInput`, the old `playlistSearch` is absent, `playlistSearchView` exists, and `playlist-workspace.js` exposes one workspace transition that removes every old `.active` class before applying one destination. Assert the status and settings controls use in-workspace URLs rather than direct full-page navigation. Assert `playlists.js` imports the modules and the static route serves them.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `PYTHONPATH=src /home/shudaizi/nas/qukuyouxu/.venv/bin/python -m unittest tests.test_v132_playlist_hub.PlaylistHubPageTests -v`

Expected: FAIL because the search remains inside the current playlist and navigation uses independent states.

- [ ] **Step 3: Implement the shell state and full-library result view**

Move the search form into `.topbar`, add `playlistSearchView`, preserve the previously open playlist, and replace `toolOpen`/`pendingPlaylist` highlight composition with one `createPlaylistWorkspace()` instance. Status and settings use the same contained workspace loader as creation tools, with `embedded=1`, so the document and audio element are never replaced:

```javascript
const workspace=createPlaylistWorkspace({document});
workspace.show({type:'playlist',kind:item.kind,key:item.key});
```

`createLibrarySearch()` owns its request generation and captures the current profile ID; only matching generations may render. Only logout, profile switch, refresh, or explicit player controls may stop playback.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Task 1 focused test command.

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/helper/static/playlist-workspace.js src/helper/static/playlist-search.js src/helper/static/playlists.html src/helper/static/playlists.js src/helper/web.py tests/test_v132_playlist_hub.py
git commit -m "feat: add one-state playlist workspace search"
```

### Task 2: Scoped media routes for whole-library results

**Files:**
- Modify: `src/helper/playlist_hub.py`
- Modify: `src/helper/static/playlists.js`
- Test: `tests/test_v132_playlist_hub.py`
- Test: `tests/test_v130_external_audio.py`

**Interfaces:**
- Consumes: `stream_track_audio(store, plex_factory, track_id, range_header, session_key)`.
- Produces: `stream_library_artwork(engine, track_id)` and authenticated `GET /api/playlists/library/tracks/{track_id}/{audio|artwork}` routes.

- [ ] **Step 1: Write failing route tests**

Add tests proving a catalog track can stream without current-playlist membership, an unavailable or unknown catalog track is rejected, and artwork must come from that same catalog row.

- [ ] **Step 2: Run focused backend tests and verify RED**

Run: `PYTHONPATH=src /home/shudaizi/nas/qukuyouxu/.venv/bin/python -m unittest tests.test_v132_playlist_hub.PlaylistHubPlaybackTests tests.test_v130_external_audio.ExternalAudioV130Tests -v`

Expected: FAIL because library media routes do not exist.

- [ ] **Step 3: Implement the scoped routes and URL selection**

Reuse `stream_track_audio` for audio. Extract the bounded artwork proxy so both playlist and library routes validate the catalog track before calling Plex. Update `mediaUrl()` to generate library URLs when `context.kind === 'library'`.

- [ ] **Step 4: Run focused backend tests and verify GREEN**

Run the Task 2 focused command.

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/helper/playlist_hub.py src/helper/static/playlists.js tests/test_v132_playlist_hub.py tests/test_v130_external_audio.py
git commit -m "feat: stream scoped library search results"
```

### Task 3: Reliable first-play state machine and Plex source selection

**Files:**
- Modify: `src/helper/clients.py`
- Create: `src/helper/static/playlist-player.js`
- Modify: `src/helper/static/playlists.html`
- Modify: `src/helper/static/playlists.js`
- Modify: `src/helper/web.py`
- Test: `tests/test_v130_external_audio.py`
- Test: `tests/test_v132_playlist_hub.py`

**Interfaces:**
- Consumes: Task 1 queue contexts and Task 2 media URLs.
- Produces: deterministic `_audio_source()` selection and `createPlaylistPlayer(options)` with tokenized start/recovery.

- [ ] **Step 1: Write failing audio-source and player-state tests**

Add a backend test with two accessible media choices and assert the selected media or first Plex-ordered safe option is used rather than raising “不唯一”. Add page tests asserting `preload="none"`, one silent retry, stale token guards, permanent player markup, and error feedback contained inside the player.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `PYTHONPATH=src /home/shudaizi/nas/qukuyouxu/.venv/bin/python -m unittest tests.test_v130_external_audio.PlexAudioPartV130Tests tests.test_v132_playlist_hub.PlaylistHubPageTests -v`

Expected: FAIL on multi-media selection, metadata preload, hidden player, and missing retry/token guards.

- [ ] **Step 3: Implement minimal reliable playback behavior**

Select one safe media tuple deterministically. Render the player unconditionally. In `playlist-player.js`, increment `playbackGeneration` on a new track, assign the source without metadata preload, then attempt one play. For non-`AbortError`/non-`NotAllowedError` failures, call `load()` and retry the same generation once after a short delay. Only the current generation may reveal `playerFeedback`; `playlists.js` only supplies queues and context callbacks.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Task 3 focused command.

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/helper/clients.py src/helper/static/playlist-player.js src/helper/static/playlists.html src/helper/static/playlists.js src/helper/web.py tests/test_v130_external_audio.py tests/test_v132_playlist_hub.py
git commit -m "fix: make first browser playback reliable"
```

### Task 4: Fixed viewport layout, compact player, and contained tools

**Files:**
- Modify: `src/helper/static/product.css`
- Modify: `src/helper/static/playlists.html`
- Modify: `src/helper/static/playlists.js`
- Test: `tests/test_v132_playlist_hub.py`

**Interfaces:**
- Consumes: Task 1 view containers and Task 3 permanent player states.
- Produces: one `100dvh` application grid whose only desktop scroll containers are `.playlist-list-scroll`, `.playlist-track-scroll`, `.playlist-search-results`, and the tool iframe.

- [ ] **Step 1: Write failing fixed-layout tests**

Assert that the playlist body is `overflow:hidden`, `#workspace` is a three-row `100dvh` grid, `.playlist-hub` has `min-height:0`, sidebar and main are grid children rather than viewport-offset fixed cards, `.playlist-track-scroll` owns `overflow:auto`, the player has no floating feedback, iframe auto-height messaging has been removed, and every in-app content switch leaves the single footer/audio node mounted.

- [ ] **Step 2: Run focused UI tests and verify RED**

Run the Task 1 focused command.

Expected: FAIL because body scrolling and dynamic iframe height remain.

- [ ] **Step 3: Implement the fixed shell and responsive player**

Build CSS grid rows `64px minmax(0,1fr) 72px`; make the content row a two-column full-bleed grid; give the main view `grid-template-rows:auto minmax(0,1fr)`; wrap track content in `.playlist-track-scroll`; keep feedback inside the 72px player; use a compact mobile layout without changing the body scroll owner. Replace floating dashboard cards with theme-variable surfaces, dense aligned track rows, a flat full-height rail, and graphical player controls. Remove `pch-tool-height` handling from the parent.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Task 1 focused command.

Expected: PASS.

- [ ] **Step 5: Perform visual and interaction verification**

Run the app locally with a controlled fixture or authenticated development data. Check 1440×900, 1920×1080, 900×700, and 390×844 for fixed chrome, last-row visibility, single active navigation, search, tool/status/settings switching without playback interruption, idle/playing/error player states, and no horizontal overflow.

- [ ] **Step 6: Commit**

```bash
git add src/helper/static/product.css src/helper/static/playlists.html src/helper/static/playlists.js tests/test_v132_playlist_hub.py
git commit -m "feat: lock playlist home into an app shell"
```

### Task 5: Release-level regression verification

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `src/helper/__init__.py`
- Modify: static asset version references in `src/helper/static/*.html`
- Test: `tests/test_release_version.py`

**Interfaces:**
- Consumes: all previous tasks.
- Produces: one internally consistent next patch version ready for review, without publishing.

- [ ] **Step 1: Write the failing version expectation**

Update the release-version test to the next patch version and verify it fails before production version files change.

- [ ] **Step 2: Run the release test and verify RED**

Run: `PYTHONPATH=src /home/shudaizi/nas/qukuyouxu/.venv/bin/python -m unittest tests.test_release_version -v`

Expected: FAIL with old `1.4.2` values.

- [ ] **Step 3: Update version and changelog**

Use the next patch version consistently in Python, HTML cache-busting URLs, and changelog. Do not push, publish, deploy, or mutate TrueNAS data in this task.

- [ ] **Step 4: Run complete verification**

Run: `PYTHONPATH=src /home/shudaizi/nas/qukuyouxu/.venv/bin/python -m unittest discover -s tests -q`

Expected: all tests pass. Run the healthcheck-binding test outside the restricted sandbox if local socket binding is denied.

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md src/helper/__init__.py src/helper/static tests/test_release_version.py
git commit -m "chore: prepare fixed playlist shell release"
```

## Self-review record

- Spec coverage: every requirement maps to Tasks 1–5.
- Placeholder scan: no deferred implementation placeholders.
- Interface consistency: Task 1 produces view and queue state used by Tasks 2–4; Task 2 media URLs feed Task 3; Task 3 permanent player markup feeds Task 4 layout.
- Review Focus: each listed failure mode has an owning task and a concrete regression test.
