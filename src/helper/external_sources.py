"""Validated external-playlist inputs and bounded public-source HTTP."""
from __future__ import annotations

import csv
import hashlib
import io
import ipaddress
import json
import re
import socket
from pathlib import PurePath
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

import requests


MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_TRACKS = 10_000
REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
SUPPORTED_HOSTS = {
    "y.qq.com": "qq",
    "c.y.qq.com": "qq",
    "music.163.com": "netease",
    "163cn.tv": "netease",
}
FORMULA_PREFIXES = ("=", "+", "-", "@")
VERSION_PATTERNS = (
    ("现场版", re.compile(r"(?:现场|live)", re.I)),
    ("伴奏", re.compile(r"(?:伴奏|instrumental)", re.I)),
    ("纯音乐", re.compile(r"(?:纯音乐|纯享|piano|钢琴)", re.I)),
    ("翻唱", re.compile(r"(?:翻唱|cover)", re.I)),
    ("重制版", re.compile(r"(?:重制|remaster)", re.I)),
    ("混音版", re.compile(r"(?:混音|remix)", re.I)),
)


class ExternalSourceError(ValueError):
    def __init__(self, message: str, *, retryable: bool = False, kind: str = "invalid"):
        super().__init__(message)
        self.retryable = bool(retryable)
        self.kind = str(kind)


def _clean(value, name, limit=300, *, required=True):
    value = str(value or "").strip()
    if required and not value:
        raise ExternalSourceError(f"{name}不能为空")
    if len(value) > limit or any(ord(char) < 32 and char not in "\t" for char in value):
        raise ExternalSourceError(f"{name}无效")
    return value


def _version_label(title, album=""):
    text = f"{title} {album}"
    return " / ".join(label for label, pattern in VERSION_PATTERNS if pattern.search(text))


def _duration_ms(value):
    if value in (None, ""):
        return 0
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ExternalSourceError("歌曲时长无效") from None
    if not 0 <= number <= 86_400:
        raise ExternalSourceError("歌曲时长无效")
    return int(number * 1000)


def make_track(position, title, artists, album="", duration_ms=0, source_id="", source_url=""):
    title = _clean(title, "歌名")
    if isinstance(artists, str):
        artists = re.split(r"\s*(?:/|、|;|；)\s*", artists)
    if not isinstance(artists, (list, tuple)) or not artists:
        raise ExternalSourceError("歌手不能为空")
    artists = [_clean(item, "歌手") for item in artists]
    album = _clean(album, "专辑", required=False)
    source_id = _clean(source_id, "平台歌曲标识", required=False)
    if not isinstance(duration_ms, int) or not 0 <= duration_ms <= 86_400_000:
        raise ExternalSourceError("歌曲时长无效")
    key_seed = "\0".join((str(position), source_id, title, "\0".join(artists), album))
    return {
        "source_track_key": "t-" + hashlib.sha256(key_seed.encode("utf-8")).hexdigest()[:24],
        "position": int(position),
        "source_track_id": source_id,
        "title": title,
        "artists": artists,
        "album": album,
        "duration_ms": duration_ms,
        "version_flags": [],
        "version_label": _version_label(title, album),
        "source_url": str(source_url or ""),
    }


def _safe_url(value, allowed_hosts):
    try:
        parsed = urlsplit(str(value or ""))
        port = parsed.port
    except (TypeError, ValueError):
        raise ExternalSourceError("来源地址无效") from None
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() != "https" or not host or host not in allowed_hosts:
        raise ExternalSourceError("只允许访问受支持的 HTTPS 公网来源")
    if parsed.username is not None or parsed.password is not None:
        raise ExternalSourceError("来源地址不能包含账号或密码")
    if port not in (None, 443):
        raise ExternalSourceError("来源地址端口无效")
    return parsed, host


def recognize_source(value: str) -> dict:
    value = _clean(value, "歌单链接", 2000)
    parsed, host = _safe_url(value, set(SUPPORTED_HOSTS))
    provider = SUPPORTED_HOSTS[host]
    path = parsed.path.rstrip("/")
    query = parse_qs(parsed.query)
    fragment = parsed.fragment
    if provider == "qq":
        match = re.fullmatch(r"/n/ryqq/(?:playlist|toplist)/(\d+)", path)
        if not match:
            candidate = (query.get("id") or query.get("disstid") or [""])[0]
            kind = "toplist" if "top" in path.lower() else "playlist"
            if not candidate.isdigit() or kind != "playlist":
                raise ExternalSourceError("这不是可识别的 QQ 音乐公开歌单链接")
            external_id = candidate
        else:
            external_id = match.group(1)
            kind = "toplist" if "/toplist/" in path else "playlist"
        canonical = f"https://y.qq.com/n/ryqq/{kind}/{external_id}"
    else:
        fragment_parsed = urlsplit(fragment if fragment.startswith("/") else "")
        fragment_query = parse_qs(fragment_parsed.query)
        candidate = (query.get("id") or fragment_query.get("id") or [""])[0]
        target_path = fragment_parsed.path or path
        if target_path not in ("/playlist", "/#/playlist") or not candidate.isdigit():
            raise ExternalSourceError("这不是可识别的网易云音乐公开歌单链接")
        external_id = candidate
        canonical = f"https://music.163.com/playlist?id={external_id}"
    return {"provider": provider, "external_id": external_id, "url": canonical}


def _decode_upload(content):
    if not isinstance(content, bytes):
        raise ExternalSourceError("上传内容无效")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ExternalSourceError("上传文件过大，最大 2 MiB")
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ExternalSourceError("文件必须使用 UTF-8 编码") from None


def _snapshot(filename, provider, tracks, content):
    if not tracks:
        raise ExternalSourceError("歌单中没有可导入的歌曲")
    if len(tracks) > MAX_TRACKS:
        raise ExternalSourceError("单个歌单最多导入 10000 首歌曲")
    digest = hashlib.sha256(content).hexdigest()
    return {
        "provider": provider,
        "external_id": f"upload:{digest}",
        "url": "",
        "title": _clean(PurePath(filename).stem, "歌单名称"),
        "revision": digest,
        "tracks": tracks,
    }


def _parse_m3u(text):
    tracks = []
    pending = None
    for line_number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line.upper().startswith("#EXTINF:"):
            try:
                prefix, display = line.split(",", 1)
                seconds = prefix.split(":", 1)[1]
                duration = 0 if seconds in ("", "-1") else _duration_ms(seconds)
                artist, title = display.split(" - ", 1)
            except (ValueError, IndexError):
                raise ExternalSourceError(f"第 {line_number} 行 EXTINF 格式无效") from None
            pending = (title, artist, duration, line_number)
        elif line.startswith("#"):
            continue
        elif pending:
            title, artist, duration, metadata_line = pending
            tracks.append(make_track(len(tracks), title, [artist], duration_ms=duration))
            pending = None
        else:
            raise ExternalSourceError(f"第 {line_number} 行缺少 EXTINF 歌曲信息")
    if pending:
        raise ExternalSourceError(f"第 {pending[3]} 行歌曲缺少后续路径行")
    return tracks


def _reject_formula(value, line_number):
    value = str(value or "").strip()
    if value.startswith(FORMULA_PREFIXES):
        raise ExternalSourceError(f"第 {line_number} 行包含不安全的表格公式")
    return value


def _parse_csv(text):
    try:
        rows = list(csv.DictReader(io.StringIO(text)))
    except csv.Error as exc:
        raise ExternalSourceError(f"CSV 格式无效：{exc}") from None
    if not rows:
        raise ExternalSourceError("CSV 中没有歌曲")
    fields = {str(name or "").strip(): name for name in (rows[0].keys() if rows else [])}
    title_key = next((fields[name] for name in ("歌名", "歌曲", "title", "Title") if name in fields), None)
    artist_key = next((fields[name] for name in ("歌手", "艺人", "artist", "Artist") if name in fields), None)
    if not title_key or not artist_key:
        raise ExternalSourceError("CSV 必须包含歌名和歌手列")
    album_key = next((fields[name] for name in ("专辑", "album", "Album") if name in fields), None)
    duration_key = next((fields[name] for name in ("时长（秒）", "时长(秒)", "duration_seconds") if name in fields), None)
    tracks = []
    for line_number, row in enumerate(rows, 2):
        clean = {key: _reject_formula(value, line_number) for key, value in row.items()}
        tracks.append(make_track(
            len(tracks), clean.get(title_key), clean.get(artist_key),
            clean.get(album_key, "") if album_key else "",
            _duration_ms(clean.get(duration_key)) if duration_key else 0,
        ))
        if len(tracks) > MAX_TRACKS:
            raise ExternalSourceError("单个歌单最多导入 10000 首歌曲")
    return tracks


def _parse_text(text):
    lines = [(number, line.strip()) for number, line in enumerate(text.splitlines(), 1) if line.strip()]
    if not lines:
        return []
    header = lines[0][1].replace(" ", "")
    if header == "歌名-歌手":
        title_first = True
    elif header == "歌手-歌名":
        title_first = False
    else:
        raise ExternalSourceError("文本歌单第一行必须是“歌名 - 歌手”或“歌手 - 歌名”")
    tracks = []
    for line_number, line in lines[1:]:
        try:
            first, second = line.split(" - ", 1)
        except ValueError:
            raise ExternalSourceError(f"第 {line_number} 行必须使用空格-空格分隔") from None
        title, artist = (first, second) if title_first else (second, first)
        tracks.append(make_track(len(tracks), title, artist))
        if len(tracks) > MAX_TRACKS:
            raise ExternalSourceError("单个歌单最多导入 10000 首歌曲")
    return tracks


def parse_uploaded_playlist(filename: str, content: bytes) -> dict:
    filename = _clean(PurePath(str(filename or "")).name, "文件名", 255)
    suffix = PurePath(filename).suffix.lower()
    text = _decode_upload(content)
    if suffix in (".m3u", ".m3u8"):
        provider, tracks = suffix[1:], _parse_m3u(text)
    elif suffix == ".csv":
        provider, tracks = "csv", _parse_csv(text)
    elif suffix == ".txt":
        provider, tracks = "txt", _parse_text(text)
    else:
        raise ExternalSourceError("只支持 M3U、M3U8、CSV 和 TXT 文件")
    return _snapshot(filename, provider, tracks, content)


class SafeSourceHttp:
    def __init__(self, session=None, resolver=socket.getaddrinfo):
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.resolver = resolver

    def _validate(self, url, allowed_hosts):
        parsed, host = _safe_url(url, {str(item).lower().rstrip(".") for item in allowed_hosts})
        try:
            addresses = self.resolver(host, 443, type=socket.SOCK_STREAM)
        except OSError:
            raise ExternalSourceError("来源域名暂时无法解析", retryable=True, kind="network") from None
        if not addresses:
            raise ExternalSourceError("来源域名没有可用地址", retryable=True, kind="network")
        for item in addresses:
            try:
                address = ipaddress.ip_address(item[4][0])
            except (ValueError, IndexError, TypeError):
                raise ExternalSourceError("来源域名解析结果无效", kind="security") from None
            if not address.is_global:
                raise ExternalSourceError("来源地址必须解析到公网", kind="security")
        clean = urlunsplit(("https", parsed.netloc, parsed.path or "/", parsed.query, ""))
        return clean

    def get_json(self, url: str, *, allowed_hosts: set[str], max_bytes: int = 4_194_304) -> dict:
        if not isinstance(max_bytes, int) or not 1 <= max_bytes <= 16 * 1024 * 1024:
            raise ValueError("响应大小限制无效")
        current = str(url)
        response = None
        for redirects in range(6):
            current = self._validate(current, allowed_hosts)
            try:
                response = self.session.get(
                    current, allow_redirects=False, stream=True, timeout=(5, 20),
                    headers={"Accept": "application/json", "User-Agent": "QukuYouxu/1"},
                )
            except requests.RequestException:
                raise ExternalSourceError("外部歌单来源暂时无法连接", retryable=True, kind="network") from None
            if response.status_code in REDIRECT_CODES:
                location = response.headers.get("Location", "")
                response.close()
                if redirects >= 5:
                    raise ExternalSourceError("外部来源重定向次数过多", kind="redirect")
                if not location:
                    raise ExternalSourceError("外部来源返回了无效重定向", kind="redirect")
                current = urljoin(current, location)
                continue
            break
        try:
            status = int(response.status_code)
            if status == 429 or status >= 500:
                raise ExternalSourceError("外部歌单来源暂时不可用", retryable=True, kind="upstream")
            if status >= 400:
                raise ExternalSourceError("外部歌单不可访问或不是公开歌单", kind="upstream")
            length = response.headers.get("Content-Length")
            if length:
                try:
                    declared_length = int(length)
                except ValueError:
                    raise ExternalSourceError("外部来源响应长度无效", kind="protocol") from None
                if declared_length > max_bytes:
                    raise ExternalSourceError("外部歌单响应过大", kind="size")
            body = bytearray()
            for chunk in response.iter_content(chunk_size=65_536):
                if not chunk:
                    continue
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise ExternalSourceError("外部歌单响应过大", kind="size")
            try:
                result = json.loads(bytes(body).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ExternalSourceError("外部来源没有返回有效 JSON", kind="protocol") from None
            if not isinstance(result, dict):
                raise ExternalSourceError("外部来源 JSON 结构无效", kind="protocol")
            return result
        finally:
            if response is not None:
                response.close()
