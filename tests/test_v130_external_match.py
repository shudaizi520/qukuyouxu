import unittest


def source(title, artists, duration_ms, key="s1", album="", version_flags=None):
    return {
        "source_track_key": key,
        "title": title,
        "artists": artists,
        "album": album,
        "duration_ms": duration_ms,
        "version_flags": list(version_flags or []),
        "version_label": "",
    }


def plex(track_id, title, artist, duration, album="", available=True, **extra):
    return {
        "id": str(track_id), "title": title, "artist": artist, "duration": duration,
        "album": album, "available": available, **extra,
    }


class ExternalMatchV130Tests(unittest.TestCase):
    def match(self, sources, tracks, overrides=None):
        from helper.external_match import match_external_tracks

        return match_external_tracks(sources, tracks, overrides or {}, "r1")

    def test_unique_same_title_artist_version_duration_matches(self):
        result = self.match(
            [source("海阔天空", ["Beyond"], 315000)],
            [plex("7", "海阔天空", "Beyond", 315)],
        )
        self.assertEqual(("matched", "7"), (result[0]["status"], result[0]["plex_track_id"]))

    def test_live_and_studio_versions_do_not_cross_match(self):
        result = self.match(
            [source("海阔天空 (Live)", ["Beyond"], 320000)],
            [plex("7", "海阔天空", "Beyond", 315)],
        )
        self.assertEqual("review", result[0]["status"])
        self.assertEqual("version_mismatch", result[0]["reason"])

    def test_equal_candidates_require_review_instead_of_bitrate_tiebreak(self):
        rows = [
            plex("7", "同名歌", "歌手", 180, album="A"),
            plex("8", "同名歌", "歌手", 180, album="B"),
        ]
        result = self.match([source("同名歌", ["歌手"], 180000)], rows)
        self.assertEqual("review", result[0]["status"])
        self.assertEqual(["7", "8"], result[0]["candidate_ids"])

    def test_artist_sets_acdc_and_featured_artists_remain_exact(self):
        tracks = [
            plex("1", "Thunderstruck", "AC/DC", 292),
            plex("2", "合唱", "歌手甲 feat. 歌手乙", 200),
            plex("3", "合唱", "歌手甲", 200),
        ]
        rows = self.match([
            source("Thunderstruck", ["AC/DC"], 292000, key="a"),
            source("合唱", ["歌手甲", "歌手乙"], 200000, key="b"),
            source("合唱", ["歌手甲", "歌手丙"], 200000, key="c"),
        ], tracks)
        self.assertEqual(("matched", "1"), (rows[0]["status"], rows[0]["plex_track_id"]))
        self.assertEqual(("matched", "2"), (rows[1]["status"], rows[1]["plex_track_id"]))
        self.assertEqual(("review", "artist_mismatch"), (rows[2]["status"], rows[2]["reason"]))

    def test_traditional_title_and_missing_source_duration_can_match_uniquely(self):
        rows = self.match(
            [source("後來", ["刘若英"], 0)],
            [plex("7", "后来", "刘若英", 326)],
        )
        self.assertEqual("matched", rows[0]["status"])

    def test_unavailable_and_metadata_conflict_are_review_not_missing(self):
        unavailable = self.match(
            [source("歌曲甲", ["歌手"], 180000)],
            [plex("7", "歌曲甲", "歌手", 180, available=False)],
        )[0]
        conflict = self.match(
            [source("正确歌名", ["歌手"], 180000)],
            [plex("8", "错误歌名", "歌手", 180, _metadata_blocked=True,
                  _metadata_suggestion={"title": "正确歌名"})],
        )[0]
        self.assertEqual(("review", "unavailable"), (unavailable["status"], unavailable["reason"]))
        self.assertEqual(("review", "metadata_conflict"), (conflict["status"], conflict["reason"]))

    def test_manual_match_missing_and_ignored_overrides_are_explicit(self):
        tracks = [plex("7", "歌曲甲", "歌手", 180)]
        rows = self.match([
            source("不存在一", ["歌手"], 180000, key="a"),
            source("不存在二", ["歌手"], 180000, key="b"),
            source("不存在三", ["歌手"], 180000, key="c"),
        ], tracks, {
            "a": {"status": "matched", "plex_track_id": "7"},
            "b": {"status": "missing"},
            "c": {"status": "ignored"},
        })
        self.assertEqual(["matched", "missing", "ignored"], [row["status"] for row in rows])
        self.assertTrue(all(row["manual"] for row in rows))
        self.assertEqual("7", rows[0]["plex_track_id"])

    def test_removed_or_unavailable_manual_rating_key_is_invalidated(self):
        rows = self.match(
            [source("歌曲甲", ["歌手"], 180000)],
            [plex("8", "另一首", "歌手", 180), plex("7", "歌曲甲", "歌手", 180, available=False)],
            {"s1": {"status": "matched", "plex_track_id": "99"}},
        )
        self.assertNotEqual("99", rows[0]["plex_track_id"])
        self.assertFalse(rows[0]["manual"])

    def test_apply_confirmation_accepts_only_available_catalog_rows(self):
        from helper.external_match import apply_external_confirmation
        from helper.match import Catalog

        row = self.match([source("歌曲甲", ["歌手"], 180000)], [plex("7", "另一首", "歌手", 180)])[0]
        catalog = Catalog([plex("7", "另一首", "歌手", 180), plex("8", "下架", "歌手", 180, available=False)])

        chosen = apply_external_confirmation(row, {"status": "matched", "plex_track_id": "7"}, catalog)
        self.assertEqual(("matched", "7", True), (chosen["status"], chosen["plex_track_id"], chosen["manual"]))
        self.assertEqual("missing", apply_external_confirmation(row, {"status": "missing"}, catalog)["status"])
        with self.assertRaises(ValueError):
            apply_external_confirmation(row, {"status": "matched", "plex_track_id": "8"}, catalog)
        with self.assertRaises(ValueError):
            apply_external_confirmation(row, {"status": "matched", "plex_track_id": "99"}, catalog)


if __name__ == "__main__":
    unittest.main()
