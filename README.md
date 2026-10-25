# Aegis: AI Fintech Risk & Operations Platform

A backend-first, API-driven fraud/risk engine. It ingests transactions, scores
them for anomalies, turns suspicious activity into incidents, and gives human
analysts evidence to investigate. AI assists; the analyst decides.

This is a learning/portfolio project reproducing the core workflow of real
fraud systems. It is built in spiral loops (see [DEVLOG.md](DEVLOG.md)).
Everything is Python: FastAPI serves both the JSON API and the analyst web
app, whose pages are rendered on the server with Jinja2 (no JavaScript).

## Status: Loop 3 (web app and analyst workflow)

| Area | Implemented now | Planned |
|---|---|---|
| Ingestion | `POST /v1/transactions`, strict Pydantic validation, duplicate `transaction_id` → 409 | Idempotency keys, API keys, batch import |
| Storage | PostgreSQL via SQLAlchemy, schema managed by Alembic migrations | |
| Detection | Logistic regression over 15 per-customer behavioural features, with per-decision explanations. Loop 1 rules kept as a fallback (`AEGIS_DETECTOR=rules`) | Retraining on analyst decisions; a public labelled dataset |
| Evaluation | Labelled simulator, time-based split, precision/recall/PR-AUC vs the rules, Isolation Forest and gradient boosting ([results](reports/loop2_results.md)) | |
| Incidents | Auto-created when the model's score passes its threshold, with severity, evidence, linked transactions and an audit trail recording detector + model version | Case reports |
| Analyst workflow | Confirm, dismiss, reopen, add notes; every action appended to the audit trail. In the web app and at `POST /v1/incidents/{id}/actions` | Logins and roles; decisions feeding retraining |
| Read API | `GET /v1/incidents` (filters, pagination), `GET /v1/incidents/{id}` | |
| Web app | Incident queue and case file (score breakdown, customer history, decisions), transaction tester, model results, demo mode with a simulated live feed and answer key | |
| AI investigator | Not started | Loop 4 |
| Ops | Dockerfile, docker-compose, pytest suite, GitHub Actions CI, one-click Render deployment | Monitoring, webhooks, queue (Loop 5) |

### Loop 2 headline (simulated data, held-out test window)

| | Loop 1 rules | Loop 2 model |
|---|---|---|
| PR-AUC | 0.118 | 0.880 |
| Precision | 13.6% | 94.1% |
| Recall | 19.8% | 79.0% |
| Alerts per 1,000 transactions | 7.6 | 4.4 |

The model finds four times as much fraud with about 40% fewer alerts. It
does *not* catch scam payments (authorised push payment fraud), which look
identical to genuine large transfers in this data; [DEVLOG.md](DEVLOG.md)
explains why and what a guardrail rule would cost. All numbers come from our
own simulator, so they show the model learns the simulated patterns, not
that it would catch real fraud.

## How a transaction flows

```
POST /v1/transactions
   │  app/api/transactions.py   HTTP layer: status codes, error mapping
   ▼
TransactionIn (app/schemas.py)  Pydantic: rejects bad amounts, naive timestamps, unknown fields
   ▼
ingest_transaction (app/services.py)
   ├─ load the customer's last 200 earlier transactions from Postgres
   ├─ detection.assess(txn, history)   (app/detection.py picks the detector)
   │      model:  app/features.py → 15 behavioural features (pure functions)
   │              app/model.py    → score = logistic(coefficients · features)
   │                                reasons = features that pushed hardest
   │      → score in [0,1] + list of {rule, reason, evidence}
   ├─ save Transaction (with score, reasons, detector, model_version)
   └─ if score ≥ threshold: save Incident + link + IncidentEvent("created")
      all in one DB transaction
   ▼
201 {transaction, flagged, incident_id}
```

### Features

Each compares the transaction with **that customer's** history, which is
what Loop 1's fixed thresholds could not do: 6,000 is routine for a business
account and alarming for someone who usually spends 30.

| Feature | Meaning |
|---|---|
| `log_amount`, `amount_z`, `amount_vs_max` | Amount, and how it compares with this customer's typical and largest spend |
| `is_tiny`, `tiny_count_1h` | Very small payments (card testing) |
| `count_10m`, `count_1h`, `count_24h`, `log_gap_minutes` | Velocity |
| `new_device`, `new_location`, `new_merchant` | Not seen in this customer's history before the last 24 hours |
| `hour_rarity`, `night_utc` | How unusual the time is for this customer |
| `log_history` | How much history the judgement is based on |

### Example explanation

```json
{"rule": "model:new_device",
 "reason": "Device dev_attacker not seen for this customer before the last 24 hours",
 "evidence": {"feature": "new_device", "value": 1.0, "contribution_log_odds": 5.431,
              "device_id": "dev_attacker", "known_device_count": 1}}
```

### Loop 1 rules (fallback detector)

| Rule | Fires when | Weight |
|---|---|---|
| `high_amount` | amount ≥ 5000.00 | 0.6 |
| `velocity` | ≥ 3 earlier transactions from the customer in the previous 10 min | 0.4 |
| `new_device` | customer has history and this device isn't in it | 0.3 |
| `night_time` | 00:00–04:59 **UTC** (not the customer's local time) | 0.2 |

Score = capped sum of the weights that fired; flagged at ≥ 0.5.

## The web app

Open http://localhost:8000/ once it's running (below).

| Page | What it does |
|---|---|
| Incident queue `/` | Open, all or decided incidents. Selecting one opens its case file: risk score against the threshold, the reasons, every feature's push on the score, the customer's recent activity, what the Loop 1 rules would have said, and the decision form and audit trail. |
| Test a transaction `/test` | Build a payment for any customer, or start from an example (stolen login abroad, card testing, scam payment). **Check score** scores it without saving; **Send to Aegis** ingests it like any other payment. |
| Model results `/results` | The Loop 2 evaluation: all five detectors, recall by fraud type, where false alarms come from, robustness across datasets. |
| How it works `/how` | The pipeline, the features and the roadmap. |

**Demo mode** (`AEGIS_DEMO=1`, the default) adds a **Load demo data** button
to an empty database: 40 simulated customers and 17 days of payments, scored
by the model. **Advance** sends the next hour, 6 hours or day of payments
through the normal ingest path, and **Show answer key** reveals which payments
were really fraud, including the fraud the model missed. **Restart demo**
deletes every row, so set `AEGIS_DEMO=0` anywhere real data lives.

Analysts type their name when they record a decision (the browser remembers
it). There are no logins yet, so don't expose an instance holding real data.

## Deploying

`render.yaml` is a blueprint for [Render](https://render.com): in the Render
dashboard choose **New > Blueprint** and pick this repository. It builds the
Docker image, creates a PostgreSQL database and connects the two; migrations
run on startup. Then open the app's URL and choose **Load demo data**. Free
plans sleep when idle (the first visit after that takes a little while) and
the free database is time-limited, so check Render's current terms.

Any other host that runs a Docker image works the same way: set
`DATABASE_URL` (a `postgres://` URL from the provider is fine) and the app
listens on `$PORT`.

## Running it

### Option A: everything in Docker

```bash
docker compose up --build
# then open http://localhost:8000/ and choose "Load demo data", or send traffic to the API:
python scripts/simulate.py --customers 30 --days 14 --seed 7   # in another terminal
```

### Option B: Postgres in Docker, API on your machine (best for development)

```bash
docker compose up -d db
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload          # applies database migrations on startup
python scripts/simulate.py --customers 30 --days 14 --seed 7
```

Then open:

- http://localhost:8000/ : the web app
- http://localhost:8000/docs : interactive OpenAPI docs
- http://localhost:8000/v1/incidents : raw JSON

The simulator prints each flagged transaction, whether it was really fraud
(the API never sees the labels), and a precision/recall summary at the end.

Configuration (see `.env.example`):

- `DATABASE_URL`: where PostgreSQL lives.
- `AEGIS_DETECTOR`: `model` (default) or `rules` (Loop 1).
- `AEGIS_DEMO`: `1` (default) shows the demo controls; `0` hides them.

### Training the model

```bash
pip install -r requirements-ml.txt
python -m ml.train              # ~3 min: simulate, train, evaluate, write the model + report
python -m ml.train --seeds 5    # also repeat on 5 independent datasets (~15 min)
python -m ml.train --check      # confirm app/model/model.json matches the training code
```

This writes `app/model/model.json` (the deployed model, as plain numbers)
and `reports/loop2_results.md`. If you change `app/features.py`, retrain:
the API refuses to load a model trained on a different feature list.

### Database migrations

The app applies pending migrations on startup. By hand:

```bash
alembic upgrade head                                # apply
alembic revision --autogenerate -m "describe change"  # after editing app/models.py; review the file
```

A database created by Loop 1 is recognised and upgraded in place, keeping its
data.

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
production. The schema is built by running the migrations, and a test fails
if the migrations and models ever disagree. GitHub Actions runs the suite,
the model reproducibility check and a Docker smoke test on every push and
pull request.

```bash
docker compose up -d db
pytest
```

Override the target with `TEST_DATABASE_URL`. The suite empties all tables
between tests, so never point it at data you care about.

## Layout

```
app/
  main.py            FastAPI app, router wiring, startup (runs migrations)
  config.py          DATABASE_URL, AEGIS_DETECTOR, AEGIS_DEMO
  db.py              engine, sessions, migration runner
  models.py          ORM tables: transactions, incidents, links, audit events
  schemas.py         Pydantic request/response contracts
  features.py        behavioural features (shared by training and the API)
  model.py           Loop 2 detector: scores with model/model.json, explains decisions
  model/model.json   the trained logistic regression, as plain numbers
  rules.py           Loop 1 detector (fallback) and the shared Assessment type
  detection.py       picks the detector
  services.py        ingest → score → incident; analyst decisions
  demo.py            demo mode: load simulated data, advance the feed, answer key
  api/               JSON API routes (transactions, incidents, decisions)
  web/               the web app: routes, presentation helpers, Jinja2 templates, CSS
ml/
  simulation.py      labelled synthetic data (stdlib only)
  dataset.py         replay → feature matrix, time-based split
  train.py           train, compare detectors, export model, write report
migrations/          Alembic migrations
reports/             evaluation results
scripts/simulate.py  replays simulated traffic through the API, scores it against the labels
tests/               unit, API, web, model and migration tests
render.yaml          one-click deployment on Render
```
