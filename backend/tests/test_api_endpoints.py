import json
import pytest
from fastapi.testclient import TestClient
from main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_health_endpoint(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "unlisted-backend"


def test_companies_endpoint(client):
    response = client.get("/api/companies")
    assert response.status_code == 200
    companies = response.json()
    assert isinstance(companies, list)
    assert len(companies) >= 3

    company_ids = [c["id"] for c in companies]
    assert "stripe" in company_ids
    assert "supabase" in company_ids
    for c in companies:
        assert "name" in c
        assert "description" in c


def test_demo_stream_sse(client):
    response = client.post("/api/analyze?demo=true")
    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")

    # Parse SSE events from response text
    events = []
    lines = response.text.split("\n")
    for line in lines:
        if line.startswith("data: "):
            payload = json.loads(line[6:].strip())
            events.append(payload)

    # Must contain 5 stages in order: extraction, synthesis, thesis, verification, pitch
    stage_names = [e["stage"] for e in events]
    assert stage_names == ["extraction", "synthesis", "thesis", "verification", "pitch"]

    for e in events:
        assert e["status"] == "done"
        assert "data" in e

    # Verification stage should have verdict
    verification_event = next(e for e in events if e["stage"] == "verification")
    assert verification_event["data"]["verdict"] in ["SUPPORTED", "PARTIALLY_SUPPORTED", "OVERREACHING"]
    assert "reason" in verification_event["data"]

    # Pitch stage should have outreach_message
    pitch_event = next(e for e in events if e["stage"] == "pitch")
    assert "outreach_message" in pitch_event["data"]
    assert "subject" in pitch_event["data"]
