from pathlib import Path
import re


STATIC = Path(__file__).resolve().parents[1] / "src/helper/static"


def _resolved_declarations(css: str, selector: str) -> dict[str, str]:
    declarations: dict[str, str] = {}
    for names, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if selector not in [name.strip() for name in names.split(",")]:
            continue
        for declaration in body.split(";"):
            if ":" in declaration:
                key, value = declaration.strip().split(":", 1)
                declarations[key] = value
    return declarations


def test_global_search_uses_one_focus_frame():
    css = (STATIC / "product.css").read_text()
    frame = _resolved_declarations(css, ".playlist-global-search")
    input_focus = _resolved_declarations(css, ".playlist-global-search input:focus-visible")
    assert frame.get("border") == "1px solid var(--playlist-line)"
    assert input_focus.get("outline") == "none"
    assert input_focus.get("box-shadow") == "none"


def test_library_candidate_rows_are_grouped_by_meaning():
    script = (STATIC / "home.js").read_text()
    for label in ("主题精选", "场景", "心情", "语种", "曲风", "版本", "歌曲年代", "专辑年代"):
        assert label in script
    assert "review-group-row" in script
    assert "reviewGroupLabel" in script


def test_library_panels_use_consistent_gutters_and_fixed_numeric_column():
    css = (STATIC / "product.css").read_text()
    assert "--library-panel-gutter" in css
    assert "padding-inline:var(--library-panel-gutter)" in css
    numeric = _resolved_declarations(
        css,
        "body[data-view=library] .review-table td:nth-child(3)",
    )
    assert numeric.get("text-align") == "right"
    assert numeric.get("font-variant-numeric") == "tabular-nums"
    assert ".review-group-row" in css


def test_expired_review_explains_the_disabled_action():
    script = (STATIC / "home.js").read_text()
    assert "预览已过期" in script
    assert "$('refreshReview').className=review.expired?'primary':'secondary'" in script
