"""Render live release data into HTML without exposing stale build markers."""
import hashlib
import hmac
import re
from pathlib import Path


FAVICON = (
    '<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,'
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E"
    "%3Crect width='64' height='64' rx='15' fill='%230f817e'/%3E"
    "%3Ctext x='32' y='45' text-anchor='middle' font-family='Arial,sans-serif' "
    "font-size='40' font-weight='700' fill='white'%3E%E2%99%AB%3C/text%3E"
    "%3C/svg%3E\">"
)


def _with_favicon(source):
    for link in re.findall(r"<link\b[^>]*>", source, re.I):
        match = re.search(
            r"\brel\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))", link, re.I
        )
        if match and "icon" in next(value for value in match.groups() if value is not None).lower().split():
            return source
    head_end = source.lower().find("</head>")
    if head_end < 0:
        return source
    return source[:head_end] + FAVICON + source[head_end:]


def static_asset_version(static_root, asset_name, release_version):
    """Return a release-scoped fingerprint for one public static asset."""
    name = str(asset_name or "")
    if not name or Path(name).name != name or "/" in name or "\\" in name:
        raise ValueError("静态资源名称无效")
    digest = hashlib.sha256((Path(static_root) / name).read_bytes()).hexdigest()[:12]
    return f"{release_version}-{digest}"


def is_current_static_asset_version(static_root, request_path, requested_version,
                                    release_version):
    prefix = "/static/"
    path = str(request_path or "")
    if not path.startswith(prefix):
        return False
    name = path[len(prefix):]
    try:
        current = static_asset_version(static_root, name, release_version)
    except (OSError, ValueError):
        return False
    return hmac.compare_digest(str(requested_version or ""), current)


def render_versioned_html(source, version, defer_version=False, static_root=None):
    marker = '<small id="version">'
    value = "" if defer_version else "v" + str(version)
    visible_slots = source.count(marker)
    if visible_slots > 1:
        raise RuntimeError("HTML page must not contain multiple visible version slots")
    rendered = source
    if visible_slots:
        start = rendered.index(marker) + len(marker)
        end = rendered.find("</small>", start)
        if end < 0:
            raise RuntimeError("HTML version slot is not closed")
        rendered = rendered[:start] + value + rendered[end:]
    meta = re.compile(r'(<meta\s+name="app-version"\s+content=")[^"]*(")', re.I)
    if meta.search(rendered):
        rendered = meta.sub(lambda match: match.group(1) + str(version) + match.group(2), rendered, count=1)
    rendered = _with_favicon(rendered)
    asset = re.compile(r'((?:href|src)="/static/([^"?]+))\?v=[^"]*')

    def version_asset(match):
        token = str(version)
        if static_root is not None:
            token = static_asset_version(static_root, match.group(2), version)
        return match.group(1) + "?v=" + token

    return asset.sub(version_asset, rendered)


def render_library_html(source, version, static_root=None):
    """Use the server release as the library page's only version writer."""
    return render_versioned_html(source, version, static_root=static_root)
