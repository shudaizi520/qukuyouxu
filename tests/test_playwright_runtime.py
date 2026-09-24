from pathlib import Path

from tools.playwright_runtime import prepare_playwright_environment


def test_prepares_bundled_linux_runtime_without_losing_existing_paths(
    tmp_path: Path, monkeypatch
):
    library_root = (
        tmp_path
        / ".playwright"
        / "sysroot"
        / "root"
        / "usr"
        / "lib"
        / "x86_64-linux-gnu"
    )
    library_root.mkdir(parents=True)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/existing/runtime")

    prepare_playwright_environment(tmp_path)

    assert __import__("os").environ["LD_LIBRARY_PATH"] == (
        f"{library_root}:/existing/runtime"
    )
    sysroot = tmp_path / ".playwright" / "sysroot" / "root"
    assert __import__("os").environ["FONTCONFIG_SYSROOT"] == str(sysroot)
    assert __import__("os").environ["FONTCONFIG_PATH"] == str(
        sysroot / "etc" / "fonts"
    )
    assert __import__("os").environ["FONTCONFIG_FILE"] == "fonts.conf"


def test_does_nothing_when_bundled_runtime_is_absent(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/existing/runtime")
    monkeypatch.delenv("FONTCONFIG_SYSROOT", raising=False)

    prepare_playwright_environment(tmp_path)

    assert __import__("os").environ["LD_LIBRARY_PATH"] == "/existing/runtime"
    assert "FONTCONFIG_SYSROOT" not in __import__("os").environ
