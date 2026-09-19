import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def test_analysis_runs_resumable_enrichment_before_category_preview():
    from helper.library_engine import LibraryEngine
    from helper.store import Store

    with tempfile.TemporaryDirectory() as root:
        engine = LibraryEngine(Store(Path(root)))
        order = []
        with patch.object(engine, "_enrich_singles", side_effect=lambda **kwargs: order.append(("scan", kwargs)) or {"status": "completed"}), \
                patch.object(engine, "_preview", side_effect=lambda force: order.append(("preview", force)) or {"id": "p"}):
            result = engine.analyze_library(force_sources=True)

    assert result == {"id": "p"}
    assert order == [("scan", {"new_only": False, "auto_connect": True}), ("preview", True)]


def test_legacy_sparse_preview_is_rejected_at_write_boundary():
    from helper.engine import SafetyError
    from helper.library_engine import LibraryEngine
    from helper.store import Store

    with tempfile.TemporaryDirectory() as root:
        store = Store(Path(root))
        engine = LibraryEngine(store)
        store.set("plan", {
            "id": "legacy", "signature": engine.signature(), "created_at": 9_999_999_999,
            "machine": "m", "applied": False, "library_count": 100,
            "groups": [{"id": "g", "title": "稀疏歌单", "desired": ["1"] * 5,
                        "add": ["1"] * 5, "blocked": [], "before": None}],
            "track_fingerprints": {},
        })

        try:
            engine.apply("legacy")
        except SafetyError as exc:
            assert "重新" in str(exc) or "规则" in str(exc)
        else:
            raise AssertionError("legacy sparse preview reached the writer")
