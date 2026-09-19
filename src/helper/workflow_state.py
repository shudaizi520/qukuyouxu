"""Pure ordering rules for persisted workflow results."""


def _timestamp(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def incremental_is_current(plan, incremental):
    checked_at = _timestamp((incremental or {}).get("updated_at"))
    if checked_at <= 0:
        return False
    return not plan or checked_at >= _timestamp((plan or {}).get("created_at"))


def current_review_plan(plan, incremental):
    """An older pending theme preview must not impersonate a newer new-track check."""
    return None if incremental_is_current(plan, incremental) else plan


def resume_job_kind(paused):
    """Return the persisted resumable job, defaulting old records to full analysis."""
    if isinstance(paused, dict) and paused.get("active") and paused.get("kind") == "incremental":
        return "incremental"
    return "preview"
