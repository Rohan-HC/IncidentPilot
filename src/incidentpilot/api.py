from pathlib import Path
import os
from typing import Annotated, Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import httpx
from pydantic import BaseModel, Field

from incidentpilot.agent import investigate
from incidentpilot.approval import ApprovalStore
from incidentpilot.auth import AuthConfig, AuthService, Role, UserRecord
from incidentpilot.models import Incident, Investigation
from incidentpilot.simulator import get_incident, list_incidents


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


def create_app(
    model: Any | None = None,
    database_path: str | Path | None = None,
    runbook_index: Any | None = None,
    auth_config: AuthConfig | None = None,
) -> FastAPI:
    app = FastAPI(title="IncidentPilot", description="Synthetic incident response only")
    db_path = Path(database_path or os.environ.get("INCIDENTPILOT_DB_PATH", "data/incidentpilot.db"))
    store = ApprovalStore(db_path)
    auth = AuthService(auth_config if auth_config is not None else AuthConfig.from_env())
    bearer = HTTPBearer(auto_error=False)

    def current_user(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> UserRecord:
        if credentials is None:
            raise HTTPException(
                status_code=401,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        user = auth.verify_token(credentials.credentials)
        if user is None:
            raise HTTPException(
                status_code=401,
                detail="Invalid or expired credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user

    def require_roles(*roles: Role):
        def dependency(user: Annotated[UserRecord, Depends(current_user)]) -> UserRecord:
            if user.role not in roles:
                raise HTTPException(status_code=403, detail="Operation not permitted")
            return user

        return dependency

    def proposal_or_404(proposal_id: str) -> dict[str, Any]:
        try:
            return store.get_proposal(proposal_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Proposal not found") from None

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/auth/token")
    def token(request: LoginRequest) -> dict[str, Any]:
        if auth.config is None:
            raise HTTPException(status_code=503, detail="Authentication unavailable")
        user = auth.authenticate(request.username, request.password)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        return {
            "access_token": auth.issue_token(user),
            "token_type": "bearer",
            "expires_in": auth.config.token_ttl_seconds,
        }

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
    def run_investigation(
        incident_id: str,
        _: Annotated[UserRecord, Depends(require_roles(Role.OPERATOR))],
    ) -> Investigation:
        try:
            item = get_incident(incident_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Incident not found") from None
        try:
            return investigate(item, model)
        except (ImportError, ConnectionError, OSError, httpx.HTTPError) as exc:
            raise HTTPException(status_code=503, detail=f"Local model unavailable: {exc}") from exc

    @app.post("/incidents/{incident_id}/analyze")
    def analyze(
        incident_id: str,
        _: Annotated[UserRecord, Depends(require_roles(Role.OPERATOR))],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        try:
            get_incident(incident_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Incident not found") from None
        from langgraph.checkpoint.sqlite import SqliteSaver
        from incidentpilot.workflow import build_graph

        if idempotency_key is not None and (not idempotency_key.strip() or len(idempotency_key) > 128):
            raise HTTPException(status_code=400, detail="Invalid idempotency key")
        run_id = (
            str(uuid5(NAMESPACE_URL, f"incidentpilot:{incident_id}:{idempotency_key}"))
            if idempotency_key is not None
            else str(uuid4())
        )
        try:
            with SqliteSaver.from_conn_string(str(db_path)) as saver:
                graph = build_graph(model, saver, runbook_index)
                config = {"configurable": {"thread_id": run_id}}
                checkpoint = graph.get_state(config)
                if checkpoint.values and checkpoint.values.get("status") in {
                    "completed", "insufficient_evidence", "blocked", "failed"
                }:
                    state = checkpoint.values
                else:
                    state = graph.invoke(
                        {
                            "incident_id": incident_id,
                            "status": "pending",
                            "retry_count": 0,
                            "transitions": [],
                        },
                        config,
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
    def get_run(
        run_id: str,
        _: Annotated[UserRecord, Depends(require_roles(Role.VIEWER, Role.OPERATOR, Role.APPROVER))],
    ) -> dict[str, Any]:
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
    def get_proposal(
        proposal_id: str,
        _: Annotated[UserRecord, Depends(require_roles(Role.VIEWER, Role.OPERATOR, Role.APPROVER))],
    ) -> dict[str, Any]:
        return proposal_or_404(proposal_id)

    @app.post("/proposals/{proposal_id}/approve")
    def approve(
        proposal_id: str,
        user: Annotated[UserRecord, Depends(require_roles(Role.APPROVER))],
    ) -> dict[str, Any]:
        proposal_or_404(proposal_id)
        try:
            return store.approve(proposal_id, user.username)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="Proposal state conflict") from exc

    @app.post("/proposals/{proposal_id}/deny")
    def deny(
        proposal_id: str,
        user: Annotated[UserRecord, Depends(require_roles(Role.APPROVER))],
    ) -> dict[str, Any]:
        proposal_or_404(proposal_id)
        try:
            return store.deny(proposal_id, user.username)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="Proposal state conflict") from exc

    @app.post("/proposals/{proposal_id}/execute")
    def execute(
        proposal_id: str,
        user: Annotated[UserRecord, Depends(require_roles(Role.OPERATOR))],
    ) -> dict[str, Any]:
        proposal_or_404(proposal_id)
        try:
            return store.execute(proposal_id, user.username)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="Proposal state conflict") from exc

    @app.get("/proposals/{proposal_id}/audit")
    def audit(
        proposal_id: str,
        _: Annotated[UserRecord, Depends(require_roles(Role.VIEWER, Role.OPERATOR, Role.APPROVER))],
    ) -> list[dict[str, Any]]:
        proposal_or_404(proposal_id)
        return store.audit(proposal_id)

    return app


app = create_app()
