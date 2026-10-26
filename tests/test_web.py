"""The web app: pages render, forms change the database, demo mode works."""

import re

import pytest

from app import demo

pytestmark = pytest.mark.usefixtures("use_model")


@pytest.fixture(autouse=True)
def small_demo(monkeypatch):
    # A small, fast demo feed with enough fraud to open incidents.
    monkeypatch.setattr(demo, "CONFIG", demo.DemoConfig(customers=12, days=10, seed=5, fraud_customer_rate=0.6, preload_days=8))


@pytest.fixture
def loaded(client):
    r = client.post("/demo/reset", follow_redirects=False)
    assert r.status_code == 303
    return client


def first_incident_id(client) -> int:
    page = client.get("/?status=all").text
    return int(re.search(r'href="/incidents/(\d+)', page).group(1))


def test_empty_database_offers_the_demo(client):
    page = client.get("/").text
    assert "No transactions yet" in page
    assert 'action="/demo/reset"' in page


def test_demo_loads_and_queue_lists_incidents(loaded):
    page = loaded.get("/").text
    assert "Demo loaded" in page  # one-time message
    assert "Demo loaded" not in loaded.get("/").text
    assert "Incident #" in page
    assert "Feed time (UTC)" in page


def loaded_count(client) -> int:
    return int(re.search(r"([\d,]+) of [\d,]+ demo payments", client.get("/").text).group(1).replace(",", ""))


def test_advance_ingests_the_next_transactions(loaded):
    before = loaded_count(loaded)
    r = loaded.post("/demo/advance", data={"hours": "24"}, follow_redirects=True)
    added = int(re.search(r"(\d+) new transactions in the next 24 hours", r.text).group(1))
    assert added > 0
    assert loaded_count(loaded) == before + added
    # Advancing to the end of the feed stops cleanly.
    for _ in range(5):
        loaded.post("/demo/advance", data={"hours": "72"})
    assert "Demo feed finished." in loaded.get("/").text


def test_case_page_explains_the_score(loaded):
    page = loaded.get(f"/incidents/{first_incident_id(loaded)}?status=all").text
    for text in ("Risk score", "What moved the score", "recent activity", "Audit trail", "Loop 1 rules would have scored this"):
        assert text in page, text


def test_decisions_are_saved_with_name_and_note(loaded):
    iid = first_incident_id(loaded)
    r = loaded.post(f"/incidents/{iid}/decision", data={"action": "confirm", "analyst": "Sam", "note": "Customer called <b>"}, follow_redirects=True)
    assert f"Incident #{iid}: confirmed as fraud." in r.text
    assert "Confirmed as fraud by <b>Sam</b>" in r.text
    assert "Customer called &lt;b&gt;" in r.text
    detail = loaded.get(f"/v1/incidents/{iid}").json()
    assert detail["status"] == "confirmed_fraud"
    assert [e["event_type"] for e in detail["events"]] == ["created", "confirmed_fraud"]
    assert detail["events"][1]["actor"] == "analyst:Sam"
    # Can't confirm twice; the page says why and nothing changes.
    r = loaded.post(f"/incidents/{iid}/decision", data={"action": "confirm", "analyst": "Sam"}, follow_redirects=True)
    assert "Can&#39;t confirm an incident that is confirmed fraud." in r.text
    assert len(loaded.get(f"/v1/incidents/{iid}").json()["events"]) == 2


def test_decision_needs_a_name(loaded):
    iid = first_incident_id(loaded)
    r = loaded.post(f"/incidents/{iid}/decision", data={"action": "dismiss", "analyst": " "}, follow_redirects=True)
    assert "Enter your name" in r.text
    assert loaded.get(f"/v1/incidents/{iid}").json()["status"] == "open"


def test_answer_key_shows_hits_and_misses(loaded):
    page = loaded.get("/?key=1").text
    assert "Fraud caught" in page and "False alarms" in page and "Missed fraud" in page


@pytest.mark.parametrize("preset", ["everyday", "takeover", "cardtest", "scam", "bigbuy"])
def test_presets_score_without_saving(loaded, preset):
    before = loaded.get("/?status=all").text.count('class="inc"')
    page = loaded.get(f"/test?preset={preset}").text
    assert "Would open" in page or "Would pass" in page
    assert loaded.get("/?status=all").text.count('class="inc"') == before


def test_takeover_preset_is_flagged(loaded):
    assert "Would open a" in loaded.get("/test?preset=takeover").text


def test_sending_a_test_transaction_opens_an_incident(loaded):
    form = loaded.get("/test?preset=takeover").text
    customer = re.search(r'<option value="(cust_\d+)" selected', form).group(1)
    when = re.search(r'name="when"[^>]*value="([^"]+)"', form).group(1)
    device = re.search(r'<option value="(dev_new_[0-9a-f]+)" selected', form).group(1)
    r = loaded.post("/test", data={"customer": customer, "amount": "1850.00", "merchant": "ElectroWorld",
                                   "location": "RO", "device": device, "when": when}, follow_redirects=True)
    assert "Aegis opened incident" in r.text
    assert "Your test" in r.text


def test_invalid_test_form_explains_the_problem(loaded):
    r = loaded.post("/test", data={"customer": "cust_00001", "amount": "-5", "merchant": "x", "location": "GB",
                                   "device": "d", "when": "2026-08-09T12:00"}, follow_redirects=True)
    assert "The amount must be more than zero." in r.text


def test_reading_pages(client):
    assert "All five detectors" in client.get("/results").text
    assert "The 15 features" in client.get("/how").text


def test_demo_controls_can_be_switched_off(client, monkeypatch):
    monkeypatch.setenv("AEGIS_DEMO", "0")
    assert client.post("/demo/reset").status_code == 404
    assert "Load demo data" not in client.get("/").text


def test_database_url_from_hosting_providers_is_normalised(monkeypatch):
    from app import config

    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h:5432/d")
    assert config.get_database_url() == "postgresql+psycopg://u:p@h:5432/d"
