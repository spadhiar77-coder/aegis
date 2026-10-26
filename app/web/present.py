"""Presentation helpers: turn database rows and model output into what the
templates show. Kept free of HTTP so it is easy to test and reuse."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from app import features

ROOT = Path(__file__).resolve().parent.parent.parent

FEATURE_LABEL = {
    "log_amount": "Amount size",
    "amount_z": "Amount vs their usual",
    "amount_vs_max": "Amount vs their largest",
    "is_tiny": "Tiny amount",
    "count_10m": "Payments, last 10 min",
    "count_1h": "Payments, last hour",
    "count_24h": "Payments, last 24 h",
    "tiny_count_1h": "Tiny payments, last hour",
    "new_device": "New device",
    "new_location": "New location",
    "new_merchant": "New merchant or payee",
    "hour_rarity": "Unusual hour for them",
    "log_gap_minutes": "Time since last payment",
    "log_history": "Length of history",
    "night_utc": "Night (UTC)",
}

SCENARIO = {
    "account_takeover": "Account takeover",
    "card_testing": "Card testing",
    "app_scam": "Scam payment (APP fraud)",
    "legit": "Ordinary spending",
    "legit:high_spender": "Business account spending normally",
    "legit:large_new_payee": "Big genuine transfer to a new payee",
    "legit:big_purchase": "One-off big purchase",
    "legit:new_payee": "Transfer to a new payee",
    "legit:burst": "Quick run of small payments",
    "legit:night_owl": "Night-time regular",
    "legit:micro": "Tiny genuine payment",
    "legit:travel": "Travelling abroad",
    "legit:new_device": "New phone",
}

DETECTOR_NAME = {
    "rules": "Loop 1 rules",
    "logreg": "Logistic regression",
    "iforest": "Isolation Forest",
    "gbm": "Gradient boosting",
    "logreg+guardrail": "Model + payee guardrail",
}

STATUS_LABEL = {"open": "Open", "confirmed_fraud": "Confirmed fraud", "dismissed": "Dismissed"}
EVENT_LABEL = {
    "created": "Opened",
    "confirmed_fraud": "Confirmed as fraud",
    "dismissed": "Dismissed as genuine",
    "reopened": "Reopened",
    "note": "Added a note",
}

COUNTRIES = ["GB", "IE", "FR", "DE", "US", "ES", "IT", "PT", "NL", "GR", "RO", "NG", "BR", "RU", "VN"]
MERCHANTS = [
    "Tesco", "Sainsbury's", "Amazon", "Uber", "Shell", "Pret", "Netflix", "Boots", "Argos", "Deliveroo", "TfL",
    "Spotify", "Lidl", "Costa", "John Lewis", "Steam", "Apple Store", "ElectroWorld", "GiftCardHub",
    "CryptoXchange", "LuxWatches", "Payee: Safe Account Ltd 41",
]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ------------------------------------------------------------------ formats


def gbp(x) -> str:
    return f"£{Decimal(x):,.2f}"


def score(x: float) -> str:
    return f"{x:.2f}"


def when(ts: datetime | None, day: bool = False) -> str:
    if ts is None:
        return "–"
    ts = ts.astimezone(timezone.utc)
    return f"{DAYS[ts.weekday()] + ' ' if day else ''}{ts.day} {MONTHS[ts.month - 1]}, {ts:%H:%M}"


def pct(x: float | None, digits: int = 1) -> str:
    return "n/a" if x is None else f"{100 * x:.{digits}f}%"


def short_device(d: str) -> str:
    return d if len(d) <= 14 else d[:14] + "…"


# ------------------------------------------------------------------ charts


@dataclass(frozen=True)
class Bar:
    name: str
    label: str
    value: float
    width: float  # percent of half the track


def contribution_bars(breakdown: dict | None, min_abs: float = 0.05) -> dict | None:
    """Rows for the "what moved the score" chart, biggest push first."""
    if not breakdown:
        return None
    contrib = breakdown["contributions"]
    items = sorted(contrib.items(), key=lambda kv: -kv[1])
    shown = [(k, v) for k, v in items if abs(v) >= min_abs]
    top = max([2.0] + [abs(v) for _, v in shown])
    logit = breakdown["intercept"] + sum(contrib.values())
    return {
        "bars": [Bar(k, FEATURE_LABEL.get(k, k), v, abs(v) / top * 50) for k, v in shown],
        "hidden": len(items) - len(shown),
        "scale": top,
        "intercept": breakdown["intercept"],
        "logit": logit,
    }


def meter(score_value: float, threshold: float) -> dict:
    s = max(0.0, min(1.0, score_value))
    return {"score": s, "threshold": threshold, "edge": "end" if s > 0.92 else "start" if s < 0.08 else ""}


# ------------------------------------------------------------------ customers


@dataclass
class Profile:
    customer_id: str
    n: int
    typical: float
    largest: float
    hours: list[int]
    devices: list[tuple[str, int]]
    locations: list[tuple[str, int]]
    merchants: list[tuple[str, int]]
    last: datetime | None

    @property
    def home(self) -> str:
        return self.locations[0][0] if self.locations else "GB"

    @property
    def main_device(self) -> str:
        return self.devices[0][0] if self.devices else "dev_unknown"

    @property
    def usual_hour(self) -> int:
        return self.hours.index(max(self.hours)) if any(self.hours) else 12

    @property
    def hour_cells(self) -> list[tuple[int, int, float]]:
        top = max(self.hours) or 1
        return [(h, c, 0.15 + 0.85 * c / top if c else 0.06) for h, c in enumerate(self.hours)]


def profile(customer_id: str, history) -> Profile:
    """Summary of a customer's history (newest-first list of transactions)."""
    import math

    n = len(history)
    logs = [math.log1p(float(t.amount)) for t in history]
    hours = [0] * 24
    for t in history:
        hours[t.timestamp.astimezone(timezone.utc).hour] += 1
    return Profile(
        customer_id=customer_id,
        n=n,
        typical=math.expm1(sum(logs) / n) if n else 30.0,
        largest=max((float(t.amount) for t in history), default=0.0),
        hours=hours,
        devices=Counter(t.device_id for t in history).most_common(),
        locations=Counter(t.location for t in history).most_common(),
        merchants=Counter(t.merchant for t in history).most_common(),
        last=max((t.timestamp for t in history), default=None),
    )


def next_hour_after(ts: datetime, hour: int) -> datetime:
    t = ts.astimezone(timezone.utc).replace(hour=hour, minute=17, second=0, microsecond=0)
    while t <= ts:
        t += timedelta(days=1)
    return t


def new_device_id(customer_id: str) -> str:
    import hashlib

    return "dev_new_" + hashlib.sha1(customer_id.encode()).hexdigest()[:6]


# Example transactions for the "Test a transaction" page.
PRESETS = {
    "everyday": "Everyday purchase",
    "takeover": "Stolen login abroad",
    "cardtest": "Card testing run",
    "scam": "Scam payment (hard case)",
    "bigbuy": "Genuine big purchase",
}
CARD_TEST_PRELUDE = [(-360, "1.00"), (-270, "2.50"), (-180, "0.99"), (-90, "3.10")]


def preset_values(key: str, p: Profile, now: datetime) -> dict:
    T = max(now, p.last) if p.last else now
    usual = next_hour_after(T, p.usual_hour)
    new_dev = new_device_id(p.customer_id)
    r2 = lambda x: f"{round(x, 2):.2f}"  # noqa: E731
    values = {
        "everyday": dict(amount=r2(p.typical * 0.9), merchant=p.merchants[0][0] if p.merchants else "Tesco", location=p.home, device=p.main_device, when=usual),
        "takeover": dict(amount=r2(min(2400, max(900, p.typical * 15))), merchant="ElectroWorld", location="RO", device=new_dev, when=next_hour_after(T, 3)),
        "cardtest": dict(amount="1.49", merchant="Steam", location="NG", device=new_dev, when=T + timedelta(minutes=10), prelude=True),
        "scam": dict(amount="4750.00", merchant="Payee: Safe Account Ltd 41", location=p.home, device=p.main_device, when=usual),
        "bigbuy": dict(amount=r2(p.typical * 8), merchant="John Lewis", location=p.home, device=p.main_device, when=usual),
    }[key]
    values.setdefault("prelude", False)
    return values


# ------------------------------------------------------------------ results


@lru_cache(maxsize=1)
def loop2_results() -> dict:
    metrics = json.loads((ROOT / "reports" / "loop2_metrics.json").read_text())
    model = json.loads((ROOT / "app" / "model" / "model.json").read_text())
    results = metrics["results"]
    for r in results.values():  # JSON has NaN for detectors without a ranking
        for k in ("pr_auc", "roc_auc"):
            if r.get(k) is not None and r[k] != r[k]:
                r[k] = None
    robust = {}
    for name, ms in (metrics.get("robustness") or {}).get("values", {}).items():
        vals = [v for v in ms["pr_auc"] if v == v]
        if vals:
            mean = sum(vals) / len(vals)
            sd = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
            robust[name] = (mean, sd)
    coefs = sorted(zip(model["features"], model["coef"]), key=lambda kv: -abs(kv[1]))
    cmax = max(abs(c) for _, c in coefs)
    fp_sources = sorted({s for r in results.values() for s in r["false_positives_by_source"]},
                        key=lambda s: -results["rules"]["false_positives_by_source"].get(s, 0))
    return {
        "r": results,
        "names": list(results),
        "scenarios": list(results["rules"]["recall_by_scenario"]),
        "fp_sources": fp_sources,
        "fp_max": max(results["rules"]["false_positives_by_source"].values()),
        "robust": robust,
        "coefs": [Bar(f, FEATURE_LABEL.get(f, f), c, abs(c) / cmax * 50) for f, c in coefs],
        "training": model["training"],
        "version": model["version"],
        "threshold": model["threshold"],
    }


FEATURE_GLOSSARY = [
    ("Amount size", "How large the payment is"),
    ("Amount vs their usual", "How far above this customer's typical spend, in standard deviations"),
    ("Amount vs their largest", "Compared with the biggest payment they have made"),
    ("Tiny amount", "Under £5, the signature of card testing"),
    ("Payments, last 10 min / hour / 24 h", "Velocity"),
    ("Tiny payments, last hour", "A run of card tests"),
    ("New device / location / merchant", "Not seen in this customer's history before the last 24 hours, so a fraudster's own first payment can't vouch for the next one"),
    ("Unusual hour for them", "How rarely this customer pays at this time of day; adapts to any time zone"),
    ("Time since last payment", "Minutes since their previous transaction"),
    ("Length of history", "How much evidence the judgement rests on"),
    ("Night (UTC)", "Kept for comparison with Loop 1"),
]

assert len(features.FEATURE_NAMES) == len(FEATURE_LABEL)
