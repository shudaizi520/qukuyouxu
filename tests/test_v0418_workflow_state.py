import unittest


class WorkflowStateV0418Tests(unittest.TestCase):
    def test_completed_incremental_check_hides_an_older_pending_preview(self):
        from helper.workflow_state import current_review_plan

        plan = {"id": "old-preview", "created_at": 100, "applied": False}
        incremental = {"status": "completed", "updated_at": 200, "new_count": 0}

        self.assertIsNone(current_review_plan(plan, incremental))

    def test_preview_created_after_incremental_check_remains_visible(self):
        from helper.workflow_state import current_review_plan

        plan = {"id": "new-preview", "created_at": 300, "applied": False}
        incremental = {"status": "completed", "updated_at": 200, "new_count": 0}

        self.assertEqual(plan, current_review_plan(plan, incremental))

    def test_no_incremental_history_preserves_existing_preview(self):
        from helper.workflow_state import current_review_plan

        plan = {"id": "preview", "created_at": 100, "applied": False}

        self.assertEqual(plan, current_review_plan(plan, {}))

    def test_workflow_status_uses_only_the_current_review_plan(self):
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "src/helper/workflow_v0317.py").read_text(encoding="utf-8")
        self.assertIn("plan = current_review_plan(saved_plan, incremental)", source)
        self.assertIn('"review": _review(plan, sources)', source)


if __name__ == "__main__":
    unittest.main()
