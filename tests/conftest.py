import pytest

from incidentpilot.auth import AuthService, Role


@pytest.fixture(scope="session")
def auth_config():
    return AuthService.for_testing({
        "test-viewer": ("viewer-test-password", Role.VIEWER),
        "test-operator": ("operator-test-password", Role.OPERATOR),
        "test-approver": ("approver-test-password", Role.APPROVER),
    }).config


@pytest.fixture
def auth_headers():
    credentials = {
        Role.VIEWER: ("test-viewer", "viewer-test-password"),
        Role.OPERATOR: ("test-operator", "operator-test-password"),
        Role.APPROVER: ("test-approver", "approver-test-password"),
    }

    def get(client, role=Role.OPERATOR):
        username, password = credentials[role]
        response = client.post("/auth/token", json={"username": username, "password": password})
        assert response.status_code == 200
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return get
