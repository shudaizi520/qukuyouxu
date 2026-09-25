from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / "src/helper/static"


def product_css() -> str:
    """Return the shipped product cascade in browser load order."""
    return (STATIC / "product.css").read_text(encoding="utf-8") + "\n" + (
        STATIC / "product-refinements.css"
    ).read_text(encoding="utf-8")
