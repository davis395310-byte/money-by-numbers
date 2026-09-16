"""Tests for /api/health and /api/data/health."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

EXPECTED_CHECK_KEYS = {
    "application",
    "database",
    "ml",
    "data",
    "injuries",
    "weather",
    "odds",
    "stripe",
    "scheduler",
}


def test_health_returns_200_with_checks_shape():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("ok", "degraded")
    assert body["version"] == "0.1.0-phase1"
    assert set(body["checks"].keys()) == EXPECTED_CHECK_KEYS
    for name, check in body["checks"].items():
        assert "status" in check, f"check {name} missing status"


def test_health_honest_statuses_without_integrations():
    body = client.get("/api/health").json()
    checks = body["checks"]
    # No integrations configured in the test env: report that honestly.
    assert checks["application"]["status"] == "ok"
    assert checks["database"]["status"] == "not_configured"
    assert checks["ml"]["status"] == "not_implemented"
    assert checks["data"]["status"] == "not_implemented"
    assert checks["injuries"]["status"] == "not_implemented"
    assert checks["weather"]["status"] == "not_implemented"
    assert checks["odds"]["status"] == "not_configured"
    assert checks["stripe"]["status"] == "not_configured"
    assert checks["scheduler"]["status"] == "not_implemented"
    # Database not in error -> overall ok.
    assert body["status"] == "ok"


def test_data_health_returns_honest_empty_states():
    resp = client.get("/api/data/health")
    assert resp.status_code == 200
    body = resp.json()
    for dataset in ("games", "injuries", "odds", "weather"):
        state = body[dataset]
        assert state["status"] == "not_yet_ingested"
        assert state["last_ingestion"] is None
        assert state["record_count"] == 0
        assert state["freshness"] is None
    assert body["errors"] == []
