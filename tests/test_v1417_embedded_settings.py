"""Keep one account switcher when settings live inside the playlist hub."""

from pathlib import Path
import re


STATIC = Path(__file__).resolve().parents[1] / "src/helper/static"


def test_embedded_settings_uses_sidebar_account_switcher_only():
    page = (STATIC / "settings.html").read_text()
    assert 'id="profileSwitcher"' in page  # standalone settings keeps its selector
    css = re.sub(r"/\*.*?\*/", "", (STATIC / "product.css").read_text(), flags=re.S)
    selector = ".pch-embedded body[data-view=settings] #profileSwitcher"
    rules = [body for names, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)
             if selector in [name.strip() for name in names.split(",")]]
    assert rules and "display:none" in rules[-1]
