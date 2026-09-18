"""Render live release data into HTML without exposing stale build markers."""
import re


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


def render_versioned_html(source, version, defer_version=False):
    marker = '<small id="version">'
    if source.count(marker) != 1:
        raise RuntimeError("HTML page must contain exactly one version slot")
    start = source.index(marker) + len(marker)
    end = source.find("</small>", start)
    if end < 0:
        raise RuntimeError("HTML version slot is not closed")
    value = "" if defer_version else "v" + str(version)
    rendered = _with_favicon(source[:start] + value + source[end:])
    asset = re.compile(r'((?:href|src)="/static/[^"?]+)\?v=[^"]*')
    return asset.sub(lambda match: match.group(1) + "?v=" + str(version), rendered)


def render_library_html(source, version):
    """Use the server release as the library page's only version writer."""
    return render_versioned_html(source, version)
