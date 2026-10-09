"""A deliberately plain HTML view of incidents, for eyeballing results.

No templating engine or frontend framework: just f-strings with every
user-supplied value passed through html.escape (merchant names and IDs come
from API clients, so they must never be injected into the page raw).
A real UI (Streamlit or React) is a later-loop decision.
"""

from html import escape

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.incidents import load_incident
from app.db import get_session
from app.models import Incident

router = APIRouter(include_in_schema=False)

STYLE = """
body { font-family: system-ui, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; color: #222; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: .4rem .6rem; border-bottom: 1px solid #ddd; vertical-align: top; }
.sev-high { color: #b00020; font-weight: 600; } .sev-medium { color: #b26a00; } .sev-low { color: #555; }
code { background: #f4f4f4; padding: 0 .2rem; }
"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{escape(title)}</title><style>{STYLE}</style></head>"
        f"<body>{body}</body></html>"
    )


@router.get("/", response_class=HTMLResponse)
def incidents_page(session: Session = Depends(get_session)) -> HTMLResponse:
    rows = session.scalars(select(Incident).order_by(Incident.id.desc()).limit(100)).all()
    trs = "".join(
        f"<tr><td><a href='/incidents/{i.id}'>#{i.id}</a></td>"
        f"<td class='sev-{escape(i.severity)}'>{escape(i.severity)}</td>"
        f"<td>{escape(i.status)}</td><td>{i.risk_score:.2f}</td>"
        f"<td>{escape(i.summary)}</td><td>{i.created_at:%Y-%m-%d %H:%M}</td></tr>"
        for i in rows
    )
    table = (
        "<table><tr><th>ID</th><th>Severity</th><th>Status</th><th>Score</th><th>Summary</th><th>Created</th></tr>"
        f"{trs}</table>"
        if rows
        else "<p>No incidents yet. POST some transactions to <code>/v1/transactions</code>.</p>"
    )
    return _page(
        "Aegis incidents",
        f"<h1>Aegis incidents</h1><p>Latest 100. API docs at <a href='/docs'>/docs</a>.</p>{table}",
    )


@router.get("/incidents/{incident_id}", response_class=HTMLResponse)
def incident_page(incident_id: int, session: Session = Depends(get_session)) -> HTMLResponse:
    i = load_incident(session, incident_id)
    if i is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    evidence = "".join(
        f"<li><b>{escape(e['rule'])}</b>: {escape(e['reason'])}</li>" for e in i.evidence
    )
    txns = "".join(
        f"<tr><td>{escape(t.transaction_id)}</td><td>{escape(t.customer_id)}</td>"
        f"<td>{t.amount} {escape(t.currency)}</td><td>{escape(t.merchant)}</td>"
        f"<td>{escape(t.location)}</td><td>{escape(t.device_id)}</td><td>{t.timestamp:%Y-%m-%d %H:%M %Z}</td></tr>"
        for t in i.transactions
    )
    events = "".join(
        f"<li>{ev.created_at:%Y-%m-%d %H:%M:%S} · <b>{escape(ev.event_type)}</b> by {escape(ev.actor)}</li>"
        for ev in i.events
    )
    body = (
        f"<p><a href='/'>&larr; all incidents</a></p>"
        f"<h1>Incident #{i.id}</h1>"
        f"<p class='sev-{escape(i.severity)}'>Severity: {escape(i.severity)} · "
        f"Score {i.risk_score:.2f} · Status: {escape(i.status)}</p>"
        f"<p>{escape(i.summary)}</p>"
        f"<h2>Evidence</h2><ul>{evidence}</ul>"
        f"<h2>Linked transactions</h2><table><tr><th>Txn</th><th>Customer</th><th>Amount</th>"
        f"<th>Merchant</th><th>Location</th><th>Device</th><th>Time</th></tr>{txns}</table>"
        f"<h2>Audit history</h2><ul>{events}</ul>"
        f"<p><a href='/v1/incidents/{i.id}'>Raw JSON</a></p>"
    )
    return _page(f"Incident #{i.id}", body)
