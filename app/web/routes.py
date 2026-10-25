"""Web pages: incident queue and case file, transaction tester, results, about.

Routes return HTML built by Jinja2 templates in app/web/templates. Actions
(analyst decisions, sending a test transaction, demo controls) are HTML forms
that POST here; the handler makes the change and redirects back with a short
message in a one-time cookie, so a refresh never repeats the action.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, unquote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import config, demo, detection, rules
from app.db import get_session
from app.models import Incident, Transaction, incident_transactions
from app.schemas import TransactionIn
from app.services import DuplicateTransactionError, InvalidDecisionError, ingest_transaction, load_customer_history, record_decision
from app.web import present as P

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
templates.env.globals.update(
    gbp=P.gbp, score=P.score, when=P.when, pct=P.pct, short_device=P.short_device,
    SCENARIO=P.SCENARIO, STATUS_LABEL=P.STATUS_LABEL, EVENT_LABEL=P.EVENT_LABEL,
    DETECTOR_NAME=P.DETECTOR_NAME, CSS=Markup((HERE / "static" / "aegis.css").read_text()),
)

router = APIRouter(include_in_schema=False)

FLASH_COOKIE = "aegis_flash"
ANALYST_COOKIE = "aegis_analyst"
QUEUE_FILTERS = {"open": "Open", "all": "All", "decided": "Decided"}


# ------------------------------------------------------------------ helpers


def render(request: Request, template: str, status_code: int = 200, **ctx) -> HTMLResponse:
    flash = request.cookies.get(FLASH_COOKIE)
    ctx.update(request=request, flash=unquote(flash) if flash else None, demo_enabled=config.demo_enabled())
    response = templates.TemplateResponse(request, template, ctx, status_code=status_code)
    if flash:
        response.delete_cookie(FLASH_COOKIE)
    return response


def redirect(url: str, message: str | None = None) -> RedirectResponse:
    response = RedirectResponse(url, status_code=303)
    if message:
        response.set_cookie(FLASH_COOKIE, quote(message), max_age=60, httponly=True, samesite="lax")
    return response


def require_demo() -> None:
    if not config.demo_enabled():
        raise HTTPException(status_code=404, detail="Demo mode is off")


def queue_url(status: str = "open", key: bool = False, path: str = "/") -> str:
    params = {k: v for k, v in {"status": status if status != "open" else None, "key": "1" if key else None}.items() if v}
    return path + ("?" + urlencode(params) if params else "")


# ------------------------------------------------------------------ queue + case


def _answer_key(session: Session) -> dict:
    """Compare what the detector flagged with the simulator's labels."""
    flagged_ids = select(incident_transactions.c.transaction_id)
    rows = session.execute(
        select(Transaction.id, Transaction.transaction_id, Transaction.customer_id, Transaction.id.in_(flagged_ids))
        .where(Transaction.transaction_id.like(demo.SIM_PREFIX + "%"))
        .order_by(Transaction.timestamp.desc())
    ).all()
    tp = fp = fn = 0
    attacks: dict[str, bool] = {}
    missed: list[int] = []
    for pk, tid, cust, flagged in rows:
        lab = demo.label(tid)
        if lab is None:
            continue
        if lab[0]:
            attacks[cust] = attacks.get(cust, False) or bool(flagged)
            if flagged:
                tp += 1
            else:
                fn += 1
                missed.append(pk)
        elif flagged:
            fp += 1
    missed_txns = [
        (t, demo.label(t.transaction_id)[1])
        for t in session.scalars(select(Transaction).where(Transaction.id.in_(missed[:60])).order_by(Transaction.timestamp.desc()))
    ]
    return {"tp": tp, "fp": fp, "fn": fn, "attacks": len(attacks), "attacks_caught": sum(attacks.values()), "missed": missed_txns, "missed_total": len(missed)}


def _case(session: Session, txn: Transaction, incident: Incident | None, key: bool) -> dict:
    history = [h for h in load_customer_history(session, txn) if h.id != txn.id]
    established = [h for h in history if h.timestamp < txn.timestamp - timedelta(hours=24)]
    known_devices = {h.device_id for h in established}
    known_locations = {h.location for h in established}
    rules_view = rules.assess(txn, history)
    threshold = (incident.events[0].detail.get("threshold") if incident and incident.events else None)
    if threshold is None:
        threshold = rules.FLAG_THRESHOLD if txn.detector == "rules" else detection_threshold()
    return {
        "txn": txn,
        "incident": incident,
        "meter": P.meter(txn.risk_score, threshold),
        "chart": P.contribution_bars(txn.risk_breakdown),
        "rules_view": rules_view,
        "recent": [txn] + history[:8],
        "history_size": len(history),
        "known_devices": known_devices,
        "known_locations": known_locations,
        "has_established": bool(established),
        "label": demo.label(txn.transaction_id) if key else None,
        "user_test": txn.transaction_id.startswith("web_"),
    }


def detection_threshold() -> float:
    from app.model import load_default

    return load_default().threshold


def _queue_page(request: Request, session: Session, status: str, key: bool, limit: int,
                txn: Transaction | None = None, incident: Incident | None = None) -> HTMLResponse:
    status = status if status in QUEUE_FILTERS else "open"
    key = key and config.demo_enabled()
    filters = {"open": [Incident.status == "open"], "decided": [Incident.status != "open"], "all": []}[status]
    incidents = session.scalars(
        select(Incident).where(*filters).order_by(Incident.id.desc()).limit(limit).options(selectinload(Incident.transactions))
    ).all()
    total_txns = session.scalar(select(func.count()).select_from(Transaction)) or 0
    total_inc = session.scalar(select(func.count()).select_from(Incident)) or 0
    open_inc = session.scalar(select(func.count()).select_from(Incident).where(Incident.status == "open")) or 0
    matching = session.scalar(select(func.count()).select_from(Incident).where(*filters)) or 0

    if txn is None and incident is None and incidents:
        incident = session.scalar(select(Incident).where(Incident.id == incidents[0].id).options(selectinload(Incident.transactions), selectinload(Incident.events)))
    if incident is not None and txn is None:
        txn = incident.transactions[0] if incident.transactions else None

    labels = {}
    if key:
        labels = {i.id: demo.label(i.transactions[0].transaction_id) for i in incidents if i.transactions}
    return render(
        request, "queue.html", active="queue",
        status=status, key=key, limit=limit, filters=QUEUE_FILTERS, queue_url=queue_url,
        incidents=incidents, labels=labels, matching=matching,
        stats={"txns": total_txns, "incidents": total_inc, "open": open_inc, "rate": 1000 * total_inc / total_txns if total_txns else 0},
        answer=_answer_key(session) if key else None,
        feed=demo.status(session) if config.demo_enabled() else None,
        case=_case(session, txn, incident, key) if txn is not None else None,
        analyst=unquote(request.cookies.get(ANALYST_COOKIE, "")),
    )


@router.get("/", response_class=HTMLResponse)
def queue_page(request: Request, status: str = "open", key: bool = False, limit: int = 100, session: Session = Depends(get_session)):
    return _queue_page(request, session, status, key, max(10, min(limit, 500)))


@router.get("/incidents/{incident_id}", response_class=HTMLResponse)
def incident_page(incident_id: int, request: Request, status: str = "open", key: bool = False, limit: int = 100,
                  session: Session = Depends(get_session)):
    incident = session.scalar(
        select(Incident).where(Incident.id == incident_id).options(selectinload(Incident.transactions), selectinload(Incident.events))
    )
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return _queue_page(request, session, status, key, max(10, min(limit, 500)), incident=incident)


@router.get("/transactions/{transaction_id}", response_class=HTMLResponse)
def transaction_page(transaction_id: str, request: Request, status: str = "open", key: bool = False,
                     session: Session = Depends(get_session)):
    txn = session.scalar(select(Transaction).where(Transaction.transaction_id == transaction_id).options(selectinload(Transaction.incidents)))
    if txn is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if txn.incidents:
        return redirect(queue_url(status, key, f"/incidents/{txn.incidents[0].id}"))
    return _queue_page(request, session, status, key, 100, txn=txn)


@router.post("/incidents/{incident_id}/decision")
async def decide(incident_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    incident = session.scalar(select(Incident).where(Incident.id == incident_id).options(selectinload(Incident.events)))
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    action = str(form.get("action", ""))
    analyst = str(form.get("analyst", "")).strip()
    back = queue_url(str(form.get("status", "open")), form.get("key") == "1", f"/incidents/{incident_id}")
    if not analyst:
        return redirect(back, "Enter your name before recording a decision.")
    try:
        record_decision(session, incident, action, analyst, str(form.get("note", "")))
    except InvalidDecisionError as e:
        return redirect(back, str(e) + ".")
    done = {"confirm": "Confirmed as fraud", "dismiss": "Dismissed as genuine", "reopen": "Reopened", "note": "Note added"}[action]
    response = redirect(back, f"Incident #{incident_id}: {done.lower()}.")
    response.set_cookie(ANALYST_COOKIE, quote(analyst[:48]), max_age=60 * 60 * 24 * 180, httponly=True, samesite="lax")
    return response


# ------------------------------------------------------------------ test a transaction


def _customers(session: Session) -> list[tuple[str, int]]:
    rows = session.execute(
        select(Transaction.customer_id, func.count()).group_by(Transaction.customer_id).order_by(Transaction.customer_id).limit(500)
    ).all()
    return [(c, n) for c, n in rows]


def _parse_when(value: str | None) -> datetime | None:
    try:
        return datetime.strptime(value or "", "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _test_form(request: Request, session: Session) -> dict:
    q = request.query_params
    customers = _customers(session)
    ids = [c for c, _ in customers]
    busiest = sorted(customers, key=lambda c: -c[1])
    customer = q.get("customer") or (busiest[min(3, len(busiest) - 1)][0] if busiest else "cust_new")
    hist = session.scalars(
        select(Transaction).where(Transaction.customer_id == customer).order_by(Transaction.timestamp.desc()).limit(200)
    ).all()
    prof = P.profile(customer, hist)
    now = session.scalar(select(func.max(Transaction.timestamp))) or datetime.now(timezone.utc)
    preset = q.get("preset")
    if preset not in P.PRESETS and not q.get("amount"):
        preset = "takeover"
    if preset in P.PRESETS:
        v = P.preset_values(preset, prof, now)
        form = {"amount": v["amount"], "merchant": v["merchant"], "location": v["location"], "device": v["device"],
                "when": v["when"].strftime("%Y-%m-%dT%H:%M"), "prelude": v["prelude"]}
    else:
        form = {"amount": q.get("amount", ""), "merchant": q.get("merchant", ""), "location": q.get("location", prof.home),
                "device": q.get("device", prof.main_device), "when": q.get("when", ""), "prelude": q.get("prelude") == "1"}
    return {"customer": customer, "customers": customers, "known_customer": customer in ids, "profile": prof,
            "preset": preset if preset in P.PRESETS else None, "form": form}


def _build_test(customer: str, form: dict) -> tuple[TransactionIn | None, list, str | None]:
    """Validate the form; return the transaction, its card-testing prelude and any error."""
    when = _parse_when(form.get("when"))
    if when is None:
        return None, [], "Enter the time as a date and time."
    try:
        amount = Decimal(str(form.get("amount", ""))).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None, [], "Enter the amount as a number, like 25.00."
    try:
        txn = TransactionIn(
            transaction_id="web_" + secrets.token_hex(6), customer_id=customer, amount=amount, timestamp=when,
            merchant=(form.get("merchant") or "").strip(), location=(form.get("location") or "").strip(),
            device_id=(form.get("device") or "").strip(),
        )
    except ValidationError as e:
        field = e.errors()[0]["loc"][0]
        msg = {"amount": "The amount must be more than zero.", "merchant": "Enter a merchant or payee.",
               "device_id": "Choose a device.", "location": "Choose a location."}.get(field, "Check the form.")
        return None, [], msg
    prelude = []
    if form.get("prelude"):
        for i, (dt, amt) in enumerate(P.CARD_TEST_PRELUDE):
            prelude.append(txn.model_copy(update={"transaction_id": f"{txn.transaction_id}_p{i}", "amount": Decimal(amt),
                                                  "timestamp": when + timedelta(seconds=dt)}))
    return txn, prelude, None


@router.get("/test", response_class=HTMLResponse)
def test_page(request: Request, session: Session = Depends(get_session)):
    ctx = _test_form(request, session)
    txn, prelude, error = _build_test(ctx["customer"], ctx["form"])
    result = None
    if txn is not None:
        history = list(load_customer_history(session, txn)) + [SimpleNamespace(**p.model_dump()) for p in prelude]
        model_view = detection.assess(txn, history)
        result = {
            "model": model_view,
            "rules": rules.assess(txn, history),
            "meter": P.meter(model_view.score, model_view.threshold),
            "chart": P.contribution_bars(model_view.breakdown),
        }
    new_dev = P.new_device_id(ctx["customer"])
    devices = ctx["profile"].devices + ([(new_dev, 0)] if all(d != new_dev for d, _ in ctx["profile"].devices) else [])
    return render(request, "test.html", active="test", error=error, result=result, presets=P.PRESETS, devices=devices,
                  new_device=new_dev, countries=sorted(set(P.COUNTRIES) | {c for c, _ in ctx["profile"].locations}),
                  merchants=P.MERCHANTS, **ctx)


@router.post("/test")
async def send_test(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    customer = str(form.get("customer", "")).strip() or "cust_new"
    values = {k: str(form.get(k, "")) for k in ("amount", "merchant", "location", "device", "when")}
    values["prelude"] = form.get("prelude") == "1"
    txn, prelude, error = _build_test(customer, values)
    if error:
        return redirect("/test?" + urlencode({"customer": customer, **{k: v for k, v in values.items() if k != "prelude"},
                                               "prelude": "1" if values["prelude"] else ""}))  # the form page shows the error
    try:
        for p in prelude:
            ingest_transaction(session, p)
        stored, incident = ingest_transaction(session, txn)
    except DuplicateTransactionError:
        return redirect("/test", "That transaction was already sent.")
    if incident is not None:
        return redirect(f"/incidents/{incident.id}?status=all", f"Aegis opened incident #{incident.id} for your transaction.")
    return redirect(f"/transactions/{stored.transaction_id}?status=all", "Your transaction passed without an alert.")


# ------------------------------------------------------------------ reading pages


@router.get("/results", response_class=HTMLResponse)
def results_page(request: Request):
    return render(request, "results.html", active="results", R=P.loop2_results())


@router.get("/how", response_class=HTMLResponse)
def how_page(request: Request):
    return render(request, "how.html", active="how", glossary=P.FEATURE_GLOSSARY)


# ------------------------------------------------------------------ demo controls


@router.post("/demo/reset", dependencies=[Depends(require_demo)])
def demo_reset(session: Session = Depends(get_session)):
    n = demo.reset(session)
    return redirect("/", f"Demo loaded: {n:,} transactions from {demo.CONFIG.customers} simulated customers.")


@router.post("/demo/advance", dependencies=[Depends(require_demo)])
async def demo_advance(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    try:
        hours = max(1, min(int(form.get("hours", "6")), 72))
    except ValueError:
        hours = 6
    before = session.scalar(select(func.count()).select_from(Incident)) or 0
    added = demo.advance(session, hours)
    opened = (session.scalar(select(func.count()).select_from(Incident)) or 0) - before
    back = queue_url(str(form.get("status", "open")), form.get("key") == "1")
    msg = f"{added} new transactions in the next {hours} hours" + (f", {opened} new incident{'s' if opened != 1 else ''}." if added else ".")
    return redirect(back, msg)
