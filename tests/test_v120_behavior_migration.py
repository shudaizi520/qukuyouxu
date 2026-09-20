import tempfile
import unittest
from pathlib import Path

from helper.behavior_store import BehaviorRepository
from helper.profiles import ProfileRegistry
from helper.store import Store


NOW = 2_000_000_000


def evidence(key, track_id):
    return {
        "event_key": key,
        "track_id": track_id,
        "kind": "completed",
        "value": 1.0,
        "at": NOW,
    }


class BehaviorMigrationV120Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.registry = ProfileRegistry(self.store)
        self.registry.update(
            "default",
            account={"id": "10", "username": "owner"},
            server={"machine": "machine-a", "url": "http://plex:32400"},
            library={"id": "15", "name": "Music"},
        )
        self.registry.create(
            "Friend", "shared", profile_id="friend", token="friend-token",
            account={"id": "11", "username": "friend"},
            server={"machine": "machine-a", "url": "http://plex:32400"},
            library={"id": "17", "name": "Friend Music"},
        )
        self.repo = BehaviorRepository(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def _seed(self):
        self.repo.append_event("default", evidence("owner-event", "1"))
        self.repo.save_track_state("default", "1", {"positive_evidence": 1})
        self.repo.save_user_state("default", {"valid_outcomes": 1})
        self.repo.append_event("friend", evidence("friend-event", "2"))
        self.repo.save_track_state("friend", "2", {"positive_evidence": 2})
        self.repo.save_user_state("friend", {"valid_outcomes": 2})

    def test_resetting_one_profile_does_not_delete_another_profile_behavior(self):
        self._seed()

        self.registry.switch_unmanaged_library("default", {"id": "16", "name": "Classical"})

        self.assertEqual([], self.repo.list_events("default", NOW))
        self.assertEqual({}, self.repo.load_track_states("default"))
        self.assertEqual({}, self.repo.load_user_state("default"))
        self.assertEqual(["2"], [row["track_id"] for row in self.repo.list_events("friend", NOW)])
        self.assertIn("2", self.repo.load_track_states("friend"))
        self.assertEqual(2, self.repo.load_user_state("friend")["valid_outcomes"])

    def test_deleting_one_profile_removes_only_its_relational_learning_rows(self):
        self._seed()

        self.registry.remove("friend")

        self.assertEqual([], self.repo.list_events("friend", NOW))
        self.assertEqual({}, self.repo.load_track_states("friend"))
        self.assertEqual(["1"], [row["track_id"] for row in self.repo.list_events("default", NOW)])


if __name__ == "__main__":
    unittest.main()
