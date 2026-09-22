"""Every active page applies a saved palette before the stylesheet paints."""
from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / "src/helper/static"


def test_appearance_initializes_before_shared_css_on_every_page():
    pages = sorted(STATIC.glob("*.html"))
    themed = [page for page in pages if "product.css" in page.read_text(encoding="utf-8")]
    assert themed
    for page in themed:
        source = page.read_text(encoding="utf-8")
        assert source.index("appearance.js") < source.index("product.css"), page.name
