"""Runtime preparation shared by local Playwright verification tools."""

from __future__ import annotations

import os
from pathlib import Path


def prepare_playwright_environment(repo_root: Path | None = None) -> None:
    """Expose the project's bundled browser libraries when they are available."""

    root = repo_root or Path(__file__).resolve().parents[1]
    sysroot = root / ".playwright" / "sysroot" / "root"
    library_root = sysroot / "usr" / "lib" / "x86_64-linux-gnu"
    if not library_root.is_dir():
        return
    existing = os.environ.get("LD_LIBRARY_PATH")
    entries = [str(library_root)]
    if existing:
        entries.extend(
            entry
            for entry in existing.split(os.pathsep)
            if entry and entry != str(library_root)
        )
    os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(entries)
    os.environ["FONTCONFIG_SYSROOT"] = str(sysroot)
    os.environ["FONTCONFIG_PATH"] = str(sysroot / "etc" / "fonts")
    os.environ["FONTCONFIG_FILE"] = "fonts.conf"
