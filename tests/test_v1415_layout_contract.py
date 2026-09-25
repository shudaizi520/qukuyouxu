"""Keep the compact embedded settings and import controls aligned."""

from pathlib import Path
import re
from html.parser import HTMLParser

from ui_css import page_css


CSS = Path(__file__).resolve().parents[1] / "src/helper/static/product.css"


def rules(selector, page):
    css = re.sub(r"/\*.*?\*/", "", page_css(page), flags=re.S)
    found = {}
    depth = 0
    rule_start = 0
    body_start = None
    for index, char in enumerate(css):
        if char == "{":
            if depth == 0:
                names = css[rule_start:index].strip()
                body_start = index + 1
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and body_start is not None:
                body = css[body_start:index]
                if not names.startswith("@") and selector in [name.strip() for name in names.split(",")]:
                    found.update(
                        part.strip().split(":", 1)
                        for part in body.split(";") if ":" in part
                    )
                rule_start = index + 1
                body_start = None
    return found


def test_embedded_settings_use_one_centered_column():
    shell = rules(".pch-embedded body[data-view=settings] .settings-shell", "settings")
    assert shell["max-width"] == "1120px"
    assert shell["grid-template-columns"] == "minmax(0,1fr)"
    assert shell["margin"] == "0 auto"


def test_import_commands_share_the_results_outer_edge():
    commands = rules("body[data-view=external] .external-command-bar", "external")
    assert commands["max-width"] == "680px"
    assert commands["margin"] == "0"
    assert commands["width"] == "100%"
    assert rules("body[data-view=external] .external-import-card", "external")["max-width"] == "920px"
    assert rules("body[data-view=external] .external-workspace", "external")["border-radius"] == "0"


def test_download_choices_share_the_same_alignment_and_hit_area():
    for selector in (
        "body[data-view=external] .external-more>div>a",
        "body[data-view=external] .external-more>div>button",
    ):
        option = rules(selector, "external")
        assert option["width"] == "100%"
        assert option["justify-content"] == "flex-start"
        assert option["min-height"] == "36px"


def test_settings_without_tabs_keeps_automation_reachable_and_policy_in_smart_page():
    class Sections(HTMLParser):
        def __init__(self):
            super().__init__()
            self.by_id = {}

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if tag == "section" and "id" in attributes:
                self.by_id[attributes["id"]] = attributes

    sections = Sections()
    sections.feed((CSS.parent / "settings.html").read_text())
    assert "settings-recommend" not in sections.by_id
    assert "hidden" not in sections.by_id["settings-automation"]
    assert 'id="dailyPolicyForm"' in (CSS.parent / "mixes.html").read_text()
