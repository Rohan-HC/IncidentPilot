from incidentpilot.models import Incident

_INCIDENTS = {
    item.id: item
    for item in (
        Incident(
            id="db-pool-001",
            title="Database connection pool exhaustion",
            service="orders-api",
            symptom="Elevated request latency and database timeout errors",
            metrics={"active_connections": 100, "pool_limit": 100, "p95_latency_ms": 2400},
            logs=[
                "orders-api: connection pool exhausted",
                "orders-api: timed out waiting for connection",
            ],
        ),
        Incident(
            id="deploy-001",
            title="Faulty deployment",
            service="checkout-api",
            symptom="5xx errors increased after a deployment",
            metrics={"error_rate_percent": 18.0, "p95_latency_ms": 1100},
            logs=["checkout-api: missing PAYMENT_ENDPOINT configuration", "checkout-api: HTTP 500"],
            deployments=[
                {
                    "version": "v2.4.1",
                    "status": "completed",
                    "change": "payment client configuration",
                }
            ],
        ),
        Incident(
            id="unknown-001",
            title="Insufficient evidence",
            service="notifications-api",
            symptom="A single unconfirmed alert with no correlated telemetry",
            metrics={},
            logs=[],
            deployments=[],
        ),
    )
}


def list_incidents() -> list[Incident]:
    return [item.model_copy(deep=True) for item in _INCIDENTS.values()]


def get_incident(incident_id: str) -> Incident:
    return _INCIDENTS[incident_id].model_copy(deep=True)
