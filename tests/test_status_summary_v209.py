import time
from unittest.mock import patch


def route_payload(app, path):
    route = next(row for row in app.routes if getattr(row, "path", None) == path)
    return route.endpoint()


def test_behavior_summary_handles_old_and_malformed_json_without_loading_states(tmp_path):
    from helper.behavior_store import BehaviorRepository
    from helper.store import Store

    now = 2_000.0
    store = Store(tmp_path)
    repository = BehaviorRepository(store)
    repository.save_track_state("default", "positive", {"affinity": 0.5})
    repository.save_track_state("default", "cooled", {"cooldown_until": now + 50})
    repository.save_track_state("default", "old-positive", {"score": 0.2})
    repository.save_track_state(
        "default", "bad-numbers", {"affinity": "many", "cooldown_until": "later"}
    )
    repository.save_track_state("default", "neutral", {})
    with store.lock, store._db() as db:
        db.execute(
            "INSERT INTO behavior_track_state(profile_id,track_id,state) VALUES (?,?,?)",
            ("default", "malformed", "{not-json"),
        )
    repository.load_track_states = lambda _profile_id: (_ for _ in ()).throw(
        AssertionError("summary must not decode all track states")
    )

    result = repository.summary("default", now)

    assert result == {
        "learned_tracks": 6,
        "preferred_tracks": 2,
        "cooled_tracks": 1,
    }
    assert all(type(value) is int for value in result.values())


def test_status_endpoint_uses_sql_summary_and_reflects_new_evidence_immediately(tmp_path):
    from helper.behavior_store import BehaviorRepository
    from helper.store import Store
    from helper.web import create_app

    store = Store(tmp_path)
    app = create_app(store=store, start_scheduler=False)
    repository = BehaviorRepository(store)
    now = time.time()
    repository.save_track_state("default", "first", {"affinity": 0.5})

    with patch.object(
        BehaviorRepository,
        "load_track_states",
        side_effect=AssertionError("status must use aggregate SQL"),
    ):
        first = route_payload(app, "/api/status")
        repository.record_evidence("default", {
            "event_key": "fresh-event", "track_id": "second",
            "kind": "completed", "value": 1.0, "at": now,
        }, now)
        second = route_payload(app, "/api/status")

    assert first["behavior"]["learned_tracks"] == 1
    assert second["behavior"]["learned_tracks"] == 2
    assert second["behavior"]["preferred_tracks"] >= 1
    assert second["scheduler"]["state"] == "error"
    assert "heartbeat_at" in second["scheduler"]


class _Runtime:
    scheduler_interval = 60

    def __init__(self, status):
        self.status = status

    def scheduler_status(self, now=None):
        return dict(self.status)


class _Engine:
    def __init__(self, running=False):
        self.job = {"running": running}


def test_scheduler_health_maps_normal_running_retrying_dead_and_stale(tmp_path):
    from helper.automation import PROFILE_STATE_KEY
    from helper.scoped_store import ScopedStore
    from helper.status_summary import scheduler_health
    from helper.store import Store

    now = 10_000.0
    store = ScopedStore(Store(tmp_path), "default")
    healthy = {
        "alive": True, "heartbeat_at": now - 10, "last_error_at": None,
        "last_error": "", "consecutive_failures": 0,
    }

    assert scheduler_health(_Runtime(healthy), store, _Engine(), now)["state"] == "normal"
    assert scheduler_health(_Runtime(healthy), store, _Engine(True), now)["state"] == "running"
    store.set(PROFILE_STATE_KEY, {"tasks": {
        "daily": {"retry_at": now + 300},
        "library": {"retry_at": now + 100},
    }})
    retrying = scheduler_health(_Runtime(healthy), store, _Engine(), now)
    assert retrying["state"] == "retrying"
    assert retrying["next_retry_at"] == now + 100
    assert scheduler_health(_Runtime({**healthy, "alive": False}), store, _Engine(), now)["state"] == "error"
    assert scheduler_health(
        _Runtime({**healthy, "heartbeat_at": now - 181}), store, _Engine(), now
    )["state"] == "error"


def test_status_details_are_bounded_and_absent_from_polling_payload(tmp_path):
    from helper.store import Store
    from helper.web import create_app

    store = Store(tmp_path)
    for index in range(35):
        store.log(f"event-{index}")
    app = create_app(store=store, start_scheduler=False)

    summary = route_payload(app, "/api/status")
    details = route_payload(app, "/api/status/details")

    assert "events" not in summary
    assert len(details["events"]) == 30
    assert details["events"][-1]["message"] == "event-34"
