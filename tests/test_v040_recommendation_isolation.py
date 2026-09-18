import tempfile
import unittest
from pathlib import Path


class RecommendationIsolationV040Tests(unittest.TestCase):
    def test_feedback_behavior_and_managed_state_are_profile_local(self):
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ScopedStore
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            base = Store(Path(root))
            registry = ProfileRegistry(base)
            registry.create(name="Friend", kind="shared", profile_id="friend-42", token="friend-secret")
            owner = ScopedStore(base, "default")
            friend = ScopedStore(base, "friend-42")
            owner.set_many({
                "feedback": {"tracks": {"1": {"value": "like"}}, "artists": {}},
                "behavior_events": [{"track_id": "1", "value": 1, "at": 100}],
                "daily_managed": {"id": "100"},
            })
            friend.set_many({
                "feedback": {"tracks": {"2": {"value": "avoid"}}, "artists": {}},
                "behavior_events": [{"track_id": "2", "value": -1, "at": 100}],
                "daily_managed": {"id": "200"},
            })
            self.assertNotEqual(owner.get("feedback"), friend.get("feedback"))
            self.assertNotEqual(owner.get("behavior_events"), friend.get("behavior_events"))
            self.assertEqual("100", owner.get("daily_managed")["id"])
            self.assertEqual("200", friend.get("daily_managed")["id"])


if __name__ == "__main__":
    unittest.main()
