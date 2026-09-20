# Adaptive Playback Learning V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fragile list-based playback learner with profile-isolated incremental evidence storage, confirmed-skip learning, adaptive cooldowns, balanced daily recommendations, and optional Plex Sonic similarity.

**Architecture:** Keep raw evidence, per-track aggregates, and per-user baselines in indexed SQLite tables inside the existing application database. A pure playback state machine converts Webhooks into evidence, a pure preference model updates aggregates, and Daily Mix V2 consumes only the aggregate interface. Plex Sonic remains an optional ranking input and never blocks playlist generation.

**Tech Stack:** Python 3.11/3.12, SQLite WAL, FastAPI/Starlette, existing vanilla JavaScript UI, `unittest` test suite.

**Spec:** `docs/superpowers/specs/2026-09-20-adaptive-playback-learning-and-daily-recommendation-design.md`

## Global Constraints

- Retain raw behavior evidence for 180 days and at most 20,000 rows per profile.
- Never read, modify, upload, or independently analyze music files.
- Keep every Plex user and library profile isolated by `profile_id`.
- A stop without a confirmed next-track transition must not create negative evidence.
- Explicit avoid and active cooldown are hard exclusions and may never be relaxed to fill a playlist.
- Plex Sonic is optional; timeouts, empty results, and errors must fall back to metadata and behavior.
- New profiles keep playback learning enabled by default; no new algorithm settings are exposed.
- Existing playlists, catalog cache, automation settings, and profile registry survive migration unchanged.
- All implementation follows test-driven development and preserves Python 3.11 compatibility.

## Review Focus

- Missing duration in a Webhook must use the profile catalog duration; missing duration in both places must remain neutral.
- An out-of-order `media.stop` after `media.scrobble` must not replace a completion with a skip.
- A next-track event from another player, user, server, or library must not confirm the pending skip.
- Re-running migration after a partial restart must not duplicate evidence or alter an already migrated profile.
- A Sonic timeout and an undersized candidate bucket must still produce a valid, isolated, duplicate-free plan without relaxing hard exclusions.

---

## File Structure

- Create `src/helper/behavior_store.py`: SQLite schema, event retention, aggregate rows, migration, and profile statistics.
- Create `src/helper/playback_learning.py`: pure playback session state machine and evidence classification.
- Create `src/helper/preference_model.py`: evidence decay, personal baseline, recovery, and cooldown calculations.
- Create `src/helper/daily_mix_v2.py`: mutually exclusive buckets, adaptive discovery quota, scoring, and diversity selection.
- Modify `src/helper/store.py`: initialize behavior tables in the existing application database.
- Modify `src/helper/plex_webhook.py`: route parsed events through the state machine and repository.
- Modify `src/helper/daily.py`: read V2 aggregates and evidence instead of the JSON event list.
- Modify `src/helper/rotation.py`: switch the active recommendation adapter to V2.
- Modify `src/helper/extra_web.py`: expose simple V2 status and V2 daily diagnostics.
- Modify `src/helper/recommend.py`: map V2 bucket names into public diagnostics.
- Modify `src/helper/static/status.html` and `src/helper/static/status.js`: rename negative count to cooldown count.
- Modify `src/helper/__init__.py`, `CHANGELOG.md`, and versioned static HTML: publish the implementation as version 1.2.0 after verification.
- Create focused V2 test modules; retain existing tests as regression coverage.

### Task 1: Indexed behavior repository and idempotent migration

**Files:**
- Create: `src/helper/behavior_store.py`
- Modify: `src/helper/store.py`
- Test: `tests/test_v120_behavior_store.py`

**Interfaces:**
- Produces: `ensure_behavior_schema(db) -> None`
- Produces: `BehaviorRepository(base_store)`
- Produces: `BehaviorRepository.append_event(profile_id: str, event: dict) -> bool`
- Produces: `BehaviorRepository.list_events(profile_id: str, now: float, limit: int = 20000) -> list[dict]`
- Produces: `BehaviorRepository.load_track_states(profile_id: str) -> dict[str, dict]`
- Produces: `BehaviorRepository.save_track_state(profile_id: str, track_id: str, state: dict) -> None`
- Produces: `BehaviorRepository.load_user_state(profile_id: str) -> dict`
- Produces: `BehaviorRepository.save_user_state(profile_id: str, state: dict) -> None`
- Produces: `BehaviorRepository.migrate_profile(profile_id: str, legacy_events: list[dict], now: float) -> dict`

- [ ] **Step 1: Write repository schema and retention tests**

```python
def test_append_is_idempotent_and_profile_scoped(self):
    repo = BehaviorRepository(self.store)
    row = {"event_key": "one", "track_id": "7", "kind": "completed", "value": 1.0, "at": NOW}
    self.assertTrue(repo.append_event("default", row))
    self.assertFalse(repo.append_event("default", row))
    self.assertEqual(["7"], [x["track_id"] for x in repo.list_events("default", NOW)])
    self.assertEqual([], repo.list_events("friend", NOW))

def test_prune_keeps_180_days_and_latest_20000_per_profile(self):
    repo = BehaviorRepository(self.store)
    repo._event_limit = 3
    for i in range(5):
        repo.append_event("default", {"event_key": str(i), "track_id": str(i), "kind": "completed", "value": 1, "at": NOW - i})
    repo.prune("default", NOW)
    self.assertEqual(["0", "1", "2"], [x["track_id"] for x in repo.list_events("default", NOW)])
```

- [ ] **Step 2: Run repository tests and verify failure**

Run: `PYTHONPATH=src python -m unittest tests.test_v120_behavior_store -v`

Expected: FAIL because `helper.behavior_store` does not exist.

- [ ] **Step 3: Add the relational schema and repository**

```python
EVENT_MAX_AGE = 180 * 86400
EVENT_LIMIT = 20_000

def ensure_behavior_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS behavior_event (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id TEXT NOT NULL,
        event_key TEXT NOT NULL,
        track_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        value REAL NOT NULL,
        at REAL NOT NULL,
        progress REAL,
        duration REAL,
        player_id TEXT NOT NULL DEFAULT '',
        playback_id TEXT NOT NULL DEFAULT '',
        payload TEXT NOT NULL DEFAULT '{}',
        UNIQUE(profile_id, event_key)
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS behavior_event_profile_at ON behavior_event(profile_id, at DESC)")
    db.execute("CREATE INDEX IF NOT EXISTS behavior_event_profile_track ON behavior_event(profile_id, track_id, at DESC)")
    db.execute("""CREATE TABLE IF NOT EXISTS behavior_track_state (
        profile_id TEXT NOT NULL,
        track_id TEXT NOT NULL,
        state TEXT NOT NULL,
        PRIMARY KEY(profile_id, track_id)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS behavior_user_state (
        profile_id TEXT PRIMARY KEY,
        state TEXT NOT NULL
    )""")
```

Use parameterized SQL, `validate_profile_id`, compact JSON payloads, and a delete query that keeps only the newest 20,000 rows after removing entries older than `now - EVENT_MAX_AGE`.

- [ ] **Step 4: Add idempotent legacy migration tests and implementation**

```python
def test_legacy_migration_is_idempotent_and_conservative(self):
    legacy = [
        {"track_id": "1", "kind": "completed", "value": 1, "at": NOW},
        {"track_id": "2", "kind": "observed_skip", "value": -0.5, "at": NOW},
    ]
    first = self.repo.migrate_profile("default", legacy, NOW)
    second = self.repo.migrate_profile("default", legacy, NOW)
    self.assertEqual(2, first["inserted"])
    self.assertEqual(0, second["inserted"])
    rows = self.repo.list_events("default", NOW)
    self.assertEqual(-0.30, next(row["value"] for row in rows if row["track_id"] == "2"))
```

Migration event keys must be deterministic hashes of profile, track, kind, timestamp, and original index. Clamp legacy ordinary skip magnitude to `0.30`; keep explicit low ratings and positive values unchanged. Store a `profile:<id>:behavior_v2_migration` marker only after the transaction commits.

- [ ] **Step 5: Initialize the schema from `Store` and run tests**

Add `ensure_behavior_schema(db)` beside the existing `state` table creation inside `Store.__init__`.

Run: `PYTHONPATH=src python -m unittest tests.test_v120_behavior_store tests.test_v108_profile_libraries -v`

Expected: PASS.

- [ ] **Step 6: Commit repository work**

```bash
git add src/helper/store.py src/helper/behavior_store.py tests/test_v120_behavior_store.py
git commit -m "feat: add indexed playback evidence store"
```

### Task 2: Playback transition state machine

**Files:**
- Create: `src/helper/playback_learning.py`
- Test: `tests/test_v120_playback_learning.py`

**Interfaces:**
- Consumes: parsed signal dictionaries from `plex_webhook.parse_webhook_payload`
- Produces: `advance_playback(sessions: dict, signal: dict, now: float, catalog_duration: float = 0) -> tuple[dict, list[dict]]`
- Evidence dictionaries contain `event_key`, `track_id`, `kind`, `value`, `at`, `progress`, `duration`, `player_id`, and `playback_id`.

- [ ] **Step 1: Write transition tests for confirmed and unconfirmed stops**

```python
def test_stop_without_next_track_is_neutral(self):
    sessions, rows = advance_playback({}, signal("media.play", "1"), 0, 240)
    sessions, rows = advance_playback(sessions, signal("media.stop", "1", offset=30), 30, 240)
    self.assertEqual([], rows)

def test_same_player_next_track_confirms_early_skip(self):
    sessions, _ = advance_playback({}, signal("media.play", "1"), 0, 240)
    sessions, _ = advance_playback(sessions, signal("media.stop", "1", offset=30), 30, 240)
    sessions, rows = advance_playback(sessions, signal("media.play", "2"), 40, 180)
    self.assertEqual(("confirmed_skip", 0.45), (rows[0]["kind"], rows[0]["value"]))

def test_different_player_does_not_confirm_pending_skip(self):
    sessions, _ = advance_playback({}, signal("media.play", "1", player="a"), 0, 240)
    sessions, _ = advance_playback(sessions, signal("media.stop", "1", player="a", offset=30), 30, 240)
    _, rows = advance_playback(sessions, signal("media.play", "2", player="b"), 40, 180)
    self.assertEqual([], rows)
```

- [ ] **Step 2: Run state-machine tests and verify failure**

Run: `PYTHONPATH=src python -m unittest tests.test_v120_playback_learning -v`

Expected: FAIL because `advance_playback` does not exist.

- [ ] **Step 3: Implement session keys, pending stops, and classification**

```python
NEXT_TRACK_WINDOW = 30

def classify_transition(session, duration):
    progress = min(1.0, max(0.0, session["effective_seconds"] / duration))
    if progress >= 0.80:
        return "substantial_listen", 0.75
    if progress < 0.20 and session["active_seconds"] <= 60:
        return "confirmed_skip", 0.45
    if progress < 0.50:
        return "confirmed_skip", 0.30
    return "late_exit", 0.10
```

Key sessions by machine, account, library, and player. Preserve pending stops for 30 seconds. A new track from the exact same identity confirms the prior transition; expiry deletes the pending stop without evidence.

- [ ] **Step 4: Add pause, seek, replay, missing-duration, duplicate, and out-of-order tests**

```python
def test_missing_webhook_duration_uses_catalog_duration(self):
    sessions, _ = advance_playback({}, signal("media.play", "1", duration=0), 0, 200)
    sessions, _ = advance_playback(sessions, signal("media.stop", "1", duration=0, offset=20), 20, 200)
    _, rows = advance_playback(sessions, signal("media.play", "2"), 25, 180)
    self.assertEqual(0.45, rows[0]["value"])

def test_missing_all_duration_remains_neutral(self):
    sessions, _ = advance_playback({}, signal("media.play", "1", duration=0), 0, 0)
    sessions, _ = advance_playback(sessions, signal("media.stop", "1", duration=0), 20, 0)
    _, rows = advance_playback(sessions, signal("media.play", "2"), 25, 180)
    self.assertEqual([], rows)
```

Also pin these invariants: paused wall time is excluded; a large seek cannot create completion; a scrobble emits one completion; a delayed stop after scrobble emits nothing; replaying the same track creates a new generation but not a skip.

- [ ] **Step 5: Run state-machine tests**

Run: `PYTHONPATH=src python -m unittest tests.test_v120_playback_learning -v`

Expected: PASS.

- [ ] **Step 6: Commit the state machine**

```bash
git add src/helper/playback_learning.py tests/test_v120_playback_learning.py
git commit -m "feat: confirm skips from playback transitions"
```

### Task 3: Preference evidence, personal baseline, cooldown, and recovery

**Files:**
- Create: `src/helper/preference_model.py`
- Test: `tests/test_v120_preference_model.py`

**Interfaces:**
- Produces: `apply_evidence(track_state: dict, user_state: dict, evidence: dict, now: float) -> tuple[dict, dict]`
- Produces: `materialize_track_state(state: dict, now: float) -> dict`
- Produces: `cooldown_days(skip_evidence: float) -> int`
- Public materialized state includes `affinity`, `confidence`, `positive_evidence`, `skip_evidence`, `fatigue`, `cooldown_until`, `hard_avoid`, and `last_event`.

- [ ] **Step 1: Write decay, baseline, cap, cooldown, and recovery tests**

```python
def test_personal_skip_multiplier_is_neutral_before_thirty_events(self):
    self.assertEqual(1.0, personal_skip_multiplier({"valid_outcomes": 29, "skip_outcomes": 20}))

def test_personal_skip_multiplier_is_bounded(self):
    self.assertEqual(1.3, personal_skip_multiplier({"valid_outcomes": 100, "skip_outcomes": 0}))
    self.assertEqual(0.7, personal_skip_multiplier({"valid_outcomes": 100, "skip_outcomes": 100}))

def test_completion_halves_skip_evidence_and_recomputes_cooldown(self):
    state = {"skip_evidence": 1.4, "positive_evidence": 0, "fatigue": 0, "updated_at": NOW}
    state, _ = apply_evidence(state, user(), evidence("completed", 1.0), NOW)
    self.assertLess(state["skip_evidence"], 0.71)
    self.assertLessEqual(state["cooldown_until"], NOW + 7 * DAY)
```

- [ ] **Step 2: Run preference tests and verify failure**

Run: `PYTHONPATH=src python -m unittest tests.test_v120_preference_model -v`

Expected: FAIL because `helper.preference_model` does not exist.

- [ ] **Step 3: Implement pure evidence calculations**

```python
def personal_skip_multiplier(user):
    valid = int(user.get("valid_outcomes", 0))
    if valid < 30:
        return 1.0
    rate = float(user.get("skip_outcomes", 0)) / max(1, valid)
    return max(0.7, min(1.3, 1.25 - rate))

def cooldown_days(value):
    if value < 0.25: return 0
    if value < 0.60: return 1
    if value < 1.20: return 7
    if value < 2.00: return 30
    if value < 3.00: return 90
    return 180
```

Use exponential half-life decay of 180 days for positive evidence, 60 days for skip evidence, and 7 days for fatigue. Apply at most 0.60 ordinary skip evidence per track per local calendar day. A completion halves ordinary skip evidence; explicit like clears it; explicit avoid sets `hard_avoid` until explicitly reversed.

- [ ] **Step 4: Implement affinity and confidence**

Use smoothed evidence so an unseen track remains neutral:

```python
probability = (1.0 + positive) / (2.0 + positive + skip)
confidence = 1.0 - math.exp(-(positive + skip) / 3.0)
affinity = (probability - 0.5) * 2.0 * confidence
```

Late exits update fatigue but not `skip_evidence`. Emit `cooldown_until = now + cooldown_days(skip) * DAY` only when the recalculated duration exceeds the remaining cooldown.

- [ ] **Step 5: Run preference and legacy adaptive tests**

Run: `PYTHONPATH=src python -m unittest tests.test_v120_preference_model tests.test_v0411_adaptive_learning -v`

Expected: PASS after adapting legacy tests to assert the V2 public state rather than the removed fixed-day implementation.

- [ ] **Step 6: Commit preference learning**

```bash
git add src/helper/preference_model.py tests/test_v120_preference_model.py tests/test_v0411_adaptive_learning.py
git commit -m "feat: add adaptive preference and cooldown model"
```

### Task 4: Integrate V2 learning with Webhooks and status

**Files:**
- Modify: `src/helper/plex_webhook.py`
- Modify: `src/helper/extra_web.py`
- Modify: `src/helper/daily.py`
- Test: `tests/test_v120_webhook_integration.py`
- Test: `tests/test_v040_webhook.py`
- Test: `tests/test_v0425_status_refresh.py`

**Interfaces:**
- Consumes: `BehaviorRepository`, `advance_playback`, `apply_evidence`
- Produces: `load_behavior_snapshot(store, now: float) -> dict[str, dict]` for Daily Mix and status
- Preserves: `/api/plex/webhook`, `/api/status`, and profile-scoped behavior enablement.

- [ ] **Step 1: Write failing integration tests**

```python
def test_stop_is_recorded_only_after_same_player_starts_next_track(self):
    apply_webhook_event(self.store, self.registry, payload("media.play", track="1"), now=0)
    apply_webhook_event(self.store, self.registry, payload("media.stop", track="1", viewOffset=20000), now=20)
    self.assertEqual([], self.repo.list_events("default", 20))
    apply_webhook_event(self.store, self.registry, payload("media.play", track="2"), now=25)
    self.assertEqual("confirmed_skip", self.repo.list_events("default", 25)[0]["kind"])

def test_webhook_duration_falls_back_to_profile_catalog_only(self):
    ScopedStore(self.store, "default").set("catalog", [{"id": "1", "duration": 200}])
    # Send play/stop/next with duration omitted and assert one profile-scoped skip.
```

- [ ] **Step 2: Run integration tests and verify failure**

Run: `PYTHONPATH=src python -m unittest tests.test_v120_webhook_integration -v`

Expected: FAIL because Webhooks still write the legacy JSON list.

- [ ] **Step 3: Replace legacy Webhook classification with V2 orchestration**

Keep multipart parsing, secret validation, profile matching, ingress receipts, and replay deduplication. Replace direct `behavior_events` append logic with:

```python
repo = BehaviorRepository(base_store)
duration = catalog_duration(ScopedStore(base_store, profile_id, registry), signal["track_id"])
sessions, evidence_rows = advance_playback(saved_sessions, signal, now, duration)
for evidence in evidence_rows:
    if repo.append_event(profile_id, evidence):
        track = repo.load_track_state(profile_id, evidence["track_id"])
        user = repo.load_user_state(profile_id)
        track, user = apply_evidence(track, user, evidence, now)
        repo.save_track_state(profile_id, evidence["track_id"], track)
        repo.save_user_state(profile_id, user)
```

Persist active sessions in the existing profile-scoped state store because the set is small and bounded. Keep `active_session_count` behavior unchanged.

- [ ] **Step 4: Migrate on first profile read and switch consumers**

`load_behavior_snapshot` must call `migrate_profile` once, return materialized V2 track states, and never mix another profile. Change `DailyMixin._preview_daily` and `extensions_status` to use repository evidence/states. Status fields become `learned_tracks`, `preferred_tracks`, `cooled_tracks`, and `active_sessions`.

- [ ] **Step 5: Run Webhook, isolation, status, and rolling tests**

Run: `PYTHONPATH=src python -m unittest tests.test_v120_webhook_integration tests.test_v040_webhook tests.test_v040_recommendation_isolation tests.test_v0419_childrens_isolation tests.test_v0424_rolling_daily tests.test_v0425_status_refresh -v`

Expected: PASS.

- [ ] **Step 6: Commit V2 integration**

```bash
git add src/helper/plex_webhook.py src/helper/extra_web.py src/helper/daily.py tests/test_v120_webhook_integration.py tests/test_v040_webhook.py tests/test_v0425_status_refresh.py
git commit -m "feat: connect webhooks to playback learning v2"
```

### Task 5: Daily Mix V2 buckets, adaptive discovery, and Sonic fallback

**Files:**
- Create: `src/helper/daily_mix_v2.py`
- Modify: `src/helper/rotation.py`
- Modify: `src/helper/recommend.py`
- Test: `tests/test_v120_daily_mix.py`
- Test: `tests/test_v120_sonic_fallback.py`
- Modify: `tests/test_v0411_daily_policy.py`

**Interfaces:**
- Produces: `select_daily_mix_v2(..., behavior: dict, user_state: dict, similar_ids: dict, preserve_ids: list[str]) -> dict`
- Produces: `recommend_rotating_v2(engine, base_recommend, *args, **kwargs) -> dict`
- Produces algorithm version `daily-mix-v2.0.0`.
- Reuses: `read_plex_history_cached` and a bounded Sonic reader compatible with existing `read_plex_similar` behavior.

- [ ] **Step 1: Write mutually exclusive bucket and quota tests**

```python
def test_fifty_song_mix_uses_v2_targets_without_bucket_overlap(self):
    result = select_daily_mix_v2(...)
    self.assertEqual(50, len(result["items"]))
    self.assertEqual({
        "稳定偏好": 14, "近期口味": 8, "新鲜发现": 14,
        "久未重听": 8, "跨口味探索": 4, "恢复观察": 2,
    }, result["stats"]["bucket_counts"])
    self.assertEqual(50, len({row["id"] for row in result["items"]}))
```

Build test fixtures with enough candidates in every bucket. Assert a newly added stable-looking track is classified only as `新鲜发现` because bucket precedence is exclusive.

- [ ] **Step 2: Write hard exclusion, repeat, diversity, and adaptive discovery tests**

```python
def test_hard_avoid_and_cooldown_are_never_relaxed(self):
    result = select_daily_mix_v2(..., settings={"size": 50})
    ids = {row["id"] for row in result["items"]}
    self.assertNotIn("hard-avoid", ids)
    self.assertNotIn("cooled", ids)

def test_discovery_target_stays_between_fourteen_and_twenty_four(self):
    high = discovery_target({"discovery_valid": 40, "discovery_completed": 32, "discovery_early_skips": 3})
    low = discovery_target({"discovery_valid": 40, "discovery_completed": 4, "discovery_early_skips": 30})
    self.assertEqual(24, high)
    self.assertEqual(14, low)
```

Also assert 21-day ordinary repeat protection, 14-day stable preference protection, at most 2 recovery tracks, default artist cap 2, album cap 1, and ordered soft-cap relaxation only when safe candidates are insufficient.

- [ ] **Step 3: Run Daily Mix V2 tests and verify failure**

Run: `PYTHONPATH=src python -m unittest tests.test_v120_daily_mix -v`

Expected: FAIL because `daily_mix_v2` does not exist.

- [ ] **Step 4: Implement bucket assignment, scoring, and selection**

Use one bucket assignment function with this precedence:

```python
def bucket_for(track, learned, now, recent_similarity):
    if is_new_or_unplayed(track, now): return "新鲜发现"
    if is_recovery_candidate(learned, now): return "恢复观察"
    if learned.get("affinity", 0) > 0.20 and learned.get("confidence", 0) >= 0.20: return "稳定偏好"
    if str(track["id"]) in recent_similarity: return "近期口味"
    if is_rediscovery(track, now): return "久未重听"
    return "跨口味探索"
```

Select target counts first, then fill safe vacancies by score. Globally deduplicate by ID and normalized title/artist key. Order the final list with deterministic seeded jitter while avoiding adjacent artists and categories.

- [ ] **Step 5: Pin Sonic success and fallback behavior**

```python
def test_sonic_neighbors_boost_fresh_tracks_but_do_not_override_cooldown(self):
    result = recommend_rotating_v2(..., similar_ids={"seed": ["fresh", "cooled"]})
    self.assertIn("fresh", [row["id"] for row in result["items"]])
    self.assertNotIn("cooled", [row["id"] for row in result["items"]])

def test_sonic_exception_falls_back_to_metadata(self):
    client._xml.side_effect = TimeoutError("sonic timeout")
    result = recommend_rotating_v2(...)
    self.assertEqual("曲库关系", result["stats"]["similarity_source"])
    self.assertTrue(result["items"])
```

Keep the 7-day similarity cache. Do not add an application setting or touch audio files.

- [ ] **Step 6: Switch rotation adapter and run recommendation regressions**

Run: `PYTHONPATH=src python -m unittest tests.test_v120_daily_mix tests.test_v120_sonic_fallback tests.test_v0411_daily_policy tests.test_daily_v0326 tests.test_v0424_rolling_daily -v`

Expected: PASS with algorithm version `daily-mix-v2.0.0` and updated bucket expectations.

- [ ] **Step 7: Commit Daily Mix V2**

```bash
git add src/helper/daily_mix_v2.py src/helper/rotation.py src/helper/recommend.py tests/test_v120_daily_mix.py tests/test_v120_sonic_fallback.py tests/test_v0411_daily_policy.py
git commit -m "feat: add balanced adaptive daily mix v2"
```

### Task 6: Minimal status UI and public diagnostics

**Files:**
- Modify: `src/helper/static/status.html`
- Modify: `src/helper/static/status.js`
- Modify: `src/helper/extra_web.py`
- Test: `tests/test_v120_status_ui.py`

**Interfaces:**
- `/api/status.behavior` returns `event_count`, `learned_tracks`, `preferred_tracks`, `cooled_tracks`, `active_sessions`, and `updated_at`.
- Existing Webhook connection state remains independent from learning counts.

- [ ] **Step 1: Write failing status copy and API tests**

```python
def test_status_uses_cooled_tracks_instead_of_negative_tracks(self):
    page = Path("src/helper/static/status.html").read_text()
    script = Path("src/helper/static/status.js").read_text()
    self.assertIn("冷却中", page)
    self.assertNotIn("负向歌曲", page)
    self.assertIn("b.cooled_tracks", script)
    self.assertNotIn("b.negative_tracks", script)
```

- [ ] **Step 2: Run status tests and verify failure**

Run: `PYTHONPATH=src python -m unittest tests.test_v120_status_ui -v`

Expected: FAIL because the old copy and field names remain.

- [ ] **Step 3: Implement the four-card UI without new helper text**

Change only the labels and data bindings:

```html
<span>已学习</span>
<span>偏好歌曲</span>
<span>冷却中</span>
<span>当前播放</span>
```

Keep the existing layout, polling behavior, and connection chip. Do not add thresholds, formulas, tooltips, or settings.

- [ ] **Step 4: Run status and static-page tests**

Run: `PYTHONPATH=src python -m unittest tests.test_v120_status_ui tests.test_v0425_status_refresh tests.test_release_version -v`

Expected: PASS before the later version bump changes release expectations.

- [ ] **Step 5: Commit UI changes**

```bash
git add src/helper/static/status.html src/helper/static/status.js src/helper/extra_web.py tests/test_v120_status_ui.py
git commit -m "feat: simplify playback learning status"
```

### Task 7: Migration safety, end-to-end isolation, and performance

**Files:**
- Create: `tests/test_v120_behavior_migration.py`
- Create: `tests/test_v120_learning_end_to_end.py`
- Create: `tests/test_v120_recommendation_performance.py`
- Modify: `src/helper/profiles.py`
- Modify: `src/helper/web.py`

**Interfaces:**
- Consumes all V2 public interfaces.
- Produces profile creation/reset behavior that removes relational behavior rows only for the exact target profile.

- [ ] **Step 1: Test profile reset and migration isolation**

```python
def test_resetting_one_profile_does_not_delete_another_profile_behavior(self):
    self.repo.append_event("default", evidence("a", "1"))
    self.repo.append_event("friend", evidence("b", "2"))
    self.registry.reset("default")
    self.assertEqual([], self.repo.list_events("default", NOW))
    self.assertEqual(["2"], [x["track_id"] for x in self.repo.list_events("friend", NOW)])
```

Update profile replacement/reset code to delete `behavior_event`, `behavior_track_state`, and `behavior_user_state` rows for the validated target `profile_id` in the same transaction as scoped state replacement.

- [ ] **Step 2: Test the complete play-to-recommendation path**

Create two profiles sharing a server but using different libraries. Feed play/stop/next events, verify only the matching profile learns, generate previews, and assert the cooled track is absent only from that profile.

Run: `PYTHONPATH=src python -m unittest tests.test_v120_learning_end_to_end -v`

Expected: PASS.

- [ ] **Step 3: Add deterministic 3,000 and 10,000 track performance coverage**

```python
def test_ten_thousand_track_generation_is_linear_enough_for_nas(self):
    tracks = [track(i) for i in range(10_000)]
    started = time.perf_counter()
    result = select_daily_mix_v2(tracks, ..., settings={"size": 50})
    elapsed = time.perf_counter() - started
    self.assertEqual(50, len(result["items"]))
    self.assertLess(elapsed, 5.0)
```

Use a generous 5-second CI threshold and fixed random seed; do not assert microbenchmarks. Add a repository test proving one appended event updates one aggregate row without rewriting an event JSON array.

- [ ] **Step 4: Run migration, end-to-end, and performance tests**

Run: `PYTHONPATH=src python -m unittest tests.test_v120_behavior_migration tests.test_v120_learning_end_to_end tests.test_v120_recommendation_performance -v`

Expected: PASS.

- [ ] **Step 5: Commit safety and performance coverage**

```bash
git add src/helper/profiles.py src/helper/web.py tests/test_v120_behavior_migration.py tests/test_v120_learning_end_to_end.py tests/test_v120_recommendation_performance.py
git commit -m "test: harden learning migration and isolation"
```

### Task 8: Release identity and full verification

**Files:**
- Modify: `src/helper/__init__.py`
- Modify: `CHANGELOG.md`
- Modify: `src/helper/static/daily.html`
- Modify: `src/helper/static/home.html`
- Modify: `src/helper/static/mixes.html`
- Modify: `src/helper/static/settings.html`
- Modify: `src/helper/static/status.html`
- Modify: `tests/test_project_entrypoint.py`
- Modify: `tests/test_release_version.py`

**Interfaces:**
- Produces application version `1.2.0`.
- Does not create a Git tag, GitHub release, container image, or NAS deployment in this implementation plan.

- [ ] **Step 1: Update release identity and changelog**

Set:

```python
__version__ = "1.2.0"
```

Add a `1.2.0` changelog section covering confirmed skip learning, profile baselines, relational 20,000-event retention, balanced Daily Mix V2, Plex Sonic reuse, safe migration, and the simplified cooldown status.

- [ ] **Step 2: Update static asset versions and release tests**

Replace static `?v=1.1.9` and visible fallback `v1.1.9` markers with `1.2.0`. Update exact-version tests to expect `1.2.0`.

- [ ] **Step 3: Run formatting and targeted suites**

Run:

```bash
git diff --check
PYTHONPATH=src python -m unittest tests.test_v120_behavior_store tests.test_v120_playback_learning tests.test_v120_preference_model tests.test_v120_webhook_integration tests.test_v120_daily_mix tests.test_v120_sonic_fallback tests.test_v120_status_ui tests.test_v120_behavior_migration tests.test_v120_learning_end_to_end tests.test_v120_recommendation_performance -v
```

Expected: no whitespace errors and all V2 tests PASS.

- [ ] **Step 4: Run the complete regression suite**

Run: `PYTHONPATH=src python -m unittest discover -s tests -v`

Expected: all tests PASS with no errors or failures.

- [ ] **Step 5: Validate packaging and release identity**

Run:

```bash
python -m build
PYTHONPATH=src python tools/check_release_version.py v1.2.0
```

Expected: wheel and source archive build successfully; release version check prints `1.2.0` and exits zero.

- [ ] **Step 6: Commit the release-ready implementation**

```bash
git add src/helper/__init__.py src/helper/static CHANGELOG.md tests/test_project_entrypoint.py tests/test_release_version.py
git commit -m "release: prepare adaptive learning v1.2.0"
```

- [ ] **Step 7: Record final evidence**

Capture the final commit hash, complete test count, package build result, and clean `git status --short`. Report that the code is release-ready but not yet tagged, uploaded, or deployed.
