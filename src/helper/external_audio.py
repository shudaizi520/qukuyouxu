"""Authenticated, bounded proxy for auditioning matched local Plex audio."""
from __future__ import annotations

import hashlib
import re
import threading

import requests
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from .clients import PlexError, validate_audio_offset, validate_audio_range
from .external_store import ExternalRepository


MAX_AUDIO_BYTES = 1024 ** 3
CHUNK_SIZE = 64 * 1024
_active_lock = threading.Lock()
_active_streams: dict[str, int] = {}
MAX_ACTIVE_STREAMS = 3


class _AudioStreamingResponse(StreamingResponse):
    """Release the upstream even when ASGI streaming is cancelled mid-body."""
    def __init__(self, content, *, cleanup, **kwargs):
        self.source_iterator = content
        self._audio_cleanup = cleanup
        super().__init__(content, **kwargs)

    async def stream_response(self, send):
        try:
            await super().stream_response(send)
        finally:
            self._audio_cleanup()


def _stream_key(profile_id, session_key):
    session_key = str(session_key or '')
    if not session_key or len(session_key) > 4096:
        raise ValueError('试听登录会话无效')
    return hashlib.sha256(f'{profile_id}\0{session_key}'.encode()).hexdigest()


def _acquire(key):
    with _active_lock:
        active = _active_streams.get(key, 0)
        if active >= MAX_ACTIVE_STREAMS:
            raise ValueError('当前试听数量已达上限，请先停止一首再试')
        _active_streams[key] = active + 1


def _release(key):
    with _active_lock:
        active = _active_streams.get(key, 0)
        if active <= 1:
            _active_streams.pop(key, None)
        else:
            _active_streams[key] = active - 1


def _declared_size(headers):
    try:
        length = int(headers.get('Content-Length') or 0)
    except (TypeError, ValueError):
        raise ValueError('Plex音频长度无效') from None
    if length < 0 or length > MAX_AUDIO_BYTES:
        raise ValueError('Plex音频文件过大，拒绝试听')
    content_range = str(headers.get('Content-Range') or '')
    match = re.fullmatch(r'bytes \d+-\d+/(\d+|\*)', content_range)
    if content_range and not match:
        raise ValueError('Plex音频范围响应无效')
    if match and match.group(1) != '*' and int(match.group(1)) > MAX_AUDIO_BYTES:
        raise ValueError('Plex音频文件过大，拒绝试听')
    return length


def _matched_track(repository, profile_id, source_id, track_key, candidate_id=''):
    try:
        repository.get_source(profile_id, source_id)
        source_tracks = repository.list_tracks(profile_id, source_id)
        matches = repository.list_matches(profile_id, source_id)
    except ValueError:
        raise ValueError('当前歌单中没有这首可试听歌曲') from None
    if not any(row.get('source_track_key') == track_key for row in source_tracks):
        raise ValueError('当前歌单中没有这首可试听歌曲')
    row = next((item for item in matches if item.get('source_track_key') == track_key), None)
    if not row:
        raise ValueError('这首歌还没有可靠匹配，不能试听')
    candidate_id = str(candidate_id or '')
    if row.get('status') == 'matched' and not candidate_id:
        return str(row.get('plex_track_id') or '')
    if row.get('status') == 'review' and candidate_id:
        if candidate_id not in [str(value) for value in (row.get('candidate_ids') or [])]:
            raise ValueError('所选试听候选不属于当前歌曲')
        return candidate_id
    raise ValueError('这首歌还没有可靠匹配，不能试听')


def stream_track_audio(store, plex_factory, track_id, range_header, session_key, offset_seconds=0, *, allow_uncached=False):
    """Stream one catalog track after the caller has established its playlist scope."""
    profile_id = str(getattr(store, 'profile_id', 'default') or 'default')
    track_id = str(track_id or '')
    if not track_id.isdigit():
        raise ValueError('当前歌单中没有这首可试听歌曲')
    range_header = validate_audio_range(range_header)
    offset_seconds = validate_audio_offset(offset_seconds)
    if not allow_uncached:
        catalog = store.catalog_tracks([track_id])
        if track_id not in catalog or not catalog[track_id].get('available', True):
            raise ValueError('这首歌已不在当前曲库中，请先检查新增歌曲')
    settings = store.get('settings', {}) or {}
    key = _stream_key(profile_id, session_key)
    _acquire(key)
    upstream = None
    released = False

    def cleanup():
        nonlocal released
        if released:
            return
        released = True
        try:
            if upstream is not None:
                upstream.close()
        finally:
            _release(key)

    async def background_cleanup():
        cleanup()

    try:
        plex = plex_factory(settings)
        if allow_uncached:
            section=str(settings.get('section') or '')
            if not section.isdigit() or plex.track_section(track_id)!=section:
                raise ValueError('这首歌不属于当前曲库')
        try:
            upstream = plex.open_browser_audio(track_id, range_header, offset_seconds)
        except requests.RequestException:
            raise PlexError('Plex音频连接失败，请稍后重试') from None
        if upstream.status_code not in (200, 206):
            raise PlexError(f'Plex音频返回HTTP {upstream.status_code}')
        _declared_size(upstream.headers)
        allowed = ('Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges')
        headers = {name: upstream.headers[name] for name in allowed if upstream.headers.get(name) is not None}

        def chunks():
            total = 0
            try:
                for chunk in upstream.iter_content(CHUNK_SIZE):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_AUDIO_BYTES:
                        raise PlexError('Plex音频流超过大小上限')
                    yield chunk
            finally:
                cleanup()

        return _AudioStreamingResponse(
            chunks(), status_code=upstream.status_code, headers=headers,
            cleanup=cleanup,
            background=BackgroundTask(background_cleanup),
        )
    except Exception:
        cleanup()
        raise


def stream_local_audio(store, plex_factory, source_id, track_key, range_header, session_key, *, candidate_id='', offset_seconds=0):
    profile_id = str(getattr(store, 'profile_id', 'default') or 'default')
    track_key = str(track_key or '')
    if not track_key or len(track_key) > 300:
        raise ValueError('当前歌单中没有这首可试听歌曲')
    repository = ExternalRepository(store)
    track_id = _matched_track(repository, profile_id, str(source_id), track_key, candidate_id)
    return stream_track_audio(
        store, plex_factory, track_id, range_header, session_key, offset_seconds,
    )
