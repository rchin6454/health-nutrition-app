from fastapi.testclient import TestClient

from app import db
from app.main import app


def test_health_reports_degraded_without_database() -> None:
    db.set_pool(None)
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "degraded", "database": "unavailable"}


def test_schema_endpoint_returns_chat_response_schema() -> None:
    client = TestClient(app)
    schema = client.get("/api/schema").json()
    assert schema["title"] == "ChatResponse"
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["Claim"]["properties"]["source"] == {"title": "Source", "type": "null"}
