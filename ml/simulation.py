"""Labelled synthetic transaction generator (standard library only).

Loop 1's simulator injected anomalies that were, almost by construction, the
things the Loop 1 rules looked for. To measure anything we need two changes:

1. Every transaction carries a ground-truth label (`is_fraud`) and the name of
   the scenario that produced it (`scenario`), so precision and recall can be
   computed.
2. Legitimate customers do odd-but-innocent things too, so a detector can be
   wrong in both directions:

   legit:high_spender   business accounts that routinely spend thousands
   legit:night_owl      customers whose normal hours fall in the UTC night
                        (shift workers, or simply living in the US)
   legit:big_purchase   a one-off purchase several times the usual amount
   legit:new_payee      a bank transfer to someone never paid before (rent,
                        a friend), the innocent twin of a scam payment
   legit:large_new_payee  a big genuine transfer to someone new (a builder,
                        a car): looks exactly like a scam payment
   legit:micro          tiny payments (parking, app trials), the innocent
                        twin of card testing
   legit:new_device     a phone upgrade: a new device that then becomes normal
   legit:travel         a few days abroad, so a new location
   legit:burst          several small payments in quick succession

Fraud scenarios (label 1), modelled loosely on common UK fraud types:

   account_takeover     a criminal using stolen credentials: new device, often
                        abroad, at any hour, a few purchases well above the
                        customer's usual spend (usually *below* 5,000). 30%
                        are remote-access scams on the victim's own device.
   card_testing         stolen card details checked with a run of tiny
                        payments, sometimes followed by one cash-out purchase
   app_scam             authorised push payment fraud: the real customer, on
                        their own device, at home, during normal hours, is
                        tricked into paying a new payee. Hard by design: the
                        only signals are the amount and the new payee.

Caveat, stated once here and again in the dev log: the detectors are being
tested on data whose generating rules we wrote. Good numbers here show that a
model can learn these patterns, not that it would catch real fraud.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

# country -> fixed UTC offset in hours (daylight saving ignored for simplicity)
HOMES = {"GB": 0, "IE": 0, "FR": 1, "DE": 1, "US": -5}
HOME_WEIGHTS = {"GB": 0.70, "IE": 0.08, "FR": 0.07, "DE": 0.07, "US": 0.08}
TRAVEL_DESTINATIONS = ["ES", "IT", "PT", "NL", "US", "FR", "GR"]
FRAUD_COUNTRIES = ["RO", "NG", "BR", "RU", "VN", "US", "NL"]

EVERYDAY_MERCHANTS = [
    "Tesco", "Sainsbury's", "Amazon", "Uber", "Shell", "Pret", "Netflix", "Boots",
    "Argos", "Deliveroo", "TfL", "Spotify", "Lidl", "Costa", "John Lewis", "Steam",
]
BURST_MERCHANTS = ["TfL", "Uber", "Deliveroo", "Costa"]
TESTING_MERCHANTS = ["Steam", "Netflix", "Spotify", "Amazon", "Apple Store"]
CASHOUT_MERCHANTS = ["ElectroWorld", "GiftCardHub", "CryptoXchange", "LuxWatches", "Apple Store"]
PAYEE_NAMES = [
    "J Smith", "A Khan", "M Patel", "L Jones", "S Brown", "R Taylor", "K Williams",
    "Safe Account Ltd", "HMRC Refunds", "Invest Partners", "D Evans", "P Wilson",
]

# Hour-of-day weights in the customer's *local* time.
DAY_PROFILE = [0, 0, 0, 0, 0, 0, 1, 3, 5, 5, 5, 6, 8, 7, 5, 5, 5, 6, 8, 8, 6, 4, 3, 1]
NIGHT_OWL_PROFILE = DAY_PROFILE[-5:] + DAY_PROFILE[:-5]  # same shape, 5 hours later

HISTORY_START = datetime(2026, 8, 1, tzinfo=timezone.utc)


@dataclass
class Customer:
    customer_id: str
    home: str
    utc_offset: int
    devices: list[str]
    mu: float  # mean of log(amount)
    sigma: float  # std of log(amount)
    rate_per_day: float
    night_owl: bool
    high_spender: bool
    favourite_merchants: list[str]
    # Mutable state while generating:
    payees: list[str] = field(default_factory=list)


@dataclass
class SimTxn:
    transaction_id: str
    customer_id: str
    amount: Decimal
    currency: str
    timestamp: datetime
    merchant: str
    location: str
    device_id: str
    status: str
    is_fraud: bool
    scenario: str  # "legit", "legit:<quirk>" or the fraud scenario name

    def api_body(self) -> dict:
        """The JSON the API accepts: no labels."""
        return {
            "transaction_id": self.transaction_id,
            "customer_id": self.customer_id,
            "amount": str(self.amount),
            "currency": self.currency,
            "timestamp": self.timestamp.isoformat(),
            "merchant": self.merchant,
            "location": self.location,
            "device_id": self.device_id,
            "status": self.status,
        }


# ----------------------------------------------------------------- helpers


def _money(x: float) -> Decimal:
    return Decimal(f"{max(0.5, min(x, 99_999.0)):.2f}")


def _poisson(rng: random.Random, lam: float) -> int:
    # Knuth's method; fine for the small rates used here.
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def _device_id(rng: random.Random, prefix: str = "dev") -> str:
    return f"{prefix}_{rng.getrandbits(40):010x}"


def _weighted(rng: random.Random, weights: dict[str, float]) -> str:
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def _make_customer(i: int, rng: random.Random) -> Customer:
    home = _weighted(rng, HOME_WEIGHTS)
    high_spender = rng.random() < 0.05
    if high_spender:
        mu, sigma = math.log(rng.uniform(800, 4000)), rng.uniform(0.4, 0.6)
    else:
        mu, sigma = math.log(rng.uniform(12, 90)), rng.uniform(0.4, 0.8)
    return Customer(
        customer_id=f"cust_{i:05d}",
        home=home,
        utc_offset=HOMES[home],
        devices=[_device_id(rng) for _ in range(rng.choice([1, 1, 2]))],
        mu=mu,
        sigma=sigma,
        rate_per_day=rng.uniform(0.6, 4.0) if not high_spender else rng.uniform(0.5, 2.0),
        night_owl=rng.random() < 0.10,
        high_spender=high_spender,
        favourite_merchants=rng.sample(EVERYDAY_MERCHANTS, 5),
    )


def _local_hour_to_utc(day: datetime, local_hour: int, minute: int, offset: int) -> datetime:
    return day + timedelta(hours=local_hour - offset, minutes=minute)


# ------------------------------------------------------------- legit flow


def _legit_day(c: Customer, day: datetime, rng: random.Random, state: dict) -> list[SimTxn]:
    out: list[SimTxn] = []
    profile = NIGHT_OWL_PROFILE if c.night_owl else DAY_PROFILE
    traveling = state["trip"] and state["trip"][0] <= day < state["trip"][1]
    location = state["trip_country"] if traveling else c.home
    if state["upgrade_day"] is not None and day >= state["upgrade_day"] and not state["upgraded"]:
        c.devices = [state["new_device"]] + c.devices
        state["upgraded"] = True

    for _ in range(_poisson(rng, c.rate_per_day)):
        hour = rng.choices(range(24), weights=profile)[0]
        ts = _local_hour_to_utc(day, hour, rng.randint(0, 59), c.utc_offset)
        amount = math.exp(rng.gauss(c.mu, c.sigma))
        merchant = rng.choice(c.favourite_merchants) if rng.random() < 0.85 else rng.choice(EVERYDAY_MERCHANTS)
        device = c.devices[0] if rng.random() < 0.8 else rng.choice(c.devices)

        scenario = "legit"
        r = rng.random()
        if r < 0.02:
            amount *= rng.uniform(4, 12)
            scenario = "legit:big_purchase"
        elif r < 0.06:
            # Parking, a coffee top-up, an app trial: tiny amounts are normal too.
            amount = rng.uniform(0.5, 4.99)
            merchant = rng.choice(TESTING_MERCHANTS + ["TfL", "Costa", "RingGo"])
            scenario = "legit:micro"
        elif r < 0.065:
            # A large, genuine transfer to someone new: a builder, a car, a deposit.
            merchant = f"Payee: {rng.choice(PAYEE_NAMES)} {rng.randint(10, 99)}"
            c.payees.append(merchant)
            amount = rng.uniform(1500, 9000)
            scenario = "legit:large_new_payee"
        elif r < 0.09:
            # Transfer to a payee: new 40% of the time, otherwise a known one.
            if c.payees and rng.random() < 0.6:
                merchant = rng.choice(c.payees)
            else:
                merchant = f"Payee: {rng.choice(PAYEE_NAMES)} {rng.randint(10, 99)}"
                c.payees.append(merchant)
            amount = rng.uniform(50, 1500) if not c.high_spender else rng.uniform(1000, 9000)
            scenario = "legit:new_payee"

        if c.high_spender and scenario == "legit":
            scenario = "legit:high_spender"
        elif c.night_owl and scenario == "legit":
            scenario = "legit:night_owl"
        elif traveling and scenario == "legit":
            scenario = "legit:travel"
        elif state["upgraded"] and device == c.devices[0] and scenario == "legit" and day - state["upgrade_day"] < timedelta(days=2):
            scenario = "legit:new_device"

        out.append(_txn(rng, c, ts, amount, merchant, location, device, False, scenario))

    if rng.random() < 0.01:  # a quick run of small payments, e.g. a night out
        hour = rng.choices(range(24), weights=profile)[0]
        ts = _local_hour_to_utc(day, hour, rng.randint(0, 50), c.utc_offset)
        merchant = rng.choice(BURST_MERCHANTS)
        for _ in range(rng.randint(3, 5)):
            ts += timedelta(minutes=rng.randint(1, 4), seconds=rng.randint(0, 59))
            out.append(_txn(rng, c, ts, rng.uniform(3, 25), merchant, location, c.devices[0], False, "legit:burst"))
    return out


# -------------------------------------------------------------- fraud flow


def _fraud_event(c: Customer, day: datetime, rng: random.Random) -> list[SimTxn]:
    scenario = _weighted(rng, {"account_takeover": 0.45, "card_testing": 0.30, "app_scam": 0.25})
    typical = math.exp(c.mu)
    out: list[SimTxn] = []

    if scenario == "account_takeover":
        # 30% are remote-access scams: the criminal drives the victim's own
        # device, so there is no new device or location to spot.
        remote_access = rng.random() < 0.30
        device = c.devices[0] if remote_access else _device_id(rng, "dev")
        if remote_access:
            location = c.home
        else:
            location = rng.choice([x for x in FRAUD_COUNTRIES if x != c.home]) if rng.random() < 0.6 else c.home
        ts = day + timedelta(hours=rng.randint(0, 23), minutes=rng.randint(0, 59))  # criminals keep their own hours
        for _ in range(rng.randint(1, 4)):
            amount = min(typical * rng.uniform(3, 15), rng.uniform(2500, 9000))
            merchant = rng.choice(CASHOUT_MERCHANTS) if rng.random() < 0.8 else rng.choice(EVERYDAY_MERCHANTS)
            out.append(_txn(rng, c, ts, amount, merchant, location, device, True, scenario))
            ts += timedelta(minutes=rng.randint(2, 25))

    elif scenario == "card_testing":
        device = _device_id(rng, "dev")
        location = rng.choice(FRAUD_COUNTRIES + [c.home])
        ts = day + timedelta(hours=rng.randint(0, 23), minutes=rng.randint(0, 59))
        for _ in range(rng.randint(4, 9)):
            out.append(_txn(rng, c, ts, rng.uniform(0.5, 4.99), rng.choice(TESTING_MERCHANTS), location, device, True, scenario))
            ts += timedelta(seconds=rng.randint(20, 180))
        if rng.random() < 0.5:
            amount = min(typical * rng.uniform(5, 20), 6000)
            out.append(_txn(rng, c, ts, amount, rng.choice(CASHOUT_MERCHANTS), location, device, True, scenario))

    else:  # app_scam
        profile = NIGHT_OWL_PROFILE if c.night_owl else DAY_PROFILE
        hour = rng.choices(range(24), weights=profile)[0]
        ts = _local_hour_to_utc(day, hour, rng.randint(0, 40), c.utc_offset)
        payee = f"Payee: {rng.choice(PAYEE_NAMES)} {rng.randint(10, 99)}"
        for _ in range(rng.choice([1, 1, 2])):
            out.append(_txn(rng, c, ts, rng.uniform(1500, 9500), payee, c.home, c.devices[0], True, scenario))
            ts += timedelta(minutes=rng.randint(5, 40))

    return out


def _txn(rng, c, ts, amount, merchant, location, device, is_fraud, scenario) -> SimTxn:
    return SimTxn(
        transaction_id=f"sim_{rng.getrandbits(64):016x}",
        customer_id=c.customer_id,
        amount=_money(amount),
        currency="GBP",
        timestamp=ts,
        merchant=merchant,
        location=location,
        device_id=device,
        status="approved",
        is_fraud=is_fraud,
        scenario=scenario,
    )


# -------------------------------------------------------------------- API


def generate(
    customers: int = 1500,
    days: int = 45,
    seed: int = 7,
    fraud_customer_rate: float = 0.15,
    start: datetime = HISTORY_START,
) -> list[SimTxn]:
    """Simulate `days` days of activity for `customers` customers.

    Each customer has a `fraud_customer_rate` chance of suffering one fraud
    event, placed after their first five days so there is some history to
    compare against. Returns transactions sorted by timestamp.
    """
    rng = random.Random(seed)
    out: list[SimTxn] = []
    for i in range(customers):
        c = _make_customer(i, rng)
        state = {
            "trip": None,
            "trip_country": None,
            "upgrade_day": None,
            "upgraded": False,
            "new_device": _device_id(rng),
        }
        if rng.random() < 0.30:
            d0 = start + timedelta(days=rng.randint(3, max(3, days - 4)))
            state["trip"] = (d0, d0 + timedelta(days=rng.randint(3, 7)))
            state["trip_country"] = rng.choice([x for x in TRAVEL_DESTINATIONS if x != c.home])
        if rng.random() < 0.15:
            state["upgrade_day"] = start + timedelta(days=rng.randint(3, max(3, days - 1)))
        fraud_day = start + timedelta(days=rng.randint(5, days - 1)) if rng.random() < fraud_customer_rate and days > 5 else None

        for d in range(days):
            day = start + timedelta(days=d)
            out.extend(_legit_day(c, day, rng, state))
            if fraud_day == day:
                out.extend(_fraud_event(c, day, rng))

    end = start + timedelta(days=days)
    out = [t for t in out if start <= t.timestamp < end]
    out.sort(key=lambda t: (t.timestamp, t.transaction_id))
    return out
