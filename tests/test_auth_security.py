from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from incidentpilot.api import create_app
from incidentpilot.approval import ApprovalStore
from incidentpilot.auth import AuthConfig, AuthService, Role, UserRecord


@pytest.fixture(scope="module")
def auth_setup():
    service = AuthService.for_testing(
        {
            "viewer": ("viewer-password", Role.VIEWER),
            "operator": ("operator-password", Role.OPERATOR),
            "approver": ("approver-password", Role.APPROVER),
        }
    )
    return service.config


def login(client, username, password):
    response = client.post("/auth/token", json={"username": username, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def create_pending(store):
    return store.create_proposal("secure-run", "db-pool-001", "increase_pool_limit", "sufficient")


def test_passwords_are_argon2_hashed_and_login_uses_verified_identity(auth_setup, tmp_path):
    assert auth_setup.users["approver"].password_hash.startswith("$argon2")
    client = TestClient(create_app(database_path=tmp_path / "state.db", auth_config=auth_setup))

    invalid = client.post("/auth/token", json={"username": "approver", "password": "wrong"})
    valid = client.post(
        "/auth/token", json={"username": "approver", "password": "approver-password"}
    )

    assert invalid.status_code == 401
    assert invalid.json()["detail"] == "Invalid credentials"
    assert valid.status_code == 200
    assert valid.json()["token_type"] == "bearer"


def test_malformed_configured_password_hash_fails_authentication_safely(tmp_path):
    config = AuthConfig(
        secret="x" * 32,
        users={
            "operator": UserRecord(
                username="operator", password_hash="not-an-argon-hash", role=Role.OPERATOR
            )
        },
    )
    client = TestClient(create_app(database_path=tmp_path / "state.db", auth_config=config))

    response = client.post(
        "/auth/token", json={"username": "operator", "password": "operator-password"}
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid credentials"


def test_unauthenticated_and_wrong_role_approval_are_rejected(auth_setup, tmp_path):
    store = ApprovalStore(tmp_path / "state.db")
    proposal = create_pending(store)
    client = TestClient(create_app(database_path=tmp_path / "state.db", auth_config=auth_setup))

    assert client.post(f"/proposals/{proposal['id']}/approve").status_code == 401
    viewer = login(client, "viewer", "viewer-password")
    assert client.post(f"/proposals/{proposal['id']}/approve", headers=viewer).status_code == 403


def test_actor_is_derived_from_token_and_client_forgery_is_ignored(auth_setup, tmp_path):
    store = ApprovalStore(tmp_path / "state.db")
    proposal = create_pending(store)
    client = TestClient(create_app(database_path=tmp_path / "state.db", auth_config=auth_setup))
    approver = login(client, "approver", "approver-password")

    response = client.post(
        f"/proposals/{proposal['id']}/approve",
        headers=approver,
        json={"actor": "forged-admin"},
    )

    assert response.status_code == 200
    assert response.json()["actor"] == "approver"
    assert store.audit(proposal["id"])[-1]["actor"] == "approver"


def test_invalid_and_expired_tokens_are_rejected(auth_setup, tmp_path):
    service = AuthService(auth_setup)
    expired = service.issue_token(
        UserRecord(
            username="viewer",
            password_hash=auth_setup.users["viewer"].password_hash,
            role=Role.VIEWER,
        ),
        expires_delta=timedelta(seconds=-1),
    )
    client = TestClient(create_app(database_path=tmp_path / "state.db", auth_config=auth_setup))

    assert (
        client.get("/runs/missing", headers={"Authorization": "Bearer forged"}).status_code == 401
    )
    assert (
        client.get("/runs/missing", headers={"Authorization": f"Bearer {expired}"}).status_code
        == 401
    )


def test_investigation_and_audit_require_authorized_access(auth_setup, tmp_path):
    store = ApprovalStore(tmp_path / "state.db")
    proposal = create_pending(store)
    client = TestClient(create_app(database_path=tmp_path / "state.db", auth_config=auth_setup))
    viewer = login(client, "viewer", "viewer-password")

    assert client.post("/incidents/db-pool-001/analyze", headers=viewer).status_code == 403
    assert client.get(f"/proposals/{proposal['id']}/audit").status_code == 401
    assert client.get(f"/proposals/{proposal['id']}/audit", headers=viewer).status_code == 200


def test_operator_cannot_execute_without_approval(auth_setup, tmp_path):
    store = ApprovalStore(tmp_path / "state.db")
    proposal = create_pending(store)
    client = TestClient(create_app(database_path=tmp_path / "state.db", auth_config=auth_setup))
    operator = login(client, "operator", "operator-password")

    response = client.post(f"/proposals/{proposal['id']}/execute", headers=operator)

    assert response.status_code == 409
    assert store.get_proposal(proposal["id"])["status"] == "pending"


def test_concurrent_approval_and_denial_allow_one_terminal_decision(tmp_path):
    store = ApprovalStore(tmp_path / "state.db")
    proposal = create_pending(store)

    def decide(action):
        try:
            return action(proposal["id"], "approver")["status"]
        except ValueError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(decide, [store.approve, store.deny]))

    assert sorted(outcomes) in (["approved", "conflict"], ["conflict", "denied"])
    assert [event["event"] for event in store.audit(proposal["id"])] in (
        ["proposed", "approved"],
        ["proposed", "denied"],
    )


def test_simultaneous_execution_is_idempotent_and_audited_once(tmp_path):
    store = ApprovalStore(tmp_path / "state.db")
    proposal = create_pending(store)
    store.approve(proposal["id"], "approver")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: store.execute(proposal["id"], "operator"), range(2)))

    assert results[0] == results[1]
    assert [event["event"] for event in store.audit(proposal["id"])].count("executed") == 1


@pytest.mark.parametrize(("first", "second"), [("approve", "deny"), ("deny", "approve")])
def test_conflicting_decision_after_terminal_state_is_rejected(tmp_path, first, second):
    store = ApprovalStore(tmp_path / "state.db")
    proposal = create_pending(store)
    getattr(store, first)(proposal["id"], "approver")

    with pytest.raises(ValueError, match="no longer pending"):
        getattr(store, second)(proposal["id"], "approver")
