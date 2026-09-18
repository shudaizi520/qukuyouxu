"""Installed command-line interface for portable music-cache archives."""

import argparse
from pathlib import Path
import sys

from .cache_archive import (
    CacheArchiveError,
    export_music_cache,
    import_music_cache,
    inspect_music_cache,
)


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description="曲库有序歌曲缓存迁移工具")
    actions = command.add_subparsers(dest="action", required=True)

    export = actions.add_parser("export", help="从数据库导出歌曲缓存")
    export.add_argument("--database", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--profile", help="只导出指定 Plex 档案的缓存")

    inspect = actions.add_parser("inspect", help="校验缓存归档")
    inspect.add_argument("--archive", type=Path, required=True)

    restore = actions.add_parser("import", help="把歌曲缓存导入数据库")
    restore.add_argument("--archive", type=Path, required=True)
    restore.add_argument("--database", type=Path, required=True)
    restore.add_argument("--replace", action="store_true", help="明确覆盖冲突缓存")
    restore.add_argument(
        "--target-profile",
        help="归档只有一个档案时，将缓存映射到这个 Plex 档案标识",
    )
    return command


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        if arguments.action == "export":
            summary = export_music_cache(
                arguments.database,
                arguments.output,
                profile_id=arguments.profile,
            )
        elif arguments.action == "inspect":
            summary = inspect_music_cache(arguments.archive)
        else:
            summary = import_music_cache(
                arguments.archive,
                arguments.database,
                replace=arguments.replace,
                target_profile=arguments.target_profile,
            )
    except CacheArchiveError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    print(f"记录数: {summary.count}")
    print(f"SHA-256: {summary.sha256}")
    if summary.backup is not None:
        print(f"导入前备份: {summary.backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
