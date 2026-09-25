"""Reject literal UI colors outside the canonical theme token palette."""

from __future__ import annotations

from pathlib import Path
import re
import sys


RAW_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\)")
ALLOW_MARKER = "theme-contract-allow: asset-fallback"
PROTECTED = (
    Path("src/helper/static/product.css"),
    Path("src/helper/static/product-refinements.css"),
    Path("src/helper/static/design-system.css"),
    Path("src/helper/static/external-workspace.css"),
    Path("src/helper/static/playlist-now-playing.css"),
)


def unregistered_colors(path: Path) -> list[tuple[int, str]]:
    path = Path(path)
    if path.name == "theme-tokens.css":
        return []
    violations: list[tuple[int, str]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if ALLOW_MARKER in line:
            continue
        violations.extend((number, match.group(0)) for match in RAW_COLOR.finditer(line))
    return violations


def main() -> int:
    failed = False
    for path in PROTECTED:
        for number, literal in unregistered_colors(path):
            failed = True
            print(f"{path}:{number}: {literal}", file=sys.stderr)
    if failed:
        return 1
    print("Theme contract passed: zero unregistered UI colors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
