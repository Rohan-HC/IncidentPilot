# IncidentPilot Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Build a local, tested incident response agent with synthetic evidence and safe simulated remediation.

**Architecture:** A deterministic simulator supplies evidence; a model may choose read-only tools. LangGraph later coordinates diagnosis and approval, while SQLite persists incidents, approvals, and audits. Application policy, rather than model output, authorizes actions.

**Tech Stack:** Python, FastAPI, Pydantic, LangChain ChatOllama, LangGraph, ChromaDB, sentence-transformers, SQLite, pytest, Docker, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-31-incidentpilot-design.md`

## Global constraints

- £0 budget; local services only.
- All incident evidence and remediation are simulated.
- Four read-only tools; corrective actions require an allowlist, explicit approval, idempotency, and verification.

## Review focus

- Unknown incident IDs return 404.
- Unknown model-selected tools never execute.
- A model that does not call tools does not get fabricated evidence.
- Insufficient evidence does not produce an automatic corrective action.
- Repeat approval or execution cannot apply an action twice.

## Tasks

### 1. Simulator and API

- [x] Write tests for the three scenarios, known evidence, and unknown IDs.
- [x] Implement typed scenario fixtures and `/health`, `/incidents`, and `/incidents/{id}`.
- [x] Run the full test suite.

### 2. Model-selected read-only tools

- [x] Write tests where a controlled model requests metrics and logs and receives their actual tool results, plus an unknown-tool rejection.
- [x] Implement four named dispatch targets and a bounded ChatOllama tool-calling loop.
- [x] Add `/incidents/{incident_id}/investigate` and run the full suite.

### 3. Retrieval and workflow

- [x] Test runbook retrieval, including irrelevant and missing matches.
- [x] Add local embeddings/Chroma and LangGraph nodes with durable checkpointing.
- [x] Test evidence-limited and model-unavailable paths.

### 4. Approval and remediation

- [x] Test request, approval, denial, idempotent execution, and post-action verification across process restarts.
- [x] Implement SQLite policy/audit store and simulated actions.
- [x] Run the full suite.

### 5. Delivery

- [x] Add Docker, CI, and a truthful README with setup, architecture, demo commands, limitations, and interview talking points.
- [x] Run tests and local smoke checks, then review the diff. Docker Compose configuration parsed; the image build awaits a running Docker engine.
