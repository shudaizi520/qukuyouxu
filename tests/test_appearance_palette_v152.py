"""The canonical design system keeps every appearance readable and coherent."""
import re
from pathlib import Path


CSS = Path(__file__).resolve().parents[1] / "src/helper/static/design-system.css"


def _luminance(value):
    channels = [int(value[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [channel / 12.92 if channel <= .04045 else ((channel + .055) / 1.055) ** 2.4
              for channel in channels]
    return .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2]


def _contrast(first, second):
    light, dark = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (light + .05) / (dark + .05)


def _palette(css, name):
    selector = f'html[data-appearance="{name}"]{{'
    block = css.split(selector, 1)[1].split("}", 1)[0]
    return dict(re.findall(r"(--app-[\w-]+):\s*(#[0-9a-fA-F]{6})", block))


def test_warm_and_night_palettes_keep_normal_and_muted_text_legible():
    css = CSS.read_text(encoding="utf-8")
    for name in ("warm", "night"):
        palette = _palette(css, name)
        assert _contrast(palette["--app-text"], palette["--app-main"]) >= 7
        assert _contrast(palette["--app-text"], palette["--app-rail"]) >= 7
        assert _contrast(palette["--app-text"], palette["--app-control"]) >= 7
        assert _contrast(palette["--app-muted"], palette["--app-main"]) >= 4.5


def test_theme_rules_cover_player_forms_focus_and_typography_once():
    css = CSS.read_text(encoding="utf-8")
    assert "body[data-view=playlists] .playlist-player" in css
    assert "input:not([type=checkbox]):not([type=radio]):not([type=range])" in css
    assert ":focus-visible" in css
    assert "--app-font:" in css
    assert "html,body{font-family:var(--app-font)" in css


def test_night_palette_covers_navigation_dropdowns_and_secondary_surfaces():
    css = CSS.read_text(encoding="utf-8")
    for selector in (
        ".topbar nav", ".topbar .logout", ".appearance-menu",
        "select option", ".card", ".notice", ".settings-section",
    ):
        assert selector in css
    assert 'html[data-appearance="night"] select{color-scheme:dark}' in css
    assert "background:var(--app-overlay);color:var(--app-text)" in css


def test_warning_error_and_status_tokens_remain_readable_at_night():
    css = CSS.read_text(encoding="utf-8")
    palette = _palette(css, "night")
    assert _contrast(palette["--app-danger"], palette["--app-main"]) >= 4.5
    assert _contrast(palette["--app-warning"], palette["--app-main"]) >= 4.5
    for token in ("--app-danger-soft", "--theme-warning:var(--app-warning)"):
        assert token in css


def test_mobile_sidebar_artwork_and_grid_are_reduced_together():
    product = CSS.with_name("product.css").read_text(encoding="utf-8")
    mobile_css = product.rsplit("@media(max-width:700px){#customPlaylistList button", 1)[1]
    assert "grid-template-columns:30px minmax(0,1fr)" in mobile_css
    assert ".playlist-cover-sidebar{width:28px;height:28px}" in mobile_css
