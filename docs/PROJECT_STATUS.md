# Project Status & Handoff

**Verified:** 2 November 2026 against the working tree, the local PostgreSQL database
(`revenue_recovery`), and a full test run. Every claim below is a file reference or a number
measured today with the command shown next to it.

**Audience:** the next engineer/agent picking this up. Read §1 and §3 first (state + rules you must
not break), then §5 (open work) and §7 (ordered plan). You need no prior context.

---

## 0. Verdict in one paragraph

The **backend is functionally complete** for the simulated story and for a locally-signed Razorpay
story: 9 routers / 26 endpoints, 8 tables, an ML model, a deterministic policy engine, a
fail-closed webhook settlement path, value scoring, simulation, a human-review flow that can release
an `ESCALATED` case, and a dashboard banner that states plainly whether payments are real or mocked.
`416` automated tests pass. What is still missing is not core logic but
**reach**: (a) no Razorpay Test Mode keys and no public HTTPS URL, so every executed action produces
a labelled `MOCK` link that cannot be paid and books ₹0; (b) the dashboard's "% recovered" is
count-based, not money-weighted; (c) nothing runs diagnose → execute without a human click, so the
threshold-based autonomy the product promises does not exist yet; (d) no auth, no CI, no Docker.

---

## 1. Verified snapshot

| Fact | Value | How measured |
|---|---|---|
| Backend routers | 9 — `health`, `cases`, `ml`, `agent`, `policy`, `webhooks`, `dashboard`, `simulation`, `reviews` | `ls backend/app/api` |
| Endpoints | 26 (`grep -c "@router\." backend/app/api/*.py`) | same |
| Frontend pages | 6 — `Dashboard`, `Cases`, `CaseDetail`, `AgentMonitor`, `Analytics`, `Policy` (+ `Layout`) | `frontend/src/App.jsx` |
| DB tables | 8 domain tables — `revenue_risk_cases`, `transactions`, `customers`, `recovery_actions`, `policy_decisions`, `policy_configs`, `audit_events`, `webhook_events` (+ `alembic_version`) | `inspect(engine).get_table_names()` |
| Tests | **416 passed, 0 failed, 0 skipped, 13 test modules, 5 min 30 s** | `cd backend && ./.venv/Scripts/python.exe -m pytest -q` |
| Frontend lint | `oxlint`: **8 warnings, 0 errors** across 11 files | `cd frontend && npx oxlint` |
| Frontend tests | none (no test runner installed) | — |
| Migrations | **3**, applied to head `c58e0a4b9f31` — `0267b7bae8a3` initial schema → `b41c9f2a7d10` drop escalation threshold → `c58e0a4b9f31` webhook events | `alembic current` / `alembic heads` |

### Live database right now

| Metric | Value |
|---|---|
| Cases | 30 (3 simulation runs × 10 synthetic transactions) |
| Status breakdown | `RECOVERED` 9 · `OPEN` 8 · `RECOVERY_ATTEMPTED` 3 · `IN_PROGRESS` 2 · `ESCALATED` 8 |
| Recovered | ₹17,968.32 — all booked by simulation, all labelled `SIMULATED` |
| Still at risk | ₹2,611.53 |
| Dashboard recovery rate | **30.0 %** (9 of 30 cases) |

Case rows are real database rows created by `POST /api/simulation/run`; the customer and transaction
data behind them is synthetic and every simulation row carries a `SIMULATED` marker.

---

## 2. What is implemented

| # | Area | What works | Where | Evidence |
|---|---|---|---|---|
| 1 | Data model | 8 tables, enums for case status / priority / action type / outcome / audit type / policy decision. 3 Alembic migrations, applied to head. | `backend/app/models/`, `backend/alembic/versions/` | `test_models.py` |
| 2 | ML | RandomForest recovery classifier, 20 features, artifacts saved (`ml/saved_models/`). `POST /ml/predict`, `POST /ml/predict/batch`, `GET /ml/model`, `GET /ml/health`. | `ml/`, `backend/app/services/ml_service.py`, `backend/app/api/ml.py` | `test_ml.py`, `test_ml_inference_integration.py` |
| 3 | Agent diagnosis | Structured output validated against `AgentRecommendation`; writes `case.diagnosis` / `recommended_action`; `OPEN → IN_PROGRESS`; audit `DIAGNOSIS_COMPLETED` + `ACTION_RECOMMENDED`; then a **read-only** policy pre-check. The LLM is a deterministic mock. | `backend/app/services/agent_service.py`, `POST /api/agent/diagnose` | `test_agent.py` |
| 4 | Policy engine | 10 ordered checks, sole authorization authority. Decisions `APPROVED` / `BLOCKED` / `ESCALATED` persisted with every sub-check + a `POLICY_CHECKED` audit event. `persist=False` is used only for advisory ranking. | `backend/app/services/policy_engine.py` | `test_policy.py` |
| 5 | Recovery execution | Requires a recorded diagnosis; rejects non-executable actions; amount taken **only** from the transaction row; creates a Razorpay link or a `MOCK` link on `mock.invalid`; sets `RECOVERY_ATTEMPTED`; never marks a case recovered. | `backend/app/services/recovery_service.py`, `POST /api/cases/{id}/recovery` | `test_recovery_execution.py`, `test_payment.py` |
| 6 | Human review | `GET /api/reviews/queue` (read-only, shows the blocking rule + recorded recommendation per case) and `POST /api/cases/{id}/review` with `APPROVE` / `OVERRIDE` / `CLOSE` / `MARK_UNRECOVERABLE`, a **named** reviewer, mandatory reason on override, `HUMAN_REVIEW_COMPLETED` audit event on every decision **including refusals**. | `backend/app/api/reviews.py`, `backend/app/services/review_service.py`, `backend/app/schemas/review.py` | `test_review.py` (18 tests) |
| 7 | Webhooks | HMAC-SHA256, **fail-closed** (401 if secret unset or signature bad); durable idempotency ledger (`webhook_events`); only `payment.captured` / `payment_link.paid` settle; only for the exact amount due; settled cases immutable; `payment.authorized` recorded but never settles. | `backend/app/api/webhooks.py` | `test_payment.py` |
| 8 | Value scoring | Expected gross / intervention cost / net recovery / utility score + candidate ranking. Explicitly advisory — it never authorizes. | `backend/app/services/value_service.py`, `GET /api/cases/{id}/value` | `test_value.py` |
| 9 | Analytics API | `/dashboard/stats`, 5 chart endpoints, `/audit` (paginated, filter by case/type/actor), `/cases/{id}/detail`. | `backend/app/api/dashboard.py`, `dashboard_service.py` | `test_dashboard.py` |
| 9b | Payment-mode reporting | `GET /api/config/status` returns `mode` (`TEST_MODE` / `MOCK`), `razorpay_configured`, `key_type` (`test`/`live`/`null`) and `webhook_configured` — presence flags only, never a key or secret. `Dashboard` renders it as a banner above the KPI cards: amber `MOCK — no provider call` when unconfigured, blue `TEST MODE` when keys are loaded, with a red line when the webhook secret is missing (settlement impossible) or live keys are detected. | `backend/app/api/health.py`, `frontend/src/pages/Dashboard.jsx`, `frontend/src/services/api.js` | `test_api.py::TestConfigStatusEndpoint` |
| 10 | UI | 6 pages: KPI cards + charts; filterable case table with a **Simulation card** and a **Review queue**; case detail with diagnosis, timeline, value panel, **AI diagnosis panel**, **Execute panel with action picker**, **Human Review panel**; audit monitor; analytics gauges; policy editor. | `frontend/src/pages/`, `frontend/src/services/api.js` | manual |
| 11 | Simulation | Synthetic transactions through the whole pipeline with deterministic outcomes, labelled `SIMULATED`. | `backend/app/services/simulation_service.py`, `POST /api/simulation/run` | `test_simulation.py` |
| 12 | End-to-end | Success, failure, policy block, low-confidence escalation across the full stack. | `backend/tests/test_e2e_scenarios.py` | part of the 411 |

---

## 3. Invariants — do not break these

These are the point of the project. Any change that violates one is a regression even if tests
still pass:

1. **The AI never authorizes and never executes.** It produces a recommendation; only the Policy
   Engine authorizes; only `recovery_service` executes. `value_service` is advisory only.
2. **The amount always comes from the failed transaction row**, never from the agent, the request,
   or the recovery action row.
3. **Confidence and recovery probability always come from the recorded diagnosis**, never from the
   execution request.
4. **A human approval is not a policy bypass.** `authorized_by` satisfies only the autonomy gates
   (`HUMAN_REVIEW_REQUIRED`, amount / confidence / probability thresholds). Settled case, expired
   case, exhausted retry / reminder / attempt budget still refuse — and the refusal is audited
   against the reviewer.
5. **Settled money is immutable.** A `RECOVERED` case can never be executed against, closed, or
   re-labelled.
6. **Webhooks fail closed.** No secret or bad signature ⇒ 401. Duplicate delivery ⇒ no state change.
   Wrong amount ⇒ no settlement. `payment.authorized` never settles.
7. **`MOCK` is always labelled `MOCK`.** Simulated rows say `SIMULATED`; nothing may present
   synthetic or unpaid data as real recovered revenue.
8. **Every state change writes an audit event.** `CASE_CREATED`, `DIAGNOSIS_COMPLETED`,
   `ACTION_RECOMMENDED`, `POLICY_CHECKED`, `ACTION_EXECUTED`, `ACTION_FAILED`, `CASE_ESCALATED`,
   `CASE_CLOSED`, `HUMAN_REVIEW_COMPLETED`.

### Policy engine, exactly (the authority)

Order in `evaluate_action` — the first failing check decides:

1. `PAYMENT_NOT_SUCCESSFUL` → hard **BLOCKED**
2. `CASE_NOT_CLOSED` → hard **BLOCKED**
3. `HUMAN_REVIEW_REQUIRED` (case already `ESCALATED`) → hard **BLOCKED** — *unless* `authorized_by`
   is set, in which case a passing `HUMAN_AUTHORISATION` check naming the reviewer is recorded instead
4. `CASE_EXPIRY` (older than `case_lifetime_days`, default 7) → hard **BLOCKED**
5. `MAX_RETRIES` (default 2) → blocks only `RETRY`
6. `MAX_REMINDERS` (default 2) → blocks only `SEND_PAYMENT_REMINDER`
7. `MAX_RECOVERY_ATTEMPTS` (default 3) → hard **BLOCKED**
8. `AUTONOMOUS_AMOUNT_LIMIT` (default ₹10,000) → **ESCALATED**
9. `MINIMUM_AI_CONFIDENCE` (default 0.30) → **ESCALATED**
10. `MINIMUM_RECOVERY_PROBABILITY` (default 0.20) → **ESCALATED**

Checks 8–10 are overridden by a named reviewer (recorded as overridden, not silently dropped).
Valid ranges are enforced in `backend/app/schemas/policy.py`; `1.0` is accepted for both thresholds
because it is the documented way to say "never act autonomously".

Executable actions are only `RETRY`, `CREATE_PAYMENT_LINK`, `SEND_PAYMENT_REMINDER`
(`backend/app/schemas/recovery.py:EXECUTABLE_ACTIONS`). `WAIT_AND_RETRY`, `ESCALATE_TO_HUMAN`,
`NO_ACTION`, `MARK_UNRECOVERABLE` are *decisions*; posting them to `/recovery` returns HTTP 400
`UNSUPPORTED_ACTION`.

---

## 4. Built most recently (uncommitted — commit before branching)

`git status` currently shows these as modified/untracked. **They are the newest work; the full suite
covers them, but nothing is committed yet.**

| Work | Files |
|---|---|
| Diagnose panel, executable-action picker, Human Review panel | `frontend/src/pages/CaseDetail.jsx` (+456) |
| Simulation card, Review queue | `frontend/src/pages/Cases.jsx` (+299) |
| API client for review/diagnose/simulation | `frontend/src/services/api.js` |
| `POST /cases/{id}/review`, `GET /reviews/queue` | `backend/app/api/reviews.py` **(new)**, `backend/app/services/review_service.py` **(new)**, `backend/app/schemas/review.py` **(new)** |
| `authorized_by` support + overridden autonomy gates | `backend/app/services/policy_engine.py` |
| `HUMAN_REVIEW_COMPLETED` audit type | `backend/app/models/enums.py`, `backend/tests/test_models.py` |
| Threshold range validators (1.0 allowed) | `backend/app/schemas/policy.py`, `frontend/src/pages/Policy.jsx` |
| Router registration | `backend/app/main.py` |
| Review tests (18) | `backend/tests/test_review.py` **(new)** |
| Payment-mode endpoint + dashboard banner (P1.4) | `backend/app/api/health.py`, `frontend/src/pages/Dashboard.jsx`, `frontend/src/services/api.js`, `backend/tests/test_api.py` |
| Handoff docs | `docs/` **(new)**, `README.md` |
| Guard: escalated / settled cases refused on `/recovery` | `backend/app/services/recovery_service.py` |

---

## 5. What is NOT implemented

| # | Gap | Impact today | Size |
|---|---|---|---|
| G1 | **No Razorpay Test Mode credentials / public webhook URL.** | Every executed action is a `MOCK` link on `mock.invalid`; no payment is possible; `recovered_amount` stays 0. | config |
| G2 | **Recovery % is count-based only** (`RECOVERED / total × 100`). No money-weighted rate is exposed, and `total_at_risk` *excludes* recovered cases, so Analytics has no fixed denominator. | You cannot read "how much of the ₹ at risk came back" off the dashboard. | S |
| G3 | **No autonomy loop.** Policy can approve, but nothing runs diagnose → execute automatically. | Contradicts the threshold-based autonomy the product promises. | M |
| G4 | No scheduler / background jobs. | No expiry sweep, no automatic reminders, no retry-after-delay (`WAIT_AND_RETRY` is a decision only). | M |
| G5 | No authentication on mutating endpoints (`/recovery`, `/agent/diagnose`, `/reviews/*`, `PUT /policies`, `/simulation/run`). Single tenant, no user table. | Fine for a local demo; disqualifying if deployed publicly. | L |
| G6 | No CI (no GitHub Actions), no Dockerfile/compose, deployment config is placeholder (`frontend/vercel.json` points at `your-backend-url.onrender.com`, `render.yaml` CORS is a placeholder). | Cannot deploy without editing files. | M |
| G7 | LLM is a deterministic mock. | "AI" is rule-based; fine for a demo, not for production. | M |
| G8 | No frontend tests; `oxlint` reports 8 warnings (React `set-state-in-effect` hints), 0 errors. | UI regressions are caught only by eye. | M |
| G9 | ~~No credentials / mode banner on the Dashboard.~~ **Done (P1.4):** `/api/config/status` + banner on `Dashboard`. | A viewer now sees `MOCK` vs `TEST MODE` before clicking anything. | done |
| G10 | Model quality is weak: v1 ROC-AUC **0.668**, precision 0.33, recall 0.19, revenue-weighted recall 0.27 (`ml/saved_models/evaluation_summary_v1.json`). | Probability scores are usable as a ranking signal, not as a reliable predictor. | L |
| G11 | No pagination on `GET /cases`; the table loads everything. | Fine at 30 cases, not at 10,000. | S |

---

## 6. The money loop: how amount-at-risk becomes "recovery % up"

### 6.1 The chain

```
1  failed payment           → case created (amount, transaction_id)         CODE READY
2  diagnose                 → recorded recommendation + confidence          CODE READY (UI button)
3  execute                  → policy APPROVED → payment link created       CODE READY (MOCK today — G1)
4  customer pays            → Razorpay captures money                      YOU (test card)
5  webhook payment.captured → amount must equal amount due                 CODE READY (needs a public URL — G1)
6  process_payment_success  → status RECOVERED, recovered_amount = amount  CODE READY
7  dashboard refetch        → recovery_rate + total_recovered rise         CODE READY
```

Links 2–3 and 5–7 are complete. Link 4 is outside the code. Today links 3→5 are dead because link 3
produces an unpayable `MOCK` link.

### 6.2 Metric definitions (as implemented, `dashboard_service.get_dashboard_stats`)

```python
total_at_risk   = SUM(amount)           WHERE status != 'RECOVERED'    # excludes settled money
total_recovered = SUM(recovered_amount) WHERE recovered_amount > 0
recovery_rate   = COUNT(status='RECOVERED') / COUNT(cases) * 100      # count-based, 1 dp
```

Money-weighted rate — not exposed yet, this is G2 — is
`total_recovered / (total_recovered + total_at_risk)`.

### 6.3 Why the number does not move today

1. `MOCK` links cannot be paid (no keys) → no webhook → no settlement.
2. Cases older than the 7-day `case_lifetime_days` are blocked by `CASE_EXPIRY`; two of the three
   simulation runs are already past it.
3. Simulated recoveries are already booked; re-running simulation only adds synthetic money, it does
   not demonstrate a recovery *the agent performed and you settled*.

### 6.4 Three ways to make it move

| Route | Real money? | Owner does | Agent/code does |
|---|---|---|---|
| **A. Test Mode round trip** (recommended) | test money | drop keys + tunnel into `backend/.env`, pay a test card | nothing more — already built |
| **B. Signed webhook replay** | none, but real code path | nothing | add a dev-only `POST /api/dev/webhooks/replay` that signs `payment_link.paid` with the webhook secret for one case |
| **C. Re-run simulation** | none (synthetic) | `curl -X POST /api/simulation/run -d '{"num_transactions":10}'` | nothing — proves nothing about execution |

Route A is what "my agent recovered this amount" should mean in a demo. B is the fallback when keys
or a tunnel are unavailable. C is never evidence.

---

## 7. Next steps, ordered

**P1.4 — Mode/credentials banner — DONE.** `GET /api/config/status` exposes `mode`,
`razorpay_configured`, `key_type` and `webhook_configured` (presence flags and key *prefix* class
only; the key id, secret and webhook secret are never serialized, asserted in
`test_api.py::TestConfigStatusEndpoint`). `Dashboard` shows the banner above the KPI cards and treats
a failed status call as non-fatal. Remaining nice-to-have: the same chip on `CaseDetail`'s Execute
panel, which still only learns the mode from the execute response.

**P3 — Money metrics (S–M).** Add `money_recovery_rate`, `recovered_7d`, `at_risk_7d`,
`delta_vs_previous_period` to `/dashboard/stats`. Make the money-weighted rate the headline gauge and
keep the count-based rate as a clearly labelled secondary figure, otherwise "recovery %" means two
different things on one screen. Fix Analytics so "at risk vs recovered" shares one denominator (face
value of all cases) and the remainder is labelled "outstanding". Acceptance: the four numbers
reconcile with each other on the same page.

**P4 — Threshold autonomy (M, the feature the owner actually asked for).** Persist
`auto_execute_enabled`, `auto_execute_min_confidence`, `auto_execute_max_amount` on `PolicyConfig`
(+ migration). Add a sweep — `POST /api/agent/run` for a manual trigger first, APScheduler later —
that for each `OPEN` case runs diagnose → policy → executes **only** when `APPROVED` *and* above the
auto-execute thresholds; everything else is left for human review. Every autonomous action must write
the same `POLICY_CHECKED` / `ACTION_EXECUTED` rows and the same audit events, with the actor marked
as the agent rather than a human. Add a kill switch on the Policy page. Acceptance: an OPEN case below
the thresholds is executed with no human click and appears in the audit trail attributed to the agent;
an `ESCALATED` case is never auto-executed.

**P5 — Production hardening (L).** Auth + roles on mutating endpoints, GitHub Actions (pytest,
oxlint, vite build), Dockerfile/compose, real deploy config, real LLM behind `LLM_API_KEY`,
real-time dashboard updates (poll or SSE), frontend tests, `/cases` pagination.

---

## 8. What the owner must provide (nothing else)

| # | Item | Where it goes | Unblocks |
|---|---|---|---|
| 1 | Razorpay Test Mode **Key ID** (`rzp_test_…`) | `backend/.env` → `RAZORPAY_KEY_ID` | Real payment links instead of `MOCK` |
| 2 | Razorpay Test Mode **Key Secret** | `backend/.env` → `RAZORPAY_KEY_SECRET` | same |
| 3 | A **webhook secret** of your choice (any random string) | `backend/.env` → `RAZORPAY_WEBHOOK_SECRET` **and** the Razorpay dashboard webhook config | Signature-verified settlement (the handler rejects everything with 401 while this is empty) |
| 4 | A **public HTTPS URL** to this backend — `ngrok http 8000` / `cloudflared tunnel --url http://localhost:8000`, or a deployed host | Razorpay → Settings → Webhooks → `https://<host>/api/webhooks/razorpay`, events `payment.captured`, `payment_link.paid`, `payment.failed` | Razorpay can deliver the event at all |
| 5 | Product decisions (below) | answer in chat | scoping P3/P4 |
| 6 | *Optional* `LLM_API_KEY` | `backend/.env` | replacing the mock LLM |
| 7 | *Optional* Vercel / Render / cloud-Postgres accounts | deploy config | public demo |

Open product decisions: (a) reviewer model — free-text reviewer name (today's behaviour) or real
accounts/roles? (b) autonomy — confirm the confidence / probability / amount numbers for P4, or keep
"a human always clicks Execute". (c) headline metric — count-based or money-weighted recovery %.

---

## 9. Running and verifying it

```bash
# backend (Windows paths as used in this repo)
cd backend
./.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
./.venv/Scripts/python.exe -m pytest -q          # 411 passed, ~5.5 min
./.venv/Scripts/python.exe -m alembic upgrade head

# frontend
cd frontend
npm install
npm run dev        # http://localhost:5173, expects the API on :8000
npx oxlint         # 8 warnings, 0 errors
npm run build
```

Environment lives in `backend/.env` (copy `.env.example`). `DATABASE_URL` is required; without
`RAZORPAY_*` the system runs in `MOCK` mode by design, and without `RAZORPAY_WEBHOOK_SECRET` every
webhook is rejected with 401 (that is the intended fail-closed behaviour, not a bug).

Tests use SQLite fixtures per test (`backend/tests/conftest.py`) — they never touch the developer's
PostgreSQL data.

Quick sanity checks against a running server:

```bash
curl localhost:8000/api/health
curl localhost:8000/api/dashboard/stats
curl localhost:8000/api/reviews/queue
curl -X POST localhost:8000/api/simulation/run -H 'Content-Type: application/json' -d '{"num_transactions":10}'
curl -X POST localhost:8000/api/agent/diagnose -H 'Content-Type: application/json' -d '{"case_id":"SIM-CASE-…"}'
curl -X POST localhost:8000/api/cases/SIM-CASE-…/review -H 'Content-Type: application/json' \
     -d '{"reviewer":"you","decision":"APPROVE"}'
```

### Demo script that actually demonstrates the claim

1. Dashboard → note recovery % and ₹ recovered. 2. Cases → Simulation card → 10 cases. 3. Open a
case → **Run AI diagnosis** (see recommendation, confidence, probability). 4. **Execute** (picker
defaults to the recommendation): with keys you get a payable test link, without keys a `MOCK` one.
5. For a case that escalated, open **Human Review**: the blocking rule is shown, enter a name,
approve. 6. Pay the test link → webhook → case becomes `RECOVERED` with the amount attributed to it.
7. Dashboard → recovery % higher, when the money metric of P3 exists it rises in ₹ too. 8. Audit
monitor → the full chain, including the reviewer's name.

---

## 10. Endpoint map (25)

| Area | Endpoints |
|---|---|
| health / config | `GET /api/health`, `GET /api/config/status` (payment mode + presence flags, never keys) |
| cases | `GET /api/cases`, `GET /api/cases/{id}`, `GET /api/cases/{id}/value`, `POST /api/cases/{id}/recovery` |
| agent | `POST /api/agent/diagnose` |
| reviews | `GET /api/reviews/queue`, `POST /api/cases/{id}/review` |
| policy | `GET /api/policies`, `PUT /api/policies`, `POST /api/policies/evaluate` |
| ml | `POST /api/ml/predict`, `POST /api/ml/predict/batch`, `GET /api/ml/model`, `GET /api/ml/health` |
| dashboard | `GET /api/dashboard/stats`, `GET /api/dashboard/charts/{status,priority,actions,daily-cases,daily-recovered}`, `GET /api/cases/{id}/detail`, `GET /api/audit` |
| simulation | `POST /api/simulation/run` |
| webhooks | `POST /api/webhooks/razorpay` |
