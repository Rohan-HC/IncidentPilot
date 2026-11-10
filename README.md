# IncidentPilot

IncidentPilot is a local, **simulated** incident response project. It is being built incrementally as an AI engineering portfolio piece. All metrics, logs, deployments, and remediation in this repo are synthetic. No production incident or operational impact is claimed.

## What it demonstrates

- Three deterministic incident fixtures: database pool exhaustion, faulty deployment, and insufficient evidence.
- FastAPI endpoints to list, inspect, investigate, analyze, approve, deny, execute, and audit an incident.
- Qwen3 4B Instruct through ChatOllama can request four named read-only tools. Python validates the request, executes a local tool, and returns the observation to the model. The model does not have direct access to arbitrary Python functions.
- `search_runbooks` embeds local Markdown runbooks with sentence-transformers `all-MiniLM-L6-v2`, indexes them in persistent ChromaDB, and returns source IDs, text, and distances.
- LangGraph runs retrieve, observe, and assess steps with SQLite checkpoints. A deterministic evidence gate requires a relevant runbook and corroborating telemetry before proposing an action; model prose cannot authorize one.
- SQLite persists proposals, explicit approval or denial, idempotent simulated execution, verification results, and audit events.

## Why the boundary matters

Tool calling means the LLM emits a structured request such as `get_metrics({"incident_id": "db-pool-001"})`. The application owns execution and decides whether that request is allowed. Here the dispatcher exposes only synthetic read-only observations and prevents a model from switching incident IDs mid-investigation. RAG retrieves candidate runbook passages; it does not prove a diagnosis. The separate policy path handles proposed corrective actions and explicit approval.

```text
FastAPI -> LangGraph retrieve -> observe -> ChatOllama/Qwen3 <-> read-only dispatcher
                                      |                 synthetic data / Chroma
                                      v
                       deterministic evidence assessment
                                      v
                      SQLite proposal -> explicit approval
                                      v
                    simulated action -> verification + audit
```

## Run locally

Use Python 3.12 for the reproducible path. Python 3.14 passed locally but some dependencies emitted deprecation warnings. Install Ollama and pull `qwen3:4b-instruct`. The plain `qwen3:4b` tag points to a thinking variant that produced a truncated answer without tool calls in our local check.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e '.[dev,rag,graph]'
ollama pull qwen3:4b-instruct
.\.venv\Scripts\python -m uvicorn incidentpilot.api:app --reload
```

Open `http://127.0.0.1:8000/docs` for the API. The first RAG call downloads the free MiniLM model; later calls use the local cache. Docker users can run `docker compose up --build` with Ollama on the host.

### Guided demo

```powershell
$incidents = Invoke-RestMethod http://127.0.0.1:8000/incidents
$analysis = Invoke-RestMethod -Method Post http://127.0.0.1:8000/incidents/db-pool-001/analyze
$analysis | ConvertTo-Json -Depth 8

# Before approval, this returns HTTP 409.
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/proposals/$($analysis.proposal.id)/execute"

Invoke-RestMethod -Method Post "http://127.0.0.1:8000/proposals/$($analysis.proposal.id)/approve" -ContentType application/json -Body '{"actor":"demo-operator"}'
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/proposals/$($analysis.proposal.id)/execute"
Invoke-RestMethod "http://127.0.0.1:8000/proposals/$($analysis.proposal.id)/audit"
Invoke-RestMethod "http://127.0.0.1:8000/runs/$($analysis.run_id)"

# No corroborating evidence means no proposal.
Invoke-RestMethod -Method Post http://127.0.0.1:8000/incidents/unknown-001/analyze
```

`POST /incidents/{id}/investigate` exposes the direct model/tool loop. `POST /proposals/{id}/deny` records a denial instead of approval. `GET /proposals/{id}` returns the durable state.

Run tests with `.\.venv\Scripts\python -m pytest -q`. Tests use controlled model responses and a tiny deterministic embedding model, so CI needs no Ollama or model download. They cover tool dispatch, Chroma persistence, LangGraph checkpoints, insufficient evidence, approval, denial, idempotency, and the API flow.

On 31 October 2026, a live `qwen3:4b-instruct` investigation of `db-pool-001` requested `get_metrics`, `get_logs`, and `get_recent_deployments` and received their synthetic results. The full live API path reached a pending proposal, explicit approval, and verified simulated execution. A later RAG-plus-Qwen3 smoke test retrieved `db-pool.md` and produced a proposal only after the evidence gate saw that source. `pip check` found no broken requirements.

## GitHub Actions CI and container delivery

[![CI and Container Delivery](https://github.com/Rohan-HC/IncidentPilot/actions/workflows/tests.yml/badge.svg)](https://github.com/Rohan-HC/IncidentPilot/actions/workflows/tests.yml)

The [CI workflow](.github/workflows/tests.yml) runs on pull requests and pushes to `main`, and can be started manually from the Actions tab.

1. **Python tests:** Install Python 3.12 with project extras and execute pytest. Scripted model responses and deterministic embeddings mean **no Ollama, API key, or model download is required**.
2. **Container verification:** After pytest passes, build the CPU-only Docker image and run a health-check smoke test against `GET /health`.
3. **Image publishing:** Only a successful **push to `main`** uploads the verified image to GitHub Container Registry (GHCR). Pull requests build/test but never publish.

Published tags:
- `ghcr.io/rohan-hc/incidentpilot:latest`
- `ghcr.io/rohan-hc/incidentpilot:sha-<first-seven-commit-characters>`

The workflow uses GitHub's built-in `GITHUB_TOKEN` with scoped `packages: write` permission; no personal token or paid CI service is needed. Newly created GHCR packages are **private by default**. To allow public pulls, go to your GitHub profile's Packages, select `incidentpilot`, open **Package settings**, and change its visibility to **Public**.

To run a published image on a machine with local Ollama and `qwen3:4b-instruct` installed:

```bash
docker pull ghcr.io/rohan-hc/incidentpilot:latest
docker run --rm --name incidentpilot -p 8000:8000 \
  --add-host=host.docker.internal:host-gateway \
  -e OLLAMA_HOST=http://host.docker.internal:11434 \
  -e INCIDENTPILOT_DB_PATH=/app/data/incidentpilot.db \
  -e INCIDENTPILOT_CHROMA_PATH=/app/data/chroma \
  -e HF_HOME=/app/data/huggingface \
  -v incidentpilot-data:/app/data \
  ghcr.io/rohan-hc/incidentpilot:latest
```

The health endpoint can respond without Ollama; **analysis and remediation proposals require a running Ollama model**. Publishing an image is continuous delivery, **not** a public hosted production deployment. This app is intentionally a single-user simulated local system with no authentication; do not expose it publicly.

## Limitations and honest portfolio wording

- The LLM can make unsupported inferences. In one live run it asserted high request volume without a request-volume metric. The deterministic gate limits action proposals; the prose still needs human review.
- Qwen3 sometimes emits a malformed tool call. The graph records this as a durable `blocked` run with no proposal; retry the analysis after checking Ollama.
- This is a single-user local demo. The `actor` field is self-declared, not authenticated. SQLite audit rows are durable but not tamper-evident. Do not expose the API to untrusted users.
- Remediation and verification are fixed simulation outcomes. No database pool, deployment, or live service changes.
- The rules and runbooks cover three examples, not a general incident response policy. The Chroma relevance threshold is a demo heuristic, not a calibrated retrieval evaluation.
- Docker Compose was verified locally: the image built successfully, the API returned 200 from `/health`, all three incidents were listed, and the container reached host Ollama. A complete database-pool analysis also produced a pending proposal; pre-approval execution returned 409, approval and simulated execution succeeded, verification was true, repeated execution was idempotent, and audit events persisted. The original GitHub-hosted Python CI run passed on 1 November 2026; the expanded Docker build, smoke test, and GHCR publish jobs must be validated separately.

Suitable résumé language: **“Built a local simulated incident-response agent with Ollama tool calling, Chroma runbook retrieval, LangGraph/SQLite state, and approval-gated, idempotent simulated remediation.”**

