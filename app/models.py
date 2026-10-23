"""ORM models.

The schema is managed by Alembic (migrations/). After changing a model, run
`alembic revision --autogenerate -m "..."`, review the generated file, and
commit it with the model change; tests/test_migrations.py fails if the two
ever disagree.

Tables:

    transactions            one row per ingested transaction
    incidents               a case opened when a transaction looks suspicious
    incident_transactions   link table (an incident can cover many transactions,
                            a transaction can appear in many incidents)
    incident_events         append-only audit history of each incident

Money is stored as Numeric (exact decimal), never float, so 0.1 + 0.2 issues
cannot creep into amounts. Timestamps are timezone-aware.
"""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import JSON, Column, DateTime, Float, ForeignKey, Integer, Numeric, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


incident_transactions = Table(
    "incident_transactions",
    Base.metadata,
    Column("incident_id", ForeignKey("incidents.id", ondelete="CASCADE"), primary_key=True),
    Column("transaction_id", ForeignKey("transactions.id", ondelete="CASCADE"), primary_key=True),
)


class Transaction(Base):
    __tablename__ = "transactions"

    # Internal surrogate key; `transaction_id` is the caller's own ID.
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    customer_id: Mapped[str] = mapped_column(String(64), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    merchant: Mapped[str] = mapped_column(String(128))
    location: Mapped[str] = mapped_column(String(128))
    device_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32))

    # Output of the detector, stored on the transaction so every transaction
    # has a score and an explanation, not just the flagged ones.
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    risk_reasons: Mapped[list] = mapped_column(JSON, default=list)
    # Which detector scored it ("rules" or "model") and which version, so a
    # score can always be traced back to the exact logic that produced it.
    detector: Mapped[str] = mapped_column(String(32), default="rules", server_default="rules")
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    incidents: Mapped[list["Incident"]] = relationship(
        secondary=incident_transactions, back_populates="transactions"
    )


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    severity: Mapped[str] = mapped_column(String(16), index=True)  # low | medium | high
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    risk_score: Mapped[float] = mapped_column(Float)
    summary: Mapped[str] = mapped_column(String(512))
    # List of {"rule", "reason", "evidence"} dicts copied from the detector.
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    transactions: Mapped[list[Transaction]] = relationship(
        secondary=incident_transactions, back_populates="incidents"
    )
    events: Mapped[list["IncidentEvent"]] = relationship(
        back_populates="incident", order_by="IncidentEvent.id", cascade="all, delete-orphan"
    )


class IncidentEvent(Base):
    """One line of an incident's audit trail. Rows are only ever appended."""

    __tablename__ = "incident_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(32))  # Loop 1 only writes "created"
    actor: Mapped[str] = mapped_column(String(64))  # "system" for now; analyst IDs in Loop 3
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    incident: Mapped[Incident] = relationship(back_populates="events")
