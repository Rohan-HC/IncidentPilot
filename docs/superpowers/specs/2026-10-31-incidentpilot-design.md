# IncidentPilot design

IncidentPilot is a local, portfolio-grade incident response simulator. It uses synthetic evidence and never touches a production service. The aim is to show a complete, inspectable agent workflow on a £0 software budget.

## Scope and constraints

- Three deterministic scenarios: database pool exhaustion, faulty deployment, and insufficient evidence.
- Python API, typed boundaries, model-selected read-only tools, later retrieval over local runbooks, and a durable approval gate before any simulated corrective action.
- Corrective actions must be named in an allowlist, explicitly approved, idempotent, logged, and followed by a verification check.
- A missing model, weak evidence, or unavailable retrieval must produce an honest blocked or insufficient-evidence state.
- Local Ollama Qwen3 4B is the only planned LLM; SQLite and local Chroma provide persistence. No paid services or production credentials.

## Approach

Use a deterministic simulator as the source of truth. Let the LLM select read-only tools and propose a diagnosis, while application code owns tool dispatch, action policy, approval, and verification. This keeps the system testable without an LLM and makes model behavior visible in the audit trail. A model-led executor would obscure safety boundaries; a purely scripted assistant would not demonstrate tool calling.

## Delivery sequence

1. Simulated incidents, FastAPI inspection endpoints, and a real ChatOllama tool-calling loop with tests.
2. Local runbook RAG, LangGraph state flow, and SQLite checkpoints.
3. Durable approvals, allowlisted simulated remediation, verification, audit history, Docker, CI, and README walkthrough.

The model can be unavailable during tests. Tests exercise dispatch with a controlled model response; a separate local smoke test exercises Qwen3 when Ollama and its model are installed.
