"""Pydantic schemas: the public API contract.

These are deliberately separate from the ORM models. The database layout can
change without breaking API clients, and FastAPI uses these classes to validate
requests, shape responses and generate the OpenAPI docs at /docs.
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

Severity = Literal["low", "medium", "high"]
TransactionStatus = Literal["pending", "approved", "declined", "reversed"]


# ---------------------------------------------------------------- requests


class TransactionIn(BaseModel):
    """What a client sends to POST /v1/transactions."""

    # extra="forbid": unknown fields are rejected, so typos surface as 422s
    # instead of being silently dropped.
    model_config = ConfigDict(extra="forbid")

    transaction_id: str = Field(min_length=1, max_length=64, examples=["txn_000123"])
    customer_id: str = Field(min_length=1, max_length=64, examples=["cust_42"])
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2, examples=["129.99"])
    currency: str = Field(default="GBP", pattern=r"^[A-Z]{3}$", examples=["GBP"])
    # AwareDatetime rejects naive timestamps ("2026-10-09T14:30:00" with no
    # offset) because "whose 14:30?" is ambiguous.
    timestamp: AwareDatetime = Field(examples=["2026-10-09T14:30:00Z"])
    merchant: str = Field(min_length=1, max_length=128, examples=["Tesco"])
    location: str = Field(min_length=1, max_length=128, description="Country code or city", examples=["GB"])
    device_id: str = Field(min_length=1, max_length=128, examples=["dev_ab12"])
    status: TransactionStatus = "approved"


# --------------------------------------------------------------- responses


class RuleHitOut(BaseModel):
    rule: str
    reason: str
    evidence: dict


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    transaction_id: str
    customer_id: str
    amount: Decimal
    currency: str
    timestamp: datetime
    merchant: str
    location: str
    device_id: str
    status: str
    risk_score: float
    risk_reasons: list[RuleHitOut]
    received_at: datetime


class IngestResult(BaseModel):
    """Response to POST /v1/transactions: the stored transaction plus, if it
    was flagged, the ID of the incident that was opened."""

    transaction: TransactionOut
    flagged: bool
    incident_id: int | None


class IncidentEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_type: str
    actor: str
    detail: dict
    created_at: datetime


class IncidentSummary(BaseModel):
    """Row shape for GET /v1/incidents (list view, no nested detail)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    severity: Severity
    status: str
    risk_score: float
    summary: str
    created_at: datetime


class IncidentDetail(IncidentSummary):
    """Full case file for GET /v1/incidents/{id}."""

    evidence: list[RuleHitOut]
    updated_at: datetime
    transactions: list[TransactionOut]
    events: list[IncidentEventOut]


class IncidentList(BaseModel):
    items: list[IncidentSummary]
    total: int
    limit: int
    offset: int
