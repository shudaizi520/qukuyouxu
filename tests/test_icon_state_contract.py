from html.parser import HTMLParser
from pathlib import Path
import re


STATIC = Path(__file__).resolve().parents[1] / "src/helper/static"
COMPONENTS = STATIC / "ui-components.css"


class _InteractiveSvgParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.missing = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        interactive = tag in {"button", "a"} or any(item[1] for item in self.stack)
        if tag == "svg" and interactive:
            classes = set(values.get("class", "").split())
            if not ({"ui-icon", "ui-icon--fill"} & classes):
                self.missing.append(values.get("id") or values.get("viewbox") or "svg")
        self.stack.append((tag, interactive))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


def test_shared_controls_and_icons_have_one_state_owner():
    css = COMPONENTS.read_text(encoding="utf-8")
    for selector in (
        ".ui-icon", ":focus-visible", ".primary:hover:not(:disabled)",
        ".secondary:hover:not(:disabled)", ".danger:hover:not(:disabled)",
        ":disabled",
    ):
        assert selector in css
    assert "stroke:currentColor" in css
    assert "--icon-hover" in css
    assert "--icon-active" in css
    legacy = "\n".join(
        (STATIC / name).read_text(encoding="utf-8")
        for name in ("product.css", "design-system.css")
    )
    for pattern in (
        r"(?<![\w-])\.primary:hover:not\(:disabled\)\s*\{",
        r"(?<![\w-]):is\(\.secondary,\.danger,\.button\):hover:not\(:disabled\)\s*\{",
        r"(?<![\w-]):is\(button,a,input,select,textarea\):focus-visible\s*\{",
        r"(?<![\w-])button:focus-visible,a:focus-visible,summary:focus-visible\s*\{",
    ):
        assert re.search(pattern, legacy) is None

    non_component_css = "\n".join(
        path.read_text(encoding="utf-8")
        for path in STATIC.glob("*.css")
        if path.name not in {"ui-components.css", "home.css"}
    )
    for forbidden in (
        r"\.management-action:hover:not\(:disabled\)",
        r"\.management-action\.primary:hover:not\(:disabled\)",
        r"\.management-action\.danger",
        r"\.management-action:disabled",
        r"\.management-action\.danger-text",
        r"\.mix-schedule input:disabled\s*\{[^}]*opacity",
        r"\.mix-actions button:disabled\s*\{[^}]*opacity",
        r"input\.toggle:disabled\s*\{[^}]*opacity",
    ):
        assert re.search(forbidden, non_component_css) is None


def test_every_interactive_svg_uses_the_shared_icon_contract():
    for name in ("playlists.html", "mixes.html", "status.html"):
        parser = _InteractiveSvgParser()
        parser.feed((STATIC / name).read_text(encoding="utf-8"))
        assert parser.missing == [], f"{name}: {parser.missing}"

    appearance = (STATIC / "appearance.js").read_text(encoding="utf-8")
    assert appearance.count('<svg class="ui-icon"') == 3
