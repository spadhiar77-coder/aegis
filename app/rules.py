"""Loop 1 detector: a handful of hard-coded rules.

Design notes
------------
* Every rule is a pure function: (transaction, customer history) -> RuleHit or
  None. No database access inside rules, which makes them trivial to unit-test.
  The caller (app/services.py) is responsible for fetching the history.
* A hit carries a human-readable `reason` and machine-readable `evidence`.
  "Explainable by default": nothing gets flagged without saying why.
* Scoring is a capped sum of rule weights. It is a placeholder, not a
  calibrated probability. Loop 2 replaces it with a trained model and measured
  precision/recall; the RuleHit shape is meant to survive that change.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Protocol

# Thresholds live here as named constants so they are easy to find and tune.
HIGH_AMOUNT_THRESHOLD = Decimal("5000.00")
NIGHT_START_HOUR_UTC = 0  # inclusive
NIGHT_END_HOUR_UTC = 5  # exclusive
VELOCITY_WINDOW = timedelta(minutes=10)
VELOCITY_MAX_PRIOR = 3  # this many earlier txns inside the window triggers the rule

WEIGHTS = {
    "high_amount": 0.6,
    "velocity": 0.4,
    "new_device": 0.3,
    "night_time": 0.2,
}

FLAG_THRESHOLD = 0.5  # score at or above this opens an incident


class TxnLike(Protocol):
    """The fields rules read. Both the Pydantic input and the ORM row fit."""

    customer_id: str
    amount: Decimal
    timestamp: datetime
    device_id: str


@dataclass(frozen=True)
class RuleHit:
    rule: str
    reason: str
    evidence: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"rule": self.rule, "reason": self.reason, "evidence": self.evidence}


RULES_VERSION = "rules-loop1"


@dataclass(frozen=True)
class Assessment:
    """A detector's verdict on one transaction.

    Shared by every detector (these rules, the Loop 2 model) so the rest of
    the app doesn't care which one produced it.
    """

    score: float
    hits: list[RuleHit]
    threshold: float = FLAG_THRESHOLD
    detector: str = "rules"
    version: str = RULES_VERSION
    high_at: float = 0.8
    medium_at: float = 0.6
    # Model only: {"intercept": float, "contributions": {feature: log-odds}}
    breakdown: dict | None = None

    @property
    def flagged(self) -> bool:
        return self.score >= self.threshold

    @property
    def severity(self) -> str:
        if self.score >= self.high_at:
            return "high"
        if self.score >= self.medium_at:
            return "medium"
        return "low"


# ------------------------------------------------------------------- rules


def rule_high_amount(txn: TxnLike, history: list[TxnLike]) -> RuleHit | None:
    if txn.amount >= HIGH_AMOUNT_THRESHOLD:
        return RuleHit(
            rule="high_amount",
            reason=f"Amount {txn.amount} is at or above the {HIGH_AMOUNT_THRESHOLD} threshold",
            evidence={"amount": str(txn.amount), "threshold": str(HIGH_AMOUNT_THRESHOLD)},
        )
    return None


def rule_night_time(txn: TxnLike, history: list[TxnLike]) -> RuleHit | None:
    # Crude: uses UTC, not the customer's local time. Good enough for a
    # skeleton; a per-customer "usual hours" feature replaces it in Loop 2.
    hour = txn.timestamp.astimezone(timezone.utc).hour
    if NIGHT_START_HOUR_UTC <= hour < NIGHT_END_HOUR_UTC:
        return RuleHit(
            rule="night_time",
            reason=f"Transaction at {hour:02d}:xx UTC, inside the {NIGHT_START_HOUR_UTC:02d}-{NIGHT_END_HOUR_UTC:02d} UTC night window",
            evidence={"hour_utc": hour},
        )
    return None


def rule_velocity(txn: TxnLike, history: list[TxnLike]) -> RuleHit | None:
    window_start = txn.timestamp - VELOCITY_WINDOW
    recent = [h for h in history if window_start <= h.timestamp <= txn.timestamp]
    if len(recent) >= VELOCITY_MAX_PRIOR:
        minutes = int(VELOCITY_WINDOW.total_seconds() // 60)
        return RuleHit(
            rule="velocity",
            reason=f"{len(recent)} earlier transactions from this customer in the {minutes} minutes before this one",
            evidence={"prior_count": len(recent), "window_minutes": minutes},
        )
    return None


def rule_new_device(txn: TxnLike, history: list[TxnLike]) -> RuleHit | None:
    # Only meaningful once we know the customer; a first-ever transaction is
    # "new device" by definition and would flag every new customer.
    if not history:
        return None
    known = sorted({h.device_id for h in history})
    if txn.device_id not in known:
        return RuleHit(
            rule="new_device",
            reason=f"Device {txn.device_id} has not been seen for this customer before",
            evidence={"device_id": txn.device_id, "known_devices": known},
        )
    return None


RULES = [rule_high_amount, rule_velocity, rule_new_device, rule_night_time]


def assess(txn: TxnLike, history: list[TxnLike]) -> Assessment:
    """Run every rule and combine the hits into one score in [0, 1].

    `history` is the customer's earlier transactions (excluding `txn`).
    """
    hits = [hit for rule in RULES if (hit := rule(txn, history)) is not None]
    score = min(1.0, round(sum(WEIGHTS[h.rule] for h in hits), 4))
    return Assessment(score=score, hits=hits)
