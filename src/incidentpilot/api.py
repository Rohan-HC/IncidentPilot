from pathlib import Path
import os
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
import httpx
from pydantic import BaseModel, Field

from incidentpilot.agent import investigate
from incidentpilot.approval import ApprovalStore
from incidentpilot.models import Incident, Investigation
from incidentpilot.simulator import get_incident, list_incidents


class Decision(BaseModel):
    actor: str = Field(min_length=1)


def create_app(model: Any | None = None, database_path: str | Path | None = None, runbook_index: Any | None = None) -> FastAPI:
    app = FastAPI(title="IncidentPilot", description="Synthetic incident response only")
    db_path = Path(database_path or os.environ.get("INCIDENTPILOT_DB_PATH", "data/incidentpilot.db"))
    store = ApprovalStore(db_path)

    def proposal_or_404(proposal_id: str) -> dict[str, Any]:
        try:
            return store.get_proposal(proposal_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Proposal not found") from None

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/incidents", response_model=list[Incident])
    def incidents() -> list[Incident]:
        return list_incidents()

    @app.get("/incidents/{incident_id}", response_model=Incident)
    def incident(incident_id: str) -> Incident:
        try:
            return get_incident(incident_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Incident not found") from None

    @app.post("/incidents/{incident_id}/investigate", response_model=Investigation)
    def run_investigation(incident_id: str) -> Investigation:
        try:
            item = get_incident(incident_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Incident not found") from None
        try:
            return investigate(item, model)
        except (ImportError, ConnectionError, OSError, httpx.HTTPError) as exc:
            raise HTTPException(status_code=503, detail=f"Local model unavailable: {exc}") from exc

    @app.post("/incidents/{incident_id}/analyze")
    def analyze(incident_id: str) -> dict[str, Any]:
        try:
            get_incident(incident_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Incident not found") from None
        from langgraph.checkpoint.sqlite import SqliteSaver
        from incidentpilot.workflow import build_graph

        run_id = str(uuid4())
        try:
            with SqliteSaver.from_conn_string(str(db_path)) as saver:
                state = build_graph(model, saver, runbook_index).invoke(
                    {"incident_id": incident_id}, {"configurable": {"thread_id": run_id}}
                )
        except (ImportError, ConnectionError, OSError, httpx.HTTPError) as exc:
            raise HTTPException(status_code=503, detail=f"Local model unavailable: {exc}") from exc
        action = state["proposed_action"]
        proposal = (
            store.create_proposal(run_id, incident_id, action, state["evidence_status"])
            if action else None
        )
        return {"run_id": run_id, **state, "proposal": proposal}

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        from langgraph.checkpoint.sqlite import SqliteSaver
        from incidentpilot.workflow import build_graph

        with SqliteSaver.from_conn_string(str(db_path)) as saver:
            state = build_graph(model, saver, runbook_index).get_state(
                {"configurable": {"thread_id": run_id}}
            )
        if not state.values:
            raise HTTPException(status_code=404, detail="Run not found")
        return state.values

    @app.get("/proposals/{proposal_id}")
    def get_proposal(proposal_id: str) -> dict[str, Any]:
        return proposal_or_404(proposal_id)

    @app.post("/proposals/{proposal_id}/approve")
    def approve(proposal_id: str, decision: Decision) -> dict[str, Any]:
        proposal_or_404(proposal_id)
        try:
            return store.approve(proposal_id, decision.actor)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/proposals/{proposal_id}/deny")
    def deny(proposal_id: str, decision: Decision) -> dict[str, Any]:
        proposal_or_404(proposal_id)
        try:
            return store.deny(proposal_id, decision.actor)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/proposals/{proposal_id}/execute")
    def execute(proposal_id: str) -> dict[str, Any]:
        proposal_or_404(proposal_id)
        try:
            return store.execute(proposal_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/proposals/{proposal_id}/audit")
    def audit(proposal_id: str) -> list[dict[str, Any]]:
        proposal_or_404(proposal_id)
        return store.audit(proposal_id)

    return app


app = create_app()
