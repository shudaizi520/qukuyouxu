from html.parser import HTMLParser
from pathlib import Path
import re


STATIC = Path(__file__).resolve().parents[1] / "src/helper/static"


class _SettingsMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = {}
        self.links = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids[values["id"]] = (tag, values)
        if tag == "a" and "settings-tab" in values.get("class", "").split():
            self.links.append(values.get("href"))


def _rule(css, selector):
    cleaned = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    result = {}
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", cleaned):
        if selector not in [value.strip() for value in selectors.split(",")]:
            continue
        result.update(part.strip().split(":", 1) for part in body.split(";") if ":" in part)
    return result


def test_settings_uses_only_the_shared_top_navigation_without_a_second_heading_or_tab_row():
    markup = _SettingsMarkup()
    markup.feed((STATIC / "settings.html").read_text(encoding="utf-8"))

    assert "settingsHeading" not in markup.ids
    assert "settingsTabs" not in markup.ids
    assert markup.links == []
    script = (STATIC / "settings.js").read_text(encoding="utf-8")
    assert "new URLSearchParams(location.search).get('panel')" in script


def test_settings_has_four_clear_modules_and_no_separator_lattice():
    html = (STATIC / "settings.html").read_text(encoding="utf-8")
    css = (STATIC / "management-shell.css").read_text(encoding="utf-8")

    classes = re.findall(r'class="([^"]+)"', html)
    assert sum("settings-card" in value.split() for value in classes) == 4
    assert 'class="automation-grid"' in html
    assert html.index('id="currentUser"') < html.index('id="people"') < html.index('id="settings-automation"') < html.index('id="settings-system"')

    workspace = _rule(css, "body[data-management-page=settings] .settings-workspace")
    card = _rule(css, "body[data-management-page=settings] .settings-card")
    separator = _rule(css, "body[data-management-page=settings] .settings-section+.settings-section::before")
    panel_separator = _rule(css, "body[data-management-page=settings] .settings-panel+.settings-panel::before")

    assert workspace["max-width"] == "760px"
    assert workspace["margin"] == "0"
    assert card["background"] == "transparent"
    assert card["border"] == "0"
    assert card["border-radius"] == "0"
    assert separator["content"] == "none"
    assert panel_separator["content"] == "none"


def test_automation_is_a_compact_aligned_list_and_logout_finishes_on_the_left():
    html = (STATIC / "settings.html").read_text(encoding="utf-8")
    css = (STATIC / "management-shell.css").read_text(encoding="utf-8")
    grid = _rule(css, "body[data-management-page=settings] .automation-grid")
    row = _rule(css, "body[data-management-page=settings] .automation-row")
    logout = _rule(css, "body[data-management-page=settings] .settings-account-actions")

    assert grid["grid-template-columns"] == "1fr"
    assert grid["max-width"] == "520px"
    assert row["display"] == "grid"
    assert row["grid-template-columns"] == "144px minmax(0,1fr)"
    assert row["border"] == "0"
    assert row["border-radius"] == "0"
    assert logout["justify-content"] == "flex-start"
    assert logout["margin-top"] == "18px"
    assert html.rindex("settings-account-actions") > html.index('id="settings-system"')


def test_settings_removes_static_explanations_but_keeps_operational_state():
    html = (STATIC / "settings.html").read_text(encoding="utf-8")
    script = (STATIC / "settings.js").read_text(encoding="utf-8")
    css = (STATIC / "management-shell.css").read_text(encoding="utf-8")

    for text in (
        "推荐、智能歌单和新增歌曲按这里的时间自动更新。",
        "管理登录密码和当前应用版本。",
    ):
        assert text not in html
    assert 'id="tokenHint"' not in html
    assert 'id="sectionHint"' not in html
    assert "$('tokenHint')" not in script
    assert "$('sectionHint')" not in script
    assert "settings-card-heading p" not in css
    assert 'id="plexState"' in html
    assert 'id="webhookMessage"' in html
    assert 'id="webhookLast"' in html


def test_settings_typography_uses_windows_chinese_ui_fonts_and_consistent_weights():
    css = (STATIC / "product.css").read_text(encoding="utf-8")

    body = _rule(css, "body[data-view=settings]")
    title = _rule(css, "body[data-view=settings] .settings-section-title h3")

    assert '"Segoe UI Variable Text"' in body["font-family"]
    assert '"Microsoft YaHei UI"' in body["font-family"]
    assert body["font-synthesis"] == "none"
    assert title["font-weight"] == "600"
