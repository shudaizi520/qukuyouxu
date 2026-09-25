from contextlib import contextmanager
import threading


def test_transient_retry_delays_are_bounded():
    from helper.scheduler_retry import schedule_retry

    task = {"slot": 1000, "next_at": 1000}

    assert schedule_retry(task, 1000) and task["next_at"] == 1300
    assert schedule_retry(task, 1300) and task["next_at"] == 3100
    assert schedule_retry(task, 3100) and task["next_at"] == 10300
    assert schedule_retry(task, 10300) is False
    assert task["failure_count"] == 3
    assert task["retry_slot"] == 1000


def test_identity_or_fingerprint_failure_is_safety_error():
    from helper.engine import SafetyError
    from helper.scheduler_retry import classify_scheduled_failure

    assert classify_scheduled_failure(SafetyError("指纹不一致")) == "safety"


def test_explicit_pre_write_failure_is_transient():
    from helper.scheduler_retry import TransientScheduleError, classify_scheduled_failure

    assert classify_scheduled_failure(TransientScheduleError("Plex 暂时不可用")) == "transient"


def test_ambiguous_plex_write_failure_is_not_retried():
    from helper.clients import PlexError
    from helper.scheduler_retry import classify_scheduled_failure

    assert classify_scheduled_failure(PlexError("写入超时，结果未知")) == "safety"


def test_daily_preview_failure_becomes_explicit_pre_write_error(tmp_path):
    from helper.clients import PlexError
    from helper.daily import DailyMixin
    from helper.scheduler_retry import TransientScheduleError
    from helper.store import Store

    class DailyHarness(DailyMixin):
        def __init__(self):
            self.store = Store(tmp_path)

        @contextmanager
        def exclusive(self):
            yield

        def daily_due(self, now=None, schedule=None):
            return True

        def _preview_daily(self, now, origin="manual", force_full=False):
            raise PlexError("read timed out")

    harness = DailyHarness()

    try:
        harness.daily_auto(schedule={"hour": 6}, scheduled=True, now=1_800_000_000)
    except Exception as exc:
        assert isinstance(exc, TransientScheduleError)
        assert isinstance(exc.__cause__, PlexError)
    else:
        raise AssertionError("每日推荐只读预览失败应标记为可重试")


def test_daily_publish_failure_remains_non_retryable_plex_error(tmp_path):
    from helper.clients import PlexError
    from helper.daily import DailyMixin
    from helper.store import Store

    class DailyHarness(DailyMixin):
        def __init__(self):
            self.store = Store(tmp_path)

        @contextmanager
        def exclusive(self):
            yield

        def daily_due(self, now=None, schedule=None):
            return True

        def _preview_daily(self, now, origin="manual", force_full=False):
            return {"id": "plan-1", "blocked": [], "rolling": {"unchanged": False}}

        def _publish_daily(self, plan_id, now):
            raise PlexError("write timed out")

    harness = DailyHarness()

    try:
        harness.daily_auto(schedule={"hour": 6}, scheduled=True, now=1_800_000_000)
    except Exception as exc:
        assert type(exc) is PlexError
    else:
        raise AssertionError("写入结果不确定时不得转换为可重试错误")


def test_library_read_phase_failure_becomes_explicit_pre_write_error(tmp_path):
    from helper.clients import PlexError
    from helper.library_engine import LibraryEngine
    from helper.scheduler_retry import TransientScheduleError
    from helper.store import Store

    engine = object.__new__(LibraryEngine)
    engine.store = Store(tmp_path)
    engine.gate = threading.Lock()
    engine.progress = lambda _message: None
    engine._enrich_singles = lambda **_kwargs: (_ for _ in ()).throw(PlexError("read timed out"))

    try:
        engine.refresh_new_tracks()
    except Exception as exc:
        assert isinstance(exc, TransientScheduleError)
        assert isinstance(exc.__cause__, PlexError)
    else:
        raise AssertionError("曲库只读检查失败应标记为可重试")
