import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def payload(event, account="10", machine="machine-a", track="123", **metadata):
    values = {"ratingKey": track, "type": "track", "title": "Song", "duration": 200000}
    values.update(metadata)
    return {
        "event": event,
        "Account": {"id": account, "title": "owner"},
        "Server": {"uuid": machine, "title": "Main"},
        "Player": {"uuid": "player-a", "title": "Phone"},
        "Metadata": values,
    }


class WebhookHealthV047Tests(unittest.TestCase):
    def setUp(self):
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ScopedStore
        from helper.store import Store

        self.temp = tempfile.TemporaryDirectory()
        self.base = Store(Path(self.temp.name))
        self.registry = ProfileRegistry(self.base)
        self.registry.update(
            "default",
            token="owner-secret",
            account={"id": "10", "username": "owner"},
            server={"machine": "machine-a", "name": "Main", "url": "http://plex:32400"},
            library={"id": "15", "name": "Music"},
        )
        self.profile = ScopedStore(self.base, "default")
        from helper.plex_webhook import webhook_secret
        with patch("helper.plex_webhook.time.time", return_value=0):
            webhook_secret(self.base)

    def tearDown(self):
        self.temp.cleanup()

    def test_never_seen_is_not_reported_as_learning(self):
        from helper.plex_webhook import webhook_health

        health = webhook_health(self.base, self.profile, now=100)
        second = webhook_health(self.base, self.profile, now=101)
        self.assertEqual("not_connected", health["state"])
        self.assertFalse(health["connected"])
        self.assertEqual(0, health["event_count"])
        self.assertEqual(health["endpoint_path"], second["endpoint_path"])
        self.assertTrue(health["endpoint_path"].startswith("/api/plex/webhook?secret="))
        self.assertGreaterEqual(len(health["endpoint_path"].split("=", 1)[1]), 40)

    def test_neutral_play_proves_connection_without_inventing_learning(self):
        from helper.plex_webhook import apply_webhook_event, webhook_health

        result = apply_webhook_event(self.base, self.registry, payload("media.play"), now=100)
        health = webhook_health(self.base, self.profile, now=101)

        self.assertEqual("accepted", result["status"])
        self.assertEqual("connected_waiting", health["state"])
        self.assertTrue(health["connected"])
        self.assertEqual("media.play", health["last_event"])
        self.assertEqual(0, health["event_count"])

    def test_disabled_profile_delivery_still_proves_global_webhook_connection(self):
        from helper.plex_webhook import apply_webhook_event, webhook_health

        self.profile.set("product_settings", {"behavior_enabled": False})
        result = apply_webhook_event(self.base, self.registry, payload("media.play"), now=100)
        health = webhook_health(self.base, self.profile, now=101)

        self.assertEqual("ignored", result["status"])
        self.assertEqual("disabled", result["reason"])
        self.assertEqual("disabled", health["state"])
        self.assertFalse(health["connected"])
        self.assertTrue(health["global_connected"])

        apply_webhook_event(
            self.base, self.registry, payload("media.play", machine="unknown"), now=102
        )
        later = webhook_health(self.base, self.profile, now=103)
        self.assertTrue(later["global_connected"])

    def test_scored_event_changes_state_to_learning(self):
        from helper.plex_webhook import apply_webhook_event, webhook_health

        apply_webhook_event(self.base, self.registry, payload("media.scrobble"), now=100)
        health = webhook_health(self.base, self.profile, now=101)

        self.assertEqual("learning", health["state"])
        self.assertEqual(1, health["event_count"])
        self.assertEqual(100, health["last_behavior_at"])

    def test_owner_webhook_account_one_maps_to_unique_owner_on_same_server(self):
        from helper.plex_webhook import apply_webhook_event, webhook_health

        result = apply_webhook_event(
            self.base, self.registry, payload("media.play", account="1"), now=100
        )
        health = webhook_health(self.base, self.profile, now=101)

        self.assertEqual("accepted", result["status"])
        self.assertEqual("default", result["profile_id"])
        self.assertTrue(health["connected"])

    def test_owner_webhook_account_one_is_rejected_when_owner_is_not_unique(self):
        from helper.plex_webhook import apply_webhook_event

        self.registry.create(
            name="Other owner",
            kind="owner",
            profile_id="other-owner",
            token="other-secret",
            account={"id": "99", "username": "other"},
            server={"machine": "machine-a", "name": "Main", "url": "http://plex:32400"},
            library={"id": "15", "name": "Music"},
        )
        result = apply_webhook_event(
            self.base, self.registry, payload("media.play", account="1"), now=100
        )

        self.assertEqual("ignored", result["status"])
        self.assertEqual("identity_not_unique", result["reason"])

    def test_unknown_identity_is_visible_but_not_connected_to_current_profile(self):
        from helper.plex_webhook import apply_webhook_event, webhook_health

        result = apply_webhook_event(
            self.base, self.registry, payload("media.play", machine="unknown"), now=100
        )
        health = webhook_health(self.base, self.profile, now=101)

        self.assertEqual("ignored", result["status"])
        self.assertEqual("identity_not_unique", health["last_reason"])
        self.assertFalse(health["connected"])
        self.assertEqual("not_connected", health["state"])

    def test_unrelated_delivery_does_not_erase_a_profiles_connection_receipt(self):
        from helper.plex_webhook import WEBHOOK_INGRESS_KEY, apply_webhook_event, webhook_health

        apply_webhook_event(self.base, self.registry, payload("media.play", track="1"), now=100)
        receipt = self.base.get(WEBHOOK_INGRESS_KEY)
        self.base.set(WEBHOOK_INGRESS_KEY, {**receipt, "received_at": 101, "status": "ignored", "profile_id": ""})
        health = webhook_health(self.base, self.profile, now=102)
        self.assertTrue(health["connected"])

    def test_global_webhook_status_stays_connected_when_viewing_another_profile(self):
        from helper.plex_webhook import apply_webhook_event, webhook_health
        from helper.scoped_store import ScopedStore

        self.registry.create(
            name="Friend", kind="shared", profile_id="friend-42", token="friend-token",
            account={"id": "42", "username": "friend"},
            server={"machine": "machine-a", "name": "Main", "url": "http://plex:32400"},
            library={"id": "15", "name": "Music"},
        )
        apply_webhook_event(self.base, self.registry, payload("media.play"), now=100)

        health = webhook_health(
            self.base, ScopedStore(self.base, "friend-42"), now=101
        )

        self.assertFalse(health["connected"])
        self.assertTrue(health["global_connected"])
        self.assertEqual(100, health["global_last_received_at"])
        self.assertEqual("media.play", health["global_last_event"])

    def test_old_receipt_does_not_validate_an_existing_secret_after_upgrade(self):
        from helper.plex_webhook import WEBHOOK_INGRESS_KEY, WEBHOOK_SECRET_KEY, webhook_health

        self.base.set(WEBHOOK_SECRET_KEY, "x" * 48)
        self.base.set("plex_webhook_secret_created_at_v1", None)
        self.base.set(WEBHOOK_INGRESS_KEY, {
            "received_at": 100,
            "event": "media.stop",
            "status": "recorded",
            "profile_id": "default",
            "profiles": {
                "default": {
                    "received_at": 100,
                    "event": "media.stop",
                    "status": "recorded",
                    "profile_id": "default",
                }
            },
        })
        self.profile.set("behavior_events", [{"at": 100, "value": 1, "track_id": "123"}])

        health = webhook_health(self.base, self.profile, now=200)

        self.assertEqual("not_connected", health["state"])
        self.assertFalse(health["connected"])
        self.assertEqual(200, self.base.get("plex_webhook_secret_created_at_v1"))

    def test_expired_learning_events_do_not_keep_status_counts_alive(self):
        from helper.behavior import MAX_EVENT_AGE
        from helper.plex_webhook import apply_webhook_event, webhook_health

        now = MAX_EVENT_AGE + 1_000
        apply_webhook_event(self.base, self.registry, payload("media.play"), now=now - 10)
        self.profile.set(
            "behavior_events",
            [{"at": now - MAX_EVENT_AGE - 1, "value": 1, "track_id": "123"}],
        )

        health = webhook_health(self.base, self.profile, now=now)

        self.assertEqual("connected_waiting", health["state"])
        self.assertEqual(0, health["event_count"])
        self.assertIsNone(health["last_behavior_at"])


if __name__ == "__main__":
    unittest.main()
