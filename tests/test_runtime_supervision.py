import asyncio
import time


def configured_runtime(tmp_path):
    from helper.profile_runtime import ProfileRuntime
    from helper.profiles import ProfileRegistry
    from helper.store import Store

    store = Store(tmp_path)
    return ProfileRuntime(store, ProfileRegistry(store))


def wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("等待调度器状态更新超时")


def test_scheduler_survives_one_cycle_exception(tmp_path):
    runtime = configured_runtime(tmp_path)
    calls = []

    def run_due():
        calls.append(len(calls))
        if len(calls) == 1:
            raise RuntimeError("sensitive upstream detail")
        return []

    runtime.scheduler_interval = 0.01
    runtime.run_due = run_due
    thread = runtime.start_scheduler()
    wait_until(
        lambda: len(calls) >= 2
        and runtime.scheduler_status()["last_cycle_finished_at"] is not None
    )
    status = runtime.scheduler_status()
    runtime.close()

    assert not thread.is_alive()
    assert status["alive"] is True
    assert status["consecutive_failures"] == 0
    assert status["last_error_at"] is not None
    assert status["last_error"].startswith("RuntimeError")
    assert "sensitive upstream detail" not in status["last_error"]


def test_start_scheduler_reuses_the_live_supervisor(tmp_path):
    runtime = configured_runtime(tmp_path)
    runtime.scheduler_interval = 60

    first = runtime.start_scheduler()
    second = runtime.start_scheduler()
    runtime.close(join_timeout=1)

    assert first is second
    assert not first.is_alive()


def test_scheduler_survives_when_error_logging_also_fails(tmp_path):
    runtime = configured_runtime(tmp_path)
    calls = []

    def run_due():
        calls.append(len(calls))
        if len(calls) == 1:
            raise RuntimeError("database unavailable")
        return []

    def fail_log(*_args, **_kwargs):
        raise RuntimeError("log storage unavailable")

    runtime.scheduler_interval = 0.01
    runtime.run_due = run_due
    runtime.base_store.log = fail_log
    thread = runtime.start_scheduler()
    try:
        wait_until(lambda: len(calls) >= 2)
    finally:
        runtime.close()

    assert not thread.is_alive()
    assert runtime.scheduler_status()["last_error_at"] is not None


def test_close_wakes_and_joins_waiting_scheduler(tmp_path):
    runtime = configured_runtime(tmp_path)
    runtime.scheduler_interval = 60
    thread = runtime.start_scheduler()

    runtime.close(join_timeout=1)

    assert not thread.is_alive()
    assert runtime.scheduler_status()["alive"] is False


def test_lifespan_closes_runtime_when_application_body_raises(tmp_path):
    from helper.store import Store
    from helper.web import create_app

    app = create_app(store=Store(tmp_path), start_scheduler=False)
    runtime = app.state.profile_runtime

    async def fail_inside_lifespan():
        try:
            async with app.router.lifespan_context(app):
                raise RuntimeError("application failure")
        except RuntimeError:
            pass

    asyncio.run(fail_inside_lifespan())

    assert runtime.stop.is_set()
