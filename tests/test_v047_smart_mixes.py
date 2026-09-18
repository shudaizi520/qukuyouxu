import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

NOW = 1_800_000_000
DAY = 86400


def track(index, artist=None, **values):
    row = {
        "id": str(index),
        "title": f"Song {index}",
        "artist": artist or f"Artist {index}",
        "album": f"Album {index}",
        "duration": 180,
        "available": True,
        "view_count": index % 5,
        "last_viewed_at": NOW - (index + 1) * 30 * DAY,
        "added_at": NOW - index * 10 * DAY,
        "user_rating": 8 if index % 4 == 0 else 0,
        "year": 1990 + index,
        "genres": ["流行" if index % 2 else "摇滚"],
    }
    row.update(values)
    return row


class SmartMixSelectionV047Tests(unittest.TestCase):
    def test_weekly_uses_recent_history_to_find_top_artists_deterministically(self):
        from helper.smart_mixes import select_smart_mix

        tracks = [track(1, "A"), track(2, "A"), track(3, "B"), track(4, "B"), track(5, "C")]
        events = [
            {"id": "1", "viewed_at": NOW - DAY},
            {"id": "2", "viewed_at": NOW - 2 * DAY},
            {"id": "2", "viewed_at": NOW - 3 * DAY},
            {"id": "3", "viewed_at": NOW - 4 * DAY},
        ]
        first = select_smart_mix("weekly", tracks, events, {"size": 10}, NOW, "same")
        second = select_smart_mix("weekly", tracks, events, {"size": 10}, NOW, "same")

        self.assertEqual(first["items"], second["items"])
        self.assertEqual(4, len(first["items"]))
        self.assertTrue(all(item["artist"] in {"A", "B"} for item in first["items"]))
        self.assertEqual("每周常听", first["title"])

    def test_time_capsule_only_returns_played_tracks_older_than_threshold(self):
        from helper.smart_mixes import select_smart_mix

        tracks = [
            track(1, last_viewed_at=NOW - 400 * DAY, view_count=5),
            track(2, last_viewed_at=NOW - 100 * DAY, view_count=5),
            track(3, last_viewed_at=NOW - 500 * DAY, view_count=0),
        ]
        result = select_smart_mix(
            "time_capsule", tracks, [], {"size": 10, "stale_days": 180}, NOW, "capsule"
        )

        self.assertEqual(["1"], [item["id"] for item in result["items"]])
        self.assertIn("400", result["items"][0]["reasons"][0])

    def test_recent_additions_respects_window_and_song_dedupe(self):
        from helper.smart_mixes import select_smart_mix

        tracks = [
            track(1, title="Same", artist="A", added_at=NOW - 2 * DAY),
            track(2, title="Same", artist="A", added_at=NOW - DAY),
            track(3, added_at=NOW - 120 * DAY),
        ]
        result = select_smart_mix(
            "recent_additions", tracks, [], {"size": 10, "added_days": 90}, NOW, "new"
        )

        self.assertEqual(1, len(result["items"]))
        self.assertEqual("2", result["items"][0]["id"])

    def test_custom_filters_year_rating_playcount_genre_and_artist_cap(self):
        from helper.smart_mixes import select_smart_mix

        tracks = [
            track(1, "A", year=2001, user_rating=9, view_count=1, genres=["摇滚"]),
            track(2, "A", year=2002, user_rating=8, view_count=2, genres=["摇滚"]),
            track(3, "A", year=2003, user_rating=10, view_count=0, genres=["摇滚"]),
            track(4, "B", year=2004, user_rating=9, view_count=1, genres=["摇滚"]),
            track(5, "C", year=1999, user_rating=9, view_count=1, genres=["摇滚"]),
            track(6, "D", year=2005, user_rating=4, view_count=1, genres=["摇滚"]),
            track(7, "E", year=2005, user_rating=9, view_count=9, genres=["民谣"]),
        ]
        result = select_smart_mix(
            "custom",
            tracks,
            [],
            {
                "size": 10,
                "year_min": 2000,
                "year_max": 2010,
                "min_rating": 8,
                "max_play_count": 2,
                "genres": ["摇滚"],
                "artist_cap": 2,
                "sort_by": "rating",
            },
            NOW,
            "custom",
        )

        self.assertEqual({"1", "3", "4"}, {item["id"] for item in result["items"]})
        self.assertEqual(2, sum(item["artist"] == "A" for item in result["items"]))

    def test_custom_without_year_filter_keeps_tracks_with_missing_year(self):
        from helper.smart_mixes import select_smart_mix

        tracks = [track(index, year=0) for index in range(1, 13)]
        result = select_smart_mix(
            "custom", tracks, [], {"size": 10, "sort_by": "random"}, NOW, "no-year"
        )

        self.assertEqual(10, len(result["items"]))

    def test_custom_explicit_year_filter_excludes_tracks_with_missing_year(self):
        from helper.smart_mixes import select_smart_mix

        tracks = [track(1, year=0), track(2, year=2008), track(3, year=2020)]
        result = select_smart_mix(
            "custom", tracks, [], {"size": 10, "year_min": 2000, "year_max": 2010}, NOW, "year"
        )

        self.assertEqual(["2"], [item["id"] for item in result["items"]])

    def test_invalid_kind_and_bounds_are_rejected(self):
        from helper.smart_mixes import select_smart_mix

        with self.assertRaises(ValueError):
            select_smart_mix("unknown", [], [], {"size": 30}, NOW, "x")
        for size in (9, 101, True):
            with self.assertRaises(ValueError):
                select_smart_mix("custom", [], [], {"size": size}, NOW, "x")

    def test_plex_track_parser_keeps_year_style_and_mood_for_custom_filters(self):
        from helper.clients import parse_plex_track

        element = ET.fromstring(
            '<Track ratingKey="1" title="T" grandparentTitle="A" parentTitle="B" '
            'duration="180000" year="2008" originallyAvailableAt="2008-02-03">'
            '<Genre tag="摇滚"/><Style tag="独立"/><Mood tag="热烈"/>'
            '<Media bitrate="1000"><Part file="/music/a.flac" exists="1"/></Media></Track>'
        )
        parsed = parse_plex_track(element)

        self.assertEqual(2008, parsed["year"])
        self.assertEqual(["独立"], parsed["styles"])
        self.assertEqual(["热烈"], parsed["moods"])


if __name__ == "__main__":
    unittest.main()
