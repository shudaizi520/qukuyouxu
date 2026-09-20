"""Read-only adapter for public QQ Music playlists and charts."""
from __future__ import annotations

import hashlib
import html
import json
import math
import re

from .clients import SourceError, parse_qq_tracks
from .external_sources import ExternalSourceError, make_track


def _revision(tracks):
    payload = [
        (row["source_track_id"], row["title"], row["artists"], row["album"], row["duration_ms"])
        for row in tracks
    ]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _retryable_qq_message(message):
    lowered = message.lower()
    return any(token in lowered for token in ("连接失败", "http 429", "http 5", "timeout", "限流"))


def _artists(value):
    if isinstance(value, str):
        values = re.split(r"\s*(?:、|/|;|；)\s*", value)
    elif isinstance(value, list):
        values = [item.get("name", "") if isinstance(item, dict) else item for item in value]
    else:
        values = []
    return [html.unescape(str(item)).strip() for item in values if str(item).strip()]


class QQPublicPlaylistSource:
    def __init__(self, qq_client):
        self.qq = qq_client

    def _toplist(self, top_id):
        if hasattr(self.qq, "external_toplist"):
            data = self.qq.external_toplist(top_id)
        else:
            if not hasattr(self.qq, "_rpc"):
                raise ExternalSourceError("当前 QQ 连接不支持公开榜单")
            data = self.qq._rpc(
                "musicToplist.ToplistInfoServer", "GetDetail",
                {"topId": int(top_id), "offset": 0, "num": 10000, "period": ""},
                key="req_1",
            )
        if not isinstance(data, dict):
            raise ExternalSourceError("QQ 榜单返回结构不完整", kind="protocol")
        nested = data.get("data") if isinstance(data.get("data"), dict) else data
        tracks = nested.get("songInfoList") or nested.get("songlist") or nested.get("song") or []
        total = nested.get("totalNum", nested.get("total", len(tracks)))
        try:
            parsed = parse_qq_tracks(tracks, int(total))
        except SourceError as exc:
            raise ExternalSourceError(str(exc), retryable=False, kind="upstream") from None
        return {
            "title": nested.get("title") or (nested.get("topInfo") or {}).get("title") or "QQ榜单",
            "url": f"https://y.qq.com/n/ryqq/toplist/{top_id}",
            "tracks": parsed,
            "total": int(total),
        }

    def fetch(self, recognized: dict) -> dict:
        if not isinstance(recognized, dict) or recognized.get("provider") != "qq":
            raise ExternalSourceError("QQ 歌单来源无效")
        external_id = str(recognized.get("external_id") or "")
        url = str(recognized.get("url") or "")
        if not external_id.isdigit() or url not in {
            f"https://y.qq.com/n/ryqq/playlist/{external_id}",
            f"https://y.qq.com/n/ryqq/toplist/{external_id}",
        }:
            raise ExternalSourceError("QQ 歌单来源无效")
        try:
            result = self._toplist(external_id) if "/toplist/" in url else self.qq.playlist(external_id)
        except ExternalSourceError:
            raise
        except SourceError as exc:
            message = str(exc)
            raise ExternalSourceError(
                message, retryable=_retryable_qq_message(message), kind="upstream"
            ) from None
        except (TypeError, ValueError, KeyError):
            raise ExternalSourceError("QQ 歌单返回结构不完整", kind="protocol") from None
        if not isinstance(result, dict):
            raise ExternalSourceError("QQ 歌单返回结构不完整", kind="protocol")
        raw_tracks = result.get("tracks")
        if not isinstance(raw_tracks, list) or not raw_tracks or len(raw_tracks) > 10_000:
            raise ExternalSourceError("QQ 歌单为空或数量异常", kind="upstream")
        claimed = result.get("total")
        if claimed is not None and (isinstance(claimed, bool) or int(claimed) != len(raw_tracks)):
            raise ExternalSourceError("QQ 歌单分页不完整", retryable=True, kind="upstream")
        seen = set()
        tracks = []
        for position, raw in enumerate(raw_tracks):
            if not isinstance(raw, dict):
                raise ExternalSourceError("QQ 歌单曲目结构不完整", kind="protocol")
            source_id = str(raw.get("id") or raw.get("mid") or raw.get("songmid") or "").strip()
            if not source_id or source_id in seen:
                raise ExternalSourceError("QQ 歌单包含重复或无标识歌曲", kind="upstream")
            seen.add(source_id)
            duration = raw.get("duration", raw.get("interval", 0))
            try:
                duration = float(duration)
            except (TypeError, ValueError, OverflowError):
                raise ExternalSourceError("QQ 歌曲时长无效", kind="protocol") from None
            if not math.isfinite(duration) or not 0 <= duration <= 86_400:
                raise ExternalSourceError("QQ 歌曲时长无效", kind="protocol")
            album = raw.get("album") or ""
            if isinstance(album, dict):
                album = album.get("name", "")
            tracks.append(make_track(
                position, html.unescape(str(raw.get("title") or raw.get("songname") or "")),
                _artists(raw.get("artist", raw.get("singer"))), html.unescape(str(album)),
                duration_ms=int(duration * 1000), source_id=source_id,
                source_url=f"https://y.qq.com/n/ryqq/songDetail/{source_id}",
            ))
        return {
            "provider": "qq",
            "external_id": external_id,
            "url": url,
            "title": html.unescape(str(result.get("title") or "QQ歌单")).strip(),
            "revision": _revision(tracks),
            "tracks": tracks,
        }
