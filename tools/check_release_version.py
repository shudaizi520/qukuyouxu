"""Fail a release when its Git tag and application version differ."""

import re
import sys


def check_release_version(tag: str, version: str) -> str:
    tag = str(tag or "")
    version = str(version or "")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(f"程序版本格式无效：{version!r}")
    expected = f"v{version}"
    if tag != expected:
        raise ValueError(f"发布标签 {tag!r} 与程序版本 {expected!r} 不一致")
    return version


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        print("用法: python tools/check_release_version.py vX.Y.Z", file=sys.stderr)
        return 2
    from helper import __version__

    try:
        version = check_release_version(arguments[0], __version__)
    except ValueError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    print(f"发布版本已核对: v{version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
