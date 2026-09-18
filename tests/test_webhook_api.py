import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import sample_event


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def test_rejects_without_token(client):
    r = client.post("/webhooks/talk", json=sample_event())
    assert r.status_code == 401


def test_accepts_with_query_token_and_dedupes(client):
    r = client.post("/webhooks/talk", params={"token": "segredo"}, json=sample_event())
    assert r.status_code == 202
    assert r.json() == {"accepted": True, "duplicate": False, "event_id": "evt_msg_1"}

    r = client.post(
        "/webhooks/talk",
        headers={"X-Webhook-Token": "segredo", "x-attempt": "2"},
        json=sample_event(),
    )
    assert r.status_code == 200
    assert r.json()["duplicate"] is True

    events = client.get("/admin/events").json()
    assert len(events) == 1 and events[0]["status"] == "pending"


def test_bad_payloads(client):
    r = client.post(
        "/webhooks/talk",
        params={"token": "segredo"},
        content=b"nao json",
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 400
    r = client.post("/webhooks/talk", params={"token": "segredo"}, json={"Type": "Message"})
    assert r.status_code == 400


def test_health_and_retry(client):
    client.post("/webhooks/talk", params={"token": "segredo"}, json=sample_event())
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["stats"]["events_pending"] == 1
    r = client.post("/admin/events/evt_msg_1/retry")
    assert r.status_code == 200 and r.json()["queued"] is False  # sem worker nos testes
    assert client.post("/admin/events/nao_existe/retry").status_code == 404
    assert client.get("/admin/events/evt_msg_1").json()["type"] == "Message"


def test_unknown_contact_briefing_is_404(client):
    assert client.get("/contacts/nope/briefing").status_code == 404
    assert client.get("/contacts/nope/context").status_code == 404
    assert client.get("/contacts").json() == []
