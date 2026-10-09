"""Synthetic transaction simulator.

Posts a stream of mostly-ordinary transactions to the API, with a few
deliberately suspicious ones mixed in (large amounts, bursts, new devices,
night-time activity), so there is something for the rules to find.

Usage:
    python scripts/simulate.py --count 200 --url http://localhost:8000

Only uses the standard library so it runs without extra installs. `--seed`
makes a run reproducible.
"""

import argparse
import json
import random
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

MERCHANTS = ["Tesco", "Sainsbury's", "Amazon", "Uber", "Shell", "Pret", "Netflix", "Boots", "Argos", "Deliveroo"]
LOCATIONS = ["GB", "GB", "GB", "GB", "IE", "FR", "DE", "US"]


def make_customers(n: int, rng: random.Random) -> list[dict]:
    return [
        {
            "customer_id": f"cust_{i:04d}",
            "devices": [f"dev_{uuid.UUID(int=rng.getrandbits(128)).hex[:8]}" for _ in range(rng.randint(1, 2))],
            "home": rng.choice(LOCATIONS[:4]),
            "typical_amount": rng.uniform(10, 120),
        }
        for i in range(n)
    ]


def ordinary(c: dict, ts: datetime, rng: random.Random) -> dict:
    return {
        "customer_id": c["customer_id"],
        "amount": f"{max(1.0, rng.gauss(c['typical_amount'], c['typical_amount'] / 3)):.2f}",
        "currency": "GBP",
        "timestamp": ts.isoformat(),
        "merchant": rng.choice(MERCHANTS),
        "location": c["home"],
        "device_id": rng.choice(c["devices"]),
        "status": "approved",
    }


def generate(count: int, rng: random.Random, anomaly_rate: float) -> list[dict]:
    customers = make_customers(max(5, count // 10), rng)
    # Start yesterday at 08:00 UTC and walk forward.
    ts = datetime.now(timezone.utc).replace(hour=8, minute=0, second=0, microsecond=0) - timedelta(days=1)
    out: list[dict] = []
    while len(out) < count:
        ts += timedelta(minutes=rng.randint(1, 15))
        c = rng.choice(customers)
        t = ordinary(c, ts, rng)
        if rng.random() < anomaly_rate:
            kind = rng.choice(["big", "burst", "new_device", "night_big"])
            if kind == "big":
                t["amount"] = f"{rng.uniform(5000, 15000):.2f}"
            elif kind == "new_device":
                t["device_id"] = "dev_unknown_" + uuid.UUID(int=rng.getrandbits(128)).hex[:6]
                t["amount"] = f"{rng.uniform(800, 3000):.2f}"
            elif kind == "night_big":
                t["timestamp"] = ts.replace(hour=3).isoformat()
                t["amount"] = f"{rng.uniform(5000, 9000):.2f}"
            elif kind == "burst":
                # Several quick transactions; the 4th+ trips the velocity rule.
                for k in range(4):
                    b = ordinary(c, ts + timedelta(minutes=k), rng)
                    b["device_id"] = "dev_burst_" + c["customer_id"]
                    out.append(b)
                continue
        out.append(t)
    for t in out:
        t["transaction_id"] = "sim_" + uuid.UUID(int=rng.getrandbits(128)).hex[:16]
    return out[:count]


def post(url: str, body: dict) -> dict:
    req = urllib.request.Request(
        url + "/v1/transactions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--count", type=int, default=100)
    p.add_argument("--anomaly-rate", type=float, default=0.05)
    p.add_argument("--seed", type=int, default=None)
    args = p.parse_args()

    rng = random.Random(args.seed)
    flagged = errors = 0
    txns = generate(args.count, rng, args.anomaly_rate)
    for t in txns:
        try:
            result = post(args.url.rstrip("/"), t)
        except urllib.error.HTTPError as e:
            errors += 1
            print(f"{t['transaction_id']}: HTTP {e.code} {e.read().decode()[:200]}")
            continue
        if result["flagged"]:
            flagged += 1
            reasons = ", ".join(r["rule"] for r in result["transaction"]["risk_reasons"])
            print(f"FLAGGED {t['transaction_id']} -> incident #{result['incident_id']} ({reasons})")
    print(f"\nSent {len(txns)} transactions: {flagged} flagged, {errors} errors.")


if __name__ == "__main__":
    main()
