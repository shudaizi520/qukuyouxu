"""Generated membership is authoritative; private imports retain user edits."""
from __future__ import annotations


GENERATED_KINDS = frozenset({"daily", "smart", "category"})


def clear_generated_manual_edits(store):
    """Idempotent, profile-local cleanup; never touches Plex or update switches."""
    with store.lock:
        edits = store.get("playlist_manual_edits", {}) or {}
        retained = {
            key: value for key, value in edits.items()
            if str(key).partition(":")[0] not in GENERATED_KINDS
        }
        if retained != edits:
            changes = {"playlist_manual_edits": retained}
            if any(str(key).startswith("category:") for key in edits.keys() - retained.keys()):
                plan = store.get("plan")
                if plan and not plan.get("applied"):
                    changes["plan"] = {
                        **plan, "signature": None,
                        "invalidated_reason": "生成歌单的成员规则已更新，请重新分析曲库。",
                    }
            store.set_many(changes)


def apply_manual_edits(store, kind, key, desired):
    """Keep generated members, or apply private inclusions/exclusions safely."""
    edits = {} if kind in GENERATED_KINDS else (
        (store.get("playlist_manual_edits", {}) or {}).get(str(kind) + ":" + str(key), {}) or {}
    )
    excluded = {str(value) for value in edits.get("exclude", []) if str(value).isdigit()}
    included = [str(value) for value in edits.get("include", []) if str(value).isdigit()]
    catalog = list(store.get("catalog", []) or [])
    available = {
        str(row.get("id")) for row in catalog
        if isinstance(row, dict) and row.get("available", True) and str(row.get("id") or "").isdigit()
    }
    if not catalog:
        available = {str(value) for value in [*(desired or []), *included] if str(value).isdigit()}
    result, seen = [], set()
    for value in [*map(str, desired or []), *included]:
        if value in excluded or value in seen or value not in available:
            continue
        result.append(value)
        seen.add(value)
    return result
