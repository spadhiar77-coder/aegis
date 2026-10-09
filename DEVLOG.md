# Aegis dev log

One entry per spiral loop: plan, build, evaluate, improve.

---

## Loop 1: skeleton (2026-10-09)

### Plan
Get one thin path working end to end: a transaction comes in over REST, is
validated and stored in PostgreSQL, a hard-coded rule check scores it, a
suspicious one becomes an incident, and the incident can be read back via the
API and a basic web page. No auth, webhooks, queues, ML or LLM yet.

### Build
- **FastAPI + Pydantic** for the API. The request schema forbids unknown
  fields and requires timezone-aware timestamps and positive 2-dp amounts.
- **PostgreSQL + SQLAlchemy 2** with four tables: `transactions`, `incidents`,
  `incident_transactions` (many-to-many link) and `incident_events`
  (append-only audit trail). Money is `NUMERIC(14,2)`; timestamps are
  `timestamptz`.
- **Rules** (`app/rules.py`) are pure functions that take the transaction
  plus the customer's history. Each hit carries a reason string and an
  evidence dict, so every score is explainable. Four rules: high amount,
  velocity, new device, night time (UTC).
- **Service layer** (`app/services.py`) does ingest → score → incident in a
  single DB transaction. A duplicate `transaction_id` returns 409, including
  under a race (the unique constraint is the backstop).
- **Basic HTML page** at `/`, with all user-supplied text HTML-escaped.
- **Simulator** (`scripts/simulate.py`, stdlib only) generates ordinary
  traffic with injected anomalies; `--seed` makes runs reproducible.
- **Docker**: `Dockerfile` + `docker-compose.yml` (Postgres 16 + API).
- **Tests**: 15 pytest tests (7 rule unit tests, 8 API tests against real
  Postgres).

Deliberate shortcuts:
- Tables are created with `create_all` at startup; no migrations yet.
- The rules' history is the customer's last 200 transactions, queried per
  request. Fine at this scale; not how features should be built long-term.
- The night-time rule uses UTC, not the customer's local time.

### Evaluate
- `pytest`: 15 passed.
- Simulator, `--count 200 --seed 7`, 5% anomaly rate: **200 ingested, 6
  flagged, 0 errors**. Every incident was readable via
  `GET /v1/incidents/{id}` and the HTML page; `/docs` renders.
- The Docker image and compose file were written but not built in the dev
  environment; the API was run directly with uvicorn against a local
  Postgres 16.
- No precision/recall yet: the simulator doesn't record ground-truth labels,
  and the rule weights are hand-picked. Measuring is Loop 2's job.

Observations:
- **Velocity alone can't flag.** Its weight (0.4) is under the 0.5
  threshold, so a burst of small transactions on a known device scores 0.4
  and opens no incident. `new_device` alone (0.3) never flags either. Only
  `high_amount` can flag by itself. That's a weakness of additive
  hand-weights, not a bug.
- Night time in UTC will misfire for customers outside the UK.
- The rules see raw history with no notion of "normal for this customer"
  (e.g. typical amount), which is one of the most useful signals real
  systems use.

### Improve (inputs to Loop 2)
- Have the simulator emit ground-truth labels so we can measure precision
  and recall, and/or switch to a public labelled dataset.
- Build per-customer behavioural features (amount vs. own history,
  time-of-day profile, device count, velocity) as a separate step.
- Train a logistic regression baseline and an Isolation Forest, compare them
  to these rules on the same data, and record the numbers here.
- Keep the `{rule, reason, evidence}` output shape so incidents stay
  explainable when a model takes over.
- Add Alembic before the schema changes.
