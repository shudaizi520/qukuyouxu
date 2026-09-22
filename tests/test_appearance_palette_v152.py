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
        ".daily-meta", "a.primary.link",
    ):
        assert selector in theme_css
