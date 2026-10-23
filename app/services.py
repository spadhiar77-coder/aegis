"""Business logic: ingest a transaction, score it, open an incident if needed.

Kept out of the route handlers so it can be called from other entry points
later (a queue worker, a batch import) without going through HTTP.
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import detection, features, rules
from app.models import Incident, IncidentEvent, Transaction
from app.schemas import TransactionIn

# How many of the customer's previous transactions the detector gets to see.
# Shared with the offline training code so the model is scored on the same
# view of history it was trained on.
HISTORY_LIMIT = features.HISTORY_LIMIT


class DuplicateTransactionError(Exception):
    """Raised when a transaction_id has already been ingested."""


def load_customer_history(session: Session, txn: TransactionIn) -> list[Transaction]:
    """Most recent earlier transactions for the same customer, newest first."""
    stmt = (
        select(Transaction)
        .where(Transaction.customer_id == txn.customer_id)
        .where(Transaction.timestamp <= txn.timestamp)
        .order_by(Transaction.timestamp.desc())
        .limit(HISTORY_LIMIT)
    )
    return list(session.scalars(stmt))


def ingest_transaction(session: Session, txn_in: TransactionIn) -> tuple[Transaction, Incident | None]:
    """Store a transaction and, if the rules flag it, an incident.

    Everything happens in one database transaction: either the transaction,
    its incident and the audit event are all saved, or none of them are.
    """
    if session.scalar(select(Transaction.id).where(Transaction.transaction_id == txn_in.transaction_id)):
        raise DuplicateTransactionError(txn_in.transaction_id)

    history = load_customer_history(session, txn_in)
    assessment = detection.assess(txn_in, history)
    reasons = [hit.as_dict() for hit in assessment.hits]

    txn = Transaction(
        **txn_in.model_dump(),
        risk_score=assessment.score,
        risk_reasons=reasons,
        detector=assessment.detector,
        model_version=assessment.version,
    )
    session.add(txn)

    incident = None
    if assessment.flagged:
        incident = Incident(
            severity=assessment.severity,
            status="open",
            risk_score=assessment.score,
            summary=_summarise(txn_in, assessment),
            evidence=reasons,
            transactions=[txn],
        )
        incident.events.append(
            IncidentEvent(
                event_type="created",
                actor="system",
                detail={
                    "trigger_transaction_id": txn_in.transaction_id,
                    "rules": [h.rule for h in assessment.hits],
                    "score": assessment.score,
                    "threshold": assessment.threshold,
                    "detector": assessment.detector,
                    "model_version": assessment.version,
                },
            )
        )
        session.add(incident)

    try:
        session.commit()
    except IntegrityError:
        # Two requests with the same transaction_id raced past the check
        # above; the unique constraint caught the second one.
        session.rollback()
        raise DuplicateTransactionError(txn_in.transaction_id) from None

    return txn, incident


def _summarise(txn: TransactionIn, assessment: rules.Assessment) -> str:
    rule_names = ", ".join(h.rule for h in assessment.hits) or "overall risk score"
    return (
        f"{txn.amount} {txn.currency} at {txn.merchant} by customer {txn.customer_id} "
        f"flagged by: {rule_names}"
    )
