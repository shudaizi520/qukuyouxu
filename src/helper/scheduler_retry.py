"""Persistent retry policy for scheduled operations that are safe to repeat."""
from __future__ import annotations

from typing import Literal


RETRY_DELAYS = (300, 1800, 7200)


class TransientScheduleError(RuntimeError):
    """A temporary failure proven to have happened before any remote write."""


def classify_scheduled_failure(exc: Exception) -> Literal["transient", "safety"]:
    return "transient" if isinstance(exc, TransientScheduleError) else "safety"


def schedule_retry(task: dict, now: float) -> bool:
    failures = max(0, int(task.get("failure_count") or 0))
    if failures >= len(RETRY_DELAYS):
        return False
    if not task.get("retry_slot"):
        task["retry_slot"] = task.get("slot")
    failures += 1
    retry_at = float(now) + RETRY_DELAYS[failures - 1]
    task.update(
        failure_count=failures,
        retry_at=retry_at,
        next_at=retry_at,
    )
    return True


def clear_retry(task: dict) -> None:
    task.pop("failure_count", None)
    task.pop("retry_at", None)
    task.pop("retry_slot", None)
