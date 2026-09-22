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


def test_cover_route_does_not_take_the_interactive_playlist_gate():
    from helper.playlist_hub import attach_playlist_hub_routes

    class App:
        routes = {}

        def get(self, path):
            return lambda function: self.routes.setdefault(path, function)

        post = get

    class BusyEngine:
        def exclusive(self):
            raise AssertionError("封面请求不能抢占开歌单的任务锁")

    engine = BusyEngine()
    store = type("Store", (), {"profile_id": "p1"})()
    profiles = type("Profiles", (), {"get": lambda _self, _profile: {"id": "p1"}})()
    runtime = type("Runtime", (), {"engine": lambda _self, _profile: engine})()
    app = App()
    attach_playlist_hub_routes(app, store, runtime, profiles, None, None)

    with patch("helper.playlist_hub.playlist_cover_candidates", return_value={"track_ids": ["11"]}) as candidates:
        result = app.routes["/api/playlists/{kind}/{key}/cover"]("daily", "daily")
    assert result == {"track_ids": ["11"]}
    candidates.assert_called_once_with(engine, "daily", "daily", ())
