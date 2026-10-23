"""Unit tests for the exported model and its explanations. No database."""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app import features
from app.model import MODEL_PATH, LogisticModel, load_default

NOON = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


@dataclass
class Txn:
    customer_id: str = "c1"
    amount: Decimal = Decimal("30.00")
    timestamp: datetime = NOON
    device_id: str = "d1"
    location: str = "GB"
    merchant: str = "Tesco"


def normal_history() -> list[Txn]:
    # Five weeks of everyday spending around midday on one phone.
    merchants = ["Tesco", "Pret", "TfL", "Boots", "Amazon"]
    return [
        Txn(
            amount=Decimal(f"{20 + (d * 7) % 25}.00"),
            timestamp=NOON - timedelta(days=d, hours=(d % 5) - 2),
            merchant=merchants[d % len(merchants)],
        )
        for d in range(1, 36)
    ]


@pytest.fixture(scope="module")
def model() -> LogisticModel:
    return load_default()


def test_committed_model_matches_feature_code(model):
    assert model.feature_names == features.FEATURE_NAMES
    assert 0.0 < model.threshold < 1.0


def test_ordinary_purchase_is_not_flagged(model):
    a = model.assess(Txn(amount=Decimal("28.00"), merchant="Tesco"), normal_history())
    assert not a.flagged
    assert a.detector == "model"
    assert a.version == model.version


def test_account_takeover_pattern_is_flagged_with_reasons(model):
    history = normal_history()
    txn = Txn(
        amount=Decimal("2400.00"),
        timestamp=NOON.replace(hour=3),
        device_id="dev_attacker",
        location="RO",
        merchant="ElectroWorld",
    )
    a = model.assess(txn, history)
    assert a.flagged
    assert a.severity in {"medium", "high"}
    rules = [h.rule for h in a.hits]
    assert rules and all(r.startswith("model:") for r in rules)
    assert len(rules) <= 3
    for hit in a.hits:
        assert hit.reason
        assert hit.evidence["contribution_log_odds"] >= 0.5
    contribs = [h.evidence["contribution_log_odds"] for h in a.hits]
    assert contribs == sorted(contribs, reverse=True)


def test_card_testing_burst_is_flagged(model):
    history = normal_history()
    probes = [
        Txn(amount=Decimal("1.00"), timestamp=NOON - timedelta(minutes=m), device_id="dev_x", merchant="Steam", location="NG")
        for m in (1, 2, 4, 5)
    ]
    a = model.assess(
        Txn(amount=Decimal("1.50"), device_id="dev_x", merchant="Steam", location="NG"), history + probes
    )
    assert a.flagged


def test_rejects_a_model_trained_on_other_features(tmp_path):
    raw = json.loads(MODEL_PATH.read_text())
    raw["features"] = raw["features"][:-1]
    bad = tmp_path / "model.json"
    bad.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="retrain"):
        LogisticModel.load(bad)
