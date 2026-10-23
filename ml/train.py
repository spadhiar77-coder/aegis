"""Loop 2: train detectors, compare them with the Loop 1 rules, export the model.

    python -m ml.train                  # train, evaluate, write model + report
    python -m ml.train --seeds 5        # also repeat on 5 independent datasets
    python -m ml.train --check          # retrain and confirm the committed model matches

Detectors compared on the same time-based test window:

  rules     Loop 1's hand-weighted rules, flag threshold 0.5 (as deployed)
  logreg    logistic regression on the behavioural features (deployed in Loop 2)
  iforest   Isolation Forest: unsupervised, never sees a label
  gbm       gradient-boosted trees: a "how much are we leaving on the table"
            reference, not deployed (harder to explain per decision)

Thresholds for the learned detectors are chosen on the validation window
(maximum F1) and then frozen before the test window is scored, so the test
numbers are not tuned to the test data.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier, IsolationForest
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, precision_recall_curve, roc_auc_score
from sklearn.preprocessing import StandardScaler

from app import features, rules
from ml import dataset, simulation

ROOT = Path(__file__).resolve().parent.parent
MODEL_PATH = ROOT / "app" / "model" / "model.json"
REPORT_DIR = ROOT / "reports"


# ------------------------------------------------------------ evaluation


def best_f1_threshold(y: np.ndarray, scores: np.ndarray) -> float:
    precision, recall, thresholds = precision_recall_curve(y, scores)
    f1 = 2 * precision * recall / np.clip(precision + recall, 1e-12, None)
    # precision_recall_curve returns one more P/R point than thresholds.
    return float(thresholds[int(np.argmax(f1[:-1]))])


def evaluate(ds: dataset.Dataset, scores: np.ndarray, threshold: float) -> dict:
    y = ds.y
    flagged = scores >= threshold
    tp = int(np.sum(flagged & (y == 1)))
    fp = int(np.sum(flagged & (y == 0)))
    fn = int(np.sum(~flagged & (y == 1)))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0

    by_scenario = {}
    for s in sorted({x for x in ds.scenario if not x.startswith("legit")}):
        m = ds.scenario == s
        by_scenario[s] = {"recall": float(flagged[m].mean()), "n": int(m.sum())}

    # An attack is "caught" if any of its transactions is flagged. Each
    # simulated customer suffers at most one attack, so customer = attack.
    events: dict[str, bool] = defaultdict(bool)
    for t, f in zip(ds.txns, flagged):
        if t.is_fraud:
            events[t.customer_id] |= bool(f)

    fraud_amount = sum(float(t.amount) for t in ds.txns if t.is_fraud)
    caught_amount = sum(float(t.amount) for t, f in zip(ds.txns, flagged) if t.is_fraud and f)

    fp_sources = Counter(s for s, f, label in zip(ds.scenario, flagged, y) if f and label == 0)

    return {
        "pr_auc": float(average_precision_score(y, scores)),
        "roc_auc": float(roc_auc_score(y, scores)),
        "threshold": float(threshold),
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "alerts_per_1000": 1000 * (tp + fp) / len(y),
        "attacks_caught": sum(events.values()),
        "attacks_total": len(events),
        "fraud_value_caught": caught_amount / fraud_amount if fraud_amount else 0.0,
        "recall_by_scenario": by_scenario,
        "false_positives_by_source": dict(fp_sources.most_common()),
    }


# --------------------------------------------------------------- training


def run(seed: int, customers: int, days: int, train_days: int, val_days: int, verbose: bool = True):
    def log(msg: str) -> None:
        if verbose:
            print(msg, file=sys.stderr, flush=True)

    log(f"[seed {seed}] simulating {customers} customers x {days} days ...")
    txns = simulation.generate(customers=customers, days=days, seed=seed)
    log(f"[seed {seed}] {len(txns)} transactions, {sum(t.is_fraud for t in txns)} fraudulent; building features ...")
    full = dataset.build(txns)
    train, val, test = dataset.time_split(full, simulation.HISTORY_START, train_days, val_days)
    log(f"[seed {seed}] train {len(train.y)} / val {len(val.y)} / test {len(test.y)} rows")

    scaler = StandardScaler().fit(train.X)
    Xtr, Xva, Xte = scaler.transform(train.X), scaler.transform(val.X), scaler.transform(test.X)

    # No class weighting: the output then stays close to a probability (on
    # the simulator's fraud rate), which makes severity bands meaningful.
    logreg = LogisticRegression(C=1.0, max_iter=5000).fit(Xtr, train.y)
    iforest = IsolationForest(n_estimators=300, random_state=seed).fit(train.X)
    gbm = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, class_weight="balanced", random_state=seed).fit(
        train.X, train.y
    )

    scorers = {
        "rules": lambda d, Xs: d.rule_scores,
        "logreg": lambda d, Xs: logreg.predict_proba(Xs)[:, 1],
        "iforest": lambda d, Xs: -iforest.score_samples(d.X),
        "gbm": lambda d, Xs: gbm.predict_proba(d.X)[:, 1],
    }
    results = {}
    for name, score in scorers.items():
        threshold = rules.FLAG_THRESHOLD if name == "rules" else best_f1_threshold(val.y, score(val, Xva))
        results[name] = evaluate(test, score(test, Xte), threshold)

    # Not a model: logreg plus one guardrail rule (large AND to a new
    # merchant/payee). Evaluated to price the trade-off for scam payments,
    # which no transaction-level feature separates from genuine ones.
    lr_flag = scorers["logreg"](test, Xte) >= results["logreg"]["threshold"]
    i_amt, i_new = features.FEATURE_NAMES.index("log_amount"), features.FEATURE_NAMES.index("new_merchant")
    guard = (test.X[:, i_amt] >= np.log1p(float(rules.HIGH_AMOUNT_THRESHOLD))) & (test.X[:, i_new] == 1)
    hybrid = evaluate(test, (lr_flag | guard).astype(float), 0.5)
    hybrid["pr_auc"] = hybrid["roc_auc"] = float("nan")  # a yes/no flag has no ranking
    results["logreg+guardrail"] = hybrid

    model = {
        "format": "aegis-logreg/1",
        "version": f"logreg-seed{seed}-{customers}c{days}d",
        "trained_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "features": features.FEATURE_NAMES,
        "mean": scaler.mean_.tolist(),
        "scale": scaler.scale_.tolist(),
        "coef": logreg.coef_[0].tolist(),
        "intercept": float(logreg.intercept_[0]),
        "threshold": results["logreg"]["threshold"],
        "training": {
            "seed": seed,
            "customers": customers,
            "days": days,
            "train_days": train_days,
            "val_days": val_days,
            "rows": {"train": len(train.y), "val": len(val.y), "test": len(test.y)},
            "fraud_rows": {"train": int(train.y.sum()), "val": int(val.y.sum()), "test": int(test.y.sum())},
            "sklearn": sklearn.__version__,
            "numpy": np.__version__,
        },
        "test_metrics": {k: results["logreg"][k] for k in ("pr_auc", "precision", "recall", "alerts_per_1000")},
    }
    return results, model


# ---------------------------------------------------------------- reports


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _num(x: float) -> str:
    return "n/a" if x != x else f"{x:.3f}"  # NaN != NaN


def write_report(results: dict, model: dict, robustness: dict | None, path: Path) -> None:
    names = list(results)
    lines = [
        "# Loop 2 results",
        "",
        f"Generated by `python -m ml.train` on {model['trained_at']}. "
        f"Simulated data: {model['training']['customers']} customers, {model['training']['days']} days, seed {model['training']['seed']}. "
        f"Time-based split: first {model['training']['train_days']} days train, next {model['training']['val_days']} validate "
        "(threshold choice), remainder test. All numbers below are on the test window.",
        "",
        f"Test window: {model['training']['rows']['test']} transactions, {model['training']['fraud_rows']['test']} fraudulent.",
        "",
        "| | " + " | ".join(names) + " |",
        "|---|" + "---|" * len(names),
    ]

    def row(label, fn):
        lines.append(f"| {label} | " + " | ".join(fn(results[n]) for n in names) + " |")

    row("PR-AUC (ranking quality)", lambda r: _num(r["pr_auc"]))
    row("ROC-AUC", lambda r: _num(r["roc_auc"]))
    row("Threshold", lambda r: f"{r['threshold']:.3f}")
    row("Precision", lambda r: _pct(r["precision"]))
    row("Recall (transactions)", lambda r: _pct(r["recall"]))
    row("F1", lambda r: f"{r['f1']:.3f}")
    row("Alerts per 1,000 txns", lambda r: f"{r['alerts_per_1000']:.1f}")
    row("Attacks caught", lambda r: f"{r['attacks_caught']}/{r['attacks_total']}")
    row("Fraud value caught", lambda r: _pct(r["fraud_value_caught"]))
    for s in results["rules"]["recall_by_scenario"]:
        row(f"Recall: {s}", lambda r, s=s: _pct(r["recall_by_scenario"][s]["recall"]))

    lines += ["", "## Where the false alarms come from", "", "| Source | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    sources = sorted({s for r in results.values() for s in r["false_positives_by_source"]})
    for s in sources:
        lines.append(f"| {s} | " + " | ".join(str(results[n]["false_positives_by_source"].get(s, 0)) for n in names) + " |")

    lines += ["", "## Deployed model: logistic regression coefficients", "",
              "Per standard deviation of each feature, in log-odds. Positive pushes towards fraud.", "",
              "| Feature | Coefficient |", "|---|---|"]
    for name, c in sorted(zip(model["features"], model["coef"]), key=lambda x: -abs(x[1])):
        lines.append(f"| `{name}` | {c:+.2f} |")

    if robustness:
        lines += ["", f"## Robustness: {robustness['n']} independent datasets", "",
                  f"Seeds {robustness['seeds']}, {robustness['customers']} customers x {robustness['days']} days each. Mean ± standard deviation.", "",
                  "| Metric | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
        for metric in ("pr_auc", "precision", "recall", "alerts_per_1000"):
            cells = []
            for n in names:
                v = np.array(robustness["values"][n][metric])
                if metric == "pr_auc" and np.isnan(v).all():
                    cells.append("n/a")
                    continue
                fmt = (lambda x: f"{x:.3f}") if metric == "pr_auc" else (lambda x: f"{x:.1f}") if metric == "alerts_per_1000" else _pct
                cells.append(f"{fmt(v.mean())} ± {fmt(v.std())}")
            lines.append(f"| {metric} | " + " | ".join(cells) + " |")

    lines += ["", "Caveat: the data comes from our own simulator, so these numbers show the models can learn the "
              "simulated patterns. They are not evidence of performance on real fraud.", ""]
    path.write_text("\n".join(lines))


# -------------------------------------------------------------------- CLI


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--customers", type=int, default=1500)
    p.add_argument("--days", type=int, default=45)
    p.add_argument("--train-days", type=int, default=27)
    p.add_argument("--val-days", type=int, default=9)
    p.add_argument("--seeds", type=int, default=0, help="extra independent datasets for a robustness check")
    p.add_argument("--robust-customers", type=int, default=800)
    p.add_argument("--check", action="store_true", help="retrain and compare with the committed model; write nothing")
    p.add_argument("--report-only", action="store_true", help="rewrite the markdown report from the saved metrics")
    args = p.parse_args()

    if args.report_only:
        saved = json.loads((REPORT_DIR / "loop2_metrics.json").read_text())
        write_report(saved["results"], json.loads(MODEL_PATH.read_text()), saved["robustness"], REPORT_DIR / "loop2_results.md")
        return

    results, model = run(args.seed, args.customers, args.days, args.train_days, args.val_days)

    if args.check:
        committed = json.loads(MODEL_PATH.read_text())
        same = all(
            np.allclose(committed[k], model[k], rtol=1e-6, atol=1e-8) for k in ("mean", "scale", "coef", "intercept", "threshold")
        ) and committed["features"] == model["features"]
        print("model reproduces" if same else "MODEL DIFFERS from app/model/model.json: rerun python -m ml.train")
        sys.exit(0 if same else 1)

    robustness = None
    if args.seeds:
        seeds = [args.seed + 100 + i for i in range(args.seeds)]
        values = {n: defaultdict(list) for n in results}
        for s in seeds:
            r, _ = run(s, args.robust_customers, args.days, args.train_days, args.val_days)
            for n in r:
                for metric in ("pr_auc", "precision", "recall", "alerts_per_1000"):
                    values[n][metric].append(r[n][metric])
        robustness = {"n": len(seeds), "seeds": seeds, "customers": args.robust_customers, "days": args.days, "values": values}

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    MODEL_PATH.write_text(json.dumps(model, indent=2) + "\n")
    REPORT_DIR.mkdir(exist_ok=True)
    (REPORT_DIR / "loop2_metrics.json").write_text(
        json.dumps({"results": results, "robustness": robustness}, indent=2, default=list) + "\n"
    )
    write_report(results, model, robustness, REPORT_DIR / "loop2_results.md")
    print(f"wrote {MODEL_PATH.relative_to(ROOT)} and reports/loop2_results.md")
    for n, r in results.items():
        print(f"{n:8s} PR-AUC {r['pr_auc']:.3f}  precision {_pct(r['precision']):>6s}  recall {_pct(r['recall']):>6s}  "
              f"alerts/1k {r['alerts_per_1000']:5.1f}  attacks {r['attacks_caught']}/{r['attacks_total']}")


if __name__ == "__main__":
    main()
