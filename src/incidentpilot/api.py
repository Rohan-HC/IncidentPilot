from typing import Any

from fastapi import FastAPI, HTTPException

from incidentpilot.agent import investigate
from incidentpilot.models import Incident, Investigation
from incidentpilot.simulator import get_incident, list_incidents


def create_app(model: Any | None = None) -> FastAPI:
    app = FastAPI(title="IncidentPilot", description="Synthetic incident response only")

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
        except (ImportError, ConnectionError, OSError) as exc:
            raise HTTPException(status_code=503, detail=f"Local model unavailable: {exc}") from exc

    return app


app = create_app()
