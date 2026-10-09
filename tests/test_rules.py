"""Unit tests for the rule engine. No database or HTTP involved."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app import rules

NOON = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


@dataclass
class Txn:
    customer_id: str = "c1"
    amount: Decimal = Decimal("20.00")
    timestamp: datetime = NOON
    device_id: str = "d1"


def test_ordinary_transaction_is_not_flagged():
    a = rules.assess(Txn(), history=[Txn(timestamp=NOON - timedelta(days=1))])
    assert a.hits == []
    assert a.score == 0.0
    assert not a.flagged


def test_high_amount_alone_flags_as_medium_severity():
    a = rules.assess(Txn(amount=Decimal("5000.00")), history=[])
    assert [h.rule for h in a.hits] == ["high_amount"]
    assert a.score == 0.6
    assert a.flagged
    assert a.severity == "medium"


def test_amount_just_below_threshold_does_not_fire():
    assert rules.rule_high_amount(Txn(amount=Decimal("4999.99")), []) is None


def test_night_time_uses_utc():
    # 01:30 in UTC+2 is 23:30 UTC the day before: not night by our rule.
    plus_two = timezone(timedelta(hours=2))
    assert rules.rule_night_time(Txn(timestamp=datetime(2026, 10, 9, 1, 30, tzinfo=plus_two)), []) is None
    assert rules.rule_night_time(Txn(timestamp=datetime(2026, 10, 9, 3, 0, tzinfo=timezone.utc)), []) is not None


def test_velocity_counts_only_the_window():
    inside = [Txn(timestamp=NOON - timedelta(minutes=m)) for m in (1, 4, 9)]
    outside = [Txn(timestamp=NOON - timedelta(minutes=30))]
    hit = rules.rule_velocity(Txn(), inside + outside)
    assert hit is not None and hit.evidence["prior_count"] == 3
    assert rules.rule_velocity(Txn(), inside[:2] + outside) is None


def test_new_device_needs_history():
    assert rules.rule_new_device(Txn(device_id="d9"), []) is None
    hit = rules.rule_new_device(Txn(device_id="d9"), [Txn(device_id="d1")])
    assert hit is not None and hit.evidence["known_devices"] == ["d1"]


def test_score_is_capped_and_severity_high():
    history = [Txn(timestamp=NOON.replace(hour=3) - timedelta(minutes=m)) for m in (1, 2, 3)]
    a = rules.assess(Txn(amount=Decimal("9000"), timestamp=NOON.replace(hour=3), device_id="new"), history)
    assert {h.rule for h in a.hits} == {"high_amount", "velocity", "new_device", "night_time"}
    assert a.score == 1.0
    assert a.severity == "high"
