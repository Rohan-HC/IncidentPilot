# IncidentPilot

IncidentPilot is a local, simulated incident-response agent built with FastAPI, LangGraph,
Ollama, SentenceTransformers, and SQLite. It demonstrates defensive LLM tool calling, deterministic
evidence gates, checkpointed workflow recovery, authenticated human approval, idempotent
simulated remediation, structured observability, and reproducible agent evaluation.

All incidents, telemetry, deployments, actions, and verification outcomes are synthetic.
IncidentPilot has no production infrastructure integrations or remediation credentials.

## Architecture

```text
FastAPI + JWT roles
        |
        v
LangGraph: retrieve -> investigate -> deterministic evidence assessment
        |                    |
        |                    +-> Ollama/Qwen3 <-> allowlisted read-only tools
        |                                         (synthetic fixtures only)
        +-> SQLite vector runbooks
        +-> SQLite checkpoints
        v
SQLite proposal -> authenticated approval/denial -> simulated execution -> audit
```

The model may select four read-only tools: `get_metrics`, `get_logs`,
`get_recent_deployments`, and `search_runbooks`. Server code validates every tool name,
call ID, argument schema, incident ID, size, round count, and call count. Model prose and
retrieved text cannot authorize an action; only deterministic evidence rules can create an
allowlisted proposal.

## Local setup

Python 3.12 is the supported development and CI version.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e '.[dev,rag,graph]'
ollama pull qwen3:4b-instruct
```

Create a signing secret and Argon2 password hashes interactively. These commands print
configuration values but never write plaintext passwords:

```powershell
.\.venv\Scripts\incidentpilot generate-secret
.\.venv\Scripts\incidentpilot create-user local-viewer --role viewer
.\.venv\Scripts\incidentpilot create-user local-operator --role operator
.\.venv\Scripts\incidentpilot create-user local-approver --role approver
```

Set `INCIDENTPILOT_AUTH_SECRET` to the generated secret. Merge the generated user records
into one JSON object and set it as `INCIDENTPILOT_USERS_JSON`. Do not commit populated
values. `.env.example` lists all supported settings.

```powershell
$env:INCIDENTPILOT_AUTH_SECRET = '<generated-secret>'
$env:INCIDENTPILOT_USERS_JSON = '{"local-operator":{"password_hash":"<argon2-hash>","role":"operator"}}'
.\.venv\Scripts\python -m uvicorn incidentpilot.api:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/docs`. Readiness intentionally returns HTTP 503 until valid
authentication configuration and SQLite storage are available; liveness remains independent.

## Authenticated workflow

Obtain a short-lived bearer token from `POST /auth/token` with JSON credentials. Roles are:

- `viewer`: read runs, proposals, audits, and local operational counters.
- `operator`: investigate, analyze, and execute an approved simulated action.
- `approver`: approve or deny a pending proposal.

The complete flow is:

1. `POST /incidents/{incident_id}/analyze` with an operator token and optional
   `Idempotency-Key` header.
2. Review the returned evidence, terminal status, and pending proposal.
3. `POST /proposals/{proposal_id}/approve` with an approver token.
4. `POST /proposals/{proposal_id}/execute` with an operator token.
5. Inspect `GET /proposals/{proposal_id}/audit` and `GET /runs/{run_id}`.

Denial, insufficient evidence, blocked investigations, direct execution, conflicting decisions,
and non-allowlisted actions cannot execute. Repeated execution returns the persisted result and
does not create a second simulated action.

## Evaluation

`evals/datasets/v1.json` is a versioned synthetic scorer-validation dataset covering correct
investigations, partial and contradictory evidence, retrieval problems, tool-protocol failures,
cross-incident attempts, prompt injection, unauthorized operations, duplicate calls, model
failure, and incorrect recommendations.

```powershell
.\.venv\Scripts\python -m incidentpilot.evaluation `
  --mode synthetic_fixture `
  --dataset evals/datasets/v1.json `
  --output evals/results/local-fixture.json
```

Fixture scores validate metric calculations; they are not model reliability measurements.
Optional live local inference uses only the three synthetic incident scenarios:

```powershell
.\.venv\Scripts\python -m incidentpilot.evaluation `
  --mode live_local `
  --model qwen3:4b-instruct `
  --output evals/results/local-live.json
```

Generated evaluation outputs are ignored by Git.

## Verification

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m ruff format --check .
.\.venv\Scripts\python -m pip check
.\.venv\Scripts\python -m pip_audit
docker build -t incidentpilot:local .
```

Tests use scripted model responses and deterministic embeddings; CI does not need Ollama or a
paid API. GitHub Actions runs formatting, static analysis, dependency checks, tests, a Docker
build, and a liveness smoke test. Images publish to GHCR only after a successful push to `main`.

Docker Compose binds the API to `127.0.0.1:8000`, requires authentication variables, and stores
SQLite and local model data in a named volume:

```bash
docker compose up --build
```

## Operational endpoints

- `GET /health/live`: process liveness; no dependency checks.
- `GET /health/ready`: SQLite and authentication readiness.
- `GET /ops/metrics`: authenticated local counters.
- Every response includes `X-Correlation-ID`; callers may supply a safe ID in the request.
- JSON logs include correlation/investigation IDs, durations, transitions, and safe failure
  categories. Credentials, tokens, prompts, and runbook text are excluded.

## Security and reliability limits

- Local HMAC JWTs are suitable for this single-service demonstration, not centralized identity,
  revocation, or enterprise tenancy.
- SQLite transactions serialize approval, denial, and execution; this is not a distributed lock.
- Audit records are append-only through the API but are not cryptographically tamper-evident
  against a host administrator.
- Agent timeouts stop waiting for a model call; Python cannot forcibly terminate an already
  running provider thread.
- Vector relevance thresholds and deterministic evidence rules cover the included synthetic
  scenarios only.
- Live Ollama outputs vary by model/runtime version and hardware. Only explicitly recorded live
  evaluation output should be described as live-model behavior.
- Do not expose this demonstration publicly or connect its simulated actions to real systems.
