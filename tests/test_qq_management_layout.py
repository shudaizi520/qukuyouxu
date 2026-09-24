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
    heading = people.split('<div class="settings-section-title"', 1)[1].split(
        "</div>", 1
    )[0]
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
    assert 'id="openAddUser"' in heading
    assert user_section["max-width"] == "760px"
    assert header["grid-template-columns"] == "minmax(220px,1fr) repeat(3,92px) 92px"
    assert row["grid-template-columns"] == "minmax(220px,1fr) repeat(3,92px) 92px"
    assert row["background"] == "transparent"
    assert hover["background"] == "var(--management-hover)"


def test_system_settings_automation_uses_fixed_compact_columns():
    css = _text("management-shell.css")
    row = _rule(css, "body[data-management-page=settings] .automation-row")
    controls = _rule(css, "body[data-management-page=settings] .automation-controls")

    assert row["grid-template-columns"] == "120px minmax(0,1fr)"
    assert row["background"] == "transparent"
    assert controls["grid-template-columns"] == "72px 72px 30px"
    assert controls["justify-content"] == "start"


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

    assert settings["max-width"] == "820px"
    assert settings["border"] == "0"
    assert listing["max-width"] == "820px"
    assert listing["border"] == "0"
    assert row["border"] == "0"
    assert row["background"] == "transparent"
    assert hover["background"] == "var(--management-hover)"
