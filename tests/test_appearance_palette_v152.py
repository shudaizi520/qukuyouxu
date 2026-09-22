"""Theme palettes meet readable contrast without changing typography."""
import re
from pathlib import Path


CSS = Path(__file__).resolve().parents[1] / "src/helper/static/product.css"


def _luminance(value):
    channels = [int(value[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [channel / 12.92 if channel <= .04045 else ((channel + .055) / 1.055) ** 2.4
              for channel in channels]
    return .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2]


def _contrast(first, second):
    light, dark = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (light + .05) / (dark + .05)


def _palette(css, name):
    block = css.split(f"html[data-appearance={name}]{{", 1)[1].split("}", 1)[0]
    return dict(re.findall(r"(--theme-[\w-]+):\s*(#[0-9a-fA-F]{6})", block))


def test_warm_and_night_palettes_keep_normal_and_muted_text_legible():
    css = CSS.read_text(encoding="utf-8")
    for name in ("warm", "night"):
        palette = _palette(css, name)
        assert _contrast(palette["--theme-text"], palette["--theme-canvas"]) >= 7
        assert _contrast(palette["--theme-text"], palette["--theme-surface"]) >= 7
        assert _contrast(palette["--theme-text"], palette["--theme-soft"]) >= 7
        assert _contrast(palette["--theme-muted"], palette["--theme-surface"]) >= 4.5


def test_theme_rules_cover_player_forms_and_focus_without_restyling_fonts():
    css = CSS.read_text(encoding="utf-8")
    theme_css = css.split("/* Appearance palettes: backgrounds and color only. */", 1)[1]
    assert "html[data-appearance=night] body[data-view=playlists]" in theme_css
    assert ".appearance-menu[hidden]" in theme_css
    assert ".appearance-trigger:focus-visible" in theme_css
    assert ".playlist-player" in theme_css
    assert ".auth-card" in theme_css
    assert not re.search(r"\b(?:font|font-family|font-size|font-weight|line-height)\s*:", theme_css)


def test_night_palette_covers_header_navigation_and_secondary_surfaces():
    css = CSS.read_text(encoding="utf-8")
    theme_css = css.split("/* Appearance palettes: backgrounds and color only. */", 1)[1]
    for selector in (
        ".topbar nav a", ".topbar nav button", ".topbar .logout",
        ".external-source-panel", ".flow-strip", ".pch-dialog-body",
        ".daily-meta", "a.primary.link", ".library-overview", ".mix-automation-row",
    ):
        assert selector in theme_css
    overview_rule = theme_css.split("html[data-appearance=night] body[data-view=library] .library-overview,", 1)[1].split("}", 1)[0]
    assert "background:var(--theme-soft)" in overview_rule
    assert "color:var(--theme-text)" in overview_rule


def test_night_theme_keeps_native_dropdown_options_readable():
    css = CSS.read_text(encoding="utf-8")
    theme_css = css.split("/* Appearance palettes: backgrounds and color only. */", 1)[1]
    assert "html[data-appearance=night] :is(select option,select optgroup)" in theme_css
    assert "background:var(--theme-surface);color:var(--theme-text)" in theme_css
    assert "html[data-appearance=night] select option:disabled" in theme_css


def test_night_theme_covers_light_only_status_and_choice_surfaces():
    css = CSS.read_text(encoding="utf-8")
    theme_css = css.split("/* Appearance palettes: backgrounds and color only. */", 1)[1]
    for selector in (
        ".theme-choice-grid label", ".theme-choice-grid label:has(input:checked)",
        ".flow-step", ".flow-step.active", ".flow-step.done",
        ".mix-state", ".status-chip", ".daily-meta-target",
    ):
        assert selector in theme_css


def test_night_theme_keeps_warning_and_import_status_text_legible():
    css = CSS.read_text(encoding="utf-8")
    theme_css = css.split("/* Appearance palettes: backgrounds and color only. */", 1)[1]
    for selector in (
        ".source-warning", ".theme-missing", ".warning-block dt",
        ".step-head p", ".mini-stats span", ".mix-warning",
        ".bucket.warn", ".daily-main-card #publishHint:not([hidden])",
        ".external-command-bar .danger-text", "#missingSummary",
    ):
        assert selector in theme_css


def test_night_theme_keeps_settings_badges_readable():
    css = CSS.read_text(encoding="utf-8")
    theme_css = css.split("/* Appearance palettes: backgrounds and color only. */", 1)[1]
    for selector in (
        ".settings-avatar", ".mix-icon", ".webhook-guide li span",
        ".settings-save.is-saved", ".batch-auto-partial",
    ):
        assert selector in theme_css
    assert "html[data-appearance=night] :is(.settings-avatar,.mix-icon,.webhook-guide li span){color:var(--theme-accent)}" in theme_css


def test_night_theme_covers_account_errors_import_count_and_explanations():
    css = CSS.read_text(encoding="utf-8")
    theme_css = css.split("/* Appearance palettes: backgrounds and color only. */", 1)[1]
    for selector in (
        "body[data-view=settings] .settings-person .settings-user-status",
        "body[data-view=settings] .settings-user-status p",
        "body[data-view=external] #reviewSelectedCount",
        "body[data-view=mixes] .custom-mix>summary small",
        ".daily-main-card .daily-message",
    ):
        assert selector in theme_css
    assert _contrast(_palette(css, "night")["--theme-warning"], _palette(css, "night")["--theme-surface"]) >= 4.5


def test_mobile_sidebar_artwork_and_grid_are_reduced_together():
    css = CSS.read_text(encoding="utf-8")
    mobile_css = css.rsplit("@media(max-width:700px){#customPlaylistList button", 1)[1]
    assert "grid-template-columns:30px minmax(0,1fr)" in mobile_css
    assert ".playlist-cover-sidebar{width:28px;height:28px}" in mobile_css


def test_night_daily_failure_feedback_uses_readable_error_color():
    css = CSS.read_text(encoding="utf-8")
    theme_css = css.split("/* Appearance palettes: backgrounds and color only. */", 1)[1]
    for selector in (
        "body[data-view=daily] #dailyJobFeedback.is-error",
        "body[data-view=daily] #dailyFeedback .pch-inline-feedback.is-error",
    ):
        assert selector in theme_css
    palette = _palette(css, "night")
    assert _contrast(palette["--theme-danger"], palette["--theme-surface"]) >= 4.5
