import unittest


def signal(event, track="1", *, player="living-room", offset=0, duration=0,
           machine="server", account="owner", library="music"):
    return {
        "event": event,
        "track_id": str(track),
        "player_id": player,
        "machine": machine,
        "account_id": account,
        "library_id": library,
        "offset_seconds": float(offset),
        "duration_seconds": float(duration),
    }


class PlaybackLearningV120Tests(unittest.TestCase):
    def test_stop_without_next_track_is_neutral(self):
        from helper.playback_learning import advance_playback

        sessions, _ = advance_playback({}, signal("media.play"), 0, 240)
        sessions, rows = advance_playback(sessions, signal("media.stop", offset=30), 30, 240)

        self.assertEqual([], rows)

    def test_same_player_next_track_confirms_early_skip(self):
        from helper.playback_learning import advance_playback

        sessions, _ = advance_playback({}, signal("media.play"), 0, 240)
        sessions, _ = advance_playback(sessions, signal("media.stop", offset=30), 30, 240)
        _, rows = advance_playback(sessions, signal("media.play", "2"), 40, 180)

        self.assertEqual(("confirmed_skip", 0.45), (rows[0]["kind"], rows[0]["value"]))
        self.assertAlmostEqual(0.125, rows[0]["progress"])

    def test_different_identity_does_not_confirm_pending_skip(self):
        from helper.playback_learning import advance_playback

        dimensions = ("player_id", "machine", "account_id", "library_id")
        changes = ("bedroom", "other-server", "friend", "classical")
        for field, changed in zip(dimensions, changes):
            sessions, _ = advance_playback({}, signal("media.play"), 0, 240)
            sessions, _ = advance_playback(sessions, signal("media.stop", offset=30), 30, 240)
            next_signal = signal("media.play", "2")
            next_signal[field] = changed
            _, rows = advance_playback(sessions, next_signal, 40, 180)
            self.assertEqual([], rows, field)

    def test_missing_webhook_duration_uses_catalog_duration(self):
        from helper.playback_learning import advance_playback

        sessions, _ = advance_playback({}, signal("media.play"), 0, 200)
        sessions, _ = advance_playback(sessions, signal("media.stop", offset=20), 20, 200)
        _, rows = advance_playback(sessions, signal("media.play", "2"), 25, 180)

        self.assertEqual(0.45, rows[0]["value"])
        self.assertEqual(200.0, rows[0]["duration"])

    def test_missing_all_duration_remains_neutral(self):
        from helper.playback_learning import advance_playback

        sessions, _ = advance_playback({}, signal("media.play"), 0, 0)
        sessions, _ = advance_playback(sessions, signal("media.stop", offset=20), 20, 0)
        _, rows = advance_playback(sessions, signal("media.play", "2"), 25, 180)

        self.assertEqual([], rows)

    def test_pause_time_is_not_counted_as_listening(self):
        from helper.playback_learning import advance_playback

        sessions, _ = advance_playback({}, signal("media.play"), 0, 100)
        sessions, _ = advance_playback(sessions, signal("media.pause", offset=10), 10, 100)
        sessions, _ = advance_playback(sessions, signal("media.resume", offset=10), 90, 100)
        sessions, _ = advance_playback(sessions, signal("media.stop", offset=20), 100, 100)
        _, rows = advance_playback(sessions, signal("media.play", "2"), 105, 100)

        self.assertEqual("confirmed_skip", rows[0]["kind"])
        self.assertAlmostEqual(0.20, rows[0]["progress"])

    def test_large_seek_does_not_create_false_completion(self):
        from helper.playback_learning import advance_playback

        sessions, _ = advance_playback({}, signal("media.play"), 0, 240)
        sessions, _ = advance_playback(sessions, signal("media.pause", offset=220), 10, 240)
        sessions, _ = advance_playback(sessions, signal("media.resume", offset=220), 11, 240)
        sessions, _ = advance_playback(sessions, signal("media.stop", offset=225), 16, 240)
        _, rows = advance_playback(sessions, signal("media.play", "2"), 20, 180)

        self.assertEqual("confirmed_skip", rows[0]["kind"])
        self.assertLess(rows[0]["progress"], 0.20)

    def test_scrobble_emits_one_completion_and_delayed_stop_emits_nothing(self):
        from helper.playback_learning import advance_playback

        sessions, _ = advance_playback({}, signal("media.play"), 0, 100)
        sessions, first = advance_playback(sessions, signal("media.scrobble", offset=95), 95, 100)
        sessions, duplicate = advance_playback(sessions, signal("media.scrobble", offset=95), 96, 100)
        _, delayed = advance_playback(sessions, signal("media.stop", offset=95), 97, 100)

        self.assertEqual(("completed", 1.0), (first[0]["kind"], first[0]["value"]))
        self.assertEqual([], duplicate)
        self.assertEqual([], delayed)

    def test_new_track_while_old_track_is_active_confirms_transition(self):
        from helper.playback_learning import advance_playback

        sessions, _ = advance_playback({}, signal("media.play"), 0, 240)
        _, rows = advance_playback(sessions, signal("media.play", "2"), 15, 180)

        self.assertEqual("confirmed_skip", rows[0]["kind"])

    def test_same_track_replay_creates_new_generation_without_skip(self):
        from helper.playback_learning import advance_playback

        sessions, _ = advance_playback({}, signal("media.play"), 0, 240)
        sessions, _ = advance_playback(sessions, signal("media.stop", offset=20), 20, 240)
        old_id = next(iter(sessions.values()))["playback_id"]
        sessions, rows = advance_playback(sessions, signal("media.play"), 25, 240)
        new_id = next(iter(sessions.values()))["playback_id"]

        self.assertEqual([], rows)
        self.assertNotEqual(old_id, new_id)

    def test_pending_stop_expires_without_negative_evidence(self):
        from helper.playback_learning import advance_playback

        sessions, _ = advance_playback({}, signal("media.play"), 0, 240)
        sessions, _ = advance_playback(sessions, signal("media.stop", offset=20), 20, 240)
        _, rows = advance_playback(sessions, signal("media.play", "2"), 51, 180)

        self.assertEqual([], rows)


if __name__ == "__main__":
    unittest.main()
