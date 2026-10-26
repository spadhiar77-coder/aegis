"""Replay labelled synthetic transactions through the running API.

Generates customers with ordinary behaviour, innocent quirks and a few fraud
attacks (see ml/simulation.py), posts every transaction to
POST /v1/transactions in time order, then compares the API's verdicts with the
ground-truth labels, which the API never sees.

Usage:
    python scripts/simulate.py --customers 30 --days 14 --seed 7
    python scripts/simulate.py --count 200 --seed 7        # stop after 200 transactions

Standard library only, so it runs without extra installs. `--seed` makes a
run reproducible (and re-running the same seed against the same database will
hit the duplicate check: use a fresh seed or database).
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ml.simulation import generate  # noqa: E402


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
    p.add_argument("--customers", type=int, default=30)
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--fraud-rate", type=float, default=0.15, help="share of customers who suffer one attack")
    p.add_argument("--count", type=int, default=None, help="stop after this many transactions")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--quiet", action="store_true", help="only print the summary")
    args = p.parse_args()

    seed = args.seed if args.seed is not None else int.from_bytes(__import__("os").urandom(4), "big")
    txns = generate(customers=args.customers, days=args.days, seed=seed, fraud_customer_rate=args.fraud_rate)
    if args.count is not None:
        txns = txns[: args.count]

    outcome = Counter()
    detector = None
    for t in txns:
        try:
            result = post(args.url.rstrip("/"), t.api_body())
        except urllib.error.HTTPError as e:
            outcome["error"] += 1
            print(f"{t.transaction_id}: HTTP {e.code} {e.read().decode()[:200]}")
            continue
        detector = detector or f"{result['transaction']['detector']} ({result['transaction']['model_version']})"
        flagged = result["flagged"]
        outcome[("flagged" if flagged else "passed", "fraud" if t.is_fraud else "legit")] += 1
        if flagged and not args.quiet:
            reasons = ", ".join(r["rule"] for r in result["transaction"]["risk_reasons"])
            truth = f"TRUE fraud ({t.scenario})" if t.is_fraud else f"false alarm ({t.scenario})"
            print(f"FLAGGED {t.transaction_id} -> incident #{result['incident_id']}: {truth}; {reasons}")
        elif t.is_fraud and not args.quiet:
            print(f"missed  {t.transaction_id}: {t.scenario} {t.amount} {t.currency} at {t.merchant}")

    tp, fp = outcome[("flagged", "fraud")], outcome[("flagged", "legit")]
    fn = outcome[("passed", "fraud")]
    print(f"\nSeed {seed}, detector {detector}: sent {len(txns)} transactions, {outcome['error']} errors.")
    print(f"Fraudulent: {tp + fn}. Caught {tp}, missed {fn}. False alarms: {fp}.")
    if tp + fp:
        print(f"Precision {tp / (tp + fp):.0%}", end="")
    if tp + fn:
        print(f", recall {tp / (tp + fn):.0%}", end="")
    print()
    sys.exit(1 if outcome["error"] else 0)


if __name__ == "__main__":
    main()
