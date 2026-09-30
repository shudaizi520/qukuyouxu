"""Interactive reads ask Plex for the selected subset, not the full library."""
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest

from helper.clients import PlexClient, PlexError
from helper.playlist_hub import search_library_live, favorite_playlist_detail_live


class Store:
    def get(self, key, default=None):
        return {"section": "11"} if key == "settings" else default


def client_with_rows(rows):
    client = object.__new__(PlexClient)
    calls = []

    def xml(path, method="GET", params=None):
        calls.append((path, params))
        root = ET.Element("MediaContainer", totalSize=str(len(rows)))
        for row in rows:
            track = ET.SubElement(root, "Track", row)
            ET.SubElement(ET.SubElement(track, "Media"), "Part", file="/music/song.flac")
        return root

    client._xml = xml
    return client, calls


def test_search_preserves_and_between_words_and_or_between_fields():
    client, calls = client_with_rows([
        {"ratingKey": "7", "title": "稻香", "grandparentTitle": "周杰伦", "librarySectionID": "11"},
    ])
    engine = SimpleNamespace(store=Store(), plex_factory=lambda _cfg: client)
    assert [row["id"] for row in search_library_live(engine, "周杰伦 稻香")] == ["7"]
    path, params = calls[0]
    assert path == "/library/sections/11/all"
    assert params == [
        ("type", 10), ("X-Plex-Container-Start", 0), ("X-Plex-Container-Size", 40),
        ("push", 1),
        ("push", 1), ("track.title", "周杰伦"), ("or", 1),
        ("artist.title", "周杰伦"), ("or", 1), ("album.title", "周杰伦"), ("pop", 1),
        ("and", 1),
        ("push", 1), ("track.title", "稻香"), ("or", 1),
        ("artist.title", "稻香"), ("or", 1), ("album.title", "稻香"), ("pop", 1),
        ("pop", 1),
    ]
    assert len(calls) == 1


def test_search_rejects_foreign_library_results():
    client, _calls = client_with_rows([
        {"ratingKey": "7", "title": "稻香", "librarySectionID": "22"},
    ])
    with pytest.raises(PlexError):
        client.search_tracks("11", "稻香")


def test_favorites_filter_is_sent_to_plex_and_ratings_still_checked():
    client, calls = client_with_rows([
        {"ratingKey": "7", "title": "四星", "userRating": "8", "librarySectionID": "11"},
        {"ratingKey": "8", "title": "三星", "userRating": "6", "librarySectionID": "11"},
    ])
    engine = SimpleNamespace(store=Store(), plex_factory=lambda _cfg: client)
    assert [row["id"] for row in favorite_playlist_detail_live(engine)["tracks"]] == ["7"]
    assert calls[0][0] == "/library/sections/11/all"
    assert calls[0][1]["track.userRating>"] == 8


def test_favorites_read_all_filtered_pages_not_just_first_page():
    client = object.__new__(PlexClient)
    calls = []

    def xml(path, method="GET", params=None):
        calls.append(params)
        start = params["X-Plex-Container-Start"]
        root = ET.Element("MediaContainer", totalSize="301")
        for index in range(start, min(start + 300, 301)):
            ET.SubElement(root, "Track", ratingKey=str(index + 1), title="喜欢", userRating="10")
        return root

    client._xml = xml
    assert len(client.liked_tracks("11")) == 301
    assert [row["X-Plex-Container-Start"] for row in calls] == [0, 300]
    assert all(row["track.userRating>"] == 8 for row in calls)
