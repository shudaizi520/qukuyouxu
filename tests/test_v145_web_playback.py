import json
import tempfile
import unittest
from pathlib import Path

from helper.behavior_store import BehaviorRepository
from helper.profiles import ProfileRegistry
from helper.scoped_store import ScopedStore
from helper.store import Store
from tests.test_v130_external_api import request


def event(kind, *, track="1", player="tab-a", event_id=None, position=0, duration=200):
    return {
        "event": kind,
        "track_id": str(track),
        "player_id": player,
        "event_id": event_id or f"{kind}-{track}-{position}",
        "position": position,
        "duration": duration,
    }


class WebPlaybackV145Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Store(Path(self.temp.name))
        self.registry = ProfileRegistry(self.base)
        self.registry.update(
            "default",
            account={"id": "10", "username": "owner"},
            server={"machine": "machine-a", "name": "Main"},
            library={"id": "15", "name": "Music"},
        )
        self.registry.create(
            "朋友", "shared", profile_id="friend",
            account={"id": "20", "username": "friend"},
            server={"machine": "machine-a", "name": "Main"},
            library={"id": "15", "name": "Music"},
        )
        self.owner = ScopedStore(self.base, "default", registry=self.registry)
        self.friend = ScopedStore(self.base, "friend", registry=self.registry)
        for store in (self.owner, self.friend):
            store.set("catalog", [
                {"id": "1", "title": "One", "duration": 200, "available": True},
                {"id": "2", "title": "Two", "duration": 180, "available": True},
            ])
        self.repo = BehaviorRepository(self.base)

    def tearDown(self):
        self.temp.cleanup()

    def apply(self, payload, now, profile="default"):
        from helper.web_playback import apply_web_playback_event

        return apply_web_playback_event(
            self.base, self.registry, profile, payload, now=now,
        )

    def test_play_progress_pause_resume_stop_and_scrobble_update_status_and_learning(self):
        from helper.plex_webhook import active_session_count

        self.assertEqual("accepted", self.apply(event("play"), 0)["status"])
        self.assertEqual("accepted", self.apply(event("progress", position=20), 20)["status"])
        paused = self.apply(event("pause", position=30), 30)
        self.assertEqual(0, paused["active"])
        self.assertEqual("accepted", self.apply(event("resume", position=30), 40)["status"])
        self.assertEqual("accepted", self.apply(event("stop", position=60), 70)["status"])
        completed = self.apply(event("scrobble", position=190), 80)

        status = self.owner.get("behavior_status")
        sessions = self.owner.get("behavior_sessions")
        rows = self.repo.list_events("default", 80)
        self.assertEqual("recorded", completed["status"])
        self.assertEqual("web_player", status["source"])
        self.assertEqual(0, active_session_count(sessions, now=80))
        self.assertEqual("completed", rows[0]["kind"])
        self.assertEqual("web:tab-a", rows[0]["player_id"])
        self.assertGreater(self.repo.load_track_state("default", "1")["positive_evidence"], 0)

    def test_duplicate_event_id_is_idempotent(self):
        payload = event("scrobble", event_id="same-delivery", position=190)
        first = self.apply(payload, 100)
        second = self.apply(payload, 100)

        self.assertEqual(("recorded", "duplicate"), (first["status"], second["status"]))
        self.assertEqual(1, len(self.repo.list_events("default", 100)))

    def test_selected_profile_is_the_only_profile_updated(self):
        result = self.apply(event("play"), 100, profile="friend")

        self.assertEqual("friend", result["profile_id"])
        self.assertEqual({}, self.owner.get("behavior_sessions"))
        self.assertTrue(self.friend.get("behavior_sessions"))
        self.assertFalse(self.owner.get("behavior_status"))
        self.assertEqual("web_player", self.friend.get("behavior_status")["source"])

    def test_invalid_or_unavailable_payload_fields_are_rejected(self):
        cases = (
            {"track_id": "x"},
            {"track_id": "999"},
            {"player_id": ""},
            {"player_id": "bad player"},
            {"event_id": ""},
            {"position": -1},
            {"position": float("inf")},
            {"duration": float("inf")},
            {"duration": 86401},
            {"event": "rate"},
        )
        for index, changes in enumerate(cases):
            payload = event("play", event_id=f"invalid-{index}")
            payload.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.apply(payload, 100 + index)

    def test_disabled_learning_accepts_no_session_or_evidence(self):
        self.owner.set("product_settings", {"behavior_enabled": False})

        result = self.apply(event("play"), 100)

        self.assertEqual("ignored", result["status"])
        self.assertEqual({}, self.owner.get("behavior_sessions"))
        self.assertEqual([], self.repo.list_events("default", 100))


class WebPlaybackRouteV145Tests(unittest.TestCase):
    def setUp(self):
        from helper.auth import AuthManager, COOKIE_NAME
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ScopedStore
        from helper.store import Store
        from helper.web import create_app

        self.temp = tempfile.TemporaryDirectory()
        self.base = Store(Path(self.temp.name))
        registry = ProfileRegistry(self.base)
        registry.create("朋友", "shared", profile_id="friend")
        for profile in ("default", "friend"):
            ScopedStore(self.base, profile).set("catalog", [
                {"id": "1", "title": "One", "duration": 200, "available": True},
            ])
        auth = AuthManager(self.base)
        auth.create_account("admin", "safe-password")
        token, _ = auth.create_session("admin")
        self.cookie = f"{COOKIE_NAME}={token}"
        self.app = create_app(store=self.base, start_scheduler=False)

    def tearDown(self):
        self.app.state.profile_runtime.close()
        self.temp.cleanup()

    def post(self, headers):
        status, _headers, raw = request(
            self.app, "/api/playback/events", method="POST", headers=headers,
            body=event("play", event_id="route-play"),
        )
        return status, json.loads(raw or b"{}")

    def test_route_requires_login_and_uses_authenticated_profile_header(self):
        status, _payload = self.post({})
        self.assertEqual(401, status)

        status, payload = self.post({
            "cookie": self.cookie, "X-Plex-Profile": "friend",
        })
        self.assertEqual(200, status)
        self.assertEqual("friend", payload["profile_id"])
        self.assertEqual({}, ScopedStore(self.base, "default").get("behavior_sessions"))
        self.assertTrue(ScopedStore(self.base, "friend").get("behavior_sessions"))


if __name__ == "__main__":
    unittest.main()
