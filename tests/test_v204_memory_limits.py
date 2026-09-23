"""Bound thread-backed allocator growth under repeated artwork and sync work."""
import tempfile
import unittest
from pathlib import Path

import anyio.to_thread

from helper.store import Store
from helper.web import create_app


ROOT = Path(__file__).resolve().parents[1]


class MemoryLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_web_thread_pool_is_bounded(self):
        with tempfile.TemporaryDirectory() as root:
            app = create_app(store=Store(Path(root)), start_scheduler=False)
            async with app.router.lifespan_context(app):
                self.assertEqual(
                    8,
                    anyio.to_thread.current_default_thread_limiter().total_tokens,
                )

    def test_container_bounds_glibc_arenas_and_trims_free_heap(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("MALLOC_ARENA_MAX=2", dockerfile)
        self.assertIn("MALLOC_TRIM_THRESHOLD_=131072", dockerfile)


if __name__ == "__main__":
    unittest.main()
