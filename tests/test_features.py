"""Unit tests for the behavioural features. No database or HTTP involved."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app import features

NOON = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


@dataclass
class Txn:
    customer_id: str = "c1"
    amount: Decimal = Decimal("20.00")
    timestamp: datetime = NOON
    device_id: str = "d1"
    location: str = "GB"
    merchant: str = "Tesco"


def daily_history(days: int = 10, **kw) -> list[Txn]:
    return [Txn(timestamp=NOON - timedelta(days=d), **kw) for d in range(1, days + 1)]


def test_every_feature_is_present_and_in_order():
    v = features.compute(Txn(), daily_history()).values
    assert list(v) == features.FEATURE_NAMES


def test_first_transaction_gets_neutral_values():
    v = features.compute(Txn(amount=Decimal("9000.00"), device_id="new"), []).values
    for name in ("amount_z", "amount_vs_max", "new_device", "new_location", "new_merchant", "hour_rarity", "count_1h"):
        assert v[name] == 0.0, name
    assert v["log_history"] == 0.0


def test_amount_is_judged_against_the_customers_own_spending():
    small_spender = daily_history(amount=Decimal("20.00"))
    big_spender = daily_history(amount=Decimal("6000.00"))
    txn = Txn(amount=Decimal("6000.00"))
    assert features.compute(txn, small_spender).values["amount_z"] == 10.0  # clipped maximum
    assert features.compute(txn, big_spender).values["amount_z"] == pytest.approx(0.0)


def test_novelty_ignores_the_last_24_hours():
    # A device first seen 5 minutes ago is still "new": a fraudster's first
    # transaction must not vouch for their second.
    history = daily_history(device_id="d1") + [Txn(timestamp=NOON - timedelta(minutes=5), device_id="evil")]
    v = features.compute(Txn(device_id="evil"), history).values
    assert v["new_device"] == 1.0
    assert features.compute(Txn(device_id="d1"), history).values["new_device"] == 0.0


def test_new_location_and_merchant():
    v = features.compute(Txn(location="RO", merchant="GiftCardHub"), daily_history()).values
    assert v["new_location"] == 1.0
    assert v["new_merchant"] == 1.0


def test_velocity_and_tiny_counts():
    burst = [Txn(timestamp=NOON - timedelta(minutes=m), amount=Decimal("1.00")) for m in (1, 2, 3, 30)]
    r = features.compute(Txn(amount=Decimal("1.50")), daily_history() + burst)
    assert r.context["count_10m"] == 3
    assert r.context["count_1h"] == 4
    assert r.context["tiny_count_1h"] == 4
    assert r.values["is_tiny"] == 1.0


def test_future_history_is_ignored():
    later = [Txn(timestamp=NOON + timedelta(minutes=1))]
    assert features.compute(Txn(), later).context["history_size"] == 0


def test_hour_rarity_adapts_to_the_customer():
    # Someone who always pays at 03:00 UTC (e.g. a US evening) is not unusual at 03:00.
    night_owl = [Txn(timestamp=NOON.replace(hour=3) - timedelta(days=d)) for d in range(1, 11)]
    assert features.compute(Txn(timestamp=NOON.replace(hour=3)), night_owl).values["hour_rarity"] == 0.0
    assert features.compute(Txn(timestamp=NOON), night_owl).values["hour_rarity"] == 1.0
