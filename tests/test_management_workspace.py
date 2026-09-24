from html.parser import HTMLParser
from pathlib import Path
import re


STATIC = Path(__file__).resolve().parents[1] / "src/helper/static"
PAGES = {
    "external.html": "external",
    "mixes.html": "mixes",
    "home.html": "library",
    "status.html": "status",
    "settings.html": "settings",
    "appearance.html": "appearance",
}


class _ManagementPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.body = {}
        self.elements = {}
        self.scripts = []
        self.shells = []
        self.visible_text = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.elements[values["id"]] = (tag, values)
        if tag == "body":
            self.body = values
        if tag == "script" and values.get("src"):
            self.scripts.append(values["src"])
        if "data-management-shell" in values:
            self.shells.append((tag, values))

    def handle_data(self, data):
        if data.strip():
            self.visible_text.append(data.strip())


def _rule(css: str, selector: str) -> dict[str, str]:
    declarations: dict[str, str] = {}
    cleaned = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", cleaned):
        if selector not in [item.strip() for item in selectors.split(",")]:
            continue
        for part in body.split(";"):
            if ":" in part:
                key, value = part.strip().split(":", 1)
                declarations[key] = value
    return declarations


def test_management_pages_share_one_shell_controller_and_identify_their_route():
    for filename, page_id in PAGES.items():
        page = _ManagementPage()
        page.feed((STATIC / filename).read_text(encoding="utf-8"))

        assert page.body.get("data-management-page") == page_id, filename
        assert page.scripts.count("/static/management-shell.js?v=2.0.7") == 1, filename
        assert len(page.shells) == 1, filename


def test_management_navigation_is_one_shared_six_page_route_map_in_the_requested_order():
    script = (STATIC / "management-shell.js").read_text(encoding="utf-8")

    expected = [
        ("settings", "/settings", "系统设置"),
        ("external", "/external", "导入歌单"),
        ("mixes", "/mixes", "智能歌单"),
        ("library", "/library", "曲库整理"),
        ("status", "/status", "运行状态"),
        ("appearance", "/appearance", "外观"),
    ]
    positions = []
    for page_id, href, label in expected:
        needle = f"id:'{page_id}',href:'{href}',label:'{label}'"
        assert needle in script
        positions.append(script.index(needle))
    assert positions == sorted(positions)
    assert "aria-current" in script
    assert "panel')==='appearance'" not in script
    assert "link.target='_top'" in script


def test_management_shell_fills_only_the_existing_right_hand_workspace():
    css = (STATIC / "design-system.css").read_text(encoding="utf-8")
    shell = _rule(css, "body[data-management-page] [data-management-shell]")
    stage = _rule(css, ".management-stage")
    embedded_topbar = _rule(css, ".pch-embedded body[data-management-page] .topbar")

    assert shell.get("width") == "100%"
    assert shell.get("max-width") == "none"
    assert shell.get("margin") == "0"
    assert shell.get("padding") == "0"
    assert stage.get("width") == "min(100%,1280px)"
    assert stage.get("margin") == "0 auto 0 0"
    assert stage.get("padding") == "0 34px 48px"
    assert embedded_topbar.get("display") == "none"
    assert "body[data-view=playlists] .playlist-hub{grid-template-columns:var(--app-rail-width) minmax(0,1fr)" in css


def test_management_controller_wraps_navigation_and_page_content_in_one_stage():
    script = (STATIC / "management-shell.js").read_text(encoding="utf-8")

    assert "stage.className='management-stage'" in script
    assert "const content=[...shell.childNodes]" in script
    assert "stage.append(createNavigation(activePage),...content)" in script
    assert "shell.append(stage)" in script


def test_legacy_centered_page_shells_are_neutralized_for_embedded_management_pages():
    css = (STATIC / "design-system.css").read_text(encoding="utf-8")
    selector = ".pch-embedded body[data-management-page] [data-management-shell][data-management-shell]"
    reset = _rule(css, selector)

    assert reset.get("width") == "100%"
    assert reset.get("max-width") == "none"
    assert reset.get("margin") == "0"
    assert reset.get("padding") == "0"


def test_management_components_use_shared_theme_tokens_and_control_geometry():
    css = (STATIC / "design-system.css").read_text(encoding="utf-8")
    root = _rule(css, "body[data-management-page]")
    action = _rule(css, "body[data-management-page] .management-action")
    danger = _rule(css, "body[data-management-page] .management-action.danger")
    field = _rule(css, "body[data-management-page] input:not([type=checkbox]):not([type=radio]):not([type=range])")

    assert root.get("--management-canvas") == "var(--app-main)"
    assert root.get("--management-control") == "var(--app-control)"
    assert root.get("--management-accent") == "var(--app-accent)"
    assert root.get("--management-line") == "var(--app-line)"
    assert action.get("min-width") == "96px"
    assert action.get("height") == "40px"
    assert action.get("border-radius") == "9px"
    assert danger.get("color") == "var(--management-text)"
    assert danger.get("background") == "transparent"
    assert field.get("min-height") == "40px"
    assert field.get("border-radius") == "9px"


def test_management_nav_is_horizontal_and_uses_the_theme_accent_for_active_state():
    css = (STATIC / "design-system.css").read_text(encoding="utf-8")
    nav = _rule(css, ".management-nav")
    active = _rule(css, ".management-nav a[aria-current=page]::after")

    assert nav.get("display") == "flex"
    assert nav.get("min-height") == "64px"
    assert nav.get("overflow-x") == "auto"
    assert active.get("background") == "var(--management-accent)"


def test_management_navigation_replaces_all_duplicate_page_title_regions():
    titles = {
        "external.html": "导入歌单",
        "mixes.html": "智能歌单",
        "home.html": "曲库整理",
        "status.html": "运行状态",
        "appearance.html": "外观",
    }
    for filename, title in titles.items():
        source = (STATIC / filename).read_text(encoding="utf-8")
        assert 'class="management-page-copy"' not in source, filename
        assert "management-page-header" not in source, filename
        assert f"<h1>{title}</h1>" not in source, filename

    settings = (STATIC / "settings.html").read_text(encoding="utf-8")
    assert 'class="management-page-copy"' not in settings
    assert "management-page-header" not in settings


def test_relocated_page_actions_remain_inside_existing_content_modules():
    library = (STATIC / "home.html").read_text(encoding="utf-8")
    task = library.split('id="task"', 1)[1].split('id="upstreamError"', 1)[0]
    assert 'id="incrementalAction"' in task

    status = (STATIC / "status.html").read_text(encoding="utf-8")
    assistant = status.split('id="assistantStatusCard"', 1)[1].split("</article>", 1)[0]
    assert 'id="refresh"' in assistant

    appearance = (STATIC / "appearance.html").read_text(encoding="utf-8")
    assert "appearance-theme-heading" not in appearance
    assert "选择主题" not in appearance


def test_management_pages_remove_secondary_explanations_but_keep_live_feedback():
    pages = {
        filename: (STATIC / filename).read_text(encoding="utf-8")
        for filename in PAGES
    }

    forbidden = {
        "external.html": ("集中管理外部来源",),
        "mixes.html": (
            "规则、发布和自动更新集中在同一个工作区",
            "设置每日歌单数量与偏好",
            "最近真正听过的歌手与歌曲",
            "曾经听过、最近很久没播放",
            "刚进入曲库、还没充分探索",
            "按年代、类型、评分组合筛选",
            "只维护本助手创建的歌单",
        ),
        "home.html": (
            "统一查看分类覆盖",
            "无需再次授权 QQ",
            "核对无误后可采用文件名中的歌名和歌手",
            "因此没有被强行塞进错误歌单",
            "用手机 QQ 扫码并确认",
            "不代表逐曲查询过 QQ 标签",
            "手工歌单、“我喜欢”和音乐文件保持不变",
            'id="nextStep"',
        ),
        "status.html": (
            "连接、后台任务和最近记录按优先级汇总",
            "连接与推荐统计",
            "最近 20 条",
        ),
        "appearance.html": (
            "选择一套更适合你的界面氛围",
            "点击预览即可立即应用",
            "明亮 · 干净",
            "柔和 · 温暖",
            "沉浸 · 低亮度",
        ),
    }
    for filename, phrases in forbidden.items():
        for phrase in phrases:
            assert phrase not in pages[filename], (filename, phrase)

    assert 'id="notice"' in pages["external.html"]
    assert 'id="dailyContextMessage"' not in pages["mixes.html"]
    assert 'id="taskMessage"' in pages["home.html"]
    assert "$('nextStep')" not in (STATIC / "home.js").read_text(encoding="utf-8")
    assert 'id="events"' in pages["status.html"]
    assert 'id="plexState"' in pages["settings.html"]

    home_script = (STATIC / "home.js").read_text(encoding="utf-8")
    contextual_script = (STATIC / "contextual-settings.js").read_text(encoding="utf-8")
    for phrase in (
        "点击整理新增歌曲",
        "已经设置过的不需要重新填写",
        "扫一次码即可",
        "勾选需要的歌单，确认后才同步",
        "不处理的歌曲会保持原样",
        "请先处理“标签待核对”",
        "歌曲已保留在曲库，不会丢失",
        "删除自己 Plex 中的歌单后",
        "主账户还没有发布分类歌单",
    ):
        assert phrase not in home_script, phrase
    for phrase in (
        "确认后可发布到 Plex",
        "已发布的歌单可从左侧直接播放",
    ):
        assert phrase not in contextual_script, phrase
    assert "dailyContextMessage" not in contextual_script


def test_management_inputs_use_short_prompts_and_daily_schedule_has_no_caption():
    playlists = _ManagementPage()
    playlists.feed((STATIC / "playlists.html").read_text(encoding="utf-8"))
    external = _ManagementPage()
    external.feed((STATIC / "external.html").read_text(encoding="utf-8"))
    mixes = _ManagementPage()
    mixes.feed((STATIC / "mixes.html").read_text(encoding="utf-8"))

    assert playlists.elements["librarySearchInput"][1]["placeholder"] == "搜索音乐"
    assert external.elements["sourceUrl"][1]["placeholder"] == "粘贴链接"
    assert "每日推荐时间（北京时间）" not in " ".join(mixes.visible_text)
    assert mixes.elements["dailyAutomationHour"][1]["aria-label"] == "每日推荐时间"
    assert "dailyAutomationEnabled" in mixes.elements


def test_appearance_is_a_dedicated_page_and_not_embedded_in_system_settings():
    settings = (STATIC / "settings.html").read_text(encoding="utf-8")
    appearance = (STATIC / "appearance.html").read_text(encoding="utf-8")
    settings_script = (STATIC / "settings.js").read_text(encoding="utf-8")

    assert 'id="settings-appearance"' not in settings
    assert "data-appearance-choice" not in settings
    assert "appearance:'settings-appearance'" not in settings_script
    assert appearance.count("data-appearance-choice=") == 3
    assert 'class="appearance-theme-grid"' in appearance
    assert appearance.count('class="appearance-theme-card"') == 3


def test_server_exposes_the_dedicated_appearance_route():
    source = (STATIC.parent / "web.py").read_text(encoding="utf-8")

    assert "@app.get('/appearance')" in source
    assert "STATIC / 'appearance.html'" in source


def test_appearance_cards_are_compact_fixed_width_previews():
    css = (STATIC / "design-system.css").read_text(encoding="utf-8")
    grid = _rule(css, "body[data-management-page=appearance] .appearance-theme-grid")
    card = _rule(css, "body[data-management-page=appearance] .appearance-theme-card")
    preview = _rule(css, "body[data-management-page=appearance] .appearance-theme-preview")

    assert grid.get("grid-template-columns") == "repeat(3,190px)"
    assert grid.get("justify-content") == "start"
    assert card.get("width") == "190px"
    assert card.get("min-height") == "0"
    assert preview.get("height") == "92px"


def test_each_management_page_uses_shared_content_primitives_not_only_the_outer_shell():
    minimum_sections = {
        "external.html": 2,
        "mixes.html": 2,
        "home.html": 3,
        "status.html": 3,
        "settings.html": 3,
    }
    for filename, minimum in minimum_sections.items():
        source = (STATIC / filename).read_text(encoding="utf-8")
        assert source.count("management-section") >= minimum, filename

    css = (STATIC / "design-system.css").read_text(encoding="utf-8")
    section = _rule(css, "body[data-management-page] .management-section")
    row = _rule(css, "body[data-management-page] .management-row")
    summary = _rule(css, "body[data-management-page] .management-summary")

    assert section.get("border-bottom") == "1px solid var(--management-line)"
    assert section.get("background") == "transparent"
    assert row.get("min-height") == "68px"
    assert summary.get("border") == "1px solid var(--management-line)"


def test_page_specific_layouts_use_continuous_rows_instead_of_scattered_cards():
    css = (STATIC / "design-system.css").read_text(encoding="utf-8")
    external = _rule(css, "body[data-management-page=external] .external-import-card")
    mix = _rule(css, "body[data-management-page=mixes] .mix-row")
    library = _rule(css, "body[data-management-page=library] .library-overview")
    status = _rule(css, "body[data-management-page=status] .health-grid")
    settings = _rule(css, "body[data-management-page=settings] .settings-workspace")

    assert external.get("max-width") == "none"
    assert external.get("border-bottom") == "1px solid var(--management-line)"
    assert mix.get("border-bottom") == "1px solid var(--management-line)"
    assert mix.get("background") == "transparent"
    assert library.get("display") == "grid"
    assert library.get("border") == "1px solid var(--management-line)"
    assert status.get("gap") == "0"
    assert status.get("border") == "1px solid var(--management-line)"
    assert settings.get("max-width") == "1040px"
