import logging
import os
import re
from pathlib import Path
from time import perf_counter
from typing import Annotated, Any
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from incidentpilot.agent import investigate
from incidentpilot.approval import ApprovalStore
from incidentpilot.auth import AuthConfig, AuthService, Role, UserRecord
from incidentpilot.models import Incident, Investigation
from incidentpilot.observability import (
    COUNTERS,
    bind_context,
    configure_logging,
    log_event,
)
from incidentpilot.simulator import get_incident, list_incidents


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


logger = logging.getLogger("incidentpilot")
_CORRELATION_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def create_app(
    model: Any | None = None,
    database_path: str | Path | None = None,
    runbook_index: Any | None = None,
    auth_config: AuthConfig | None = None,
) -> FastAPI:
    configure_logging()
    app = FastAPI(title="IncidentPilot", description="Synthetic incident response only")
    db_path = Path(
        database_path or os.environ.get("INCIDENTPILOT_DB_PATH", "data/incidentpilot.db")
    )
    store = ApprovalStore(db_path)
    auth = AuthService(auth_config if auth_config is not None else AuthConfig.from_env())
    bearer = HTTPBearer(auto_error=False)

    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next):
        supplied = request.headers.get("X-Correlation-ID", "")
        correlation_id = supplied if _CORRELATION_ID.fullmatch(supplied) else str(uuid4())
        started = perf_counter()
        COUNTERS.increment("http_requests_total")
        with bind_context(correlation_id=correlation_id):
            try:
                response = await call_next(request)
            except Exception:
                COUNTERS.increment("http_errors_total")
                log_event(
                    logger,
                    "request_failed",
                    method=request.method,
                    path=request.url.path,
                    duration_ms=round((perf_counter() - started) * 1000, 3),
                )
                raise
            if response.status_code >= 500:
                COUNTERS.increment("http_errors_total")
            log_event(
                logger,
                "request_completed",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=round((perf_counter() - started) * 1000, 3),
            )
            response.headers["X-Correlation-ID"] = correlation_id
            return response

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

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        dependencies = {
            "sqlite": "ok" if store.check_connection() else "unavailable",
            "authentication": "ok" if auth.config is not None else "unavailable",
        }
        ready_status = all(value == "ok" for value in dependencies.values())
        payload = {
            "status": "ready" if ready_status else "not_ready",
            "scope": "control_plane",
            "dependencies": dependencies,
        }
        return payload if ready_status else JSONResponse(status_code=503, content=payload)

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
            with bind_context(investigation_id=f"direct:{incident_id}"):
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

        if idempotency_key is not None and (
            not idempotency_key.strip() or len(idempotency_key) > 128
        ):
            raise HTTPException(status_code=400, detail="Invalid idempotency key")
        run_id = (
            str(uuid5(NAMESPACE_URL, f"incidentpilot:{incident_id}:{idempotency_key}"))
            if idempotency_key is not None
            else str(uuid4())
        )
        try:
            with (
                bind_context(investigation_id=run_id),
                SqliteSaver.from_conn_string(str(db_path)) as saver,
            ):
                graph = build_graph(model, saver, runbook_index)
                config = {"configurable": {"thread_id": run_id}}
                checkpoint = graph.get_state(config)
                terminal_statuses = {
                    "completed",
                    "insufficient_evidence",
                    "blocked",
                    "failed",
                }
                if checkpoint.values and checkpoint.values.get("status") in {
                    *terminal_statuses,
                }:
                    state = checkpoint.values
                elif checkpoint.values:
                    if checkpoint.values.get("incident_id") != incident_id:
                        raise HTTPException(status_code=409, detail="Checkpoint state conflict")
                    if not checkpoint.next:
                        raise HTTPException(status_code=409, detail="Checkpoint cannot be resumed")
                    state = graph.invoke(None, config)
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
            if action
            else None
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
            result, changed = store.approve_with_transition(proposal_id, user.username)
            if changed:
                COUNTERS.increment("proposals_approved_total")
                log_event(logger, "proposal_approved", proposal_id=proposal_id, actor=user.username)
            return result
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="Proposal state conflict") from exc

    @app.post("/proposals/{proposal_id}/deny")
    def deny(
        proposal_id: str,
        user: Annotated[UserRecord, Depends(require_roles(Role.APPROVER))],
    ) -> dict[str, Any]:
        proposal_or_404(proposal_id)
        try:
            result, changed = store.deny_with_transition(proposal_id, user.username)
            if changed:
                COUNTERS.increment("proposals_denied_total")
                log_event(logger, "proposal_denied", proposal_id=proposal_id, actor=user.username)
            return result
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="Proposal state conflict") from exc

    @app.post("/proposals/{proposal_id}/execute")
    def execute(
        proposal_id: str,
        user: Annotated[UserRecord, Depends(require_roles(Role.OPERATOR))],
    ) -> dict[str, Any]:
        proposal_or_404(proposal_id)
        try:
            result, changed = store.execute_with_transition(proposal_id, user.username)
            if changed:
                COUNTERS.increment("simulated_executions_total")
                log_event(
                    logger,
                    "simulated_action_executed",
                    proposal_id=proposal_id,
                    actor=user.username,
                    verified=result["verified"],
                )
            return result
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="Proposal state conflict") from exc

    @app.get("/proposals/{proposal_id}/audit")
    def audit(
        proposal_id: str,
        _: Annotated[UserRecord, Depends(require_roles(Role.VIEWER, Role.OPERATOR, Role.APPROVER))],
    ) -> list[dict[str, Any]]:
        proposal_or_404(proposal_id)
        return store.audit(proposal_id)

    @app.get("/ops/metrics")
    def metrics(
        _: Annotated[UserRecord, Depends(require_roles(Role.VIEWER, Role.OPERATOR, Role.APPROVER))],
    ) -> dict[str, int]:
        return COUNTERS.snapshot()

    return app


app = create_app()
