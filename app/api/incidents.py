"""GET /v1/incidents and GET /v1/incidents/{id}: read-only in Loop 1."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db import get_session
from app.models import Incident
from app.schemas import IncidentDetail, IncidentList, IncidentSummary, Severity

router = APIRouter(prefix="/v1/incidents", tags=["incidents"])


@router.get("", response_model=IncidentList)
def list_incidents(
    session: Session = Depends(get_session),
    status_: str | None = Query(None, alias="status", description="Filter by status, e.g. open"),
    severity: Severity | None = Query(None, description="Filter by severity"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> IncidentList:
    """Newest incidents first, with simple filtering and offset pagination."""
    filters = []
    if status_ is not None:
        filters.append(Incident.status == status_)
    if severity is not None:
        filters.append(Incident.severity == severity)

    total = session.scalar(select(func.count()).select_from(Incident).where(*filters)) or 0
    rows = session.scalars(
        select(Incident).where(*filters).order_by(Incident.id.desc()).limit(limit).offset(offset)
    ).all()
    return IncidentList(
        items=[IncidentSummary.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def load_incident(session: Session, incident_id: int) -> Incident | None:
    # selectinload fetches the linked transactions and events up front (two
    # extra queries) instead of one query per row later.
    return session.scalar(
        select(Incident)
        .where(Incident.id == incident_id)
        .options(selectinload(Incident.transactions), selectinload(Incident.events))
    )


@router.get("/{incident_id}", response_model=IncidentDetail, responses={404: {"description": "Not found"}})
def get_incident(incident_id: int, session: Session = Depends(get_session)) -> IncidentDetail:
    """The full case: evidence, linked transactions and audit history."""
    incident = load_incident(session, incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    return IncidentDetail.model_validate(incident)
