import tempfile
import unittest
from pathlib import Path

from helper.behavior_store import BehaviorRepository
from helper.profiles import ProfileRegistry
from helper.scoped_store import ScopedStore
from helper.store import Store


def payload(event, track="1", *, player="phone", duration=200000, offset=0,
            account="10", machine="machine-a", library="15", rating=None):
    metadata = {
        "ratingKey": str(track),
        "type": "track",
        "title": f"Song {track}",
        "duration": duration,
        "viewOffset": offset,
        "librarySectionID": library,
    }
    if rating is not None:
        metadata["userRating"] = rating
    return {
        "event": event,
        "Account": {"id": account, "title": "owner"},
        "Server": {"uuid": machine, "title": "Main"},
        "Player": {"uuid": player, "title": "Phone"},
        "Metadata": metadata,
    }


class WebhookIntegrationV120Tests(unittest.TestCase):
    def setUp(self):
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
        self.repo = BehaviorRepository(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_stop_is_recorded_only_after_same_player_starts_next_track(self):
        from helper.plex_webhook import apply_webhook_event

        apply_webhook_event(self.store, self.registry, payload("media.play"), now=0)
        apply_webhook_event(
            self.store, self.registry,
            payload("media.stop", offset=20000), now=20,
        )
        self.assertEqual([], self.repo.list_events("default", 20))

        apply_webhook_event(self.store, self.registry, payload("media.play", "2"), now=25)

        rows = self.repo.list_events("default", 25)
        self.assertEqual("confirmed_skip", rows[0]["kind"])
        self.assertEqual(0.45, rows[0]["value"])

    def test_webhook_duration_falls_back_to_matching_profile_catalog_only(self):
        from helper.plex_webhook import apply_webhook_event

        ScopedStore(self.store, "default").set("catalog", [{"id": "1", "duration": 200}])
        apply_webhook_event(self.store, self.registry, payload("media.play", duration=0), now=0)
        apply_webhook_event(
            self.store, self.registry,
            payload("media.stop", duration=0, offset=20000), now=20,
        )
        apply_webhook_event(self.store, self.registry, payload("media.play", "2"), now=25)

        row = self.repo.list_events("default", 25)[0]
        self.assertEqual(200.0, row["duration"])
        self.assertEqual("confirmed_skip", row["kind"])

    def test_scrobble_and_rating_update_relational_aggregates(self):
        from helper.plex_webhook import apply_webhook_event

        complete = apply_webhook_event(
            self.store, self.registry,
            payload("media.scrobble", offset=190000), now=100,
        )
        avoid = apply_webhook_event(
            self.store, self.registry,
            payload("media.rate", "2", rating=2), now=110,
        )

        states = self.repo.load_track_states("default")
        self.assertEqual("recorded", complete["status"])
        self.assertEqual("recorded", avoid["status"])
        self.assertGreater(states["1"]["positive_evidence"], 0)
        self.assertTrue(states["2"]["hard_avoid"])

    def test_load_snapshot_migrates_legacy_once_and_keeps_profiles_isolated(self):
        from helper.plex_webhook import load_behavior_snapshot

        owner = ScopedStore(self.store, "default")
        owner.set("behavior_events", [{"track_id": "7", "kind": "completed", "value": 1, "at": 100}])
        first = load_behavior_snapshot(owner, 100)
        second = load_behavior_snapshot(owner, 100)

        self.assertIn("7", first)
        self.assertEqual(first, second)
        self.assertEqual([], self.repo.list_events("friend", 100))


if __name__ == "__main__":
    unittest.main()
