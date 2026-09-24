from pathlib import Path
import re


STATIC = Path(__file__).resolve().parents[1] / "src/helper/static"


def _text(filename: str) -> str:
    return (STATIC / filename).read_text(encoding="utf-8")


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


def test_management_actions_are_compact_neutral_and_hover_only():
    action = _rule(
        _text("management-shell.css"),
        "body[data-management-page] .management-action",
    )
    primary = _rule(
        _text("ui-components.css"),
        "body[data-management-page] .management-action.primary",
    )
    hover = _rule(
        _text("ui-components.css"),
        "body[data-management-page] .management-action:hover:not(:disabled)",
    )

    assert action["height"] == "30px"
    assert action["min-height"] == "30px"
    assert action["min-width"] == "0"
    assert action["border-radius"] == "4px"
    assert primary["background"] == "transparent"
    assert hover["background"] == "var(--app-hover)"


def test_management_fields_are_compact_and_stage_stays_left_aligned():
    css = _text("management-shell.css")
    field = _rule(
        css,
        "body[data-management-page] input:not([type=checkbox]):not([type=radio]):not([type=range])",
    )
    stage = _rule(css, ".management-stage")

    assert field["min-height"] == "32px"
    assert field["border"] == "1px solid var(--management-line)"
    assert field["border-radius"] == "5px"
    assert field["background"] == "transparent"
    assert stage["width"] == "100%"
    assert stage["margin"] == "0 auto 0 0"
    assert stage["padding"] == "0 30px 48px"


def test_system_settings_use_a_capped_aligned_user_table():
    html = _text("settings.html")
    css = _text("management-shell.css")
    people = html.split('<section id="people"', 1)[1].split("</section>", 1)[0]
    user_section = _rule(
        css, "body[data-management-page=settings] .settings-user-section"
    )
    header = _rule(
        css, "body[data-management-page=settings] .settings-user-header"
    )
    row = _rule(
        css,
        "body[data-management-page=settings] #managedUserList .settings-user-row",
    )
    hover = _rule(
        css,
        "body[data-management-page=settings] #managedUserList .settings-user-row:hover",
    )

    assert "settings-user-section" in people.split(">", 1)[0]
    assert 'class="settings-layout-label"><h3>用户管理</h3>' in people
    assert people.index('id="openAddUser"') < people.index('class="settings-user-header"')
    assert user_section["max-width"] == "820px"
    assert header["grid-template-columns"] == "minmax(190px,1fr) repeat(3,76px) 58px"
    assert row["grid-template-columns"] == "minmax(190px,1fr) repeat(3,76px) 58px"
    assert row["background"] == "transparent"
    assert hover["background"] == "var(--management-hover)"


def test_system_settings_automation_uses_fixed_compact_columns():
    css = _text("management-shell.css")
    row = _rule(css, "body[data-management-page=settings] .automation-row")
    controls = _rule(css, "body[data-management-page=settings] .automation-controls")

    assert row["grid-template-columns"] == "160px minmax(0,1fr)"
    assert row["background"] == "transparent"
    assert controls["grid-template-columns"] == "76px 88px 20px"
    assert controls["justify-content"] == "start"


def test_management_checkboxes_are_small_qq_style_boxes():
    css = _text("management-shell.css")
    control = _rule(css, "body[data-management-page] input[type=checkbox]")
    checked = _rule(css, "body[data-management-page] input[type=checkbox]:checked")
    mark = _rule(css, "body[data-management-page] input[type=checkbox]:checked::after")

    assert control["appearance"] == "none"
    assert control["width"] == "16px"
    assert control["height"] == "16px"
    assert control["border"] == "1px solid var(--management-muted)"
    assert control["border-radius"] == "1px"
    assert control["background"] == "transparent"
    assert checked["border-color"] == "var(--management-accent)"
    assert checked["background"] == "transparent"
    assert mark["background"] == "var(--management-accent)"


def test_import_workspace_is_capped_and_missing_download_stays_with_count():
    html = _text("external.html")
    css = _text("management-shell.css")
    import_form = _rule(
        css, "body[data-management-page=external] .external-import-form"
    )
    source_row = _rule(
        css, "body[data-management-page=external] .external-command-bar"
    )
    results = _rule(
        css, "body[data-management-page=external] .external-detail-card"
    )
    missing_download = _rule(
        css, "body[data-management-page=external] .external-missing-download"
    )

    counts_start = html.index('class="external-counts"')
    results_start = html.index('id="reviewToolbar"')
    replenishment = html.index('id="replenishmentCard"')
    download_menu = html.index('id="downloadMenu"')

    assert import_form["max-width"] == "680px"
    assert source_row["max-width"] == "680px"
    assert results["max-width"] == "940px"
    assert counts_start < replenishment < download_menu < results_start
    assert missing_download["background"] == "transparent"


def test_smart_playlist_workspace_is_compact_and_rows_fill_only_on_hover():
    css = _text("management-shell.css")
    settings = _rule(
        css, "body[data-management-page=mixes] .contextual-settings-card"
    )
    listing = _rule(css, "body[data-management-page=mixes] .mix-list")
    row = _rule(css, "body[data-management-page=mixes] .mix-row")
    hover = _rule(css, "body[data-management-page=mixes] .mix-row:hover")
    icon = _rule(css, "body[data-management-page=mixes] .mix-icon")

    assert settings["max-width"] == "820px"
    assert settings["border"] == "0"
    assert listing["max-width"] == "820px"
    assert listing["border"] == "0"
    assert row["border"] == "0"
    assert row["background"] == "transparent"
    assert hover["background"] == "var(--management-hover)"
    assert icon["color"] == "var(--management-text)"


def test_library_and_status_summaries_are_capped_unboxed_groups():
    css = _text("management-shell.css")
    library = _rule(css, "body[data-management-page=library] .library-overview")
    health = _rule(css, "body[data-management-page=status] .health-grid")
    status = _rule(css, "body[data-management-page=status] .status-primary")
    disclosure = _rule(css, "body[data-management-page=status] .page-disclosure")

    assert library["max-width"] == "760px"
    assert library["border"] == "0"
    assert health["max-width"] == "620px"
    assert health["border"] == "0"
    assert health["gap"] == "18px"
    assert status["grid-template-columns"] == "1fr"
    assert status["max-width"] == "620px"
    assert disclosure["max-width"] == "620px"


def test_appearance_tiles_use_a_thin_selected_outline_without_a_green_badge():
    css = _text("management-shell.css")
    card = _rule(
        css, "body[data-management-page=appearance] .appearance-theme-card"
    )
    selected = _rule(
        css,
        "body[data-management-page=appearance] .appearance-theme-card.selected",
    )

    assert card["border"] == "1px solid transparent"
    assert card["background"] == "transparent"
    assert selected["border-color"] == "var(--management-accent)"
    assert selected["background"] == "transparent"


def test_embedded_settings_and_empty_library_keep_the_same_readable_width():
    css = _text("management-shell.css")
    embedded = _rule(
        css,
        ".pch-embedded body[data-management-page=settings] .settings-workspace",
    )
    setup = _rule(css, "body[data-management-page=library] .setup-card")
    assert embedded["max-width"] == "820px"
    assert embedded["margin"] == "0"
    assert setup["max-width"] == "760px"
    assert setup["border"] == "0"
    assert setup["background"] == "transparent"
    assert setup["text-align"] == "left"


def test_lists_use_faint_row_separators_without_status_boxes():
    css = _text("management-shell.css")
    mix_sibling = _rule(
        css, "body[data-management-page=mixes] .mix-row+.mix-row"
    )
    stat = _rule(css, "body[data-management-page=status] .stat")
    status_row = _rule(
        css, "body[data-management-page=status] .status-list>div"
    )
    mix_head = _rule(css, "body[data-management-page=mixes] .mix-row-head")
    status_meta = _rule(css, "body[data-management-page=status] .status-meta")
    status_card = _rule(
        css, "body[data-management-page=status] .status-primary>.card"
    )
    assert mix_sibling["border-top"] == "1px solid var(--management-line)"
    assert stat["border"] == "0"
    assert stat["background"] == "transparent"
    assert status_row["border-top"] == "1px solid var(--management-line)"
    assert mix_head["min-height"] == "56px"
    assert mix_head["padding"] == "8px"
    assert status_meta["border-top"] == "1px solid var(--management-line)"
    assert status_card["box-shadow"] == "none"


def test_status_values_stay_near_their_labels_and_chips_have_no_fill():
    css = _text("management-shell.css")
    status_row = _rule(css, "body[data-management-page=status] .status-list>div")
    status_meta = _rule(css, "body[data-management-page=status] .status-meta")
    chip = _rule(css, "body[data-management-page=status] .status-chip")
    connection = _rule(css, "body[data-management-page=settings] .connection-state")
    online = _rule(
        css,
        "body[data-management-page=settings] #plexState[data-state=online]",
    )

    assert status_row["grid-template-columns"] == "140px minmax(0,1fr)"
    assert status_meta["grid-template-columns"] == "140px minmax(0,1fr)"
    assert chip["background"] == "transparent"
    assert connection["background"] == "transparent"
    assert connection["background-image"] == "none"
    assert connection["box-shadow"] == "none"
    assert connection["border-radius"] == "0"
    assert online["background"] == "transparent"
    assert online["background-color"] == "transparent"


def test_settings_use_real_left_label_and_right_content_rows():
    html = _text("settings.html")
    css = _text("management-shell.css")
    layout = _rule(css, "body[data-management-page=settings] .settings-layout-row")
    divider = _rule(
        css,
        "body[data-management-page=settings] .settings-layout-row+.settings-layout-row",
    )
    label = _rule(css, "body[data-management-page=settings] .settings-layout-label h3")

    assert html.count('class="settings-layout-row') >= 7
    assert html.count('class="settings-layout-label"') >= 7
    assert html.count('class="settings-layout-content') >= 7
    assert 'class="settings-layout-label"><h3>Plex 连接</h3>' in html
    assert 'class="settings-layout-label"><h3>Plex 播放事件</h3>' in html
    assert 'class="settings-layout-label"><h3>登录密码</h3>' in html
    assert layout["grid-template-columns"] == "120px minmax(0,1fr)"
    assert layout["gap"] == "24px"
    assert divider["border-top"] == "1px solid var(--management-line)"
    assert label["font-weight"] == "400"


def test_settings_sections_keep_an_indented_faint_separator():
    css = _text("management-shell.css")
    divider = _rule(
        css,
        "body[data-management-page=settings] .settings-section+.settings-section::before",
    )

    assert divider["content"] == '""'
    assert divider["display"] == "block"
    assert divider["background"] == "var(--management-line)"
    assert "width:calc(100% - 144px);" in css
    assert "margin-left:144px;" in css


def test_settings_user_table_reflows_without_mobile_overflow():
    css = _text("management-shell.css")
    header = _rule(
        css,
        "body[data-management-page=settings] .management-stage .settings-user-header",
    )
    row = _rule(
        css,
        "body[data-management-page=settings] .management-stage #managedUserList .settings-user-row",
    )
    person = _rule(
        css,
        "body[data-management-page=settings] .management-stage #managedUserList .settings-person",
    )
    actions = _rule(
        css,
        "body[data-management-page=settings] .management-stage #managedUserList .profile-actions",
    )

    assert header["display"] == "none"
    assert row["grid-template-columns"] == "repeat(3,minmax(0,1fr))"
    assert person["grid-column"] == "1/-1"
    assert actions["grid-column"] == "1/-1"
    assert actions["justify-content"] == "flex-start"


def test_management_and_playlist_typography_avoid_heavy_list_weights():
    management = _text("management-shell.css")
    design = _text("design-system.css")
    active_nav = _rule(management, ".management-nav a[aria-current=page]")
    playlist = _rule(
        design,
        "body[data-view=playlists] .playlist-track-title",
    )
    sidebar = _rule(
        design,
        "body[data-view=playlists] .playlist-fixed-nav button",
    )
    management_policy = _rule(
        management,
        "body[data-management-page] strong",
    )
    dialog_label = _rule(management, "body[data-management-page] label")
    dialog_button = _rule(management, "body[data-management-page] button")
    dialog_summary = _rule(management, "body[data-management-page] summary")

    assert active_nav["font-weight"] == "500"
    assert playlist["font-weight"] == "400"
    assert sidebar["font-weight"] == "400"
    assert management_policy["font-weight"] == "400!important"
    assert dialog_label["font-weight"] == "400!important"
    assert dialog_button["font-weight"] == "400!important"
    assert dialog_summary["font-weight"] == "400!important"


def test_management_actions_do_not_mix_red_green_or_add_underlines():
    css = _text("management-shell.css")
    components = _text("ui-components.css")
    danger = _rule(components, "body[data-management-page] .management-action.danger")
    hover = _rule(components, "body[data-management-page] .management-action:hover:not(:disabled)")
    nav_mark = _rule(css, ".management-nav a[aria-current=page]::after")
    library_action = _rule(css, "body[data-management-page=library] .managed-playlist-action")
    library_hover = _rule(css, "body[data-management-page=library] .managed-playlist-action:hover:not(:disabled)")

    assert danger["color"] == "var(--app-text)"
    assert danger["background"] == "transparent"
    assert hover["background"] == "var(--app-hover)"
    assert hover["text-decoration"] == "none"
    assert nav_mark["content"] == "none"
    assert library_action["color"] == "var(--management-text)"
    assert library_hover["text-decoration"] == "none"
