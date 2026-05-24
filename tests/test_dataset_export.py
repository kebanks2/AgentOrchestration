from datetime import datetime, timezone

from src.orchestrator.dataset_export import build_dataset_export


SELECTION_TIME = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)


def test_export_excludes_workspaces_without_required_consent():
    records = [
        {"id": "task-1", "workspace_id": "workspace-a"},
        {"id": "task-2", "workspace_id": "workspace-b"},
        {"id": "task-3", "workspace_id": "workspace-c"},
    ]
    policies = {
        "workspace-a": {"consents": {"training": True}},
        "workspace-b": {"consents": {"training": False}},
    }

    export = build_dataset_export(
        records,
        policies,
        "training",
        SELECTION_TIME,
    )

    assert [record["id"] for record in export["records"]] == ["task-1"]
    assert export["manifest"]["exclusion_counts"] == {
        "missing_workspace": 1,
        "missing_consent": 1,
        "opted_out": 0,
    }


def test_manifest_records_purpose_consent_criteria_and_selection_time():
    export = build_dataset_export(
        [{"id": "task-1", "workspace_id": "workspace-a"}],
        {"workspace-a": {"consents": {"evaluation": True}}},
        "evaluation",
        SELECTION_TIME,
    )

    manifest = export["manifest"]
    assert manifest["purpose"] == "evaluation"
    assert manifest["selection_time"] == "2026-05-24T12:00:00+00:00"
    assert manifest["consent_criteria"] == {
        "required_consent": "evaluation",
        "opt_out_cutoff": "2026-05-24T12:00:00+00:00",
        "policy": "workspace consent must be active at selection time",
    }
    assert manifest["selected_count"] == 1
    assert manifest["selected_record_ids"] == ["task-1"]


def test_opt_out_before_scheduled_selection_excludes_workspace():
    records = [{"id": "task-1", "workspace_id": "workspace-a"}]
    policies = {
        "workspace-a": {
            "consents": {"training": True},
            "opt_out_at": "2026-05-24T11:59:59Z",
        }
    }

    export = build_dataset_export(
        records,
        policies,
        "training",
        SELECTION_TIME,
    )

    assert export["records"] == []
    assert export["manifest"]["exclusion_counts"]["opted_out"] == 1


def test_opt_out_after_scheduled_selection_does_not_rewrite_decision():
    records = [{"id": "task-1", "workspace_id": "workspace-a"}]
    policies = {
        "workspace-a": {
            "consents": {"training": True},
            "opt_out_at": "2026-05-24T12:00:01Z",
        }
    }

    export = build_dataset_export(
        records,
        policies,
        "training",
        SELECTION_TIME,
    )

    assert [record["id"] for record in export["records"]] == ["task-1"]
    assert export["manifest"]["exclusion_counts"]["opted_out"] == 0
