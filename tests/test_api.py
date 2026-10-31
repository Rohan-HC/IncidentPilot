from fastapi.testclient import TestClient

from incidentpilot.api import create_app


def test_lists_three_simulated_incidents():
    client = TestClient(create_app())

    response = client.get("/incidents")

    assert response.status_code == 200
    assert {item["id"] for item in response.json()} == {
        "db-pool-001", "deploy-001", "unknown-001"
    }
    assert all(item["simulated"] is True for item in response.json())


def test_unknown_incident_returns_404():
    client = TestClient(create_app())

    assert client.get("/incidents/missing").status_code == 404


def test_incident_exposes_synthetic_evidence():
    client = TestClient(create_app())

    response = client.get("/incidents/db-pool-001")

    assert response.status_code == 200
    assert response.json()["title"] == "Database connection pool exhaustion"
    assert response.json()["simulated"] is True


def test_investigate_uses_injected_model():
    from langchain_core.messages import AIMessage

    class Model:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            return AIMessage(content="Evidence is insufficient to recommend a change.")

    client = TestClient(create_app(model=Model()))
    response = client.post("/incidents/unknown-001/investigate")

    assert response.status_code == 200
    assert response.json()["answer"] == "Evidence is insufficient to recommend a change."
    assert response.json()["tool_results"] == []
