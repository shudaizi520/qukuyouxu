# QQ-Style Playlist Hub and Favorites Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat playlist sidebar with two stable hub pages plus Plex/Plexamp custom playlists, fix FLAC playback and seeking, and add safe playlist editing and one-click Plex favorites.

**Architecture:** Keep the existing FastAPI and browser-native ES-module stack. Extend `PlexClient` with read/write primitives, keep ownership-sensitive assistant operations in `playlist_hub.py`, and add pure playlist-inventory helpers so assistant and native Plex rows are merged by Plex ID with explicit capabilities. All local tracks use one catalog-scoped media route; the browser handles seekable FLAC via byte ranges. A focused front-end section module renders hub cards and the custom-playlist list without turning `playlists.js` into another monolith.

**Tech Stack:** Python 3.11/3.12, FastAPI 0.141.1, Starlette 1.6.0, Requests 2.34.2, SQLite-backed `Store`, browser ES modules, HTML/CSS, `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-21-qq-style-playlist-hub-favorites-design.md`

## Global Constraints

- Do not implement theme switching in this release; keep every new color behind the existing semantic CSS variables.
- Do not copy QQ Music branding, artwork, recommendation data, or proprietary assets.
- Never expose Plex tokens, internal server URLs, media paths, or authentication cookies.
- Playlist deletion must never call a media-file deletion endpoint.
- Preserve ownership-marker and fingerprint protection for assistant-managed playlists.
- Native Plex smart playlists can be played, renamed, and deleted, but their dynamic members cannot be manually added or removed.
- The visible preference control is exactly one binary heart; no stars or numeric rating UI.
- No periodic Plex polling; refresh only on page/profile load and explicit user refresh.
- Use TDD for every behavior change and keep each task independently green before committing.

## Review Focus

- A Plex playlist containing tracks from another selected library must remain visible but expose only current-catalog playable rows and an accurate unavailable count; Task 2 pins this behavior.
- Two assistant records or a native Plex row sharing one `ratingKey` must never render twice; Task 2 tests deterministic assistant precedence.
- A profile switch during a pending list, rating, or edit request must not apply the old response to the new profile; Tasks 4 and 6 test generation guards.
- A failed rating write or mismatched Plex readback must restore the old heart and catalog value; Tasks 4 and 6 cover backend and UI rollback.
- Aborting and immediately replacing a FLAC stream must release the per-session stream slot and must not display an error from the old source; Tasks 1 and 7 exercise cleanup and stale-event handling.

---

### Task 1: Make FLAC playback direct, seekable, and route-independent

**Files:**
- Modify: `src/helper/clients.py:341-389`
- Modify: `src/helper/static/playlists.js:48-59`
- Modify: `src/helper/static/playlist-player.js:1-210`
- Test: `tests/test_v130_external_audio.py`
- Test: `tests/test_v132_playlist_hub.py`

**Interfaces:**
- Consumes: existing `PlexClient.open_browser_audio(track_id, range_header='', offset_seconds=0)` and `/api/playlists/library/tracks/{track_id}/{audio|artwork}` routes.
- Produces: direct FLAC responses with preserved Range headers; `mediaUrl(type, track, context, options)` always returns the catalog-scoped route for non-preview tracks; `seekTo(seconds)` never requests a non-zero transcode offset.

- [ ] **Step 1: Write failing backend and front-end contract tests**

```python
def test_browser_audio_direct_plays_seekable_flac_with_range(self):
    from helper.clients import PlexClient

    class Session:
        def __init__(self):
            self.calls = []
        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return FakeAudioResponse(status=206, headers={
                "Content-Type": "audio/flac", "Content-Length": "1024",
                "Content-Range": "bytes 4096-5119/12000", "Accept-Ranges": "bytes",
            })

    client = object.__new__(PlexClient)
    client.base = "http://plex"
    client.session = Session()
    client._xml = lambda _path: ET.fromstring(
        '<MediaContainer><Track ratingKey="10"><Media container="flac" audioCodec="flac">'
        '<Part key="/library/parts/1/file.flac" container="flac" accessible="1" exists="1" />'
        '</Media></Track></MediaContainer>'
    )
    response = client.open_browser_audio("10", "bytes=4096-")
    method, url, kwargs = client.session.calls[-1]
    self.assertEqual("http://plex/library/parts/1/file.flac", url)
    self.assertEqual("bytes=4096-", kwargs["headers"]["Range"])
    self.assertIsNone(kwargs.get("params"))
    response.close()

def test_all_local_playlist_media_uses_the_catalog_route(self):
    script = (STATIC / "playlists.js").read_text(encoding="utf-8")
    media = script.split("function mediaUrl", 1)[1].split("function playlistContext", 1)[0]
    self.assertIn("/api/playlists/library/tracks/", media)
    self.assertNotIn("/api/playlists/'+encoded(context?.kind)", media)
```

- [ ] **Step 2: Run the focused tests and confirm both fail**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_v130_external_audio tests.test_v132_playlist_hub -v`

Expected: FAIL because FLAC currently enters the transcode branch and playlist contexts still build playlist-specific media URLs.

- [ ] **Step 3: Implement the minimal playback fix**

```python
browser_safe = (
    (container == 'flac' and codec in {'', 'flac'})
    or (container == 'mp3' and codec in {'', 'mp3'})
    or (container in {'m4a', 'mp4'} and codec in {'', 'aac', 'mp3'})
)
```

In `playlists.js`, return `/api/playlists/library/tracks/{id}/{type}` for every non-preview track and keep `context` only for queue identity. In `playlist-player.js`, remove offset URL generation for server transcodes: use native `audio.currentTime` only when `audio.duration` is finite; otherwise keep the prior position, show `当前格式暂不支持拖动`, and do not replace the source.

- [ ] **Step 4: Run focused playback tests**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_v130_external_audio tests.test_v132_playlist_hub -v`

Expected: PASS, including existing automatic-retry, cleanup, range-validation, and stale-generation tests.

- [ ] **Step 5: Commit the playback repair**

```bash
git add src/helper/clients.py src/helper/static/playlists.js src/helper/static/playlist-player.js tests/test_v130_external_audio.py tests/test_v132_playlist_hub.py
git commit -m "fix: make FLAC playlist playback seekable"
```

### Task 2: Build one capability-aware playlist inventory

**Files:**
- Create: `src/helper/playlist_inventory.py`
- Modify: `src/helper/playlist_hub.py:18-116,301-384,488-560`
- Modify: `src/helper/clients.py:406-435`
- Test: `tests/test_v146_playlist_inventory.py`
- Test: `tests/test_v132_playlist_hub.py`

**Interfaces:**
- Consumes: `assistant_playlist_rows(store) -> list[dict]`, `PlexClient.playlists() -> list[dict]`, `PlexClient.playlist_view(playlist_id) -> dict`.
- Produces: `merge_playlist_rows(assistant_rows, plex_rows) -> list[dict]`; `playlist_rows(engine) -> list[dict]` with `section`, `source`, `smart`, and five `can_*` flags; native kind `plex` whose key is the numeric Plex playlist ID; `playlist_detail(engine, 'plex', key)` with `unavailable_count`.

- [ ] **Step 1: Write failing pure-inventory tests**

```python
def test_assistant_rows_win_by_rating_key_and_native_rows_become_custom(self):
    assistant = [{"kind": "daily", "playlist_id": "9", "title": "每日推荐"}]
    plex = [
        {"ratingKey": "9", "title": "duplicate", "playlistType": "audio", "smart": "0"},
        {"ratingKey": "10", "title": "工作", "playlistType": "audio", "smart": "0", "leafCount": "4"},
        {"ratingKey": "11", "title": "四星", "playlistType": "audio", "smart": "1", "leafCount": "8"},
    ]
    rows = merge_playlist_rows(assistant, plex)
    self.assertEqual(["9", "10", "11"], [row["playlist_id"] for row in rows])
    self.assertEqual("custom", rows[1]["section"])
    self.assertTrue(rows[1]["can_add_tracks"])
    self.assertFalse(rows[2]["can_add_tracks"])
```

Add a native-detail test whose Plex items contain IDs `10` and `999` while the scoped catalog contains only `10`; expect track `10`, `unavailable_count == 1`, and no cross-library playback URL.

- [ ] **Step 2: Run the new inventory tests and confirm failure**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_v146_playlist_inventory -v`

Expected: FAIL because `playlist_inventory` and `playlist_view` do not exist.

- [ ] **Step 3: Implement inventory normalization and native read support**

```python
def capabilities(*, source, smart):
    native_regular = source == "plex" and not smart
    return {
        "can_play": True,
        "can_rename": source == "plex",
        "can_add_tracks": native_regular,
        "can_remove_tracks": native_regular,
        "can_delete": source == "plex",
    }

def merge_playlist_rows(assistant_rows, plex_rows):
    owned = {str(row.get("playlist_id") or ""): dict(row) for row in assistant_rows if row.get("playlist_id")}
    native = [native_playlist_row(row) for row in plex_rows if str(row.get("playlistType")) == "audio"]
    return [*assistant_rows, *(row for row in native if row["playlist_id"] not in owned)]
```

Add `PlexClient.playlist_view(pid)` that accepts normal and smart audio playlists and returns `id`, `title`, `summary`, `smart`, and parsed items. Keep `playlist_state(pid)` strict for assistant writers by delegating to `playlist_view()` and rejecting `smart=True`.

- [ ] **Step 4: Make `/api/playlists` use the engine and add fail-soft native listing**

Call `playlist_rows(fixed_engine())`, merge live Plex rows with assistant rows, and cache only the last successful native normalized list in the profile store under `playlist_native_cache_v1`. On Plex read failure, return assistant rows plus that cache with `stale=true`; never store an empty failure result over the last good list.

- [ ] **Step 5: Run inventory and existing hub tests**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_v146_playlist_inventory tests.test_v132_playlist_hub -v`

Expected: PASS with deterministic deduplication, smart capability flags, mixed-library filtering, and legacy assistant ownership checks unchanged.

- [ ] **Step 6: Commit the inventory slice**

```bash
git add src/helper/playlist_inventory.py src/helper/playlist_hub.py src/helper/clients.py tests/test_v146_playlist_inventory.py tests/test_v132_playlist_hub.py
git commit -m "feat: list Plex and assistant playlists together"
```

### Task 3: Add safe native Plex playlist editing

**Files:**
- Modify: `src/helper/playlist_hub.py:116-300,384-560`
- Modify: `src/helper/clients.py:435-493`
- Test: `tests/test_v146_playlist_inventory.py`
- Test: `tests/test_release_blockers.py`

**Interfaces:**
- Consumes: `PlexClient.playlists`, `playlist_view`, `rename`, `append`, `remove_items`, and `delete_playlist`.
- Produces: `rename_playlist(engine, kind, key, title) -> dict`, native branches in `edit_playlist_track(...)` and `remove_playlist(...)`, plus `POST /api/playlists/rename`.

- [ ] **Step 1: Write failing native-mutation tests**

```python
def test_native_regular_playlist_writes_only_after_membership_validation(self):
    result = edit_playlist_track(engine, "plex", "10", "20", "add")
    self.assertEqual("已加入歌单", result["message"])
    self.assertEqual(["10", "20"], [row["id"] for row in plex.playlist_view("10")["items"]])

def test_native_smart_playlist_rejects_manual_membership_changes(self):
    with self.assertRaisesRegex(ValueError, "智能歌单"):
        edit_playlist_track(engine, "plex", "11", "20", "add")

def test_forged_native_id_and_changed_title_cannot_be_deleted(self):
    with self.assertRaises(ValueError):
        remove_playlist(engine, "plex", "999", "伪造")
    with self.assertRaisesRegex(SafetyError, "名称"):
        remove_playlist(engine, "plex", "10", "旧名称")
```

Also assert the fake Plex client never receives a media-file delete call.

- [ ] **Step 2: Run mutation tests and confirm failure**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_v146_playlist_inventory -v`

Expected: FAIL because `plex` is not a writable playlist kind and rename route is absent.

- [ ] **Step 3: Implement fresh-list and fresh-detail validation before every native write**

```python
def _native_playlist(plex, playlist_id):
    playlist_id = str(playlist_id or "")
    listed = {str(row.get("ratingKey") or ""): row for row in plex.playlists() if row.get("playlistType") == "audio"}
    if playlist_id not in listed:
        raise ValueError("当前 Plex 账户没有这个音乐歌单")
    return listed[playlist_id], plex.playlist_view(playlist_id)
```

For rename, add/remove, and delete: validate list membership, numeric IDs, smart restrictions, requested title, and current catalog membership; perform one mutation; read back until the exact expected title/member/deletion state appears. Do not retry mutations after network uncertainty.

- [ ] **Step 4: Add authenticated routes and destructive confirmation copy**

Add `POST /api/playlists/rename` with `confirm:true`. Extend the existing edit/remove routes for `kind='plex'`; keep `ensure_idle()` and `target.exclusive()`. Return copy that states `仅删除歌单，不删除音乐文件` for native deletion.

- [ ] **Step 5: Run mutation and release-blocker tests**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_v146_playlist_inventory tests.test_release_blockers -v`

Expected: PASS, including CSRF/auth/profile isolation and no media deletion primitive.

- [ ] **Step 6: Commit native editing**

```bash
git add src/helper/playlist_hub.py src/helper/clients.py tests/test_v146_playlist_inventory.py tests/test_release_blockers.py
git commit -m "feat: safely edit native Plex playlists"
```

### Task 4: Add binary Plex favorites and the virtual favorites playlist

**Files:**
- Modify: `src/helper/clients.py:219-493`
- Modify: `src/helper/playlist_hub.py:220-560`
- Test: `tests/test_v146_favorites.py`
- Test: `tests/test_v145_web_playback.py`

**Interfaces:**
- Consumes: catalog `user_rating`, Plex `PUT /:/rate`, existing `parse_plex_track`, and scoped profile execution.
- Produces: `PlexClient.rate_track(track_id, rating) -> float`; `set_track_liked(engine, track_id, liked) -> dict`; virtual `kind='favorite', key='liked'` detail; `POST /api/playlists/tracks/liked`.

- [ ] **Step 1: Write failing favorite tests**

```python
def test_existing_four_and_five_star_tracks_are_favorites(self):
    store.set("catalog", [
        {"id": "1", "title": "四星", "user_rating": 8, "available": True},
        {"id": "2", "title": "五星", "user_rating": 10, "available": True},
        {"id": "3", "title": "三星", "user_rating": 6, "available": True},
    ])
    detail = favorite_playlist_detail(store)
    self.assertEqual(["1", "2"], [row["id"] for row in detail["tracks"]])

def test_like_writes_ten_and_commits_only_verified_readback(self):
    result = set_track_liked(engine, "1", True, now=100)
    self.assertEqual(10, plex.ratings[-1])
    self.assertTrue(result["liked"])
    self.assertEqual(10, store.get("catalog")[0]["user_rating"])

def test_mismatched_readback_keeps_old_catalog_rating(self):
    plex.rating_readback = 0
    with self.assertRaisesRegex(SafetyError, "评分"):
        set_track_liked(engine, "1", True, now=100)
    self.assertEqual(0, store.get("catalog")[0]["user_rating"])
```

- [ ] **Step 2: Run favorite tests and confirm failure**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_v146_favorites -v`

Expected: FAIL because rating and favorite-detail functions do not exist.

- [ ] **Step 3: Implement verified Plex rating writes**

```python
def rate_track(self, track_id, rating):
    track_id = str(track_id or "")
    rating = float(rating)
    if not track_id.isdigit() or rating not in (0.0, 10.0):
        raise PlexError("Plex 喜欢状态无效")
    self._xml("/:/rate", "PUT", {
        "identifier": "com.plexapp.plugins.library", "key": track_id, "rating": rating,
    })
    row = parse_plex_track(self._xml(f"/library/metadata/{track_id}").find("Track"))
    return float(row.get("user_rating") or 0)
```

In `set_track_liked`, write 10 or 0, require exact readback, copy-update the matching catalog row, and return `{track_id, liked, user_rating}`. Do not interpret clearing a heart as a negative preference event; current rating and existing playback/webhook learning remain authoritative.

- [ ] **Step 4: Add favorite virtual detail and route**

Return current-library available tracks with `user_rating >= 8`, preserving catalog order. Add a smart-hub row for `favorite/liked`, but never create or overwrite a physical Plex playlist. Route writes must use the authenticated profile and `target.exclusive()`.

- [ ] **Step 5: Run favorite, playback-learning, and hub tests**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_v146_favorites tests.test_v145_web_playback tests.test_v132_playlist_hub -v`

Expected: PASS; existing behavioral learning remains unchanged and no star-selection API appears.

- [ ] **Step 6: Commit favorite support**

```bash
git add src/helper/clients.py src/helper/playlist_hub.py tests/test_v146_favorites.py tests/test_v145_web_playback.py tests/test_v132_playlist_hub.py
git commit -m "feat: add Plex-synced binary favorites"
```

### Task 5: Render the QQ-style fixed hubs and custom-playlist sidebar

**Files:**
- Create: `src/helper/static/playlist-sections.js`
- Modify: `src/helper/static/playlists.html:18-79`
- Modify: `src/helper/static/playlists.js:1-263`
- Modify: `src/helper/static/playlist-workspace.js:1-59`
- Modify: `src/helper/static/product.css:202-270`
- Modify: `src/helper/web.py:300-307`
- Test: `tests/test_v146_playlist_ui.py`
- Test: `tests/test_v132_playlist_hub.py`

**Interfaces:**
- Consumes: playlist rows from Task 2 with `section` and capabilities.
- Produces: `createPlaylistSections({document, onOpenPlaylist, onOpenTool})`; workspace view type `section`; DOM IDs `smartHubButton`, `libraryHubButton`, `customPlaylistList`, and `playlistSectionView`.

- [ ] **Step 1: Write failing structural UI tests**

```python
def test_sidebar_has_two_fixed_hubs_and_one_custom_list(self):
    page = (STATIC / "playlists.html").read_text(encoding="utf-8")
    self.assertEqual(1, page.count('id="smartHubButton"'))
    self.assertEqual(1, page.count('id="libraryHubButton"'))
    self.assertEqual(1, page.count('id="customPlaylistList"'))
    self.assertNotIn('id="playlistAddTrack"', page)

def test_sections_module_groups_without_kind_badges(self):
    script = (STATIC / "playlist-sections.js").read_text(encoding="utf-8")
    self.assertIn("row.section==='smart'", script)
    self.assertIn("row.section==='library'", script)
    self.assertIn("row.section==='custom'", script)
    self.assertNotIn("item.kind==='daily'?'日'", script)
```

- [ ] **Step 2: Run UI tests and confirm failure**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_v146_playlist_ui -v`

Expected: FAIL because the new section DOM and module do not exist.

- [ ] **Step 3: Add the section view and renderer**

```javascript
export function groupPlaylists(items){
 return {
  smart:items.filter(row=>row.section==='smart'),
  library:items.filter(row=>row.section==='library'),
  custom:items.filter(row=>row.section==='custom'),
 };
}
```

Render smart and library rows as responsive cards in the main section view. Render only `custom` rows below the fixed buttons. Add an explicit refresh button next to “自建歌单”. Empty sections show one useful action, not fake playlist rows.

- [ ] **Step 4: Update workspace navigation and styles**

Extend `matches()` and `show()` for `{type:'section', section:'smart'|'library', panel:'section'}`. Preserve a single active navigation item. Add card-grid, visible-focus, truncation, and mobile drawer styles using existing `--playlist-*` variables only.

- [ ] **Step 5: Remove the redundant add button and close-button visibility defect**

Delete `playlistAddTrack` markup, JS references, and loading state. Add explicit `.playlist-dialog-head button{color:var(--playlist-muted)}` plus hover and `:focus-visible` rules; ensure the close glyph remains visible without hover.

- [ ] **Step 6: Run UI and existing hub tests**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_v146_playlist_ui tests.test_v132_playlist_hub -v`

Expected: PASS with two fixed hubs, custom-only sidebar rows, card detail navigation, no redundant button, and one active item.

- [ ] **Step 7: Commit the navigation redesign**

```bash
git add src/helper/static/playlist-sections.js src/helper/static/playlists.html src/helper/static/playlists.js src/helper/static/playlist-workspace.js src/helper/static/product.css src/helper/web.py tests/test_v146_playlist_ui.py tests/test_v132_playlist_hub.py
git commit -m "feat: reorganize playlists into fixed hubs"
```

### Task 6: Wire playlist controls, search targets, and one-heart UI

**Files:**
- Modify: `src/helper/static/playlists.html:44-100`
- Modify: `src/helper/static/playlists.js:65-263`
- Modify: `src/helper/static/playlist-search.js:1-105`
- Modify: `src/helper/static/playlist-player.js:1-210`
- Modify: `src/helper/static/product.css:220-270`
- Test: `tests/test_v146_playlist_ui.py`
- Test: `tests/test_v132_playlist_hub.py`

**Interfaces:**
- Consumes: Task 2 capability flags, Task 3 rename/edit/delete routes, and Task 4 liked route.
- Produces: capability-gated actions, `setLiked(track, liked)`, synchronized row/player heart state, rename dialog, and editable-only search targets.

- [ ] **Step 1: Write failing UI behavior contracts**

```python
def test_ui_has_one_binary_heart_and_no_rating_selector(self):
    page = (STATIC / "playlists.html").read_text(encoding="utf-8")
    script = (STATIC / "playlists.js").read_text(encoding="utf-8")
    self.assertEqual(1, page.count('id="playerLiked"'))
    self.assertIn("track.liked?'♥':'♡'", script)
    self.assertNotRegex(page + script, r"[一二三四五]星|rating-slider|rating-select")

def test_search_targets_only_accept_capable_playlists(self):
    script = (STATIC / "playlist-search.js").read_text(encoding="utf-8")
    self.assertIn("row=>row.playlist_id&&row.can_add_tracks", script)
```

Add source-level assertions that request generations are compared before applying liked/edit/list responses after a profile switch.

- [ ] **Step 2: Run UI tests and confirm failure**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_v146_playlist_ui -v`

Expected: FAIL because heart, rename, and capability gating are absent.

- [ ] **Step 3: Render capabilities instead of inferring behavior from kind**

Show rename only when `can_rename`, delete only when `can_delete`, row remove only when `can_remove_tracks`, and search targets only when `can_add_tracks`. For assistant playlists, preserve existing manage links and manual overrides. For native smart playlists, show a short “歌曲由 Plex 规则生成” explanation instead of disabled add/remove controls.

- [ ] **Step 4: Add rename and destructive confirmation flows**

Add a small native `<dialog>` with current title, validated non-empty maximum-80-character input, and confirm button. Delete confirmation must include the current title and `只删除歌单，不删除音乐文件`; refresh the inventory only after a successful backend response.

- [ ] **Step 5: Add synchronized binary hearts**

```javascript
async function setLiked(track,liked){
 const profileId=loadedProfileId,requestId=++likedRequest;
 const previous=!!track.liked;track.liked=liked;renderTracks();playlistPlayer.syncLiked(track);
 try{
  const result=await json('/api/playlists/tracks/liked','POST',{track_id:String(track.id),liked,confirm:true});
  if(profileId!==loadedProfileId||requestId!==likedRequest)return;
  track.liked=!!result.liked;track.user_rating=result.user_rating;renderTracks();playlistPlayer.syncLiked(track);
 }catch(error){
  if(profileId===loadedProfileId&&requestId===likedRequest){track.liked=previous;renderTracks();playlistPlayer.syncLiked(track);}
  throw error;
 }
}
```

Use an always-visible row heart button and one `playerLiked` button. Both use `aria-pressed`, `♡/♥`, and a title/label that says “喜欢” or “取消喜欢”. Stop propagation on row hearts so liking does not restart playback.

- [ ] **Step 6: Run front-end and hub regressions**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_v146_playlist_ui tests.test_v132_playlist_hub -v`

Expected: PASS with no stars, accurate capability controls, profile-generation guards, and optimistic rollback contracts.

- [ ] **Step 7: Commit interaction wiring**

```bash
git add src/helper/static/playlists.html src/helper/static/playlists.js src/helper/static/playlist-search.js src/helper/static/playlist-player.js src/helper/static/product.css tests/test_v146_playlist_ui.py tests/test_v132_playlist_hub.py
git commit -m "feat: add playlist controls and binary hearts"
```

### Task 7: Run end-to-end regression and production-safe verification

**Files:**
- Modify only if a failing regression requires a scoped fix: files already listed in Tasks 1-6
- Test: full `tests/` suite
- Verify: production Plex via read-only probes, then a dedicated disposable playlist for authorized write verification

**Interfaces:**
- Consumes: all Task 1-6 public routes and UI modules.
- Produces: a green release candidate with recorded verification evidence; no release or deployment occurs without a separate explicit release step.

- [ ] **Step 1: Run focused feature tests together**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_v130_external_audio tests.test_v132_playlist_hub tests.test_v145_web_playback tests.test_v146_playlist_inventory tests.test_v146_favorites tests.test_v146_playlist_ui -v`

Expected: PASS.

- [ ] **Step 2: Run the complete suite**

Run: `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -q`

Expected: all tests PASS on Python 3.11. Run the same command with Python 3.12 when available, matching CI.

- [ ] **Step 3: Run static and packaging checks**

Run: `git diff --check && PYTHONPATH=src .venv/bin/python -m compileall -q src tests && .venv/bin/python -m build --no-isolation`

Expected: zero whitespace errors, compile errors, or package-build errors.

- [ ] **Step 4: Verify read-only production assumptions**

Using the existing approved `ssh truenas` path, confirm without printing tokens: enabled profile/library count, Plex audio playlist total and smart/regular split, FLAC Part range response `206`, and unchanged database mount. Do not modify production during this step.

- [ ] **Step 5: Verify writes only against a dedicated disposable playlist after explicit deployment authorization**

Create or select a clearly named test playlist, then verify rename, add, remove, heart on/off, and delete through the application UI. Confirm the source track remains present after playlist deletion and Plex/Plexamp reflects heart changes. This step is postponed until the user authorizes deployment and production mutation.

- [ ] **Step 6: Inspect final diff and commit any test-only corrections**

Run: `git status --short && git diff --stat HEAD~6..HEAD && git log --oneline -8`

Expected: only scoped source, test, and plan/spec changes; no credentials, database files, generated audio, or unrelated edits.

- [ ] **Step 7: Hand off for code review**

Use `superpowers:requesting-code-review` against the completed branch, resolve every verified issue with `superpowers:receiving-code-review`, rerun the complete suite, and only then use `superpowers:verification-before-completion` before claiming readiness.
