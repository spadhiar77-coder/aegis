"""Per-customer behavioural features.

One function, `compute(txn, history)`, turns a transaction plus that
customer's earlier transactions into a fixed set of numbers a model can use.
The same function is called offline when building the training set
(ml/dataset.py) and online when the API scores a transaction
(app/model.py). Sharing the code is the simplest guard against
training/serving skew: the model is always fed features computed exactly the
way it was trained on.

Design rules:
* Pure standard-library Python, no database access, no NumPy. The caller
  supplies the history (newest first or any order; timestamps decide).
* Every feature is defined for a customer's very first transaction, using a
  neutral value, so new customers are never punished for having no history.
* Most features compare the transaction with *this customer's* normal, which
  is what Loop 1's fixed thresholds could not do: 6,000 is routine for a
  business account and wildly unusual for someone who spends 30 at a time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Protocol

# Order matters: models store coefficients in this order.
FEATURE_NAMES = [
    "log_amount",
    "amount_z",
    "amount_vs_max",
    "is_tiny",
    "count_10m",
    "count_1h",
    "count_24h",
    "tiny_count_1h",
    "new_device",
    "new_location",
    "new_merchant",
    "hour_rarity",
    "log_gap_minutes",
    "log_history",
    "night_utc",
]

HISTORY_LIMIT = 200  # how many earlier transactions the API passes in (see services.py)
MIN_HISTORY_FOR_PROFILE = 5  # below this, "unusual for this customer" is not knowable
TINY_AMOUNT = Decimal("5.00")
NO_HISTORY_GAP_MINUTES = 7 * 24 * 60
NOVELTY_WINDOW = timedelta(hours=24)


class TxnLike(Protocol):
    customer_id: str
    amount: Decimal
    timestamp: datetime
    device_id: str
    location: str
    merchant: str


@dataclass(frozen=True)
class FeatureResult:
    values: dict[str, float]
    # Human-facing facts used to explain a score (not model inputs).
    context: dict


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _hour_distance(a: int, b: int) -> int:
    d = abs(a - b) % 24
    return min(d, 24 - d)


def compute(txn: TxnLike, history: list[TxnLike]) -> FeatureResult:
    ts = txn.timestamp
    amount = float(txn.amount)
    log_amt = math.log1p(amount)

    prior = [h for h in history if h.timestamp <= ts]
    n = len(prior)
    has_profile = n >= MIN_HISTORY_FOR_PROFILE

    # --- amount compared with this customer's own spending
    hist_logs = [math.log1p(float(h.amount)) for h in prior]
    if has_profile:
        mean = sum(hist_logs) / n
        std = math.sqrt(sum((x - mean) ** 2 for x in hist_logs) / n)
        amount_z = _clip((log_amt - mean) / max(std, 0.25), -5.0, 10.0)
        max_prev = max(float(h.amount) for h in prior)
        amount_vs_max = _clip(math.log(amount / max_prev), -5.0, 5.0)
        typical = math.expm1(mean)
    else:
        amount_z = amount_vs_max = 0.0
        typical = None

    # --- velocity
    def count_since(delta: timedelta, pred=lambda h: True) -> int:
        start = ts - delta
        return sum(1 for h in prior if h.timestamp >= start and pred(h))

    c10m = count_since(timedelta(minutes=10))
    c1h = count_since(timedelta(hours=1))
    c24h = count_since(timedelta(hours=24))
    tiny1h = count_since(timedelta(hours=1), lambda h: h.amount < TINY_AMOUNT)

    # --- novelty, judged against the customer's *established* history (older
    # than NOVELTY_WINDOW). Using all history would let a fraudster's own
    # first transaction "vouch" for the next: in a burst from a stolen login,
    # only the first payment would look like a new device.
    cutoff = ts - NOVELTY_WINDOW
    established = [h for h in prior if h.timestamp < cutoff]
    known_devices = {h.device_id for h in established}
    known_locations = {h.location for h in established}
    known_merchants = {h.merchant for h in established}
    new_device = float(bool(established) and txn.device_id not in known_devices)
    new_location = float(bool(established) and txn.location not in known_locations)
    new_merchant = float(len(established) >= MIN_HISTORY_FOR_PROFILE and txn.merchant not in known_merchants)

    # --- time of day compared with this customer's own habits (UTC hours,
    # but because it is relative to their own history it adapts to any
    # time zone, unlike Loop 1's fixed UTC night window)
    hour = ts.astimezone(timezone.utc).hour
    if has_profile:
        near = sum(1 for h in prior if _hour_distance(h.timestamp.astimezone(timezone.utc).hour, hour) <= 1)
        hour_rarity = 1.0 - near / n
    else:
        hour_rarity = 0.0

    last = max((h.timestamp for h in prior), default=None)
    gap_minutes = (ts - last).total_seconds() / 60 if last else NO_HISTORY_GAP_MINUTES

    values = {
        "log_amount": log_amt,
        "amount_z": amount_z,
        "amount_vs_max": amount_vs_max,
        "is_tiny": float(txn.amount < TINY_AMOUNT),
        "count_10m": math.log1p(c10m),
        "count_1h": math.log1p(c1h),
        "count_24h": math.log1p(c24h),
        "tiny_count_1h": math.log1p(tiny1h),
        "new_device": new_device,
        "new_location": new_location,
        "new_merchant": new_merchant,
        "hour_rarity": hour_rarity,
        "log_gap_minutes": math.log1p(max(0.0, gap_minutes)),
        "log_history": math.log1p(n),
        "night_utc": float(0 <= hour < 5),
    }
    context = {
        "amount": f"{txn.amount}",
        "typical_amount": None if typical is None else f"{typical:.2f}",
        "largest_previous": None if not has_profile else f"{max_prev:.2f}",
        "history_size": n,
        "count_10m": c10m,
        "count_1h": c1h,
        "count_24h": c24h,
        "tiny_count_1h": tiny1h,
        "device_id": txn.device_id,
        "known_device_count": len(known_devices),
        "location": txn.location,
        "known_locations": sorted(known_locations),
        "merchant": txn.merchant,
        "hour_utc": hour,
        "share_of_history_near_this_hour": None if not has_profile else round(1.0 - hour_rarity, 3),
        "minutes_since_previous": None if last is None else round(gap_minutes, 1),
    }
    return FeatureResult(values=values, context=context)
