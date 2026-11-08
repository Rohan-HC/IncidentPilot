import tempfile
from pathlib import Path

import pytest

from incidentpilot.approval import ApprovalStore


def test_execution_requires_explicit_approval_and_is_idempotent(tmp_path):
    db = tmp_path / "incidentpilot.db"
    store = ApprovalStore(db)
    proposal = store.create_proposal("run-db", "db-pool-001", "increase_pool_limit", "sufficient")

    with pytest.raises(ValueError, match="approved"):
        store.execute(proposal["id"], "operator@example.test")

    store.approve(proposal["id"], actor="operator@example.test")
    first = store.execute(proposal["id"], "operator@example.test")
    reopened = ApprovalStore(db)
    second = reopened.execute(proposal["id"], "operator@example.test")

    assert first == second
    assert first["simulated"] is True
    assert first["verified"] is True
    assert first["after"]["pool_limit"] == 120
    assert [event["event"] for event in reopened.audit(proposal["id"])] == [
        "proposed",
        "approved",
        "executed",
    ]


def test_denial_blocks_execution(tmp_path):
    store = ApprovalStore(tmp_path / "incidentpilot.db")
    proposal = store.create_proposal(
        "run-deploy", "deploy-001", "rollback_deployment", "sufficient"
    )
    store.deny(proposal["id"], actor="operator@example.test")

    with pytest.raises(ValueError, match="approved"):
        store.execute(proposal["id"], "operator@example.test")
    assert store.get_proposal(proposal["id"])["status"] == "denied"


def test_approved_deployment_rollback_verifies_synthetic_error_rate(tmp_path):
    store = ApprovalStore(tmp_path / "incidentpilot.db")
    proposal = store.create_proposal(
        "run-deploy", "deploy-001", "rollback_deployment", "sufficient"
    )
    store.approve(proposal["id"], actor="operator@example.test")

    result = store.execute(proposal["id"], "operator@example.test")

    assert result["after"]["version"] == "v2.4.0"
    assert result["after"]["error_rate_percent"] < result["before"]["error_rate_percent"]
    assert result["verified"] is True


def test_unknown_action_and_weak_evidence_cannot_be_proposed(tmp_path):
    store = ApprovalStore(tmp_path / "incidentpilot.db")

    with pytest.raises(ValueError, match="allowlisted"):
        store.create_proposal("run-bad", "db-pool-001", "delete_database", "sufficient")
    with pytest.raises(ValueError, match="evidence"):
        store.create_proposal("run-weak", "db-pool-001", "increase_pool_limit", "insufficient")


def test_same_run_cannot_create_two_proposals(tmp_path):
    store = ApprovalStore(tmp_path / "incidentpilot.db")
    first = store.create_proposal("run-db", "db-pool-001", "increase_pool_limit", "sufficient")
    second = store.create_proposal("run-db", "db-pool-001", "increase_pool_limit", "sufficient")

    assert first["id"] == second["id"]
    assert len(store.audit(first["id"])) == 1


def test_transition_aware_operations_distinguish_idempotent_replays(tmp_path):
    store = ApprovalStore(tmp_path / "state.db")
    proposal = store.create_proposal(
        "replay-run", "db-pool-001", "increase_pool_limit", "sufficient"
    )

    approved, approval_changed = store.approve_with_transition(proposal["id"], "approver")
    replayed_approval, replay_changed = store.approve_with_transition(proposal["id"], "approver")
    executed, execution_changed = store.execute_with_transition(proposal["id"], "operator")
    replayed_execution, execution_replay_changed = store.execute_with_transition(
        proposal["id"], "operator"
    )

    assert approved == replayed_approval
    assert approval_changed is True
    assert replay_changed is False
    assert executed == replayed_execution
    assert execution_changed is True
    assert execution_replay_changed is False


def test_store_releases_sqlite_connections_on_windows():
    with tempfile.TemporaryDirectory() as directory:
        db = Path(directory) / "incidentpilot.db"
        store = ApprovalStore(db)
        proposal = store.create_proposal(
            "run-db", "db-pool-001", "increase_pool_limit", "sufficient"
        )
        store.get_proposal(proposal["id"])
        store.audit(proposal["id"])

    assert not db.exists()
