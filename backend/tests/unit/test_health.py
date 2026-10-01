from fastapi.testclient import TestClient

from app import db
from app.knowledge import embeddings
from app.main import app


def test_health_reports_degraded_without_database() -> None:
    db.set_pool(None)
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "degraded", "database": "unavailable", "embeddings": "ok"}


def test_health_reports_when_the_embedding_model_is_not_loaded() -> None:
    db.set_pool(None)
    embeddings.set_embedder(None)
    body = TestClient(app).get("/api/health").json()
    assert body["embeddings"] == "not_loaded"
    assert body["status"] == "degraded"


def test_schema_endpoint_returns_chat_response_schema() -> None:
    client = TestClient(app)
    schema = client.get("/api/schema").json()
    assert schema["title"] == "ChatResponse"
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["Claim"]["properties"]["source"] == {"title": "Source", "type": "null"}
