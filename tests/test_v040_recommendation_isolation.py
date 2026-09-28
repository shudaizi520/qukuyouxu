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

    def test_daily_smart_and_recent_history_never_cross_profile_boundaries(self):
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
                "daily_history": [{"date": "2026-09-28", "ids": ["1", "2"]}],
                "smart_mix_history": {"weekly": [{"ids": ["1", "3"]}],
                                      "time_capsule": [{"ids": ["4"]}]},
                "recent_additions_history": [{"ids": ["8", "9"]}],
            })
            friend.set_many({
                "daily_history": [{"date": "2026-09-28", "ids": ["2", "5"]}],
                "smart_mix_history": {"weekly": [{"ids": ["2", "6"]}],
                                      "time_capsule": [{"ids": ["7"]}]},
                "recent_additions_history": [{"ids": ["8", "9"]}],
            })

            self.assertNotEqual(owner.get("daily_history"), friend.get("daily_history"))
            self.assertNotEqual(owner.get("smart_mix_history"), friend.get("smart_mix_history"))
            self.assertEqual(owner.get("recent_additions_history"), friend.get("recent_additions_history"))
            friend_daily = friend.get("daily_history")
            friend_daily.append({"date": "2026-09-29", "ids": ["10"]})
            friend.set("daily_history", friend_daily)
            self.assertEqual(1, len(owner.get("daily_history")))


if __name__ == "__main__":
    unittest.main()
