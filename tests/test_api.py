"""End-to-end tests through HTTP and the real database, using the Loop 1 rules
detector so expectations stay exact. The model detector has its own API tests
in test_model_api.py."""

import pytest

pytestmark = pytest.mark.usefixtures("use_rules")


def txn(**overrides):
    body = {
        "transaction_id": "t1",
        "customer_id": "c1",
        "amount": "25.00",
        "currency": "GBP",
        "timestamp": "2026-10-09T12:00:00Z",
        "merchant": "Tesco",
        "location": "GB",
        "device_id": "d1",
        "status": "approved",
    }
    body.update(overrides)
    return body


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_normal_transaction_is_stored_without_incident(client):
    r = client.post("/v1/transactions", json=txn())
    assert r.status_code == 201
    body = r.json()
    assert body["flagged"] is False
    assert body["incident_id"] is None
    assert body["transaction"]["risk_score"] == 0.0
    assert body["transaction"]["detector"] == "rules"
    assert body["transaction"]["model_version"] == "rules-loop1"
    assert client.get("/v1/incidents").json()["total"] == 0


def test_suspicious_transaction_opens_incident_with_evidence_and_audit(client):
    r = client.post("/v1/transactions", json=txn(transaction_id="big", amount="7500.00"))
    assert r.status_code == 201
    body = r.json()
    assert body["flagged"] is True
    incident_id = body["incident_id"]

    listing = client.get("/v1/incidents").json()
    assert listing["total"] == 1
    assert listing["items"][0]["id"] == incident_id

    detail = client.get(f"/v1/incidents/{incident_id}").json()
    assert detail["status"] == "open"
    assert detail["severity"] == "medium"
    assert [e["rule"] for e in detail["evidence"]] == ["high_amount"]
    assert [t["transaction_id"] for t in detail["transactions"]] == ["big"]
    assert [ev["event_type"] for ev in detail["events"]] == ["created"]
    assert detail["events"][0]["actor"] == "system"


def test_history_rules_use_previous_transactions(client):
    client.post("/v1/transactions", json=txn(transaction_id="a", device_id="d1"))
    r = client.post(
        "/v1/transactions",
        json=txn(transaction_id="b", device_id="d2", amount="6000.00", timestamp="2026-10-09T12:05:00Z"),
    )
    rules_hit = {h["rule"] for h in r.json()["transaction"]["risk_reasons"]}
    assert rules_hit == {"high_amount", "new_device"}


def test_duplicate_transaction_id_is_rejected(client):
    assert client.post("/v1/transactions", json=txn()).status_code == 201
    r = client.post("/v1/transactions", json=txn())
    assert r.status_code == 409


def test_validation_errors(client):
    assert client.post("/v1/transactions", json=txn(amount="-1")).status_code == 422
    assert client.post("/v1/transactions", json=txn(timestamp="2026-10-09T12:00:00")).status_code == 422
    assert client.post("/v1/transactions", json=txn(currency="pounds")).status_code == 422
    assert client.post("/v1/transactions", json=txn(unexpected="x")).status_code == 422


def test_incident_filters_and_404(client):
    client.post("/v1/transactions", json=txn(transaction_id="x", amount="9000.00"))
    assert client.get("/v1/incidents", params={"severity": "high"}).json()["total"] == 0
    assert client.get("/v1/incidents", params={"severity": "medium"}).json()["total"] == 1
    assert client.get("/v1/incidents/999").status_code == 404


def test_html_pages_escape_user_input(client):
    r = client.post("/v1/transactions", json=txn(merchant="<script>x</script>", amount="9000.00"))
    incident_id = r.json()["incident_id"]
    for path in ("/", f"/incidents/{incident_id}"):
        page = client.get(path)
        assert page.status_code == 200
        assert "<script>x</script>" not in page.text
        assert "&lt;script&gt;" in page.text
