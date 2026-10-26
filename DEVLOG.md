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

---

## Loop 2: measure, then model (2026-10-23)

### Plan
Replace the hand-weighted rules with a trained model, and be able to say by
how much it is better: labelled data, per-customer features, a logistic
regression baseline and an Isolation Forest compared with the Loop 1 rules on
the same held-out data. Keep the `{rule, reason, evidence}` shape so incidents
stay explainable. Add Alembic before changing the schema.

### Build
- **Labelled simulator** (`ml/simulation.py`, stdlib only). Every
  transaction carries `is_fraud` and a `scenario`. Three fraud types
  (account takeover, card testing, authorised push payment scams) and, just
  as important, innocent look-alikes: business accounts that spend
  thousands, night owls and US customers, one-off big purchases, tiny
  payments, phone upgrades, travel, big genuine transfers to new payees.
  Without those, any detector looks good.
- **Behavioural features** (`app/features.py`): 15 numbers comparing a
  transaction with *that customer's* history (amount vs their usual,
  velocity, new device/location/merchant, how unusual the hour is for them).
  Pure Python, and the same function runs in training and in the API, so
  there is no training/serving skew.
- **Training and evaluation** (`ml/train.py`). Time-based split (27 days
  train, 9 validation, 9 test) so the future never leaks into training.
  Thresholds are picked on validation, then frozen before scoring the test
  window. Results in `reports/loop2_results.md`.
- **Model in the API** (`app/model.py`). The logistic regression is exported
  as plain JSON (coefficients, scaler, threshold), not a pickle: scoring is a
  dot product, so the API needs no scikit-learn and loads no executable
  files. Each incident lists the top features that pushed the score up, e.g.
  "Device dev_attacker not seen for this customer before the last 24 hours".
  `AEGIS_DETECTOR=rules` switches back to Loop 1.
- **Traceability**: each transaction records `detector` and
  `model_version`; incident audit events record the threshold too.
- **Alembic**: migration 0001 is exactly the Loop 1 schema, 0002 adds the
  detector columns. A Loop 1 database is detected and stamped at 0001 on
  startup, so existing data is kept.
- **CI** (GitHub Actions): full test suite against Postgres 16, a check that
  the committed model can be reproduced from the training code, and a Docker
  build + start + simulator smoke test (Loop 1's image was never built).
- **Tests**: 15 → 35. New: features, model and explanations, simulator,
  model through the API, migrations match the models, Loop 1 database
  adoption.

### Evaluate
Test window (seed 7): 30,840 transactions, 162 fraudulent, 44 attacks.

| | Rules (Loop 1) | Logistic regression | Isolation Forest | Gradient boosting |
|---|---|---|---|---|
| PR-AUC | 0.118 | **0.880** | 0.777 | 0.876 |
| Precision | 13.6% | **94.1%** | 84.3% | 97.0% |
| Recall | 19.8% | **79.0%** | 66.0% | 80.9% |
| Alerts per 1,000 txns | 7.6 | **4.4** | 4.1 | 4.4 |

Across 5 further independent datasets, logistic regression PR-AUC was
0.880 ± 0.046 against 0.123 ± 0.046 for the rules. Full tables, per-scenario
recall and where false alarms come from are in `reports/loop2_results.md`.

What I learned:
- **The first simulator was too easy.** The first logistic regression scored
  PR-AUC 0.988: real fraud is not that separable. Adding innocent
  look-alikes (tiny genuine payments, big genuine transfers, remote-access
  takeovers on the victim's own phone) brought it to 0.88, a more believable
  benchmark. A near-perfect score is a sign to check the data, not to
  celebrate.
- **A subtle feature bug.** "New device" first meant "not in history". In a
  burst from a stolen login, the fraudster's first payment put their device
  into history, so the second payment looked normal. Novelty is now judged
  against history older than 24 hours.
- **Most rule false alarms were customers being themselves**: 84 from
  business accounts (high amounts are normal for them) and 71 from genuine
  large transfers. Comparing against each customer's own baseline removes
  almost all of them.
- **Scam payments are invisible at transaction level.** Every learned model
  caught 0% of authorised push payment scams, because in this data they look
  exactly like genuine large payments to new payees: the customer's own
  phone, at home, at a normal hour. The rules caught 67% only because they
  flag *every* payment over 5,000. That is why fraud value caught is 62% for
  the rules but 27% for the model: scams are few but large. I tried an
  interaction feature (large *and* new payee) for the linear model; it got a
  near-zero weight, since no feature can separate classes that are identical.
  Real banks rely on other signals here (Confirmation of Payee, payee account
  age, mule-account intelligence) and on friction: warnings and delays.
- **Guardrail trade-off, measured.** Model + "large payment to a new payee"
  rule: fraud value caught 27% → 82%, attacks caught 24 → 31 of 44, but
  precision 94% → 60%. Worth it only if those alerts lead to a cheap step
  like a warning or confirmation call, not an analyst investigation. That is
  a product decision, so the guardrail is measured but not deployed.
- **Isolation Forest, with no labels at all, reached PR-AUC 0.78**: a
  useful fallback before any fraud is confirmed. It is weakest where
  fraud looks normal (takeovers 27%) and noisiest on genuine bursts.
- **Gradient boosting was no better than logistic regression** (0.876 vs
  0.880), so I deployed the model whose every decision can be explained.

Not verified here: PyPI was unreachable from the development environment, so
the API, database and migration tests run only in CI. The feature, model,
simulator and rule tests (23) were run locally. The caveat for all numbers:
the data comes from my own simulator, so they show the models learn the
simulated patterns, not that they would catch real fraud.

### Improve (inputs to Loop 3)
- Analyst actions: approve/dismiss incidents, recorded in the audit trail.
  Those decisions are also the labels a real deployment would retrain on.
- Decide what happens to large new-payee payments (warn, hold or alert);
  the guardrail numbers above are the evidence for that decision.
- Try a public labelled dataset (for example a card-fraud dataset) to test
  the feature approach on data I didn't generate.
- Feature computation scans up to 200 history rows per request in Python.
  Fine now; precomputed per-customer aggregates would be needed at scale.
- Severity bands (0.7 / 0.9) are placeholders until analyst feedback shows
  what high severity should mean.

---

## Loop 3: the web app and analyst workflow (2026-10-25)

### Plan
Turn Aegis into an app someone can use, entirely in Python: an analyst
opens the incident queue, reads a case, and confirms or dismisses it, and
that decision is stored and audited. Make it easy to show (a demo that fills
itself) and easy to put online.

A browser-only version (the model ported to JavaScript) came first, as a
quick way to show the work. It was a dead end as an app: nothing it did was
saved, and the scoring logic existed twice. This loop builds the real thing
on the API instead.

### Build
- **Analyst decisions** (`services.record_decision`): confirm, dismiss,
  reopen, or add a note. Statuses only move open → confirmed fraud or
  dismissed, and back via reopen; anything else is refused with a reason.
  Every action, with the analyst's name and note, is appended to the audit
  trail, so a reopened case keeps the record of the first decision. Also
  available as `POST /v1/incidents/{id}/actions`.
- **Score breakdown stored** (migration 0003): each model score keeps every
  feature's contribution as it was at scoring time, so the case page can show
  the full picture even after the customer's history has grown.
- **Web app** (`app/web/`): FastAPI routes rendering Jinja2 templates, no
  JavaScript. Forms post back and redirect (post/redirect/get), so refreshing
  never repeats an action; one-time messages travel in a short-lived cookie.
  Pages: incident queue + case file, transaction tester, model results, how
  it works. All user text is escaped by the templates.
- **Demo mode** (`app/demo.py`): the labelled simulator with a fixed seed.
  Loading 1,600 transactions takes under a second because scoring runs in
  memory with the same code as the API and is written in one database
  transaction (the per-request path would need ~6,000 round trips, too slow
  against a hosted database). Advancing the feed uses the normal ingest path.
  The answer key is rebuilt from the seed, so no labels are stored anywhere
  the detector could see them.
- **Deployable**: listens on `$PORT`, accepts `postgres://` URLs from hosting
  providers, and `render.yaml` sets up the app and database on Render.
- **CI** now also loads the demo inside the Docker image and requests every
  page, and turns test failures into annotations readable without the logs.
- **Tests**: 35 → 54. New: every page, decisions (saved, named, escaped,
  invalid transitions refused), demo load/advance/finish, answer key, the
  transaction tester, demo mode switched off, URL normalisation, actions API.

### Evaluate
- All 54 tests pass locally against PostgreSQL 16 and in CI.
- Checked by hand: load demo, advance a day (105 transactions, 3 new
  incidents), confirm with a note containing HTML (shown escaped), confirm
  twice (refused with the reason), reopen through the API (audit trail shows
  created → confirmed → reopened), send a stolen-login test (incident opened),
  invalid amount (explained on the form).
- Pages checked at desktop and phone widths in light and dark themes; no
  horizontal overflow.

### Improve (inputs to Loop 4)
- **Logins.** Anyone who can open the app can record a decision under any
  name. Needed before real data.
- **Use the decisions.** Confirmed and dismissed incidents are exactly the
  labels a retraining job needs; nothing reads them yet.
- **AI investigator** (Loop 4): draft a case summary from the evidence on
  the case page for the analyst to check.
- The queue shows one incident per flagged transaction. Several flags for
  the same customer within minutes (a card-testing run) should group into one
  case.
