"""Container health probe that follows the configured application port."""
from __future__ import annotations

import os
import urllib.request


def main() -> int:
    port = int(os.environ.get("PORT", "9511"))
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be between 1 and 65535")
    urllib.request.urlopen(
        f"http://127.0.0.1:{port}/healthz",
        timeout=3,
    ).read()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
