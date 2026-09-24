from importlib import import_module
from pathlib import Path
import re


STATIC = Path(__file__).resolve().parents[1] / "src/helper/static"
TOKENS = STATIC / "theme-tokens.css"
PRODUCT = STATIC / "product.css"
REQUIRED = {
    "--app-rail",
    "--app-main",
    "--app-surface",
    "--app-control",
    "--app-hover",
    "--app-selected",
    "--app-overlay",
    "--app-text",
    "--app-muted",
    "--app-line",
    "--app-accent",
    "--app-accent-strong",
    "--app-on-accent",
    "--app-danger",
    "--app-danger-soft",
    "--app-warning",
    "--app-success",
    "--icon-default",
    "--icon-muted",
    "--icon-hover",
    "--icon-active",
    "--icon-danger",
    "--focus-ring",
    "--app-shadow",
    "--background-color",
    "--background-image",
    "--background-overlay",
}


def _block(css: str, selector: str) -> str:
    return css.split(f"{selector}{{", 1)[1].split("}", 1)[0]


def _names(block: str) -> set[str]:
    return set(re.findall(r"(--[\w-]+)\s*:", block))


def test_each_palette_resolves_the_full_semantic_contract():
    css = TOKENS.read_text(encoding="utf-8")
    defaults = _names(_block(css, "html"))

    assert REQUIRED <= defaults
    for theme in ("warm", "night"):
        resolved = defaults | _names(_block(css, f'html[data-appearance="{theme}"]'))
        assert REQUIRED <= resolved


def test_product_has_no_theme_specific_palette_blocks():
    css = PRODUCT.read_text(encoding="utf-8")

    assert "html[data-appearance=warm]" not in css
    assert "html[data-appearance=night]" not in css


def test_raw_color_checker_rejects_ui_colors_and_allows_documented_asset_fallback(
    tmp_path: Path,
):
    checker = import_module("tools.check_theme_contract")
    ordinary = tmp_path / "ordinary.css"
    ordinary.write_text(".button{color:#fff;background:rgba(0,0,0,.2)}\n", encoding="utf-8")
    fallback = tmp_path / "fallback.css"
    fallback.write_text(
        ".art{background:#102030} /* theme-contract-allow: asset-fallback */\n",
        encoding="utf-8",
    )

    assert checker.unregistered_colors(ordinary) == [
        (1, "#fff"),
        (1, "rgba(0,0,0,.2)"),
    ]
    assert checker.unregistered_colors(fallback) == []
