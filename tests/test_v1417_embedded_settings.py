"""Settings must expose the account whose library is being edited."""

from pathlib import Path
import re


STATIC = Path(__file__).resolve().parents[1] / "src/helper/static"


def test_embedded_settings_keeps_its_explicit_account_switcher():
    page = (STATIC / "settings.html").read_text()
    assert 'id="profileSwitcher"' in page  # standalone settings keeps its selector
    css = re.sub(r"/\*.*?\*/", "", (STATIC / "product.css").read_text(), flags=re.S)
    selector = ".pch-embedded body[data-view=settings] #profileSwitcher"
    rules = [body for names, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)
             if selector in [name.strip() for name in names.split(",")]]
    assert not rules or "display:none" not in rules[-1]
