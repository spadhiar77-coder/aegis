"""The labelled simulator: deterministic, labelled, and labels never leak to the API."""

from ml import simulation


def test_same_seed_same_data():
    a = simulation.generate(customers=30, days=10, seed=3)
    b = simulation.generate(customers=30, days=10, seed=3)
    assert [t.api_body() for t in a] == [t.api_body() for t in b]
    assert [t.is_fraud for t in a] == [t.is_fraud for t in b]


def test_sorted_labelled_and_mixed():
    txns = simulation.generate(customers=200, days=15, seed=1)
    assert [t.timestamp for t in txns] == sorted(t.timestamp for t in txns)
    scenarios = {t.scenario for t in txns}
    assert any(s.startswith("legit:") for s in scenarios)
    assert {"account_takeover", "card_testing", "app_scam"} & scenarios
    assert all(t.is_fraud == (not t.scenario.startswith("legit")) for t in txns)


def test_api_body_carries_no_labels():
    body = simulation.generate(customers=5, days=3, seed=2)[0].api_body()
    assert "is_fraud" not in body and "scenario" not in body
    assert set(body) == {
        "transaction_id", "customer_id", "amount", "currency", "timestamp",
        "merchant", "location", "device_id", "status",
    }
