"""Settings use the shell's active account instead of a duplicate selector."""

from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / "src/helper/static"


def test_embedded_settings_uses_shell_account_selection():
    page = (STATIC / "settings.html").read_text()
    js = (STATIC / "settings.js").read_text()
    assert 'id="profileSwitcher"' not in page
    assert "PCHAuth.profile()" in js
    assert "activeProfile" in js
