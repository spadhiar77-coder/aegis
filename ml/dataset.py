"""Turn a simulated transaction stream into a labelled feature matrix.

Transactions are replayed in time order and each one gets the same view the
API would give it: that customer's most recent `HISTORY_LIMIT` earlier
transactions. Features come from app.features.compute, the exact function the
API calls, so offline and online features cannot drift apart.

The split is by time, not random: train on the earliest days, pick
thresholds on the next block, and report on the last block. A random split
would leak the future (a customer's later behaviour into training) and flatter
every model.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from app import features, rules
from ml.simulation import SimTxn


@dataclass
class Dataset:
    X: np.ndarray  # (n, len(FEATURE_NAMES))
    y: np.ndarray  # 0/1
    rule_scores: np.ndarray  # Loop 1 rules on the same history, for comparison
    scenario: np.ndarray  # str labels
    timestamp: np.ndarray  # datetime64
    txns: list[SimTxn]

    def subset(self, mask: np.ndarray) -> "Dataset":
        idx = np.flatnonzero(mask)
        return Dataset(
            X=self.X[idx],
            y=self.y[idx],
            rule_scores=self.rule_scores[idx],
            scenario=self.scenario[idx],
            timestamp=self.timestamp[idx],
            txns=[self.txns[i] for i in idx],
        )


def build(txns: list[SimTxn]) -> Dataset:
    history: dict[str, deque] = defaultdict(lambda: deque(maxlen=features.HISTORY_LIMIT))
    rows, rule_scores = [], []
    for t in txns:  # already in time order
        h = list(history[t.customer_id])
        rows.append([features.compute(t, h).values[name] for name in features.FEATURE_NAMES])
        rule_scores.append(rules.assess(t, h).score)
        history[t.customer_id].append(t)
    return Dataset(
        X=np.asarray(rows, dtype=float),
        y=np.asarray([t.is_fraud for t in txns], dtype=int),
        rule_scores=np.asarray(rule_scores, dtype=float),
        scenario=np.asarray([t.scenario for t in txns]),
        timestamp=np.asarray([t.timestamp.replace(tzinfo=None) for t in txns], dtype="datetime64[s]"),
        txns=txns,
    )


def time_split(ds: Dataset, start: datetime, train_days: int, val_days: int) -> tuple[Dataset, Dataset, Dataset]:
    """[start, +train) train, [+train, +train+val) validation, the rest test."""
    t1 = np.datetime64((start + timedelta(days=train_days)).replace(tzinfo=None))
    t2 = np.datetime64((start + timedelta(days=train_days + val_days)).replace(tzinfo=None))
    return ds.subset(ds.timestamp < t1), ds.subset((ds.timestamp >= t1) & (ds.timestamp < t2)), ds.subset(ds.timestamp >= t2)
