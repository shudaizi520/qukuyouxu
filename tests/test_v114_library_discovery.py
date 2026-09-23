import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class _Engine:
    job = {}

    def single_status(self):
        return {"state": {}}

    def theme_status(self):
        return {"settings": {}, "topics": [], "unavailable": [], "skipped_references": []}


def test_adaptive_threshold_is_half_percent_clamped():
    from helper.library_discovery import discovery_min_tracks

    assert discovery_min_tracks(100) == 10
    assert discovery_min_tracks(3072) == 16
    assert discovery_min_tracks(10000) == 30


def test_unmanaged_sparse_groups_are_hidden_but_existing_managed_groups_remain():
    from helper.library_discovery import eligible_discovery_groups

    plan = {
        "library_count": 3072,
        "groups": [
            {"id": "sparse", "desired": list(range(15)), "blocked": []},
            {"id": "kept", "desired": list(range(3)), "blocked": []},
        ],
    }

    visible = eligible_discovery_groups(plan, {"kept": {"id": "plex-1"}})

    assert [row["id"] for row in visible] == ["kept"]


def test_fresh_status_starts_before_analysis_without_a_review():
    from helper.store import Store
    from helper.workflow_v0317 import build_workflow_status

    with tempfile.TemporaryDirectory() as root:
        store = Store(Path(root))

        workflow = build_workflow_status(
            store,
            _Engine(),
            {"logged_in": True, "phase": "ready"},
        )["workflow"]

        assert workflow["discovery"]["phase"] == "before_analysis"
        assert workflow["discovery"]["threshold"] == 10
        assert workflow["review"] is None


def test_completed_preview_exposes_only_eligible_candidates():
    from helper.store import Store
    from helper.workflow_v0317 import build_workflow_status

    with tempfile.TemporaryDirectory() as root:
        store = Store(Path(root))
        plan = {
            "id": "preview-1",
            "created_at": time.time(),
            "applied": False,
            "library_count": 3072,
            "groups": [
                {
                    "id": "sparse",
                    "title": "歌曲不足",
                    "kind": "qq_category",
                    "desired": [str(index) for index in range(15)],
                    "blocked": [],
                },
                {
                    "id": "eligible",
                    "title": "可以创建",
                    "kind": "qq_category",
                    "desired": [str(index) for index in range(16)],
                    "blocked": [],
                },
            ],
        }
        store.set("plan", plan)

        workflow = build_workflow_status(
            store,
            _Engine(),
            {"logged_in": True, "phase": "ready"},
        )["workflow"]

        assert workflow["discovery"]["phase"] == "choose"
        assert workflow["discovery"]["threshold"] == 16
        assert [row["id"] for row in workflow["review"]["groups"]] == ["eligible"]


def test_workflow_review_combines_theme_and_qq_field_categories():
    from helper.store import Store
    from helper.workflow_v0317 import build_workflow_status

    with tempfile.TemporaryDirectory() as root:
        store = Store(Path(root))
        store.set("catalog", [{"id": str(index)} for index in range(100)])
        store.set("plan", {
            "id": "theme-preview", "created_at": time.time(), "applied": False,
            "library_count": 100, "covered": 12,
            "groups": [{
                "id": "theme:work", "title": "工作陪伴", "kind": "qq_category",
                "desired": [str(index) for index in range(12)], "blocked": [],
            }],
        })
        store.set("base_plan", {
            "id": "base-preview", "created_at": time.time(), "applied": False,
            "library_count": 100, "union_covered": 42, "metadata_review_count": 7,
            "groups": [{
                "id": "base:pop", "title": "流行全库", "kind": "base", "dimension": "genre",
                "desired": [str(index) for index in range(42)], "blocked": [],
            }],
        })

        workflow = build_workflow_status(store, _Engine(), {"logged_in": True})["workflow"]

        assert workflow["summary"]["matched"] == 42
        assert workflow["summary"]["review_count"] == 7
        assert [row["id"] for row in workflow["review"]["groups"]] == ["theme:work", "base:pop"]
        assert workflow["review"]["theme_plan_id"] == "theme-preview"
        assert workflow["review"]["base_plan_id"] == "base-preview"


def test_base_only_preview_is_still_presented_for_confirmation():
    from helper.store import Store
    from helper.workflow_v0317 import build_workflow_status

    with tempfile.TemporaryDirectory() as root:
        store = Store(Path(root))
        store.set("base_plan", {
            "id": "base-preview", "created_at": time.time(), "applied": False,
            "library_count": 100, "union_covered": 20,
            "groups": [{
                "id": "base:pop", "title": "流行全库", "kind": "base",
                "desired": [str(index) for index in range(20)], "blocked": [],
            }],
        })

        workflow = build_workflow_status(store, _Engine(), {"logged_in": True})["workflow"]

        assert workflow["discovery"]["phase"] == "choose"
        assert workflow["review"]["theme_plan_id"] == ""
        assert workflow["review"]["base_plan_id"] == "base-preview"


def test_pending_candidates_take_priority_over_existing_managed_playlists():
    from helper.store import Store
    from helper.workflow_v0317 import build_workflow_status

    with tempfile.TemporaryDirectory() as root:
        store = Store(Path(root))
        store.set("managed", {"existing": {"id": "plex-1"}})
        store.set("plan", {
            "id": "preview-2", "created_at": time.time(), "applied": False,
            "library_count": 1000,
            "groups": [{
                "id": "new", "title": "新分类", "kind": "qq_category",
                "desired": [str(index) for index in range(10)], "blocked": [],
            }],
        })

        workflow = build_workflow_status(store, _Engine(), {"logged_in": True})["workflow"]

        assert workflow["discovery"]["phase"] == "choose"
        assert [row["id"] for row in workflow["review"]["groups"]] == ["new"]


def test_discovery_ignores_removed_legacy_theme_picker_but_preserves_managed_opt_out():
    from helper.library_discovery import prepare_discovery_sources

    current = [
        {"id": "old-unmanaged", "kind": "qq_category", "value": "1", "name": "网络热歌", "theme_key": "internet", "enabled": False},
        {"id": "old-managed", "kind": "qq_category", "value": "2", "name": "KTV金曲", "theme_key": "ktv", "enabled": False},
    ]
    tags = [{"id": "1", "name": "网络热歌"}, {"id": "2", "name": "KTV金曲"}]

    sources, _missing = prepare_discovery_sources(current, tags, {"old-managed": {"id": "plex-2"}})

    by_id = {row["id"]: row for row in sources}
    assert by_id["old-unmanaged"]["enabled"] is True
    assert by_id["old-managed"]["enabled"] is False
