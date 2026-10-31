# IncidentPilot

IncidentPilot is a local, **simulated** incident response project. It is being built incrementally as an AI engineering portfolio piece. All metrics, logs, deployments, and remediation in this repo are synthetic. No production incident or operational impact is claimed.

## Current working slice

- Three deterministic incident fixtures: database pool exhaustion, faulty deployment, and insufficient evidence.
- FastAPI endpoints to list, inspect, and investigate an incident.
- Qwen3 4B Instruct through ChatOllama can request four named read-only tools. Python validates the request, executes a local tool, and returns the observation to the model. The model does not have direct access to arbitrary Python functions.
- Runbook search is currently a small keyword baseline. ChromaDB and sentence-transformer retrieval are the next slice.

## Why the boundary matters

Tool calling means the LLM emits a structured request such as `get_metrics({"incident_id": "db-pool-001"})`. The application owns execution and decides whether that request is allowed. Here the dispatcher exposes only synthetic read-only observations and prevents a model from switching incident IDs mid-investigation. Later, a separate policy path will handle proposed corrective actions and human approval.

```text
HTTP request -> FastAPI -> investigation loop -> ChatOllama/Qwen3
                                    |                 |
                                    +<- tool call ----+
                                    |
                          allowlisted read-only dispatcher
                                    |
                          synthetic incident fixtures
```

## Run locally

Use Python 3.11–3.14. Ollama and `qwen3:4b-instruct` are needed only for live investigations. The plain `qwen3:4b` tag currently points to a thinking variant that did not reliably call tools in our local check.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e '.[dev]'
ollama pull qwen3:4b-instruct
.\.venv\Scripts\python -m uvicorn incidentpilot.api:app --reload
```

Open `http://127.0.0.1:8000/docs` for the API. Example requests:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/incidents
Invoke-RestMethod -Method Post http://127.0.0.1:8000/incidents/db-pool-001/investigate
```

Run tests with `.\.venv\Scripts\python -m pytest -q`. The tests use controlled model responses to validate dispatch without requiring Ollama. That is an integration test of the tool loop, **not** a claim that the local model selected tools correctly; a live smoke test checks that separately.

On 31 October 2026, a live `qwen3:4b-instruct` investigation of `db-pool-001` requested `get_metrics`, `get_logs`, and `get_recent_deployments` and received their synthetic results. The answer also inferred high request volume without supporting metrics. Treat model prose as a hypothesis until the evidence-validation layer is in place; never use it to authorize an action.

## Next slices

1. Move runbooks into ChromaDB with local sentence-transformer embeddings and cite retrieved passages.
2. Add LangGraph workflow and durable SQLite state.
3. Add explicit human approval, allowlisted simulated remediation, idempotency keys, verification, and audit history.
4. Add Docker, GitHub Actions, and a complete demo walkthrough.

The [design](docs/superpowers/specs/2026-10-31-incidentpilot-design.md) and [implementation plan](docs/superpowers/plans/2026-10-31-incidentpilot.md) record the intended build and safety properties.
