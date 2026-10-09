"""POST /v1/transactions: the ingestion endpoint."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_session
from app.schemas import IngestResult, TransactionOut, TransactionIn
from app.services import DuplicateTransactionError, ingest_transaction

router = APIRouter(prefix="/v1/transactions", tags=["transactions"])


@router.post(
    "",
    response_model=IngestResult,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"description": "A transaction with this transaction_id already exists"}},
)
def create_transaction(payload: TransactionIn, session: Session = Depends(get_session)) -> IngestResult:
    """Validate, store and score a transaction. Opens an incident if flagged."""
    try:
        txn, incident = ingest_transaction(session, payload)
    except DuplicateTransactionError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"transaction_id {payload.transaction_id!r} already exists",
        ) from None

    return IngestResult(
        transaction=TransactionOut.model_validate(txn),
        flagged=incident is not None,
        incident_id=incident.id if incident else None,
    )
