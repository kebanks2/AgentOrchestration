"""Dataset export selection with workspace consent checks."""

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple


WorkspacePolicies = Mapping[str, Mapping[str, Any]]


def build_dataset_export(
    records: Iterable[Mapping[str, Any]],
    workspace_policies: WorkspacePolicies,
    purpose: str,
    selection_time: Optional[Any] = None,
) -> Dict[str, Any]:
    """Select export records and return a consent manifest."""
    selection = select_export_records(
        records,
        workspace_policies,
        purpose,
        selection_time,
    )
    selected, exclusion_counts, selected_ids, selected_at = selection
    manifest = {
        "purpose": purpose,
        "selection_time": selected_at,
        "consent_criteria": {
            "required_consent": purpose,
            "opt_out_cutoff": selected_at,
            "policy": "workspace consent must be active at selection time",
        },
        "selected_count": len(selected),
        "selected_record_ids": selected_ids,
        "excluded_count": sum(exclusion_counts.values()),
        "exclusion_counts": exclusion_counts,
    }
    return {"records": selected, "manifest": manifest}


def select_export_records(
    records: Iterable[Mapping[str, Any]],
    workspace_policies: WorkspacePolicies,
    purpose: str,
    selection_time: Optional[Any] = None,
) -> Tuple[List[Mapping[str, Any]], Dict[str, int], List[Any], str]:
    selected_at = _selection_timestamp(selection_time)
    selected: List[Mapping[str, Any]] = []
    selected_ids: List[Any] = []
    exclusion_counts = {
        "missing_workspace": 0,
        "missing_consent": 0,
        "opted_out": 0,
    }

    for record in records:
        workspace_id = record.get("workspace_id")
        if workspace_id not in workspace_policies:
            exclusion_counts["missing_workspace"] += 1
            continue

        policy = workspace_policies[workspace_id]
        allowed, reason = _workspace_allows_export(
            policy,
            purpose,
            selected_at,
        )
        if not allowed:
            exclusion_counts[reason] += 1
            continue

        selected.append(record)
        if "id" in record:
            selected_ids.append(record["id"])

    return selected, exclusion_counts, selected_ids, selected_at


def _workspace_allows_export(
    policy: Mapping[str, Any],
    purpose: str,
    selection_time: str,
) -> Tuple[bool, str]:
    consents = policy.get("consents", {})
    if not consents.get(purpose):
        return False, "missing_consent"

    opt_out_at = policy.get("opt_out_at")
    if (
        opt_out_at is not None
        and _normalize_timestamp(opt_out_at) <= selection_time
    ):
        return False, "opted_out"

    return True, ""


def _selection_timestamp(value: Optional[Any]) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    return _normalize_timestamp(value)


def _normalize_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        timestamp = value
    elif isinstance(value, (int, float)):
        timestamp = datetime.fromtimestamp(value, timezone.utc)
    elif isinstance(value, str):
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise TypeError(
            "selection timestamps must be datetime, number, or ISO string"
        )

    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc).isoformat()
