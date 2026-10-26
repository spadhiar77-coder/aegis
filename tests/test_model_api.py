"""End-to-end: the default (model) detector through HTTP and the database."""

from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.usefixtures("use_model")

NOON = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


def body(i: int, **overrides) -> dict:
    b = {
        "transaction_id": f"t{i}",
        "customer_id": "c1",
        "amount": f"{20 + (i * 7) % 25}.00",
        "currency": "GBP",
        "timestamp": (NOON - timedelta(days=40 - i, hours=(i % 5) - 2)).isoformat(),
        "merchant": ["Tesco", "Pret", "TfL", "Boots", "Amazon"][i % 5],
        "location": "GB",
        "device_id": "d1",
        "status": "approved",
    }
    b.update(overrides)
    return b


def seed_history(client, n: int = 35) -> None:
    for i in range(n):
        r = client.post("/v1/transactions", json=body(i))
        assert r.status_code == 201, r.text
        assert r.json()["flagged"] is False, r.json()


def test_model_scores_and_records_its_version(client):
    seed_history(client)
    r = client.post("/v1/transactions", json=body(99, timestamp=NOON.isoformat()))
    t = r.json()["transaction"]
    assert r.json()["flagged"] is False
    assert t["detector"] == "model"
    assert t["model_version"].startswith("logreg-")
    assert 0.0 <= t["risk_score"] < 0.5


def test_account_takeover_opens_explained_incident(client):
    seed_history(client)
    r = client.post(
        "/v1/transactions",
        json=body(
            100,
            amount="2400.00",
            timestamp=NOON.replace(hour=3).isoformat(),
            device_id="dev_attacker",
            location="RO",
            merchant="ElectroWorld",
        ),
    )
    out = r.json()
    assert out["flagged"] is True, out
    detail = client.get(f"/v1/incidents/{out['incident_id']}").json()
    assert detail["evidence"] and all(e["rule"].startswith("model:") for e in detail["evidence"])
    created = detail["events"][0]
    assert created["detail"]["detector"] == "model"
    assert created["detail"]["model_version"] == out["transaction"]["model_version"]
    # The HTML case page renders model reasons too.
    assert client.get(f"/incidents/{out['incident_id']}").status_code == 200
