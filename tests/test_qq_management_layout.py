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
