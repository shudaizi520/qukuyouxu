"""Fail closed when public repository candidates contain private runtime data."""

from pathlib import Path
import re
import sys


IGNORED_DIRECTORIES = frozenset({".git", ".venv", "__pycache__", ".pytest_cache"})
MAX_SOURCE_BYTES = 1024 * 1024
FORBIDDEN_NAMES = (
    re.compile(r"\.sqlite3(?:-.+)?$", re.IGNORECASE),
    re.compile(r"\.bundle\.xz$", re.IGNORECASE),
    re.compile(r"-(?:upgrade|rollback)\.ya?ml$", re.IGNORECASE),
    re.compile(r"^music-cache.*\.json$", re.IGNORECASE),
    re.compile(r"\.before-cache-import-.+\.bak$", re.IGNORECASE),
)
SECRET_ASSIGNMENT = re.compile(
    r"(?m)^[ \t]*(?:PLEX_TOKEN|ADMIN_TOKEN|SETUP_TOKEN|WEBHOOK_SECRET|SESSION_SECRET|"
    r"QQ_COOKIE|QQMUSIC_KEY)[ \t]*[:=][ \t]*[\"']?([^ \t\r\n\"'#][^\r\n]*)$"
)
EMPTY_ENV_SUBSTITUTION = re.compile(r"^\$\{[A-Z][A-Z0-9_]*:-\}$")
PRIVATE_KEY_MARKER = re.compile(
    r"(?m)^-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----$"
)


def _files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in IGNORED_DIRECTORIES for part in path.relative_to(root).parts):
            continue
        yield path


def find_violations(root: Path) -> list[str]:
    root = Path(root).resolve()
    violations: list[str] = []
    for path in _files(root):
        relative = path.relative_to(root).as_posix()
        if any(pattern.search(path.name) for pattern in FORBIDDEN_NAMES):
            violations.append(f"禁止的运行或历史发布文件: {relative}")
            continue
        size = path.stat().st_size
        if size > MAX_SOURCE_BYTES:
            violations.append(f"文件超过 1 MiB，请确认不是生成载荷: {relative}")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            violations.append(f"公开仓库不应包含未说明的二进制文件: {relative}")
            continue
        if PRIVATE_KEY_MARKER.search(text):
            violations.append(f"发现私钥标记: {relative}")
        if any(
            not EMPTY_ENV_SUBSTITUTION.fullmatch(match.group(1).strip())
            for match in SECRET_ASSIGNMENT.finditer(text)
        ):
            violations.append(f"发现非空凭据赋值: {relative}")
    return violations


def main(argv: list[str] | None = None) -> int:
    root = Path(argv[0] if argv else ".")
    violations = find_violations(root)
    if violations:
        for violation in violations:
            print(violation, file=sys.stderr)
        return 1
    print("仓库检查通过：未发现数据库、历史补丁载荷或明显凭据。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
