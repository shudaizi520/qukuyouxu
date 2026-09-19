import asyncio
import json
import tempfile
import unittest
from pathlib import Path


def payload(event, account="10", machine="machine-a", track="123", player="player-a", library=None, **metadata):
    values = {"ratingKey": track, "type": "track", "title": "Song", "duration": 200000}
    if library is not None:
        values["librarySectionID"] = str(library)
    values.update(metadata)
    return {
        "event": event,
        "Account": {"id": account, "title": "owner"},
        "Server": {"uuid": machine, "title": "Main"},
        "Player": {"uuid": player, "title": "Phone"},
        "Metadata": values,
    }


class PlexWebhookV040Tests(unittest.TestCase):
    def setUp(self):
        from helper.profiles import ProfileRegistry
        from helper.store import Store

        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.registry = ProfileRegistry(self.store)
        self.registry.update(
            "default",
            token="owner-secret",
            account={"id": "10", "username": "owner"},
            server={"machine": "machine-a", "name": "Main", "url": "http://plex:32400"},
            library={"id": "15", "name": "Music"},
        )

    def tearDown(self):
        self.temp.cleanup()

    def events(self, profile="default"):
        from helper.scoped_store import ScopedStore
        return ScopedStore(self.store, profile).get("behavior_events", [])

    def test_scrobble_and_rating_create_profile_scoped_signals(self):
        from helper.plex_webhook import apply_webhook_event

        first = apply_webhook_event(self.store, self.registry, payload("media.scrobble"), now=100)
        second = apply_webhook_event(
            self.store, self.registry,
            payload("media.rate", track="124", userRating=3), now=120,
        )
        self.assertEqual("recorded", first["status"])
        self.assertEqual("recorded", second["status"])
        self.assertEqual([1.0, -1.0], [row["value"] for row in self.events()])
        self.assertEqual(["completed", "low_rating"], [row["kind"] for row in self.events()])

    def test_stop_is_soft_evidence_and_late_skip_remains_positive(self):
        from helper.plex_webhook import apply_webhook_event

        apply_webhook_event(
            self.store, self.registry,
            payload("media.stop", track="123", viewOffset=20000), now=100,
        )
        apply_webhook_event(
            self.store, self.registry,
            payload("media.stop", track="124", viewOffset=100000), now=120,
        )
        apply_webhook_event(
            self.store, self.registry,
            payload("media.stop", track="125", viewOffset=170000), now=140,
        )
        events = self.events()
        self.assertEqual(
            ["observed_skip", "observed_skip", "substantial_listen"],
            [row["kind"] for row in events],
        )
        self.assertLess(events[0]["value"], events[1]["value"])
        self.assertLess(events[1]["value"], 0)
        self.assertEqual(0.75, events[2]["value"])

    def test_scrobble_and_late_stop_are_counted_only_once(self):
        from helper.plex_webhook import apply_webhook_event

        apply_webhook_event(
            self.store, self.registry,
            payload("media.scrobble", track="321", viewOffset=180000), now=100,
        )
        result = apply_webhook_event(
            self.store, self.registry,
            payload("media.stop", track="321", viewOffset=190000), now=110,
        )

        self.assertEqual("accepted", result["status"])
        self.assertEqual(["completed"], [row["kind"] for row in self.events()])

    def test_scrobble_records_completion_without_ending_current_playback(self):
        from helper.plex_webhook import active_session_count, apply_webhook_event
        from helper.scoped_store import ScopedStore

        apply_webhook_event(
            self.store, self.registry,
            payload("media.play", track="320", viewOffset=0), now=10,
        )
        apply_webhook_event(
            self.store, self.registry,
            payload("media.scrobble", track="320", viewOffset=180000), now=180,
        )

        sessions = ScopedStore(self.store, "default").get("behavior_sessions")
        session = next(iter(sessions.values()))
        self.assertEqual("media.play", session["state"])
        self.assertEqual("", session["terminal_event"])
        self.assertEqual("media.scrobble", session["completion_event"])
        self.assertEqual(1, active_session_count(sessions, now=181))
        self.assertEqual(["completed"], [row["kind"] for row in self.events()])

        apply_webhook_event(
            self.store, self.registry,
            payload("media.stop", track="320", viewOffset=195000), now=195,
        )
        sessions = ScopedStore(self.store, "default").get("behavior_sessions")
        self.assertEqual(0, active_session_count(sessions, now=196))
        self.assertEqual(["completed"], [row["kind"] for row in self.events()])

    def test_late_stop_followed_by_scrobble_keeps_the_stronger_completion(self):
        from helper.plex_webhook import apply_webhook_event

        apply_webhook_event(
            self.store, self.registry,
            payload("media.stop", track="322", viewOffset=180000), now=100,
        )
        apply_webhook_event(
            self.store, self.registry,
            payload("media.scrobble", track="322", viewOffset=190000), now=110,
        )

        self.assertEqual(["completed"], [row["kind"] for row in self.events()])

    def test_same_playback_is_coalesced_even_when_stop_delivery_is_delayed(self):
        from helper.plex_webhook import apply_webhook_event

        apply_webhook_event(
            self.store, self.registry,
            payload("media.scrobble", track="323", viewOffset=180000), now=100,
        )
        apply_webhook_event(
            self.store, self.registry,
            payload("media.stop", track="323", viewOffset=190000), now=400,
        )

        self.assertEqual(["completed"], [row["kind"] for row in self.events()])

    def test_long_playback_is_coalesced_by_playback_generation_not_time_window(self):
        from helper.plex_webhook import apply_webhook_event

        apply_webhook_event(self.store, self.registry, payload("media.play", track="326"), now=10)
        apply_webhook_event(
            self.store, self.registry,
            payload("media.scrobble", track="326", viewOffset=180000), now=700,
        )
        apply_webhook_event(
            self.store, self.registry,
            payload("media.stop", track="326", viewOffset=190000), now=1400,
        )

        self.assertEqual(["completed"], [row["kind"] for row in self.events()])

    def test_replaying_same_track_on_same_player_creates_a_new_generation(self):
        from helper.plex_webhook import apply_webhook_event

        apply_webhook_event(self.store, self.registry, payload("media.play", track="327"), now=10)
        apply_webhook_event(
            self.store, self.registry,
            payload("media.scrobble", track="327", viewOffset=180000), now=100,
        )
        apply_webhook_event(self.store, self.registry, payload("media.play", track="327"), now=120)
        apply_webhook_event(
            self.store, self.registry,
            payload("media.scrobble", track="327", viewOffset=180000), now=300,
        )

        self.assertEqual(["completed", "completed"], [row["kind"] for row in self.events()])

    def test_scrobble_keeps_confirmed_replay_active_after_previous_stop(self):
        from helper.plex_webhook import active_session_count, apply_webhook_event
        from helper.scoped_store import ScopedStore

        apply_webhook_event(self.store, self.registry, payload("media.play", track="330"), now=10)
        apply_webhook_event(
            self.store, self.registry,
            payload("media.scrobble", track="330", viewOffset=180000), now=100,
        )
        apply_webhook_event(
            self.store, self.registry,
            payload("media.stop", track="330", viewOffset=190000), now=110,
        )
        replay = apply_webhook_event(
            self.store, self.registry, payload("media.play", track="330"), now=200,
        )
        apply_webhook_event(
            self.store, self.registry,
            payload("media.scrobble", track="330", viewOffset=180000), now=300,
        )

        sessions = ScopedStore(self.store, "default").get("behavior_sessions")
        session = next(iter(sessions.values()))
        self.assertEqual("duplicate", replay["status"])
        self.assertEqual("media.play", session["state"])
        self.assertEqual("", session["terminal_event"])
        self.assertEqual("media.scrobble", session["completion_event"])
        self.assertEqual(1, active_session_count(sessions, now=301))
        self.assertEqual(["completed", "completed"], [row["kind"] for row in self.events()])

    def test_duplicate_delivery_across_old_ten_second_bucket_is_ignored(self):
        from helper.plex_webhook import apply_webhook_event

        data = payload("media.scrobble", track="328", viewOffset=180000)
        first = apply_webhook_event(self.store, self.registry, data, now=100)
        duplicate = apply_webhook_event(self.store, self.registry, data, now=111)

        self.assertEqual("recorded", first["status"])
        self.assertEqual("duplicate", duplicate["status"])
        self.assertEqual(1, len(self.events()))

    def test_delayed_play_redelivery_does_not_split_one_completed_playback(self):
        from helper.plex_webhook import apply_webhook_event

        play = payload("media.play", track="329", viewOffset=0)
        apply_webhook_event(self.store, self.registry, play, now=10)
        apply_webhook_event(
            self.store, self.registry,
            payload("media.scrobble", track="329", viewOffset=180000), now=100,
        )
        replayed_delivery = apply_webhook_event(self.store, self.registry, play, now=110)
        apply_webhook_event(
            self.store, self.registry,
            payload("media.stop", track="329", viewOffset=190000), now=120,
        )

        self.assertEqual("duplicate", replayed_delivery["status"])
        self.assertEqual(["completed"], [row["kind"] for row in self.events()])

    def test_same_track_on_different_players_is_not_coalesced(self):
        from helper.plex_webhook import apply_webhook_event

        apply_webhook_event(
            self.store, self.registry,
            payload("media.scrobble", track="324", player="phone", viewOffset=180000), now=100,
        )
        apply_webhook_event(
            self.store, self.registry,
            payload("media.stop", track="324", player="speaker", viewOffset=190000), now=110,
        )

        self.assertEqual(
            ["completed", "substantial_listen"],
            [row["kind"] for row in self.events()],
        )

    def test_same_event_on_different_players_is_not_a_delivery_duplicate(self):
        from helper.plex_webhook import apply_webhook_event

        first = apply_webhook_event(
            self.store, self.registry,
            payload("media.scrobble", track="325", player="phone"), now=100,
        )
        second = apply_webhook_event(
            self.store, self.registry,
            payload("media.scrobble", track="325", player="speaker"), now=101,
        )

        self.assertEqual("recorded", first["status"])
        self.assertEqual("recorded", second["status"])
        self.assertEqual(2, len(self.events()))

    def test_duplicate_and_unknown_identity_do_not_pollute_events(self):
        from helper.plex_webhook import apply_webhook_event

        data = payload("media.scrobble")
        apply_webhook_event(self.store, self.registry, data, now=100)
        duplicate = apply_webhook_event(self.store, self.registry, data, now=101)
        unknown = apply_webhook_event(
            self.store, self.registry, payload("media.scrobble", machine="other"), now=120
        )
        self.assertEqual("duplicate", duplicate["status"])
        self.assertEqual("ignored", unknown["status"])
        self.assertEqual(1, len(self.events()))

    def test_same_server_different_accounts_are_isolated(self):
        from helper.plex_webhook import apply_webhook_event

        self.registry.create(
            name="Friend", kind="shared", profile_id="friend-42", token="friend-secret",
            account={"id": "42", "username": "friend"},
            server={"machine": "machine-a", "name": "Main", "url": "http://plex:32400"},
            library={"id": "15", "name": "Music"},
        )
        apply_webhook_event(
            self.store, self.registry,
            payload("media.scrobble", account="42", track="999"), now=100,
        )
        self.assertEqual([], self.events("default"))
        self.assertEqual("999", self.events("friend-42")[0]["track_id"])

    def test_same_account_with_two_libraries_routes_to_the_matching_library_only(self):
        from helper.plex_webhook import apply_webhook_event

        self.registry.create(
            name="Owner classics", kind="owner", profile_id="owner-classics", token="owner-secret",
            account={"id": "10", "username": "owner"},
            server={"machine": "machine-a", "name": "Main", "url": "http://plex:32400"},
            library={"id": "22", "name": "Classics"},
        )

        result = apply_webhook_event(
            self.store, self.registry,
            payload("media.scrobble", track="777", library="22"), now=100,
        )

        self.assertEqual("recorded", result["status"])
        self.assertEqual([], self.events("default"))
        self.assertEqual("777", self.events("owner-classics")[0]["track_id"])

    def test_same_account_with_two_libraries_fails_closed_without_library_identity(self):
        from helper.plex_webhook import apply_webhook_event

        self.registry.create(
            name="Owner classics", kind="owner", profile_id="owner-classics", token="owner-secret",
            account={"id": "10", "username": "owner"},
            server={"machine": "machine-a", "name": "Main", "url": "http://plex:32400"},
            library={"id": "22", "name": "Classics"},
        )

        result = apply_webhook_event(
            self.store, self.registry, payload("media.scrobble", track="778"), now=100,
        )

        self.assertEqual("ignored", result["status"])
        self.assertEqual("identity_not_unique", result["reason"])
        self.assertEqual([], self.events("default"))
        self.assertEqual([], self.events("owner-classics"))

    def test_missing_library_identity_uses_unique_cached_catalog_membership(self):
        from helper.plex_webhook import apply_webhook_event
        from helper.scoped_store import ScopedStore

        self.registry.create(
            name="Owner classics", kind="owner", profile_id="owner-classics", token="owner-secret",
            account={"id": "10", "username": "owner"},
            server={"machine": "machine-a", "name": "Main", "url": "http://plex:32400"},
            library={"id": "22", "name": "Classics"},
        )
        ScopedStore(self.store, "default").set("catalog", [{"id": "100"}])
        ScopedStore(self.store, "owner-classics").set("catalog", [{"id": "778"}])

        result = apply_webhook_event(
            self.store, self.registry, payload("media.scrobble", track="778"), now=100,
        )

        self.assertEqual("recorded", result["status"])
        self.assertEqual("owner-classics", result["profile_id"])
        self.assertEqual([], self.events("default"))
        self.assertEqual("778", self.events("owner-classics")[0]["track_id"])

    def test_missing_library_identity_rejects_ambiguous_cached_membership(self):
        from helper.plex_webhook import apply_webhook_event
        from helper.scoped_store import ScopedStore

        self.registry.create(
            name="Owner classics", kind="owner", profile_id="owner-classics", token="owner-secret",
            account={"id": "10", "username": "owner"},
            server={"machine": "machine-a", "name": "Main", "url": "http://plex:32400"},
            library={"id": "22", "name": "Classics"},
        )
        for profile_id in ("default", "owner-classics"):
            ScopedStore(self.store, profile_id).set("catalog", [{"id": "778"}])

        result = apply_webhook_event(
            self.store, self.registry, payload("media.scrobble", track="778"), now=100,
        )

        self.assertEqual("ignored", result["status"])
        self.assertEqual("identity_not_unique", result["reason"])
        self.assertEqual([], self.events("default"))
        self.assertEqual([], self.events("owner-classics"))

    def test_disabled_profile_keeps_existing_history_and_ignores_new_events(self):
        from helper.plex_webhook import apply_webhook_event
        from helper.scoped_store import ScopedStore

        scoped = ScopedStore(self.store, "default")
        existing = [{"track_id": "100", "value": 1.0, "kind": "completed", "at": 90}]
        scoped.set_many({
            "behavior_events": existing,
            "product_settings": {"behavior_enabled": False},
        })

        result = apply_webhook_event(
            self.store, self.registry,
            payload("media.scrobble", track="101", library="15"), now=100,
        )

        self.assertEqual("ignored", result["status"])
        self.assertEqual("disabled", result["reason"])
        self.assertEqual(existing, self.events())

    def test_owner_account_fallback_uses_library_identity(self):
        from helper.plex_webhook import apply_webhook_event

        self.registry.create(
            name="Owner classics", kind="owner", profile_id="owner-classics", token="owner-secret",
            account={"id": "10", "username": "owner"},
            server={"machine": "machine-a", "name": "Main", "url": "http://plex:32400"},
            library={"id": "22", "name": "Classics"},
        )

        result = apply_webhook_event(
            self.store, self.registry,
            payload("media.scrobble", account="1", track="779", library="15"), now=100,
        )

        self.assertEqual("recorded", result["status"])
        self.assertEqual("779", self.events("default")[0]["track_id"])
        self.assertEqual([], self.events("owner-classics"))

    def test_multipart_parser_extracts_only_payload_json(self):
        from helper.plex_webhook import parse_multipart_payload

        boundary = "safe-boundary"
        raw = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"payload\"\r\n"
            "Content-Type: application/json\r\n\r\n"
            + json.dumps(payload("media.play"))
            + f"\r\n--{boundary}--\r\n"
        ).encode()
        parsed = parse_multipart_payload(f"multipart/form-data; boundary={boundary}", raw)
        self.assertEqual("media.play", parsed["event"])

    def test_wrong_webhook_secret_is_rejected_before_body_is_read(self):
        from helper.plex_webhook import attach_webhook_route

        class FakeApp:
            handler = None

            def post(self, path):
                self.path = path
                def register(handler):
                    self.handler = handler
                    return handler
                return register

        class FakeRequest:
            query_params = {"secret": "wrong-secret"}
            headers = {"content-type": "multipart/form-data; boundary=x"}

            def __init__(self):
                self.read_count = 0

            async def stream(self):
                self.read_count += 1
                yield b"not-a-valid-body"

        app = FakeApp()
        attach_webhook_route(app, self.store, self.registry)
        request = FakeRequest()

        response = asyncio.run(app.handler(request))

        self.assertEqual(403, response.status_code)
        self.assertEqual(0, request.read_count)

    def test_stale_nonterminal_sessions_are_pruned(self):
        from helper.plex_webhook import apply_webhook_event
        from helper.scoped_store import ScopedStore

        profile = ScopedStore(self.store, "default")
        profile.set("behavior_sessions", {
            "10:stale": {
                "track_id": "1", "playback_id": "old", "terminal_event": "",
                "updated_at": 100,
            },
            "10:fresh": {
                "track_id": "2", "playback_id": "fresh", "terminal_event": "",
                "updated_at": 20_000,
            },
        })

        apply_webhook_event(
            self.store, self.registry, payload("media.play", player="new-player"), now=21_701
        )

        sessions = profile.get("behavior_sessions")
        self.assertNotIn("10:stale", sessions)
        self.assertIn("10:fresh", sessions)

    def test_webhook_sessions_are_bounded_to_most_recent_256(self):
        from helper.plex_webhook import apply_webhook_event
        from helper.scoped_store import ScopedStore

        profile = ScopedStore(self.store, "default")
        profile.set("behavior_sessions", {
            f"10:player-{index}": {
                "track_id": str(index), "playback_id": f"play-{index}",
                "terminal_event": "", "updated_at": 20_000 + index,
            }
            for index in range(300)
        })

        apply_webhook_event(
            self.store, self.registry, payload("media.play", player="latest"), now=21_000
        )

        sessions = profile.get("behavior_sessions")
        self.assertEqual(256, len(sessions))
        self.assertIn("10:latest", sessions)
        self.assertNotIn("10:player-0", sessions)

    def test_daily_generation_no_longer_samples_active_sessions(self):
        source = (Path(__file__).parents[1] / "src/helper/daily.py").read_text(encoding="utf-8")
        self.assertNotIn("BehaviorTracker(self.store).sample", source)


if __name__ == "__main__":
    unittest.main()
