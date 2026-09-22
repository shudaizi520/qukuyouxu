"""Playlist cover candidates never leak tracks from another playlist/profile."""
from pathlib import Path
from unittest.mock import patch
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_cover_candidates_keep_four_unique_available_artworks():
    from helper.playlist_hub import playlist_cover_candidates

    detail = {"tracks": [
        {"id": "11", "thumb": "/library/metadata/11/thumb/1"},
        {"id": "11", "thumb": "/library/metadata/11/thumb/1"},
        {"id": "12", "thumb": ""},
        {"id": "13", "thumb": "/library/metadata/13/thumb/1"},
        {"id": "bad", "thumb": "/library/metadata/99/thumb/1"},
        {"id": "14", "thumb": "/library/metadata/14/thumb/1"},
        {"id": "15", "thumb": "/library/metadata/15/thumb/1"},
        {"id": "16", "thumb": "/library/metadata/16/thumb/1"},
    ]}
    with patch("helper.playlist_hub.playlist_detail", return_value=detail):
        result = playlist_cover_candidates(object(), "plex", "500")

    assert result == {"track_ids": ["11", "13", "14", "15"]}


def test_cover_candidates_have_empty_fallback_and_preserve_playlist_access_checks():
    from helper.playlist_hub import playlist_cover_candidates

    with patch("helper.playlist_hub.playlist_detail", return_value={"tracks": [{"id": "11", "thumb": ""}]}):
        assert playlist_cover_candidates(object(), "daily", "daily") == {"track_ids": []}

    with patch("helper.playlist_hub.playlist_detail", side_effect=ValueError("隐藏的歌单")) as detail:
        with pytest.raises(ValueError, match="隐藏的歌单"):
            playlist_cover_candidates(object(), "plex", "500", {"500"})
        assert detail.call_args.args[1:] == ("plex", "500", {"500"})
