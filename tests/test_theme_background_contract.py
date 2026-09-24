from pathlib import Path
import re


STATIC = Path(__file__).resolve().parents[1] / "src/helper/static"
BACKGROUND = STATIC / "theme-background.css"


def test_background_layer_never_handles_input_or_duplicates_when_embedded():
    css = BACKGROUND.read_text(encoding="utf-8")
    compact = css.replace(" ", "")
    assert "pointer-events:none" in compact
    assert ".pch-embedded .app-theme-background" in css
    assert "display:none" in compact
    assert "prefers-reduced-motion:reduce" in compact
    assert "body{isolation:isolate}" in compact
    assert "z-index:-1" in compact


def test_background_stylesheet_never_targets_controls_or_navigation():
    css = re.sub(r"/\*.*?\*/", "", BACKGROUND.read_text(encoding="utf-8"), flags=re.S)
    selectors = "\n".join(match.group(1) for match in re.finditer(r"([^{}]+)\{", css))
    for forbidden in ("button", "input", "select", ".management-action", ".ui-icon", ".management-nav a"):
        assert forbidden not in selectors


def test_every_themed_page_loads_the_background_contract():
    for page in STATIC.glob("*.html"):
        source = page.read_text(encoding="utf-8")
        if "theme-tokens.css" in source:
            assert source.count("theme-background.css?v=app") == 1, page.name
