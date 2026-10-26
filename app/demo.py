"""Demo mode: fill the database with simulated customers and replay a live feed.

The demo feed is the labelled simulator (ml/simulation.py) with a fixed
seed, so it is identical on every machine and every restart. That gives two
things without storing anything extra:

* "Advance the feed" just finds the next simulated transactions that aren't
  in the database yet and ingests them through the normal service layer.
* The answer key (which transactions were really fraud) can be rebuilt at any
  time from the seed. The detector never sees it; the web pages show it only
  when asked, to check the model's mistakes.

Demo mode is on by default (AEGIS_DEMO=1) and adds a reset button that
deletes every row, so turn it off (AEGIS_DEMO=0) anywhere real data lives.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import detection, features
from app.db import Base
from app.models import Transaction
from app.schemas import TransactionIn
from app.services import DuplicateTransactionError, build_records, ingest_transaction
from ml.simulation import HISTORY_START, SimTxn, generate


@dataclass(frozen=True)
class DemoConfig:
    customers: int = 40
    days: int = 21
    seed: int = 11
    fraud_customer_rate: float = 0.35  # well above real life, so there is plenty to look at
    preload_days: int = 17  # loaded at once; the rest arrives as the live feed


CONFIG = DemoConfig()
SIM_PREFIX = "sim_"


@lru_cache(maxsize=4)
def _feed(cfg: DemoConfig) -> tuple[SimTxn, ...]:
    return tuple(generate(customers=cfg.customers, days=cfg.days, seed=cfg.seed, fraud_customer_rate=cfg.fraud_customer_rate))


def feed() -> tuple[SimTxn, ...]:
    return _feed(CONFIG)


@lru_cache(maxsize=4)
def _labels(cfg: DemoConfig) -> dict[str, tuple[bool, str]]:
    return {t.transaction_id: (t.is_fraud, t.scenario) for t in _feed(cfg)}


def label(transaction_id: str) -> tuple[bool, str] | None:
    """(is_fraud, scenario) for a demo transaction; None for anything else."""
    return _labels(CONFIG).get(transaction_id)


def _as_input(t: SimTxn) -> TransactionIn:
    return TransactionIn(**t.api_body())


@dataclass(frozen=True)
class FeedStatus:
    loaded: int  # demo transactions in the database
    total: int
    clock: datetime | None  # timestamp of the latest demo transaction loaded

    @property
    def finished(self) -> bool:
        return self.loaded >= self.total


def status(session: Session) -> FeedStatus:
    loaded, clock = session.execute(
        select(func.count(), func.max(Transaction.timestamp)).where(Transaction.transaction_id.like(SIM_PREFIX + "%"))
    ).one()
    return FeedStatus(loaded=loaded, total=len(feed()), clock=clock)


def reset(session: Session) -> int:
    """Delete everything and load the first `preload_days` of the demo feed.

    Scores are computed in memory (same detector, same 200-transaction
    history window as the API) and written in one database transaction, so
    this takes seconds even against a remote database.
    """
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))

    cutoff = HISTORY_START + timedelta(days=CONFIG.preload_days)
    history: dict[str, deque] = defaultdict(lambda: deque(maxlen=features.HISTORY_LIMIT))
    count = 0
    for t in feed():
        if t.timestamp >= cutoff:
            break
        txn_in = _as_input(t)
        assessment = detection.assess(txn_in, list(history[t.customer_id]))
        txn, incident = build_records(txn_in, assessment)
        session.add(txn)
        if incident is not None:
            session.add(incident)
        history[t.customer_id].append(t)
        count += 1
    session.commit()
    return count


def advance(session: Session, hours: int) -> int:
    """Ingest the demo transactions in the next `hours` of simulated time.

    Goes through the normal ingest path, one transaction at a time, exactly as
    if a payment system were calling the API.
    """
    st = status(session)
    if st.clock is None:
        return 0
    until = st.clock + timedelta(hours=hours)
    existing = set(session.scalars(select(Transaction.transaction_id).where(Transaction.timestamp > st.clock - timedelta(minutes=1))))
    added = 0
    for t in feed():
        if t.timestamp <= st.clock - timedelta(minutes=1) or t.transaction_id in existing:
            continue
        if t.timestamp > until:
            break
        try:
            ingest_transaction(session, _as_input(t))
            added += 1
        except DuplicateTransactionError:
            pass
    return added
