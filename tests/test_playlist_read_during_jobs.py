"""Read-only playlist routes stay available while automation owns the write lock."""
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
import sys

from fastapi import Request, Response


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class App:
    def __init__(self):
        self.routes = {}

    def get(self, path):
        return lambda function: self.routes.setdefault(path, function)

    post = get


class Store:
    profile_id = "p1"


class Profiles:
    def get(self, _profile_id):
        return {"id": "p1", "created_at": ""}

    @contextmanager
    def fixed_active(self, _profile_id, enabled_only=True):
        yield


class Runtime:
    def __init__(self, engine):
        self._engine = engine

    def engine(self, _profile_id):
        return self._engine


class BusyEngine:
    job = {"running": True, "kind": "library_maintenance"}

    def exclusive(self):
        raise AssertionError("只读请求不应抢后台任务持有的独占锁")


class IdleEngine:
    job = {"running": False}

    def __init__(self):
        self.exclusive_entries = 0

    @contextmanager
    def exclusive(self):
        self.exclusive_entries += 1
        yield


def request(path, *, cookies=(), headers=()):
    return Request({
        "type": "http", "method": "GET", "scheme": "http",
        "server": ("testserver", 80), "path": path,
        "query_string": b"", "headers": list(headers),
    })


def routes_for(engine):
    from helper.playlist_hub import attach_playlist_hub_routes

    app = App()
    attach_playlist_hub_routes(app, Store(), Runtime(engine), Profiles(), None, None)
    return app.routes


def test_busy_job_keeps_playlist_detail_audio_and_artwork_readable():
    routes = routes_for(BusyEngine())

    with patch("helper.playlist_hub.playlist_detail", return_value={"tracks": []}) as detail:
        assert routes["/api/playlists/{kind}/{key}"]("daily", "daily") == {"tracks": []}
        assert detail.call_args.kwargs["migrate_ownership"] is False

    with patch("helper.playlist_hub.stream_playlist_audio", return_value={"stream": True}) as audio:
        result = routes["/api/playlists/{kind}/{key}/tracks/{track_id}/audio"](
            "daily", "daily", "11", request("/audio"), "p1", "0",
        )
        assert result == {"stream": True}
        assert audio.call_args.kwargs["migrate_ownership"] is False

    with (
        patch("helper.playlist_hub.playlist_artwork_track", return_value={"id": "11", "thumb": "/thumb"}) as artwork,
        patch("helper.playlist_hub.artwork_etag", return_value=""),
        patch("helper.playlist_hub._stream_artwork", return_value=Response(content=b"image")),
    ):
        result = routes["/api/playlists/{kind}/{key}/tracks/{track_id}/artwork"](
            "daily", "daily", "11", request("/artwork"), "p1",
        )
        assert result.body == b"image"
        assert artwork.call_args.kwargs["migrate_ownership"] is False


def test_idle_playlist_detail_keeps_exclusive_gate_and_lazy_migration():
    engine = IdleEngine()
    routes = routes_for(engine)

    with patch("helper.playlist_hub.playlist_detail", return_value={"tracks": []}) as detail:
        assert routes["/api/playlists/{kind}/{key}"]("daily", "daily") == {"tracks": []}

    assert engine.exclusive_entries == 1
    assert detail.call_args.kwargs["migrate_ownership"] is True
