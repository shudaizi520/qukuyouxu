import tempfile
import unittest
from pathlib import Path

from helper.daily_mix_v2 import select_daily_mix_v2
from helper.plex_webhook import apply_webhook_event, load_behavior_snapshot
from helper.profiles import ProfileRegistry
from helper.scoped_store import ScopedStore
from helper.store import Store


NOW = 2_000_000_000


def payload(event, track_id, library, *, offset=0):
    return {
        "event": event,
        "Account": {"id": "10", "title": "owner"},
        "Server": {"uuid": "machine-a", "title": "Main"},
        "Player": {"uuid": "phone", "title": "Phone"},
        "Metadata": {
            "ratingKey": str(track_id), "type": "track", "title": f"Song {track_id}",
            "duration": 200000, "viewOffset": offset, "librarySectionID": str(library),
        },
    }


def tracks():
    return [{
        "id": str(index), "title": f"Song {index}", "artist": f"Artist {index}",
        "album": f"Album {index}", "duration": 200, "available": True,
        "view_count": 0, "last_viewed_at": 0, "user_rating": 0,
        "added_at": NOW - 1000, "genres": [],
    } for index in range(1, 13)]


class LearningEndToEndV120Tests(unittest.TestCase):
    def test_play_stop_next_cools_only_the_matching_user_and_library(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Store(Path(directory))
            registry = ProfileRegistry(base)
            registry.update(
                "default", account={"id": "10", "username": "owner"},
                server={"machine": "machine-a", "url": "http://plex:32400"},
                library={"id": "15", "name": "Music"},
            )
            sibling = registry.create_for_library(
                "default", {"id": "16", "name": "Classical"}, profile_id="classical",
            )
            apply_webhook_event(base, registry, payload("media.play", "1", "15"), now=NOW)
            apply_webhook_event(
                base, registry, payload("media.stop", "1", "15", offset=20000), now=NOW + 20,
            )
            apply_webhook_event(base, registry, payload("media.play", "2", "15"), now=NOW + 25)

            owner = ScopedStore(base, "default")
            other = ScopedStore(base, sibling["id"])
            owner_behavior = load_behavior_snapshot(owner, NOW + 25)
            other_behavior = load_behavior_snapshot(other, NOW + 25)
            owner_mix = select_daily_mix_v2(
                tracks(), {}, {"tracks": {}, "artists": {}}, {"size": 12},
                [], [], NOW + 25, "owner", play_events=[], behavior=owner_behavior,
                user_state={},
            )
            other_mix = select_daily_mix_v2(
                tracks(), {}, {"tracks": {}, "artists": {}}, {"size": 12},
                [], [], NOW + 25, "other", play_events=[], behavior=other_behavior,
                user_state={},
            )

        self.assertIn("1", owner_behavior)
        self.assertGreater(owner_behavior["1"]["cooldown_until"], NOW + 25)
        self.assertNotIn("1", other_behavior)
        self.assertNotIn("1", {row["id"] for row in owner_mix["items"]})
        self.assertIn("1", {row["id"] for row in other_mix["items"]})


if __name__ == "__main__":
    unittest.main()
