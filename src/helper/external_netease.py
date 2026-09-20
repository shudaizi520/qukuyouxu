"""Read-only adapter for public NetEase Cloud Music playlists."""
from __future__ import annotations

import hashlib
import html
import json
from urllib.parse import quote

from .external_sources import ExternalSourceError, make_track


HOSTS = {"music.163.com"}
BATCH_SIZE = 500


def _revision(tracks):
    payload = [
        (row["source_track_id"], row["title"], row["artists"], row["album"], row["duration_ms"])
        for row in tracks
    ]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _integer(value, name, *, minimum=0, maximum=10_000):
    if isinstance(value, bool):
        raise ExternalSourceError(f"网易云{name}无效", kind="protocol")
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ExternalSourceError(f"网易云{name}无效", kind="protocol") from None
    if not minimum <= result <= maximum:
        raise ExternalSourceError(f"网易云{name}无效", kind="protocol")
    return result


class NetEasePublicPlaylistSource:
    def __init__(self, http):
        self.http = http

    def _get(self, url):
        return self.http.get_json(url, allowed_hosts=HOSTS)

    @staticmethod
    def _valid_payload(payload):
        if not isinstance(payload, dict) or payload.get("code") != 200:
            raise ExternalSourceError("网易云歌单不可访问、已删除或不是公开歌单", kind="upstream")

    def fetch(self, recognized: dict) -> dict:
        if not isinstance(recognized, dict) or recognized.get("provider") != "netease":
            raise ExternalSourceError("网易云歌单来源无效")
        external_id = str(recognized.get("external_id") or "")
        canonical = f"https://music.163.com/playlist?id={external_id}"
        if not external_id.isdigit() or recognized.get("url") != canonical:
            raise ExternalSourceError("网易云歌单来源无效")
        payload = self._get(
            f"https://music.163.com/api/v6/playlist/detail?id={external_id}&n=10000&s=0"
        )
        self._valid_payload(payload)
        playlist = payload.get("playlist")
        if not isinstance(playlist, dict):
            raise ExternalSourceError("网易云歌单返回结构不完整", kind="protocol")
        count = _integer(playlist.get("trackCount"), "歌单数量", minimum=1)
        track_ids = playlist.get("trackIds")
        raw_tracks = playlist.get("tracks")
        if not isinstance(track_ids, list) or not isinstance(raw_tracks, list) or len(track_ids) != count:
            raise ExternalSourceError("网易云歌单分页数量发生变化或不完整", retryable=True, kind="upstream")
        ordered_ids = []
        for row in track_ids:
            if not isinstance(row, dict):
                raise ExternalSourceError("网易云歌单歌曲标识不完整", kind="protocol")
            song_id = _integer(row.get("id"), "歌曲标识", minimum=1, maximum=10**18)
            ordered_ids.append(song_id)
        if len(set(ordered_ids)) != len(ordered_ids):
            raise ExternalSourceError("网易云歌单包含重复歌曲标识", kind="upstream")
        details = {}
        for row in raw_tracks:
            if not isinstance(row, dict):
                raise ExternalSourceError("网易云歌曲详情不完整", kind="protocol")
            song_id = _integer(row.get("id"), "歌曲标识", minimum=1, maximum=10**18)
            if song_id in details or song_id not in set(ordered_ids):
                raise ExternalSourceError("网易云歌曲详情重复或不属于歌单", kind="upstream")
            details[song_id] = row
        missing = [song_id for song_id in ordered_ids if song_id not in details]
        for offset in range(0, len(missing), BATCH_SIZE):
            batch = missing[offset:offset + BATCH_SIZE]
            encoded = quote(json.dumps(batch, separators=(",", ":")))
            extra = self._get(f"https://music.163.com/api/song/detail?ids={encoded}")
            self._valid_payload(extra)
            songs = extra.get("songs")
            if not isinstance(songs, list):
                raise ExternalSourceError("网易云歌曲详情不完整", retryable=True, kind="upstream")
            for row in songs:
                if not isinstance(row, dict):
                    raise ExternalSourceError("网易云歌曲详情不完整", kind="protocol")
                song_id = _integer(row.get("id"), "歌曲标识", minimum=1, maximum=10**18)
                if song_id in details or song_id not in batch:
                    raise ExternalSourceError("网易云歌曲详情重复或越界", kind="upstream")
                details[song_id] = row
        if len(details) != count or any(song_id not in details for song_id in ordered_ids):
            raise ExternalSourceError("网易云歌单歌曲详情不完整", retryable=True, kind="upstream")
        tracks = []
        for position, song_id in enumerate(ordered_ids):
            raw = details[song_id]
            artists = raw.get("ar") or raw.get("artists")
            if not isinstance(artists, list):
                raise ExternalSourceError("网易云歌曲歌手信息不完整", kind="protocol")
            artists = [html.unescape(str(row.get("name") or "")) for row in artists if isinstance(row, dict)]
            album = raw.get("al") or raw.get("album") or {}
            album = album.get("name", "") if isinstance(album, dict) else album
            duration = _integer(raw.get("dt", raw.get("duration", 0)), "歌曲时长", maximum=86_400_000)
            tracks.append(make_track(
                position, html.unescape(str(raw.get("name") or "")), artists,
                html.unescape(str(album or "")), duration_ms=duration, source_id=str(song_id),
                source_url=f"https://music.163.com/song?id={song_id}",
            ))
        return {
            "provider": "netease",
            "external_id": external_id,
            "url": canonical,
            "title": html.unescape(str(playlist.get("name") or "网易云歌单")).strip(),
            "revision": _revision(tracks),
            "tracks": tracks,
        }
