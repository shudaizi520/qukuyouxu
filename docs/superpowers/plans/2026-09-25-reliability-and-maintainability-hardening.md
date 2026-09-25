# 曲库有序可靠性与可维护性加固实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变 2.0.8 已确认业务结果和视觉效果的前提下，让自动任务可自恢复、会话写入原子化、状态轮询轻量化，并清理高风险历史结构与 CSS 覆盖层。

**Architecture:** 保留 FastAPI、SQLite、原生 JavaScript和单进程串行写入模型。可靠性改造围绕 `ProfileRuntime` 的显式健康状态和持久化重试状态展开；会话和行为统计下沉到专用仓储；Web 入口只组装模块；样式按页面边界机械迁移并以计算后样式测试保证无视觉变化。

**Tech Stack:** Python 3.11/3.12、FastAPI、SQLite WAL、pytest、原生 JavaScript、Node test、Playwright、Docker。

**Spec:** `docs/superpowers/specs/2026-09-25-reliability-and-maintainability-hardening-design.md`

## Global Constraints

- 不重写推荐算法，不改变每日推荐、智能歌单或主题歌单的选择策略。
- 不改变 Plex 歌单归属判断、写入前确认、写入后回读或回滚规则。
- 不更换 FastAPI、SQLite、原生 JavaScript或现有部署方式，不增加产品依赖。
- 不重新设计已确认界面；以当前播放列表页面及浅色、暖色、深色三套主题为视觉基准。
- 数据库升级必须原地兼容并可从 2.0.8 数据副本启动；不删除历史数据库字段。
- 所有产品行为修改必须先看到目标测试失败，再写最小实现使其通过。
- 每个任务独立提交；发布、推送镜像和更新 TrueNAS 不属于本计划的自动授权范围。

## Review Focus

- 调度循环在 `run_due()` 抛出一次普通异常后仍执行下一轮；Task 1 的监督器测试必须注入一次失败再观察第二次成功。
- 应用在调度线程正等待或正在结束时关闭，线程必须在有限时间内退出；Task 1 的生命周期测试覆盖这两种时序。
- 网络异常重试期间重启进程，原槽位和失败次数不能丢失或重复推进；Task 2 的持久化重试测试重新创建 `ProfileRuntime`。
- 旧 JSON 会话中包含损坏、过期和重复摘要时，迁移只保留有效最新记录且最多 20 个；Task 3 的迁移测试覆盖混合输入。
- 行为状态 JSON 缺字段、含旧字段或数值异常时，聚合状态不能抛 500；Task 4 的聚合测试插入旧版和不完整状态。

---

### Task 1: 调度监督器、健康状态与可控关闭

**Files:**
- Create: `tests/test_runtime_supervision.py`
- Modify: `src/helper/profile_runtime.py:31-44,328-341`
- Modify: `src/helper/web.py:96-116`

**Interfaces:**
- Produces: `ProfileRuntime.scheduler_status(now: float | None = None) -> dict`
- Produces: `ProfileRuntime.start_scheduler() -> threading.Thread`
- Produces: `ProfileRuntime.close(join_timeout: float = 2.0) -> None`
- Task 4 consumes `scheduler_status()` in the status response.

- [ ] **Step 1: Write failing scheduler supervision tests**

Add tests that use a short injected wait instead of sleeping 60 seconds:

```python
def test_scheduler_survives_one_cycle_exception(tmp_path):
    runtime = configured_runtime(tmp_path)
    calls = []
    runtime.scheduler_interval = 0.01
    runtime.run_due = lambda: calls.append(len(calls)) or (
        (_ for _ in ()).throw(RuntimeError("once")) if len(calls) == 1 else []
    )
    thread = runtime.start_scheduler()
    wait_until(lambda: len(calls) >= 2)
    status = runtime.scheduler_status()
    runtime.close()
    assert not thread.is_alive()
    assert status["alive"] is True
    assert status["consecutive_failures"] == 0
    assert status["last_error_at"] is not None

def test_close_wakes_and_joins_waiting_scheduler(tmp_path):
    runtime = configured_runtime(tmp_path)
    runtime.scheduler_interval = 60
    thread = runtime.start_scheduler()
    runtime.close(join_timeout=1)
    assert not thread.is_alive()
    assert runtime.scheduler_status()["alive"] is False
```

- [ ] **Step 2: Run the new tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_runtime_supervision.py`

Expected: FAIL because `start_scheduler`, `scheduler_status`, stored thread state, and configurable scheduler interval do not exist.

- [ ] **Step 3: Implement supervised scheduling state**

In `ProfileRuntime.__init__`, add `scheduler_interval = 60.0`, `_scheduler_thread = None`, `_scheduler_lock = RLock()`, and a private state dictionary containing all spec fields. Implement snapshots under `_scheduler_lock`; use `safe_error()` for `last_error`.

The loop must have this control shape:

```python
def scheduler(self):
    self._mark_scheduler_started()
    try:
        while not self.stop.is_set():
            self.wake.wait(self.scheduler_interval)
            self.wake.clear()
            if self.stop.is_set():
                break
            self._mark_cycle_started()
            try:
                self.run_due()
            except Exception as exc:
                self._mark_cycle_failed(exc)
                self.base_store.log("自动任务调度异常：" + safe_error(exc), "error")
            else:
                self._mark_cycle_finished()
    finally:
        self._mark_scheduler_stopped()
```

`start_scheduler()` must be idempotent while the stored thread is alive. `close()` sets stop and wake, snapshots the thread without holding the lock during `join`, joins for the supplied limit, then sets every engine stop event.

- [ ] **Step 4: Make lifespan cleanup unconditional**

Replace direct thread construction in `web.py` with `runtime.start_scheduler()`. Wrap `yield` in `try/finally`; the `finally` calls `runtime.close()` and closes the lockfile.

- [ ] **Step 5: Run focused and existing runtime tests**

Run: `.venv/bin/python -m pytest -q tests/test_runtime_supervision.py tests/test_v040_runtime.py tests/test_release_blockers.py tests/test_v204_memory_limits.py`

Expected: PASS, including thread-pool limit and existing job mutual exclusion tests.

- [ ] **Step 6: Commit Task 1**

```bash
git add src/helper/profile_runtime.py src/helper/web.py tests/test_runtime_supervision.py
git commit -m "fix: supervise automatic task scheduler"
```

---

### Task 2: 统一临时故障重试策略

**Files:**
- Create: `src/helper/scheduler_retry.py`
- Create: `tests/test_scheduler_retry.py`
- Modify: `src/helper/profile_runtime.py:136-182`
- Modify: `tests/test_v114_global_automation.py`

**Interfaces:**
- Consumes: existing schedule rows in `automation_schedule_v1`.
- Produces: `TransientScheduleError(message: str)`; callers may raise it only before any remote write has begun.
- Produces: `classify_scheduled_failure(exc: Exception) -> Literal["transient", "safety"]`
- Produces: `schedule_retry(task: dict, now: float) -> bool`, mutating `failure_count`, `retry_at`, `retry_slot`, and `next_at`; returns `False` after the third retry is exhausted.
- Produces: `clear_retry(task: dict) -> None`.

- [ ] **Step 1: Write failing retry policy tests**

Cover exact delays, persistence-compatible fields, and safety errors:

```python
def test_transient_retry_delays_are_bounded():
    task = {"slot": 1000, "next_at": 1000}
    assert schedule_retry(task, 1000) and task["next_at"] == 1300
    assert schedule_retry(task, 1300) and task["next_at"] == 3100
    assert schedule_retry(task, 3100) and task["next_at"] == 10300
    assert schedule_retry(task, 10300) is False
    assert task["failure_count"] == 3

def test_identity_or_fingerprint_failure_is_safety_error():
    assert classify_scheduled_failure(SafetyError("指纹不一致")) == "safety"

def test_explicit_pre_write_failure_is_transient():
    assert classify_scheduled_failure(TransientScheduleError("Plex 暂时不可用")) == "transient"

def test_ambiguous_plex_write_failure_is_not_retried():
    assert classify_scheduled_failure(PlexError("写入超时，结果未知")) == "safety"
```

Also add an integration test: make the read-only daily preview raise `TransientScheduleError`, read the stored schedule, construct a new runtime over the same store, run at `retry_at`, and assert the original `slot` is retained and the operation succeeds once. Add a second integration case where publishing raises `PlexError`; assert no automatic retry is scheduled because the remote write outcome is uncertain.

- [ ] **Step 2: Run retry tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_scheduler_retry.py tests/test_v114_global_automation.py -k 'retry or failure'`

Expected: FAIL because the policy module and persisted retry fields are absent, and current library/daily exceptions advance directly to the next normal slot.

- [ ] **Step 3: Implement classification without broad message guessing**

`scheduler_retry.py` treats only `TransientScheduleError` and explicit task results with `status == "deferred"` as transient. Treat `SafetyError`, `WorkflowPaused`, `PlexError`, `SourceError`, identity conflicts, validation errors, and unknown exceptions as safety failures. Do not infer safety from exception message text or automatically retry a timeout after a remote write may have begun.

Wrap a failure in `TransientScheduleError` only at a boundary that can prove it is read-only: for example, daily preview/source discovery before `_publish_daily()` is entered, or a library connectivity preflight before mutation. Publishing and playlist-maintenance code must let `PlexError`/`SourceError` pass through as non-retryable because a timeout may mean the remote server accepted the write.

The retry delays are exactly `(300, 1800, 7200)`. Existing smart-mix per-kind retry fields remain authoritative and are not converted.

- [ ] **Step 4: Integrate retry state into `run_due()`**

For library and daily tasks:

- On success, call `clear_retry()` and advance the normal slot.
- On a `deferred` result, retain the current behavior using its supplied `retry_at` without incrementing failure count.
- On a transient exception, schedule the next retry and keep the original slot.
- On safety or exhausted transient failure, clear transient fields and advance to the next normal slot; any subsystem suspension remains untouched.

Persist the schedule in the existing `finally` block. Record a public result containing `status`, `retry_at`, and sanitized `error`, never the raw exception string for unknown exceptions.

- [ ] **Step 5: Run automation and safety regression tests**

Run: `.venv/bin/python -m pytest -q tests/test_scheduler_retry.py tests/test_v114_global_automation.py tests/test_v0415_library_maintenance.py tests/test_daily_fixed_playlist.py tests/test_recovery_regressions.py`

Expected: PASS; existing safety failures still pause rather than retry.

- [ ] **Step 6: Commit Task 2**

```bash
git add src/helper/scheduler_retry.py src/helper/profile_runtime.py tests/test_scheduler_retry.py tests/test_v114_global_automation.py
git commit -m "fix: retry transient scheduled failures"
```

---

### Task 3: SQLite 原子管理员会话与升级迁移

**Files:**
- Create: `src/helper/auth_store.py`
- Create: `tests/test_auth_sessions_v209.py`
- Modify: `src/helper/store.py:12-40`
- Modify: `src/helper/auth.py:47-134`
- Modify: `tests/test_auth.py`

**Interfaces:**
- Produces: `ensure_auth_schema(db) -> None`
- Produces: `AuthSessionRepository.create(username: str, digest: str, created_at: float, expires_at: float) -> None`
- Produces: `AuthSessionRepository.username_for(digest: str, now: float) -> str | None`
- Produces: `AuthSessionRepository.revoke(digest: str) -> None`
- Produces: `AuthSessionRepository.revoke_all() -> None`
- Produces: `AuthManager.change_password_with_session(username: str, current_password: str, new_password: str, now: float | None = None) -> tuple[str, float]`, returning the new raw token and expiry.
- Existing login, `create_session`, `username_for_session`, and logout signatures remain unchanged.

- [ ] **Step 1: Write migration and atomicity tests**

Create a store with a legacy `auth_sessions` JSON containing one valid row, one expired row, one malformed row, and duplicate hashes with different expiry. Reopen the store and assert only the valid newest digest authenticates.

Use two threads and a barrier to call `AuthManager.create_session()` concurrently, then assert both returned tokens authenticate. Race one logout against one login and assert a revoked digest is never restored. Add a password-change test that calls `change_password_with_session()` and asserts all prior digests are gone, the credential changed, and exactly the newly returned session remains valid. Add boundary cases for expired-row cleanup and the 20-session cap.

- [ ] **Step 2: Run session tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_auth_sessions_v209.py tests/test_auth.py`

Expected: FAIL because there is no `auth_session` table/repository and concurrent JSON read-modify-write can lose a session.

- [ ] **Step 3: Add schema and idempotent migration**

Create:

```sql
CREATE TABLE IF NOT EXISTS auth_session (
    digest TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS auth_session_expires ON auth_session(expires_at);
```

Run migration from the legacy key inside `Store.__init__`'s existing schema transaction. Validate digest length/hex, username, finite timestamps, and future expiry. Resolve duplicate digests by keeping the greatest expiry. Store `auth_sessions_sqlite_v1` in the state table in the same transaction. Do not delete `auth_sessions`.

- [ ] **Step 4: Implement repository transactions and adapt `AuthManager`**

Every repository method holds `store.lock` and one `_db()` transaction. `create()` first deletes expired rows, inserts/replaces the new row, then deletes all but the newest 20 by `(created_at DESC, digest DESC)`. `username_for()` deletes expired rows and performs one lookup.

Implement `change_password_with_session()` under the same `store.lock` and one SQLite transaction: verify the current credentials, derive and update the new password hash in `state`, delete every session row, insert the new token digest, and commit. Update the password HTTP route to use this combined method rather than calling `change_password()` and `create_session()` separately. Keep `_session_hash`, cookie creation and PBKDF2 behavior unchanged.

Keep `_session_hash`, cookie creation, PBKDF2 and public return types unchanged.

- [ ] **Step 5: Run auth, upgrade, and HTTP tests**

Run: `.venv/bin/python -m pytest -q tests/test_auth_sessions_v209.py tests/test_auth.py tests/test_v130_upgrade_integration.py tests/test_release_blockers.py`

Expected: PASS, including first-account registration concurrency and existing cookie security checks.

- [ ] **Step 6: Commit Task 3**

```bash
git add src/helper/auth_store.py src/helper/store.py src/helper/auth.py tests/test_auth_sessions_v209.py tests/test_auth.py
git commit -m "refactor: store administrator sessions atomically"
```

---

### Task 4: 轻量状态摘要与调度可观测性

**Files:**
- Create: `src/helper/status_summary.py`
- Create: `src/helper/status_web.py`
- Create: `tests/test_status_summary_v209.py`
- Modify: `src/helper/behavior_store.py:197-228`
- Modify: `src/helper/extra_web.py:107-137`
- Modify: `src/helper/web.py:313-317`
- Modify: `src/helper/static/status.js:1-17`
- Modify: `src/helper/static/status.html`
- Modify: `tests/settings_interactions.test.js`

**Interfaces:**
- Consumes: `ProfileRuntime.scheduler_status()` from Task 1.
- Produces: `BehaviorRepository.summary(profile_id: str, now: float) -> dict`
- Produces: `build_status_summary(app, store, engine, runtime, now: float | None = None) -> dict`
- Produces: `build_status_details(store) -> dict`
- Produces: `attach_status_routes(app, store, engine, runtime) -> None`, registering `/api/status` and `/api/status/details`.
- `/api/status` adds `scheduler` and keeps every existing field used by current JavaScript.

- [ ] **Step 1: Write failing SQL aggregate and endpoint tests**

Insert track-state rows representing positive affinity, active cooldown, old schema without fatigue, malformed numeric strings, and a normal neutral row. Assert `summary()` returns bounded integer counts and does not decode through `load_track_states()`.

Patch `BehaviorRepository.load_track_states` to raise if called, request `/api/status`, and assert HTTP 200 with `behavior.learned_tracks`, `preferred_tracks`, `cooled_tracks`, and `scheduler.state`. Record fresh evidence between two summary requests and assert the second response immediately reflects it; because this implementation has no cache, no invalidation window is permitted.

Add status mapping cases: alive/healthy → `normal`, active job → `running`, persisted retry → `retrying`, dead or stale heartbeat → `error`.

- [ ] **Step 2: Run status tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_status_summary_v209.py`

Expected: FAIL because summary aggregation and scheduler response do not exist and current status materializes all track states.

- [ ] **Step 3: Implement one-query behavior summary**

Use a SQL subquery with `CASE WHEN json_valid(state) THEN json_extract(state, '$.field') ELSE NULL END`, then coalesce missing, non-numeric and malformed values to zero. Count rows, positive affinity and future cooldown without returning state JSON to Python. Keep `event_stats()` and the existing behavior-status timestamp separate.

Do not add a cache in this task: repositories are short-lived per request, and a process-global cache would add invalidation/concurrency risk. The indexed single aggregate query is the intended bounded-cost path; the performance test must prove it does not call `load_track_states()` or scale with Python JSON decoding.

- [ ] **Step 4: Extract, split and slim status assembly**

Move response assembly to `status_summary.py`. Keep compatibility fields accessed by `home.js`, `daily.js`, `settings.js`, `external.js`, `contextual-settings.js`, and `status.js`. Replace `extensions_status()`'s full snapshot calculation with `BehaviorRepository.summary()`.

Return scheduler data with `state`, `heartbeat_at`, `last_error_at`, sanitized `last_error`, and `next_retry_at`. Do not expose tokens, webhook secret, raw exception details or full behavior state.

Register the two routes through `attach_status_routes()`. `/api/status/details` returns only the bounded recent event list used by the expanded history section. Keep the existing `/api/daily/diagnostics` endpoint as the dedicated algorithm-detail source. Remove those low-frequency payloads from the polling response only after `rg` proves no other current script consumes them.

- [ ] **Step 5: Update status UI and Node test**

Render distinct neutral text for normal/running/retrying/error. Do not add red/green blocks; use the existing small status treatment and semantic theme variables. The test must feed each state and assert the displayed Chinese text and absence of hard-coded `#000`, `black`, `#fff`, or `white` for scheduler icons.

Change initial/poll refresh to fetch only `/api/status`. On the first opening of `#statusDetails`, fetch `/api/daily/diagnostics`; on the first opening of `#eventHistory`, fetch `/api/status/details`. Cache each detail response only until the user presses “刷新”. Node tests must prove collapsed disclosures make neither detail request, opening twice makes one request, and manual refresh permits one fresh detail request.

- [ ] **Step 6: Run status, behavior and UI tests**

Run: `.venv/bin/python -m pytest -q tests/test_status_summary_v209.py tests/test_v047_webhook_health.py tests/test_v120_recommendation_performance.py tests/test_v204_memory_limits.py && node tests/settings_interactions.test.js`

Expected: PASS; `/api/status` never calls `load_track_states()`.

- [ ] **Step 7: Commit Task 4**

```bash
git add src/helper/status_summary.py src/helper/status_web.py src/helper/behavior_store.py src/helper/extra_web.py src/helper/web.py src/helper/static/status.js src/helper/static/status.html tests/test_status_summary_v209.py tests/settings_interactions.test.js
git commit -m "perf: make runtime status lightweight"
```

---

### Task 5: 明确后端模块和每日策略入口

**Files:**
- Create: `src/helper/auth_web.py`
- Create: `src/helper/management_web.py`
- Create: `tests/test_web_module_boundaries_v209.py`
- Modify: `src/helper/web.py`
- Modify: `src/helper/daily.py:1-560`
- Modify: `src/helper/rotation.py:1-49`
- Modify: `src/helper/engine.py:400-409`
- Modify: `src/helper/store.py:10`
- Modify: `src/helper/profiles.py:148-160`

**Interfaces:**
- Consumes: `attach_status_routes()` from Task 4 and existing `AuthManager` public API from Task 3.
- Produces: `attach_auth_routes(app, auth, store, body, cookie_factory, rate_limiter) -> None`
- Produces: `attach_management_routes(app, store, engine, body, ensure_idle) -> None`
- Produces: `current_daily_signature(engine) -> str` as an explicit call, without import-time modification of `DailyMixin`.

- [ ] **Step 1: Write failing architecture contract tests**

Use AST tests rather than brittle text matching:

```python
def test_web_create_app_is_composition_not_auth_implementation():
    tree = ast.parse(WEB.read_text())
    create = find_function(tree, "create_app")
    assert not nested_route(create, "/api/auth/login")
    assert not nested_route(create, "/api/settings")
    assert imported_name(tree, "attach_auth_routes")
    assert imported_name(tree, "attach_management_routes")

def test_daily_policy_does_not_monkey_patch_mixin():
    tree = ast.parse(ROTATION.read_text())
    assert not assigns_attribute(tree, "DailyMixin", "daily_signature")

def test_engine_has_no_unused_scheduler_loop():
    assert not class_has_method(ast.parse(ENGINE.read_text()), "Engine", "scheduler")
```

Add HTTP compatibility tests for auth status/login/logout/password and `/api/status` using the existing response keys.

- [ ] **Step 2: Run boundary tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_web_module_boundaries_v209.py tests/test_release_blockers.py`

Expected: FAIL because routes are nested in `create_app`, `rotation.py` monkey-patches at import time, and `Engine.scheduler()` still exists.

- [ ] **Step 3: Extract auth and management route modules without behavior changes**

Move authentication routes to `auth_web.py`. Move the routes currently left in `web.py` from `/api/settings` through `/api/report` to `management_web.py`, along with only their directly required helpers (`connection_scope` and CSV parsing). Task 4 already owns the read-only public-settings, connection and name-plan projection used by status responses. Pass dependencies explicitly; do not import `create_app` from either child module. Preserve paths, request limits, error messages, cookies and response keys. `web.py` retains application construction, lifespan, security middleware, shared body parsing and route-module attachment.

- [ ] **Step 4: Replace daily import-time patch with explicit signature function**

Move the body of `_daily_signature_v035()` to `current_daily_signature(engine)`. Make `DailyMixin.daily_signature()` call this function lazily after its base signature inputs are assembled, or introduce `DailyMixin._base_daily_signature()` and call it from the policy function. Remove the `hasattr` guard and attribute assignments from `rotation.py`. Verify importing `helper.daily`, `helper.rotation`, and `helper.daily_mix_v2` in different orders returns identical signatures.

- [ ] **Step 5: Remove proven dead scheduler compatibility**

Delete `Engine.scheduler()` and the now-unused `Engine` import from `web.py`. Stop writing `interval_minutes` in new default settings and workflow updates, while continuing to tolerate the key in old stored dictionaries. Do not delete it from existing SQLite state. Update the library-reset portable-field list accordingly.

- [ ] **Step 6: Run full backend-focused regression**

Run: `.venv/bin/python -m pytest -q tests/test_web_module_boundaries_v209.py tests/test_auth.py tests/test_release_blockers.py tests/test_daily_fixed_playlist.py tests/test_v0411_daily_policy.py tests/test_v040_runtime.py tests/test_v130_upgrade_integration.py`

Expected: PASS with unchanged HTTP response contracts and recommendation signatures.

- [ ] **Step 7: Commit Task 5**

```bash
git add src/helper/auth_web.py src/helper/management_web.py src/helper/web.py src/helper/daily.py src/helper/rotation.py src/helper/engine.py src/helper/store.py src/helper/profiles.py tests/test_web_module_boundaries_v209.py
git commit -m "refactor: clarify web and daily policy boundaries"
```

---

### Task 6: CSS 页面边界与无回归整理

**Files:**
- Create: `src/helper/static/settings-page.css`
- Create: `src/helper/static/daily-page.css`
- Create: `src/helper/static/mixes-page.css`
- Create: `src/helper/static/external-page.css`
- Create: `tests/test_css_boundaries_v209.py`
- Modify: `src/helper/static/product.css`
- Modify: `src/helper/static/product-refinements.css`
- Modify: `src/helper/static/settings.html`
- Modify: `src/helper/static/daily.html`
- Modify: `src/helper/static/mixes.html`
- Modify: `src/helper/static/external.html`
- Modify: `tests/ui_css.py`
- Modify: existing CSS and visual contract tests that intentionally load the full page bundle.

**Interfaces:**
- Each HTML page owns one ordered stylesheet bundle.
- Shared order remains `product.css`, `product-refinements.css`, theme tokens/background, components/design system; the page sheet loads after shared design and before any truly page-local workspace sheet that must override it.
- No JavaScript selector, element ID, class name or DOM structure changes in this task.

- [ ] **Step 1: Capture current computed-style baselines**

Add Playwright helpers that render the real HTML for settings, daily, mixes, external and playlists under `light`, `warm`, and `night`. Record assertions for region backgrounds, primary/secondary text, divider color, checkbox colors, icon strokes, content width and key left/right alignment. Do not store pixel screenshots in git; compare named computed properties and bounding boxes.

- [ ] **Step 2: Add failing CSS ownership tests**

`test_css_boundaries_v209.py` must assert:

- `product.css` contains no selectors beginning with the four page-specific prefixes after migration.
- Each page HTML loads its matching page stylesheet exactly once.
- Page stylesheets contain no hard-coded pure black/white icon colors.
- The combined stylesheet order keeps appearance bootstrap before CSS and theme token order unchanged.
- Repeated exact selectors within each new page sheet are zero after stripping media-query context-aware duplicates.

Run: `.venv/bin/python -m pytest -q tests/test_css_boundaries_v209.py`

Expected: FAIL because the files do not exist and page-specific rules remain in `product.css`.

- [ ] **Step 3: Mechanically move page-owned rules**

Move complete rule blocks including their media-query wrappers by these namespaces:

- Settings: `body[data-view=settings]`, `.settings-`, `.profile-`, `.webhook-` where only settings HTML uses the class.
- Daily: `body[data-view=daily]`, `.daily-` where only daily HTML uses the class.
- Mixes: `body[data-view=mixes]`, `.mix-`, `.smart-` where only mixes HTML uses the class.
- External: `body[data-view=external]`, `.external-` where only external HTML uses the class.

Before moving an unscoped selector, prove with `rg` that no second HTML page uses it. Shared selectors stay in `product.css`. Preserve source order within each page sheet so the computed cascade remains identical.

- [ ] **Step 4: Merge exact duplicates without changing specificity**

For each new sheet, combine declarations only when selector text, media context and specificity are identical. If later declarations override earlier ones, produce one declaration block with the final property values in original cascade order. Do not replace specific selectors with broader selectors merely to reduce line count.

Move settings-only corrections from `product-refinements.css` into `settings-page.css`; keep cross-page refinements in place.

- [ ] **Step 5: Update HTML bundles and test helpers**

Load the corresponding page sheet with `?v=app`. Update `tests/ui_css.py` to expose `page_css(page_name)` that concatenates the exact HTML order rather than assuming all styles live in `product.css`. Update existing tests to call the bundle helper when they assert computed page behavior; keep tests that explicitly enforce shared-token ownership pointed at the shared files.

- [ ] **Step 6: Run all CSS, Playwright and Node tests**

Run: `.venv/bin/python -m pytest -q tests/test_css_boundaries_v209.py tests/test_unified_design_system.py tests/test_player_visual_refresh.py tests/test_settings_visual_refresh.py tests/test_appearance_palette_v152.py tests/test_theme_contract.py tests/test_v200_layout.py tests/test_v205_ui_stability.py && node --test tests/appearance.test.js tests/playlist_artwork.test.js tests/playlist_sections.test.js tests/playlist_player_track.test.js`

Expected: PASS with the same computed properties and alignment in all three themes.

- [ ] **Step 7: Measure and record CSS result**

Run a small read-only Python counter over all CSS files and append the before/after sizes, selector counts and duplicate exact-selector counts to the commit body or task ledger. Success requires page-specific selectors removed from `product.css` and no increase in total gzipped CSS greater than 5%.

- [ ] **Step 8: Commit Task 6**

```bash
git add src/helper/static tests
git commit -m "refactor: separate page style boundaries"
```

---

### Task 7: Upgrade, resource and release-candidate verification

**Files:**
- Create: `tests/test_v209_upgrade_hardening.py`
- Modify: `tools/check_repository.py` only if new module/style ownership needs a repository invariant.
- Modify: `README.md` and `docs/install/truenas.md` only for observable scheduler-health or upgrade behavior users need to know.

**Interfaces:**
- Consumes every interface produced by Tasks 1–6.
- Produces no new product behavior; this task proves the integrated release candidate.

- [ ] **Step 1: Write an end-to-end 2.0.8 upgrade-copy test**

Build a representative pre-change database containing credentials, legacy sessions, multiple profiles, automation schedules including a pending retry, behavior events/state, managed playlist records, snapshots and appearance settings. Open it with the new `Store`, create the app with scheduler disabled, and assert:

- credentials still verify;
- one valid old session migrates;
- profiles and active selection remain unchanged;
- pending retry fields remain usable;
- managed IDs/fingerprints and snapshots are byte-equivalent JSON values;
- appearance setting remains unchanged;
- `/api/status` returns compatibility fields plus scheduler health.

- [ ] **Step 2: Run the integrated test and resolve only real integration defects**

Run: `.venv/bin/python -m pytest -q tests/test_v209_upgrade_hardening.py`

Expected: PASS after Tasks 1–6. If it fails, fix the owning task's implementation and add the narrower regression there before changing this integration test.

- [ ] **Step 3: Run repository hygiene and compilation**

Run: `.venv/bin/python tools/check_repository.py && .venv/bin/python -m compileall -q src tools`

Expected: exit 0 with no generated or secret files reported.

- [ ] **Step 4: Run the entire automated suite**

Run: `.venv/bin/python -m pytest -q`

Expected: all Python tests and subtests pass. If local sandbox blocks sockets or Chromium, rerun only those permission-blocked tests with approved unsandboxed execution and record both results.

Run: `node tests/settings_interactions.test.js && node --test tests/appearance.test.js tests/playlist_artwork.test.js tests/playlist_sections.test.js tests/playlist_player_track.test.js`

Expected: all Node tests pass.

- [ ] **Step 5: Build and smoke-test the container**

Run: `docker build -t qukuyouxu:hardening-preview .`

Run the container with a fresh temporary data directory, wait for the healthcheck, request the login page and `/api/auth/status`, then stop it. Expected: container becomes healthy, page returns 200, setup is required, and logs contain no traceback.

- [ ] **Step 6: Compare resources and render all themes**

Exercise status polling, a representative playlist listing and the theme pages for at least five minutes. Record idle RSS, peak RSS, CPU sample, open thread count and response time for `/api/status` with a large synthetic behavior-state fixture. Success: no meaningful regression from 2.0.8, status response time no longer scales with decoding every learned track, and thread count returns to baseline after jobs finish.

Use Playwright to save preview-only screenshots for settings, daily, mixes, external, playlists and full-screen player in light/warm/night. Visually compare region contrast, alignments, icons and controls against the accepted 2.0.8 baseline. Screenshots remain ignored artifacts, not repository files.

- [ ] **Step 7: Commit verification fixtures and documentation**

```bash
git add tests/test_v209_upgrade_hardening.py tools/check_repository.py README.md docs/install/truenas.md
git commit -m "test: verify hardening upgrade path"
```

If only the new test file changed, stage and commit only that file.

- [ ] **Step 8: Stop before external publication**

Present the verified preview, test counts, resource comparison, migration result and remaining review findings. Do not merge to `main`, push GitHub, publish a tag/image or update TrueNAS until the user explicitly approves the preview and publication.
