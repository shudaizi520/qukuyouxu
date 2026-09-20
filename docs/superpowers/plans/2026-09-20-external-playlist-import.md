# External Playlist Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import public QQ Music, NetEase Cloud Music, M3U, TXT, and CSV playlists; conservatively match them to one isolated Plex user/library; create and maintain the corresponding Plex playlist; persist uncertain and missing tracks; export a clear replenishment list; and audition matched local tracks safely in the browser.

**Architecture:** Add a profile-scoped relational repository, small source adapters, a thin wrapper around the existing conservative matcher, and an orchestration service that is the only external-playlist business entry point. Plex mutations continue through the existing global serial gate, scheduled rematching reuses the existing library task, and the new vanilla HTML/JS page consumes authenticated APIs without receiving Plex or music-platform credentials.

**Tech Stack:** Python 3.11+, FastAPI/Starlette, SQLite WAL, `requests`, existing Plex and QQ clients, vanilla JavaScript/HTML/CSS, `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-20-external-playlist-import-design.md`

## Global Constraints

- The primary product action is external playlist → local Plex match → explicit Plex playlist creation; the replenishment list is a secondary path for missing tracks.
- QQ Music and NetEase Cloud Music support is read-only and public-source-only; never store their cookies, QR tokens, passwords, or undocumented write credentials.
- Never download third-party audio and never claim a local share URL creates a platform playlist.
- Every source, track, match, run, and managed playlist is isolated by `profile_id`.
- Only a unique reliable match may enter Plex automatically; ambiguous, version-conflicting, unavailable, or metadata-conflicting tracks require review or remain missing.
- A failed, empty, truncated, or suspicious source refresh preserves the last good snapshot and existing Plex playlist.
- Never take over a hand-created Plex playlist, delete music files, or modify an unrelated managed playlist.
- All Plex mutations share the existing global serial queue; automatic refresh reuses the existing `library` automation row.
- Web audio is authenticated, profile-scoped, streamed with HTTP Range, and never records playback-learning evidence.
- No production database, NAS container, GitHub release, or live Plex playlist is changed until the complete local test and copied-data acceptance gates pass.
- Production code changes follow RED-GREEN TDD and each task ends with a focused commit.

The design review used Playlist Lab v1.3.1 only as a product/architecture reference: isolated source integrations, persistent missing-track records, scheduled refresh state, and insertion at a remembered source position. No Playlist Lab source code is copied. This plan keeps those proven boundaries while adding stricter profile isolation, conservative review states, last-good snapshots, guarded mass-removal handling, and no browser-visible Plex token.

## Review Focus

- A source refresh that changes 50 tracks to zero, or removes more than `min(20, old_count * 0.20)`, must preserve the last good snapshot and pause synchronization for confirmation; Task 3 and Task 6 pin this behavior.
- Two profiles importing the same QQ/NetEase playlist must have independent matches, managed Plex IDs, missing lists, and deletion behavior; Task 1, Task 6, and Task 7 pin this behavior.
- Two same-title tracks with different artists or versions must never be silently collapsed, and two equally plausible Plex candidates must enter review; Task 4 pins this behavior.
- A retry after Plex creates a playlist but the HTTP response is interrupted must reconcile by ownership marker and must not create a second same-name playlist; Task 5 pins this behavior.
- A forged audio request for another profile, library, or unavailable `ratingKey`, and a Range response that exceeds configured limits, must fail without exposing the Plex token; Task 9 pins this behavior.

---

## File Structure

- Create `src/helper/external_store.py`: schema, profile-isolated repository, atomic last-good snapshots, managed state, and run history.
- Create `src/helper/external_sources.py`: source and track contracts, supported URL recognition, SSRF-safe HTTP wrapper, and TXT/CSV/M3U parsing.
- Create `src/helper/external_qq.py`: QQ public playlist/top-list adapter using the existing tested QQ client boundary.
- Create `src/helper/external_netease.py`: NetEase public playlist/top-list adapter using injected bounded HTTP.
- Create `src/helper/external_match.py`: public three-state mapping over the existing `match.Catalog` and `match.match` rules.
- Create `src/helper/external_playlist_sync.py`: ownership marker, idempotent create/reconcile, ordered membership updates, and guarded removal.
- Create `src/helper/external_service.py`: import, refresh, match, confirm, publish, rematch, export-status, and automatic refresh orchestration.
- Create `src/helper/external_web.py`: authenticated page APIs, export responses, background-job dispatch, and audio route attachment.
- Create `src/helper/external_audio.py`: validated Plex part lookup and bounded Range streaming.
- Create `src/helper/static/external.html` and `src/helper/static/external.js`: the complete user workflow.
- Modify `src/helper/store.py`: install the additive external schema.
- Modify `src/helper/profiles.py`: remove external rows when a profile is replaced or deleted.
- Modify `src/helper/library_engine.py`: own the external service and run rematching after library maintenance.
- Modify `src/helper/engine.py`: dispatch external background jobs through the existing gate.
- Modify `src/helper/profile_runtime.py`: consider external managed playlists eligible for the library automation row.
- Modify `src/helper/clients.py`: expose a validated Plex audio-part response and preserve ordered playlist operations.
- Modify `src/helper/web.py`: attach routes, serve the page/assets, and keep authentication/CSP/version behavior consistent.
- Modify all five existing static HTML pages and `src/helper/static/product.css`: add one consistent navigation item and responsive external-page styling.
- Modify `README.md`, `CHANGELOG.md`, `src/helper/__init__.py`, and versioned static assets for the eventual `v1.3.0` release only after regression acceptance.
- Add focused `tests/test_v130_*.py` modules and deterministic platform fixtures under `tests/fixtures/external/`.

### Task 1: Profile-scoped external playlist repository

**Files:**
- Create: `src/helper/external_store.py`
- Modify: `src/helper/store.py`
- Modify: `src/helper/profiles.py`
- Test: `tests/test_v130_external_store.py`

**Interfaces:**
- Produces: `ensure_external_schema(db) -> None`
- Produces: `delete_external_profile_rows(db, profile_id: str) -> None`
- Produces: `ExternalRepository(base_store)`
- Produces: `ExternalRepository.upsert_source(profile_id: str, snapshot: dict, now: float) -> dict`
- Produces: `ExternalRepository.replace_snapshot(profile_id: str, source_id: str, snapshot: dict, now: float) -> dict`
- Produces: `ExternalRepository.record_failure(profile_id: str, source_id: str, message: str, now: float) -> dict`
- Produces: `ExternalRepository.list_sources(profile_id: str) -> list[dict]`
- Produces: `ExternalRepository.get_source(profile_id: str, source_id: str) -> dict`
- Produces: `ExternalRepository.list_tracks(profile_id: str, source_id: str) -> list[dict]`
- Produces: `ExternalRepository.replace_matches(profile_id: str, source_id: str, rows: list[dict], catalog_revision: str) -> None`
- Produces: `ExternalRepository.save_managed(profile_id: str, source_id: str, record: dict | None) -> None`
- Produces: `ExternalRepository.append_run(profile_id: str, source_id: str, run: dict) -> None`

- [ ] **Step 1: Write failing schema, isolation, and atomic-snapshot tests**

```python
def test_same_external_id_is_isolated_by_profile(self):
    one = self.repo.upsert_source("default", snapshot("qq", "42"), NOW)
    two = self.repo.upsert_source("parent", snapshot("qq", "42"), NOW)
    self.assertNotEqual(one["id"], two["id"])
    self.assertEqual([one["id"]], [x["id"] for x in self.repo.list_sources("default")])

def test_failed_refresh_keeps_last_good_tracks(self):
    source = self.repo.upsert_source("default", snapshot("qq", "42", count=2), NOW)
    self.repo.record_failure("default", source["id"], "QQ暂时不可用", NOW + 60)
    self.assertEqual(2, len(self.repo.list_tracks("default", source["id"])))
    self.assertEqual("QQ暂时不可用", self.repo.get_source("default", source["id"])["last_error"])

def test_profile_delete_removes_only_that_profiles_external_rows(self):
    self.repo.upsert_source("default", snapshot("qq", "42"), NOW)
    self.repo.upsert_source("parent", snapshot("qq", "42"), NOW)
    self.registry.remove("parent")
    self.assertEqual([], self.repo.list_sources("parent"))
    self.assertEqual(1, len(self.repo.list_sources("default")))
```

- [ ] **Step 2: Run the focused test and verify missing-module/schema failures**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_v130_external_store -v`

Expected: FAIL because `helper.external_store` and its tables do not exist.

- [ ] **Step 3: Add the normalized tables and repository transaction boundary**

```python
def ensure_external_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS external_source (
        id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, provider TEXT NOT NULL,
        external_id TEXT NOT NULL, source_url TEXT NOT NULL, title TEXT NOT NULL,
        follow_updates INTEGER NOT NULL DEFAULT 0, revision TEXT NOT NULL,
        fetched_at REAL NOT NULL, last_error TEXT NOT NULL DEFAULT '',
        failure_count INTEGER NOT NULL DEFAULT 0, next_retry_at REAL,
        needs_confirmation INTEGER NOT NULL DEFAULT 0,
        UNIQUE(profile_id, provider, external_id))""")
    db.execute("CREATE INDEX IF NOT EXISTS external_source_profile ON external_source(profile_id, fetched_at DESC)")
    # external_track, external_match, external_managed and external_run use
    # source_id plus profile_id, with explicit repository-side cascade deletion.
```

Validate every profile with `validate_profile_id`, validate the entire snapshot before entering the transaction, store artists/version flags as deterministic JSON, and replace tracks plus matches in one transaction only after the new list is complete. `append_run` retains the newest 500 rows per profile and newest 100 rows per source so automatic refresh history cannot grow without bound.

- [ ] **Step 4: Install and clean the schema without touching existing state**

Call `ensure_external_schema(db)` beside `ensure_behavior_schema(db)` in `Store.__init__`. Call `delete_external_profile_rows(db, profile_id)` beside `delete_profile_rows` in both profile namespace replacement and profile removal transactions.

- [ ] **Step 5: Re-run focused tests and database integrity checks**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_v130_external_store tests.test_v120_behavior_store tests.test_v108_profile_identity -v`

Expected: PASS; `PRAGMA integrity_check` in the new test returns `ok`, and existing behavior/profile rows survive schema creation.

- [ ] **Step 6: Commit the repository layer**

```bash
git add src/helper/external_store.py src/helper/store.py src/helper/profiles.py tests/test_v130_external_store.py
git commit -m "feat: add isolated external playlist storage"
```

### Task 2: Safe source contracts and local file parsers

**Files:**
- Create: `src/helper/external_sources.py`
- Test: `tests/test_v130_external_sources.py`
- Create: `tests/fixtures/external/sample.m3u8`
- Create: `tests/fixtures/external/sample.csv`

**Interfaces:**
- Produces: `ExternalSourceError(message: str, *, retryable: bool, kind: str)`
- Produces: `recognize_source(value: str) -> dict` with `provider`, `external_id`, and canonical `url`
- Produces: `parse_uploaded_playlist(filename: str, content: bytes) -> dict`
- Produces: `SafeSourceHttp(session=None, resolver=socket.getaddrinfo)`
- Produces: `SafeSourceHttp.get_json(url: str, *, allowed_hosts: set[str], max_bytes: int = 4_194_304) -> dict`
- Produces standard snapshot dictionaries with `provider`, `external_id`, `url`, `title`, and ordered `tracks`.

- [ ] **Step 1: Write failing URL, upload, and SSRF tests**

```python
def test_qq_and_netease_links_are_canonicalized(self):
    self.assertEqual("qq", recognize_source("https://y.qq.com/n/ryqq/playlist/123?ADTAG=x")["provider"])
    self.assertEqual("netease", recognize_source("https://music.163.com/#/playlist?id=456")["provider"])

def test_private_or_credentialed_urls_are_rejected(self):
    for value in ("http://127.0.0.1/x", "https://user:pass@y.qq.com/x", "file:///etc/passwd"):
        with self.assertRaises(ValueError):
            recognize_source(value)

def test_m3u_csv_and_text_keep_order_and_version_text(self):
    result = parse_uploaded_playlist("sample.m3u8", self.fixture("sample.m3u8"))
    self.assertEqual([0, 1], [row["position"] for row in result["tracks"]])
    self.assertEqual("现场版", result["tracks"][1]["version_label"])
```

Also test a manually followed redirect resolving to a private address, more than five redirects, responses above 4 MiB, files above 2 MiB, more than 10,000 tracks, invalid UTF-8 without BOM fallback, CSV formula cells beginning with `=`, `+`, `-`, or `@`, and M3U local paths that must remain metadata rather than server-readable paths.

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_v130_external_sources -v`

Expected: FAIL because the source contract and parsers do not exist.

- [ ] **Step 3: Implement canonical recognition and bounded parsing**

```python
SUPPORTED_HOSTS = {
    "y.qq.com": "qq", "c.y.qq.com": "qq",
    "music.163.com": "netease", "163cn.tv": "netease",
}

def _track(position, title, artists, album="", duration_ms=0, source_id="", source_url=""):
    return {"position": position, "source_track_id": str(source_id),
            "title": clean_text(title, 300), "artists": clean_artists(artists),
            "album": clean_text(album, 300), "duration_ms": bounded_duration(duration_ms),
            "version_label": version_label(title, album), "source_url": safe_public_url(source_url)}
```

Use `csv` from the standard library, parse `#EXTINF` metadata without opening referenced paths, accept common `歌名 - 歌手` and `歌手 - 歌名` text only when the header or column mapping makes the order unambiguous, and return line-specific validation errors rather than guessing all rows.

- [ ] **Step 4: Implement manual redirect and DNS validation**

Disable automatic redirects. Before each request and each redirect, require HTTPS, an allowlisted host, no URL credentials, and only public resolved addresses via `ipaddress.ip_address`. Stream into a bounded buffer and reject mismatched or oversized bodies before JSON decoding.

- [ ] **Step 5: Re-run source tests**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_v130_external_sources -v`

Expected: PASS with no network access.

- [ ] **Step 6: Commit source contracts and file parsing**

```bash
git add src/helper/external_sources.py tests/test_v130_external_sources.py tests/fixtures/external
git commit -m "feat: parse external playlist inputs safely"
```

### Task 3: QQ Music and NetEase public adapters

**Files:**
- Create: `src/helper/external_qq.py`
- Create: `src/helper/external_netease.py`
- Modify: `src/helper/external_sources.py`
- Test: `tests/test_v130_external_platforms.py`
- Create: `tests/fixtures/external/qq_playlist.json`
- Create: `tests/fixtures/external/qq_toplist.json`
- Create: `tests/fixtures/external/netease_playlist.json`

**Interfaces:**
- Consumes: `recognize_source`, `SafeSourceHttp`, and the standard snapshot contract from Task 2.
- Produces: `QQPublicPlaylistSource(qq_client).fetch(recognized: dict) -> dict`
- Produces: `NetEasePublicPlaylistSource(http).fetch(recognized: dict) -> dict`
- Produces: `ExternalProviderRegistry.fetch(recognized: dict) -> dict`

- [ ] **Step 1: Write fixture-driven adapter failures first**

```python
def test_qq_adapter_preserves_all_artists_and_source_order(self):
    result = QQPublicPlaylistSource(FakeQQ(self.fixture_json("qq_playlist.json"))).fetch(QQ)
    self.assertEqual("百万收藏", result["title"])
    self.assertEqual(["歌手甲", "歌手乙"], result["tracks"][0]["artists"])
    self.assertEqual(list(range(len(result["tracks"]))), [x["position"] for x in result["tracks"]])

def test_netease_adapter_rejects_truncated_track_ids(self):
    payload = self.fixture_json("netease_playlist.json")
    payload["playlist"]["trackIds"].append({"id": 999})
    with self.assertRaisesRegex(ExternalSourceError, "不完整"):
        NetEasePublicPlaylistSource(FakeHttp(payload)).fetch(NETEASE)
```

Test private/deleted playlists, platform error codes, repeated pages, total-count changes during pagination, duplicate platform track IDs, empty playlists, HTML instead of JSON, and transient 429/5xx responses classified as retryable.

- [ ] **Step 2: Run platform tests and verify missing-adapter failures**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_v130_external_platforms -v`

Expected: FAIL because platform adapters do not exist.

- [ ] **Step 3: Implement the QQ adapter behind the existing QQ boundary**

Use the existing `QQClient.playlist()` pagination and `parse_qq_tracks()` behavior for playlist IDs. Add a narrowly scoped top-list fetch method to the QQ adapter, not to matching or UI code. Convert `artist` lists without collapsing collaborators, convert seconds to milliseconds, and reject a returned count that differs from the complete unique row set.

- [ ] **Step 4: Implement the NetEase adapter with injected bounded HTTP**

Fetch the public playlist-detail endpoint through `SafeSourceHttp`, compare `trackCount`, `trackIds`, and populated `tracks`, and request missing detail pages in bounded batches. Treat incomplete expansion as a failed refresh; never save a partial snapshot as good.

- [ ] **Step 5: Add suspicious-removal policy tests**

```python
def test_large_removal_requires_confirmation(self):
    self.assertTrue(refresh_needs_confirmation(old_count=50, new_count=39))
    self.assertFalse(refresh_needs_confirmation(old_count=50, new_count=41))
    self.assertTrue(refresh_needs_confirmation(old_count=500, new_count=479))
```

The policy is `removed > min(20, old_count * 0.20)`; a zero result is always invalid. It returns a decision to the service and does not mutate stored snapshots itself.

- [ ] **Step 6: Re-run platform and source tests**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_v130_external_platforms tests.test_v130_external_sources -v`

Expected: PASS entirely from fixtures.

- [ ] **Step 7: Commit platform adapters**

```bash
git add src/helper/external_qq.py src/helper/external_netease.py src/helper/external_sources.py tests/test_v130_external_platforms.py tests/fixtures/external
git commit -m "feat: read public QQ and NetEase playlists"
```

### Task 4: Conservative external-to-Plex matching

**Files:**
- Create: `src/helper/external_match.py`
- Test: `tests/test_v130_external_match.py`

**Interfaces:**
- Consumes: `helper.match.Catalog`, `helper.match.match`, `helper.match.artist_key`, `helper.match.flags`, and `helper.match.title_key`.
- Produces: `match_external_tracks(source_tracks: list[dict], plex_tracks: list[dict], overrides: dict, catalog_revision: str) -> list[dict]`
- Produces match rows containing `source_track_key`, `status` (`matched`, `review`, `missing`, `ignored`), `plex_track_id`, `candidate_ids`, `reason`, `manual`, and `catalog_revision`.
- Produces: `apply_external_confirmation(match_row: dict, choice: dict, catalog: Catalog) -> dict`

- [ ] **Step 1: Write failing identity and ambiguity tests**

```python
def test_unique_same_title_artist_version_duration_matches(self):
    result = match_external_tracks([source("海阔天空", ["Beyond"], 315000)], [plex("7", "海阔天空", "Beyond", 315)], {}, "r1")
    self.assertEqual(("matched", "7"), (result[0]["status"], result[0]["plex_track_id"]))

def test_live_and_studio_versions_do_not_cross_match(self):
    result = match_external_tracks([source("海阔天空 (Live)", ["Beyond"], 320000)], [plex("7", "海阔天空", "Beyond", 315)], {}, "r1")
    self.assertEqual("review", result[0]["status"])
    self.assertEqual("version_mismatch", result[0]["reason"])

def test_equal_candidates_require_review_instead_of_bitrate_tiebreak(self):
    rows = [plex("7", "同名歌", "歌手", 180, album="A"), plex("8", "同名歌", "歌手", 180, album="B")]
    self.assertEqual("review", match_external_tracks([source("同名歌", ["歌手"], 180000)], rows, {}, "r1")[0]["status"])
```

Also pin artist-set mismatch, `AC/DC`, featured artists, traditional/simplified titles, unavailable Plex media, missing source duration, metadata conflicts, manual match, manual missing, and a removed Plex `ratingKey` invalidating an old confirmation.

- [ ] **Step 2: Run focused tests and verify missing-module failures**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_v130_external_match -v`

Expected: FAIL because `external_match` does not exist.

- [ ] **Step 3: Implement the three-state wrapper without weakening `match.py`**

```python
STATUS_MAP = {
    "matched": "matched", "manual_match": "matched",
    "ambiguous": "review", "artist_mismatch": "review",
    "version_mismatch": "review", "duration_mismatch": "review",
    "metadata_conflict": "review", "unavailable": "review",
    "missing": "missing", "missing_tags": "missing",
}
```

Join the complete external artist list for the existing matcher, keep the raw list in the stored row, use the existing strict duration tolerance, and never turn a review result into matched based only on title similarity. A manual choice is valid only while the chosen catalog row exists and is available.

- [ ] **Step 4: Re-run matching plus existing conservative-matcher tests**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_v130_external_match tests.test_v0415_library_maintenance -v`

Expected: PASS; existing theme/library matching behavior remains unchanged.

- [ ] **Step 5: Commit matching**

```bash
git add src/helper/external_match.py tests/test_v130_external_match.py
git commit -m "feat: match external tracks conservatively"
```

### Task 5: Idempotent owned Plex playlist synchronization

**Files:**
- Create: `src/helper/external_playlist_sync.py`
- Modify: `src/helper/clients.py`
- Test: `tests/test_v130_external_playlist_sync.py`

**Interfaces:**
- Produces: `external_marker(installation_id: str, source_id: str) -> str`
- Produces: `create_or_reconcile_external_playlist(plex, installation_id: str, source: dict, managed: dict | None, desired_ids: list[str]) -> tuple[dict, dict]`
- Produces: `delete_owned_external_playlist(plex, installation_id: str, source_id: str, managed: dict, confirm_title: str) -> dict`
- Consumes existing `PlexClient.create`, `playlist_state`, `append`, `remove_items`, `move_item`, `playlists`, and `delete_playlist`.

- [ ] **Step 1: Write failing ownership, retry, order, and deletion tests**

```python
def test_same_name_without_marker_is_never_adopted(self):
    plex = FakePlex(playlists=[{"ratingKey": "9", "title": "百万收藏"}])
    with self.assertRaisesRegex(SafetyError, "同名"):
        create_or_reconcile_external_playlist(plex, INSTALL, SOURCE, None, ["1", "2"])
    self.assertEqual([], plex.created)

def test_retry_after_lost_create_response_finds_exact_owned_marker(self):
    plex = FakePlex(existing_owned("77", SOURCE, ["1", "2"]))
    after, managed = create_or_reconcile_external_playlist(plex, INSTALL, SOURCE, None, ["1", "2"])
    self.assertEqual("77", managed["id"])
    self.assertEqual([], plex.created)

def test_newly_matched_track_is_moved_to_source_position(self):
    plex = FakePlex(existing_owned("77", SOURCE, ["1", "3"]))
    after, _ = create_or_reconcile_external_playlist(plex, INSTALL, SOURCE, MANAGED, ["1", "2", "3"])
    self.assertEqual(["1", "2", "3"], ids(after))
```

Also test empty desired lists never create/clear, duplicate desired IDs are rejected before mutation, changed title/summary/membership blocks writes, partial append is reconciled without duplicate creation, and deletion requires matching ID, title, and ownership marker.

- [ ] **Step 2: Run the sync test and verify missing-module failures**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_v130_external_playlist_sync -v`

Expected: FAIL because the external sync module does not exist.

- [ ] **Step 3: Implement guarded create/reconcile**

Use a marker of `[QKYX:external:<installation-id>:<source-id>]`. Before every mutation, read the live playlist and compare the stored fingerprint. After append/remove/move, retry only reads; never repeat an uncertain mutation. If exact ordering cannot be verified, retain the correct membership, return `order_attention=True`, and stop automatic retries until manual refresh rather than deleting and rebuilding the playlist.

- [ ] **Step 4: Add minimal client support for ordered verification**

Keep `playlist_state()` returning ordered `items`. Do not broaden `sync_owned_items`, whose 1–100 daily-playlist limit and semantics are unrelated. Add only a reusable bounded read-after-write helper if both modules can call it without changing existing daily behavior.

- [ ] **Step 5: Run sync and existing playlist safety tests**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_v130_external_playlist_sync tests.test_daily_fixed_playlist tests.test_v047_smart_mixes tests.test_v108_daily_published_view -v`

Expected: PASS with no existing ownership or rollback behavior changed.

- [ ] **Step 6: Commit Plex synchronization**

```bash
git add src/helper/external_playlist_sync.py src/helper/clients.py tests/test_v130_external_playlist_sync.py
git commit -m "feat: sync owned external Plex playlists safely"
```

### Task 6: External playlist service and automatic rematching

**Files:**
- Create: `src/helper/external_service.py`
- Modify: `src/helper/library_engine.py`
- Modify: `src/helper/engine.py`
- Modify: `src/helper/profile_runtime.py`
- Test: `tests/test_v130_external_service.py`
- Test: `tests/test_v130_external_automation.py`

**Interfaces:**
- Consumes repository, provider registry, matcher, and Plex synchronizer from Tasks 1–5.
- Produces: `ExternalPlaylistService(store, plex_factory, providers)`
- Produces: `ExternalPlaylistService.import_source(value=None, filename=None, content=None) -> dict`
- Produces: `ExternalPlaylistService.refresh(source_id: str, *, force: bool = False) -> dict`
- Produces: `ExternalPlaylistService.match(source_id: str) -> dict`
- Produces: `ExternalPlaylistService.confirm(source_id: str, track_key: str, choice: dict) -> dict`
- Produces: `ExternalPlaylistService.publish(source_id: str, title: str, expected_revision: str) -> dict`
- Produces: `ExternalPlaylistService.set_follow_updates(source_id: str, enabled: bool) -> dict`
- Produces: `ExternalPlaylistService.remove(source_id: str, confirm_title: str) -> dict`
- Produces: `ExternalPlaylistService.rematch_missing() -> dict`
- Produces: `ExternalPlaylistService.auto_refresh() -> dict`
- Produces: `ExternalPlaylistService.public_source(source_id: str) -> dict`

- [ ] **Step 1: Write failing end-to-end service tests**

```python
def test_import_match_publish_and_later_rematch_preserve_source_order(self):
    imported = self.service.import_source(value=QQ_URL)
    self.assertEqual({"matched": 2, "review": 1, "missing": 1}, imported["counts"])
    published = self.service.publish(imported["id"], "百万收藏", imported["revision"])
    self.assertEqual(["10", "30"], self.plex.playlist_ids(published["playlist_id"]))
    self.plex.catalog.append(local("20", "缺失歌", "歌手乙"))
    result = self.service.rematch_missing()
    self.assertEqual(["10", "20", "30"], self.plex.playlist_ids(published["playlist_id"]))
    self.assertEqual(0, result["missing"])
```

Add tests for stale revision rejecting publish, import deduplication, confirmation isolation, no reliable match preventing create, follow-up source additions, large removal pausing sync, failure preserving prior counts, one source failure not blocking another, and one profile never reading another profile's source ID.

Add a clock-controlled retry test proving consecutive transient failures schedule 15 minutes, 1 hour, 6 hours, then 24 hours; manual refresh bypasses the wait once, success clears it, and scheduled refresh skips until `next_retry_at` without blocking other sources.

- [ ] **Step 2: Run service tests and verify missing-service failures**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_v130_external_service tests.test_v130_external_automation -v`

Expected: FAIL because orchestration does not exist.

- [ ] **Step 3: Implement the single orchestration boundary**

```python
class ExternalPlaylistService:
    def _catalog(self):
        cfg = self.store.get("settings", {})
        plex = self.plex_factory(cfg)
        tracks = plex.tracks(cfg["section"])
        revision = digest([(row["id"], track_fingerprint(row)) for row in tracks])
        return plex, tracks, revision
```

All public methods resolve `profile_id` from the fixed scoped store, validate source ownership in the repository, record a run, and expose only token-free summaries. `publish` refuses a changed source revision and stores managed state only after a verified Plex readback.

- [ ] **Step 4: Attach the service to each fixed `LibraryEngine` and job dispatcher**

Create `self.external = ExternalPlaylistService(store, self.plex_factory, provider_registry)` in `LibraryEngine.__init__`. Add explicit `external_import`, `external_refresh`, `external_publish`, `external_confirm`, and `external_delete` cases to `Engine.start_job`; pass only validated scalar arguments and do not allow arbitrary callback dispatch.

- [ ] **Step 5: Integrate rematching with existing library automation**

After the Plex catalog has been refreshed, call `self.external.rematch_missing()` even when QQ enrichment finds zero new metadata rows. Then call `self.external.auto_refresh()` for sources with `follow_updates=True`. Include `external` results in `incremental_status` without replacing existing `base`, `theme`, or message fields.

Change `ProfileRuntime._eligible_for_task(engine, "library")` to return true when either existing category playlists or `ExternalRepository.has_managed(profile_id)` is true. Do not add another automation row or clock.

- [ ] **Step 6: Re-run service, automation, and current scheduler regressions**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_v130_external_service tests.test_v130_external_automation tests.test_v114_global_automation tests.test_v114_upgrade_integration tests.test_v0416_incremental_library -v`

Expected: PASS; task ordering stays library → smart → daily, and profiles remain serialized.

- [ ] **Step 7: Commit orchestration**

```bash
git add src/helper/external_service.py src/helper/library_engine.py src/helper/engine.py src/helper/profile_runtime.py tests/test_v130_external_service.py tests/test_v130_external_automation.py
git commit -m "feat: orchestrate external playlists and rematching"
```

### Task 7: Authenticated APIs and replenishment exports

**Files:**
- Create: `src/helper/external_web.py`
- Create: `src/helper/external_export.py`
- Modify: `src/helper/web.py`
- Test: `tests/test_v130_external_api.py`
- Test: `tests/test_v130_external_export.py`

**Interfaces:**
- Produces: `attach_external_routes(app, store, engine, runtime, profiles, body, ensure_idle) -> None`
- Produces: `format_missing_text(rows: list[dict]) -> str`
- Produces: `format_missing_csv(rows: list[dict]) -> bytes`
- HTTP: `GET /api/external/sources`
- HTTP: `GET /api/external/sources/{source_id}`
- HTTP: `POST /api/external/import`
- HTTP: `POST /api/external/sources/{source_id}/refresh`
- HTTP: `POST /api/external/sources/{source_id}/confirm`
- HTTP: `POST /api/external/sources/{source_id}/publish`
- HTTP: `POST /api/external/sources/{source_id}/settings`
- HTTP: `POST /api/external/sources/{source_id}/remove`
- HTTP: `GET /api/external/sources/{source_id}/export?format=text|csv`

- [ ] **Step 1: Write failing route contract, auth, and export tests**

```python
def test_publish_requires_confirm_and_current_revision(self):
    response = self.post(f"/api/external/sources/{SOURCE}/publish", {"title": "百万收藏", "revision": "r1"})
    self.assertEqual(400, response.status_code)
    response = self.post(f"/api/external/sources/{SOURCE}/publish", {"confirm": True, "title": "百万收藏", "revision": "stale"})
    self.assertEqual(400, response.status_code)

def test_export_contains_only_current_missing_tracks(self):
    body = format_missing_text([missing("歌一", ["甲"]), matched("歌二", ["乙"])])
    self.assertEqual("歌一 - 甲\n", body)
```

Also test unauthenticated 401, mismatched `X-Plex-Profile`, source IDs from another profile, unknown export format, CSV formula escaping, CR/LF removal, duplicate missing rows, more than 2 MiB upload rejection, and `remove` requiring exact title plus `confirm=True`.

- [ ] **Step 2: Run API/export tests and verify route failures**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_v130_external_api tests.test_v130_external_export -v`

Expected: FAIL because routes and exporters do not exist.

- [ ] **Step 3: Implement compact public serializers and exports**

Return source title, provider, counts, revision, follow state, managed summary, job/run summary, and paginated track rows. Never return a Plex token, platform response body, local file path, ownership marker, raw database error, or all candidates beyond the public maximum of three.

Text rows use `歌名 - 歌手甲 / 歌手乙`. CSV begins with the UTF-8 BOM for spreadsheet compatibility and prefixes formula-leading cells with a single quote. Download names use a sanitized source title plus `-缺失歌曲`.

- [ ] **Step 4: Dispatch mutations through fixed-profile background jobs**

Each POST validates the current request profile before starting work. Long import/refresh/publish operations use `engine.start_job`; confirmation and follow-setting changes use `engine.exclusive()` and repository transactions. Responses report “已开始” until repository/run state proves completion.

- [ ] **Step 5: Attach routes to `create_app` and rerun API tests**

Call `attach_external_routes` after profile and automation routes. Keep the global middleware as the authentication, origin, body-size, and profile-pinning boundary.

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_v130_external_api tests.test_v130_external_export tests.test_v108_profile_request_scope tests.test_auth -v`

Expected: PASS.

- [ ] **Step 6: Commit APIs and export behavior**

```bash
git add src/helper/external_web.py src/helper/external_export.py src/helper/web.py tests/test_v130_external_api.py tests/test_v130_external_export.py
git commit -m "feat: expose external playlist workflow safely"
```

### Task 8: External playlist page and unambiguous workflow UI

**Files:**
- Create: `src/helper/static/external.html`
- Create: `src/helper/static/external.js`
- Modify: `src/helper/static/product.css`
- Modify: `src/helper/static/daily.html`
- Modify: `src/helper/static/home.html`
- Modify: `src/helper/static/mixes.html`
- Modify: `src/helper/static/status.html`
- Modify: `src/helper/static/settings.html`
- Modify: `src/helper/web.py`
- Test: `tests/test_v130_external_ui.py`

**Interfaces:**
- Consumes authenticated routes from Task 7.
- Produces page route `GET /external`.
- Produces static asset `/static/external.js`.

- [ ] **Step 1: Write failing semantic and navigation tests**

```python
def test_external_page_keeps_primary_and_missing_actions_distinct(self):
    page = (STATIC / "external.html").read_text(encoding="utf-8")
    self.assertIn("在 Plex 创建", page)
    self.assertIn("生成补歌清单", page)
    self.assertNotIn("发送到 QQ 音乐", page)
    self.assertNotIn("发送到网易云音乐", page)

def test_all_pages_link_to_external_playlists_once(self):
    for name in ("daily.html", "home.html", "mixes.html", "status.html", "settings.html", "external.html"):
        self.assertEqual(1, (STATIC / name).read_text(encoding="utf-8").count('href="/external"'), name)
```

Also assert source input/file upload, counts, three tabs, publish button with actual count, explicit import instructions, TXT/CSV actions, share-link disclaimer, no platform-write claim, unique element IDs, escaped DOM rendering via `textContent`, and mobile CSS without horizontal page overflow.

- [ ] **Step 2: Run the UI test and verify missing-page failures**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_v130_external_ui -v`

Expected: FAIL because the page and asset do not exist.

- [ ] **Step 3: Build the four simple UI states**

Implement only: empty/importing, analyzed result, managed source, and replenishment panel. Use the existing cards, buttons, notices, authentication events, `X-Plex-Profile` request wrapper, and polling pattern. Keep the initial page to one input, one primary button, and one file action; do not expose confidence numbers or internal provider errors.

- [ ] **Step 4: Implement safe track rendering and actions**

Construct every song row with `document.createElement` and `textContent`. The result tabs show matched, review, and missing counts. Publish text is generated as `在 Plex 创建“<title>”歌单（<matched>首）`; it never implies that missing tracks are included.

The replenishment panel provides `复制歌单内容`, `下载歌单长图`, and `更多格式`. Generate long images from the already authenticated JSON in canvases of at most 80 rows each so a large list does not create an unbounded bitmap. `复制清单页面链接` copies `/external?source=<id>&tab=missing` and visibly states that it is only a viewing link. Each missing row also offers URL-encoded `去 QQ 音乐搜索` and `去网易云音乐搜索` links; clicking them never changes match state.

- [ ] **Step 5: Serve the page/assets and update navigation**

Add `/external` using `render_versioned_html`, add `external.js` to the static allowlist, and add one navigation link between intelligent playlists and library organization on every page. Let the renderer replace the asset version rather than adding a second version writer.

- [ ] **Step 6: Run UI and page-version regressions**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_v130_external_ui tests.test_page_version_v0330 tests.test_page_version_v0331 tests.test_v046_ui_consistency tests.test_v047_http_ui -v`

Expected: PASS.

- [ ] **Step 7: Commit the page**

```bash
git add src/helper/static/external.html src/helper/static/external.js src/helper/static/product.css src/helper/static/daily.html src/helper/static/home.html src/helper/static/mixes.html src/helper/static/status.html src/helper/static/settings.html src/helper/web.py tests/test_v130_external_ui.py
git commit -m "feat: add external playlist workflow page"
```

### Task 9: Authenticated local Plex audition

**Files:**
- Create: `src/helper/external_audio.py`
- Modify: `src/helper/clients.py`
- Modify: `src/helper/external_web.py`
- Modify: `src/helper/static/external.js`
- Test: `tests/test_v130_external_audio.py`

**Interfaces:**
- Produces: `PlexClient.open_audio_part(track_id: str, range_header: str = "") -> requests.Response`
- Produces: `stream_local_audio(store, plex_factory, source_id: str, track_key: str, range_header: str, session_key: str) -> StreamingResponse`
- HTTP: `GET /api/external/sources/{source_id}/tracks/{track_key}/audio`

- [ ] **Step 1: Write failing authorization, Range, and cleanup tests**

```python
def test_audio_requires_matched_track_in_current_profile(self):
    with self.assertRaisesRegex(ValueError, "当前歌单"):
        stream_local_audio(self.other_profile, self.factory, SOURCE, TRACK, "bytes=0-1023")

def test_range_and_safe_headers_are_forwarded_without_token(self):
    response = stream_local_audio(self.store, self.factory, SOURCE, TRACK, "bytes=0-1023")
    self.assertEqual(206, response.status_code)
    self.assertEqual("bytes 0-1023/4096", response.headers["Content-Range"])
    self.assertNotIn("X-Plex-Token", response.headers)
```

Also test invalid/multiple ranges, source track now marked missing, `ratingKey` outside the active catalog, Plex redirects, oversized `Content-Length`, upstream timeout, client disconnect closing the upstream response, and at most two active streams per authenticated session/profile.

- [ ] **Step 2: Run the audio test and verify missing-stream failures**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_v130_external_audio -v`

Expected: FAIL because the streaming boundary does not exist.

- [ ] **Step 3: Implement validated Plex part lookup and streaming**

Resolve `/library/metadata/{ratingKey}` through the existing authenticated Plex session, require one accessible audio `Part` whose key starts with `/library/parts/`, then request it with redirects disabled and the validated single Range header. Forward only `Content-Type`, `Content-Length`, `Content-Range`, `Accept-Ranges`, and status 200/206. Reject declared audio bodies above 1 GiB, stream 64 KiB chunks, derive the concurrency key from a one-way digest of the authenticated session plus profile ID, and close upstream in a response background callback.

- [ ] **Step 4: Add the review-only audio control**

Render a single HTML `<audio>` element shared by all matched/review rows. Clicking another track stops the prior stream. Missing rows have no play action. Do not call any Plex playback timeline endpoint and do not write behavior events.

- [ ] **Step 5: Run audio, webhook, and performance regressions**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_v130_external_audio tests.test_v120_webhook_integration tests.test_v120_learning_end_to_end tests.test_v120_recommendation_performance -v`

Expected: PASS; audition leaves behavior counts unchanged.

- [ ] **Step 6: Commit audition support**

```bash
git add src/helper/external_audio.py src/helper/clients.py src/helper/external_web.py src/helper/static/external.js tests/test_v130_external_audio.py
git commit -m "feat: stream matched Plex tracks for review"
```

### Task 10: Full regression, copied-data acceptance, documentation, and release readiness

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `src/helper/__init__.py`
- Modify: `src/helper/static/daily.html`
- Modify: `src/helper/static/home.html`
- Modify: `src/helper/static/mixes.html`
- Modify: `src/helper/static/status.html`
- Modify: `src/helper/static/settings.html`
- Modify: `src/helper/static/external.html`
- Create: `tests/test_v130_upgrade_integration.py`
- Modify: `tests/test_release_version.py`

**Interfaces:**
- Produces release candidate version `1.3.0` only after every earlier task passes.

- [ ] **Step 1: Add upgrade-preservation and clean-install tests**

```python
def test_v130_schema_upgrade_preserves_existing_product_state(self):
    before = snapshot_existing_state(self.store)
    ensure_external_schema_for_store(self.store)
    self.assertEqual(before, snapshot_existing_state(self.store))

def test_new_profile_starts_with_no_external_sources(self):
    created = self.registry.create_for_library("default", {"id": "22", "name": "长辈音乐"})
    self.assertEqual([], ExternalRepository(self.store).list_sources(created["id"]))
```

Also assert an old v1.2.1 database opens without rewriting existing JSON keys, external rows are excluded from credential/cache export archives unless a future archive version explicitly supports them, and a removed profile leaves no external rows.

- [ ] **Step 2: Run all focused v1.3.0 tests**

Run: `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_v130_*.py' -v`

Expected: PASS.

- [ ] **Step 3: Run the complete regression and repository gates**

Run: `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -q`

Run: `.venv/bin/python -m compileall -q src tests`

Run: `.venv/bin/python -m pip check`

Run: `PYTHONPATH=src .venv/bin/python tools/check_repository.py`

Expected: all commands exit 0; no credentials, database files, fixtures containing real user data, duplicate routes, duplicate HTML IDs, or stale versions are reported.

- [ ] **Step 4: Exercise a copied-data HTTP acceptance instance**

Copy the application data directory to a new temporary acceptance directory, never the live path. Start on an unused port and verify:

```text
GET  /healthz                                      200, version unchanged before release bump
GET  /external                                     200 after login
POST /api/external/import with fixture file        completes and returns isolated counts
POST /api/external/.../publish with fake Plex      one owned playlist, no duplicate on retry
GET  /api/external/.../export?format=text          only current missing tracks
GET  all existing daily/library/mixes/status APIs  unchanged profile-visible data
```

Stop the acceptance instance and run SQLite `PRAGMA integrity_check`; compare existing cache, behavior-event, profile, managed-playlist, and automation counts before and after schema initialization.

- [ ] **Step 5: Document the real workflow and limits**

README and changelog must say: public playlist import creates a Plex playlist only from reliable local matches; uncertain and missing tracks remain visible; the replenishment export is copied into the official client and is not direct platform writing; no external audio is downloaded; source failures preserve the last good snapshot.

- [ ] **Step 6: Bump all release-owned version surfaces to 1.3.0**

Set `helper.__version__` to `1.3.0`, update literal static asset query versions to `1.3.0` for source consistency, add the changelog entry, and extend release-version tests to require tag `v1.3.0` when preparing the release.

- [ ] **Step 7: Re-run every release gate after the version bump**

Run: `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -q`

Run: `.venv/bin/python -m compileall -q src tests`

Run: `.venv/bin/python -m pip check`

Run: `PYTHONPATH=src .venv/bin/python tools/check_repository.py`

Run: `git diff --check`

Expected: all exit 0 and every rendered page reports `v1.3.0` with `?v=1.3.0` assets.

- [ ] **Step 8: Commit the verified release candidate without publishing it**

```bash
git add README.md CHANGELOG.md src/helper/__init__.py src/helper/static tests/test_v130_upgrade_integration.py tests/test_release_version.py
git commit -m "release: prepare external playlists v1.3.0"
```

Do not push GitHub, build/push the public image, update the NAS container, or create a release tag until the user has reviewed the verified local result and explicitly asks to publish.
