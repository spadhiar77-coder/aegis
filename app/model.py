"""Loop 2 detector: logistic regression over behavioural features.

The model is trained offline (python -m ml.train) and exported to
app/model/model.json as plain numbers: feature order, scaler mean and scale,
coefficients, intercept and the decision threshold. Scoring is a dot product,
so the API needs neither scikit-learn nor pickle files (unpickling runs
arbitrary code, and pickles break across library versions).

Explanations: in a logistic regression each feature adds
    coefficient x (value - training mean) / training std
to the log-odds. The features that pushed hardest towards "fraud" become the
reasons on the incident, in the same {rule, reason, evidence} shape the Loop 1
rules used, so the API, database and UI did not change.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app import features
from app.rules import Assessment, RuleHit

MODEL_PATH = Path(__file__).resolve().parent / "model" / "model.json"

MAX_REASONS = 3
MIN_CONTRIBUTION = 0.5  # log-odds; smaller pushes are not worth showing an analyst
# Context features: they shift the score but "the customer has a long
# history" is not a reason an analyst can act on.
NOT_A_REASON = {"log_history"}
# The score is close to a probability on the training data's fraud rate.
SEVERITY_HIGH = 0.9
SEVERITY_MEDIUM = 0.7


@dataclass(frozen=True)
class LogisticModel:
    version: str
    feature_names: list[str]
    mean: list[float]
    scale: list[float]
    coef: list[float]
    intercept: float
    threshold: float

    @classmethod
    def load(cls, path: Path = MODEL_PATH) -> "LogisticModel":
        raw = json.loads(path.read_text())
        if raw.get("format") != "aegis-logreg/1":
            raise ValueError(f"{path}: unsupported model format {raw.get('format')!r}")
        if raw["features"] != features.FEATURE_NAMES:
            # Guards against a model trained on a different feature set than
            # the code now computes: silent garbage otherwise.
            raise ValueError(f"{path}: model features do not match app.features.FEATURE_NAMES; retrain")
        return cls(
            version=raw["version"],
            feature_names=raw["features"],
            mean=raw["mean"],
            scale=raw["scale"],
            coef=raw["coef"],
            intercept=raw["intercept"],
            threshold=raw["threshold"],
        )

    def contributions(self, values: dict[str, float]) -> dict[str, float]:
        return {
            name: c * (values[name] - m) / (s or 1.0)
            for name, m, s, c in zip(self.feature_names, self.mean, self.scale, self.coef)
        }

    def assess(self, txn: features.TxnLike, history: list[features.TxnLike]) -> Assessment:
        f = features.compute(txn, history)
        contrib = self.contributions(f.values)
        logit = self.intercept + sum(contrib.values())
        score = 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, logit))))
        drivers = sorted(
            (kv for kv in contrib.items() if kv[1] >= MIN_CONTRIBUTION and kv[0] not in NOT_A_REASON),
            key=lambda kv: -kv[1],
        )
        hits = [_explain(name, value, f) for name, value in drivers[:MAX_REASONS]]
        return Assessment(
            score=round(score, 4),
            hits=hits,
            threshold=self.threshold,
            detector="model",
            version=self.version,
            high_at=SEVERITY_HIGH,
            medium_at=SEVERITY_MEDIUM,
        )


@lru_cache(maxsize=1)
def load_default() -> LogisticModel:
    return LogisticModel.load()


# ------------------------------------------------------------ explanations


def _explain(name: str, contribution: float, f: features.FeatureResult) -> RuleHit:
    ctx = f.context
    reason = {
        "log_amount": lambda: f"Large amount: {ctx['amount']}",
        "amount_z": lambda: f"Amount {ctx['amount']} is far above this customer's typical {ctx['typical_amount']}",
        "amount_vs_max": lambda: f"Amount {ctx['amount']} is above anything this customer has spent before (previous largest {ctx['largest_previous']})",
        "is_tiny": lambda: f"Very small amount ({ctx['amount']}), typical of card testing",
        "count_10m": lambda: f"{ctx['count_10m']} earlier transactions in the previous 10 minutes",
        "count_1h": lambda: f"{ctx['count_1h']} earlier transactions in the previous hour",
        "count_24h": lambda: f"{ctx['count_24h']} earlier transactions in the previous 24 hours",
        "tiny_count_1h": lambda: f"{ctx['tiny_count_1h']} very small payments in the previous hour (possible card testing)",
        "new_device": lambda: f"Device {ctx['device_id']} not seen for this customer before the last 24 hours",
        "new_location": lambda: f"Location {ctx['location']} not seen for this customer before (known: {', '.join(ctx['known_locations']) or 'none'})",
        "new_merchant": lambda: f"First payment to {ctx['merchant']}",
        "hour_rarity": lambda: f"Unusual time for this customer: {ctx['hour_utc']:02d}:xx UTC",
        "log_gap_minutes": lambda: f"Only {ctx['minutes_since_previous']} minutes since the previous transaction",
        "log_history": lambda: f"Customer history: {ctx['history_size']} earlier transactions",
        "night_utc": lambda: f"Night-time transaction ({ctx['hour_utc']:02d}:xx UTC)",
    }.get(name, lambda: f"Feature {name}")()
    return RuleHit(
        rule=f"model:{name}",
        reason=reason,
        evidence={
            "feature": name,
            "value": round(f.values[name], 4),
            "contribution_log_odds": round(contribution, 3),
            **{k: v for k, v in ctx.items() if v is not None and k in _CONTEXT_KEYS.get(name, ())},
        },
    )


_CONTEXT_KEYS = {
    "log_amount": ("amount",),
    "amount_z": ("amount", "typical_amount"),
    "amount_vs_max": ("amount", "largest_previous"),
    "is_tiny": ("amount",),
    "count_10m": ("count_10m",),
    "count_1h": ("count_1h",),
    "count_24h": ("count_24h",),
    "tiny_count_1h": ("tiny_count_1h",),
    "new_device": ("device_id", "known_device_count"),
    "new_location": ("location", "known_locations"),
    "new_merchant": ("merchant",),
    "hour_rarity": ("hour_utc", "share_of_history_near_this_hour"),
    "log_gap_minutes": ("minutes_since_previous",),
    "log_history": ("history_size",),
    "night_utc": ("hour_utc",),
}
