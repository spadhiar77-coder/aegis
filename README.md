# Aegis: AI Fintech Risk & Operations Platform

A backend-first, API-driven fraud/risk engine. It ingests transactions, scores
them for anomalies, turns suspicious activity into incidents, and gives human
analysts evidence to investigate. AI assists; the analyst decides.

This is a learning/portfolio project reproducing the core workflow of real
fraud systems. It is built in spiral loops (see [DEVLOG.md](DEVLOG.md)).

## Status: Loop 1 (skeleton)

| Area | Implemented now | Planned |
|---|---|---|
| Ingestion | `POST /v1/transactions`, strict Pydantic validation, duplicate `transaction_id` → 409 | Idempotency keys, API keys, batch import |
| Storage | PostgreSQL via SQLAlchemy; tables auto-created on startup | Alembic migrations |
| Detection | 4 hard-coded rules (amount, velocity, new device, night time) with reasons + evidence | Trained models (logistic regression, Isolation Forest) on a public dataset, measured precision/recall (Loop 2) |
| Incidents | Auto-created when score ≥ 0.5, with severity, linked transactions, evidence and an audit trail (`created` event) | Approve/dismiss, analyst actions in audit trail, Word report (Loop 3) |
| Read API | `GET /v1/incidents` (filters, pagination), `GET /v1/incidents/{id}` | |
| UI | Plain HTML incident list + detail at `/` | Streamlit or React |
| AI investigator | Not started | Loop 4 |
| Ops | Dockerfile, docker-compose, pytest suite | CI, monitoring, webhooks, queue (Loop 5) |

## How a transaction flows through Loop 1

```
POST /v1/transactions
   │  app/api/transactions.py   HTTP layer: status codes, error mapping
   ▼
TransactionIn (app/schemas.py)  Pydantic: rejects bad amounts, naive timestamps, unknown fields
   ▼
ingest_transaction (app/services.py)
   ├─ load the customer's earlier transactions from Postgres
   ├─ rules.assess(txn, history)   (app/rules.py: pure functions, no DB)
   │      → score in [0,1] + list of {rule, reason, evidence}
   ├─ save Transaction (with score + reasons)
   └─ if score ≥ 0.5: save Incident + link + IncidentEvent("created")
      all in one DB transaction
   ▼
201 {transaction, flagged, incident_id}
```

### Rules and scoring

| Rule | Fires when | Weight |
|---|---|---|
| `high_amount` | amount ≥ 5000.00 | 0.6 |
| `velocity` | ≥ 3 earlier transactions from the customer in the previous 10 min | 0.4 |
| `new_device` | customer has history and this device isn't in it | 0.3 |
| `night_time` | 00:00–04:59 **UTC** (not the customer's local time) | 0.2 |

Score = sum of the weights of the rules that fired, capped at 1.0. Flagged
at ≥ 0.5. Severity: ≥ 0.8 high, ≥ 0.6 medium, otherwise low. These numbers
are hand-picked placeholders, not calibrated; Loop 2 replaces them with a
model and real metrics.

## Running it

### Option A: everything in Docker

```bash
docker compose up --build
python scripts/simulate.py --count 200 --seed 7   # in another terminal
```

### Option B: Postgres in Docker, API on your machine (best for development)

```bash
docker compose up -d db
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
python scripts/simulate.py --count 200 --seed 7
```

Then open:

- http://localhost:8000/ : incident list (click through for detail)
- http://localhost:8000/docs : interactive OpenAPI docs
- http://localhost:8000/v1/incidents : raw JSON

Configuration is one env var, `DATABASE_URL` (see `.env.example`).

### Example request

```bash
curl -X POST localhost:8000/v1/transactions -H 'Content-Type: application/json' -d '{
  "transaction_id": "txn_1", "customer_id": "cust_1", "amount": "7500.00",
  "currency": "GBP", "timestamp": "2026-10-09T03:15:00Z", "merchant": "Argos",
  "location": "GB", "device_id": "dev_1", "status": "approved"}'
```

## Tests

Tests use a real PostgreSQL database (`aegis_test`, created automatically by
the compose `db` service on first start), not SQLite, so behaviour matches
production.

```bash
docker compose up -d db
pytest
```

Override the target with `TEST_DATABASE_URL`. The suite empties all tables
between tests, so never point it at data you care about.

## Layout

```
app/
  main.py            FastAPI app, router wiring, startup
  config.py          DATABASE_URL
  db.py              engine, sessions, table creation
  models.py          ORM tables: transactions, incidents, links, audit events
  schemas.py         Pydantic request/response contracts
  rules.py           Loop 1 detector (pure functions)
  services.py        ingest → score → incident
  api/               HTTP routes (transactions, incidents, HTML pages)
scripts/simulate.py  synthetic transaction generator (stdlib only)
tests/               rule unit tests + API end-to-end tests
```
