from pathlib import Path
import re


STATIC = Path(__file__).resolve().parents[1] / "src/helper/static"


def product_css() -> str:
    """Return the shipped product cascade in browser load order."""
    return (STATIC / "product.css").read_text(encoding="utf-8") + "\n" + (
        STATIC / "product-refinements.css"
    ).read_text(encoding="utf-8")


def page_css(page_name: str) -> str:
    """Return a page's shipped stylesheets in its exact browser load order."""
    html = (STATIC / f"{page_name}.html").read_text(encoding="utf-8")
    names = re.findall(r'<link[^>]+href="/static/([^"?]+)', html)
    return "\n".join(
        (STATIC / name).read_text(encoding="utf-8")
        for name in names
    )
