# AI Revenue Recovery Agent

An AI-powered system that detects failed payments, predicts recovery probability, recommends recovery actions, validates through a deterministic policy engine, and executes controlled payment recovery via Razorpay Test Mode.

**Status:** MVP Complete (Phases 1–8)  
**Payment Integration:** Razorpay Test Mode ONLY

---

## Problem Statement

Businesses lose significant revenue from failed subscription and recurring payments. Manual recovery is slow, inconsistent, and doesn't scale. Existing solutions lack intelligent prioritization and controlled automation.

## Solution

An end-to-end AI agent that:
1. Detects failed payments and creates revenue risk cases
2. ML model predicts recovery probability for each case
3. AI agent diagnoses the failure and recommends recovery actions
4. Deterministic Policy Engine validates every recommendation before execution
5. Controlled recovery tools execute approved actions via Razorpay Test Mode
6. Webhook processing captures payment outcomes
7. Complete audit trail records every decision
8. Merchant dashboard displays real-time metrics

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        MERCHANT DASHBOARD                        │
│   React + Tailwind CSS + Recharts (Frontend on Vercel)          │
└────────────────────────────┬────────────────────────────────────┘
                             │ REST API
┌────────────────────────────▼────────────────────────────────────┐
│                     FASTAPI BACKEND                              │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │ Health   │  │ Cases    │  │ Dashboard│  │Simulation│       │
│  │ API      │  │ API      │  │ API      │  │ API      │       │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘       │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │ ML       │  │ Agent    │  │ Policy   │  │ Recovery │       │
│  │ Service  │  │ Service  │  │ Engine   │  │ Service  │       │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘       │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                     │
│  │Razorpay  │  │ Webhook  │  │ Audit    │                     │
│  │ Service  │  │ Handler  │  │ System   │                     │
│  └──────────┘  └──────────┘  └──────────┘                     │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│                    POSTGRESQL DATABASE                           │
│   Customers | Transactions | Cases | RecoveryActions            │
│   AuditEvents | PolicyConfigs | PolicyDecisions                 │
└─────────────────────────────────────────────────────────────────┘
```

### Security Invariant

```
LLM → RECOMMEND     (AI agent proposes actions)
Policy Engine → AUTHORIZE  (Deterministic rules validate)
Backend Tool → EXECUTE    (Controlled tools run)
Payment API → PROCESS     (Razorpay Test Mode)
```

**The LLM must NEVER become the payment authority.**

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 19, Vite 8, Tailwind CSS 4, Recharts 3, React Router 7 |
| Backend | Python 3.14, FastAPI, Pydantic v2, Uvicorn |
| Database | PostgreSQL (SQLAlchemy 2.0, Alembic) |
| ML | scikit-learn, pandas, NumPy, joblib |
| AI Agent | LLM API (mock for MVP), Structured Outputs |
| Payments | Razorpay Test Mode APIs |
| Testing | pytest, httpx |
| Deployment | Vercel (frontend), Render/Railway (backend) |

---

## Project Structure

```
ai-revenue-recovery-agent/
├── backend/                    # FastAPI application
│   ├── app/
│   │   ├── api/               # Route handlers
│   │   │   ├── health.py      # GET /api/health
│   │   │   ├── cases.py       # GET /api/cases, GET /api/cases/{id}
│   │   │   ├── ml.py          # POST /api/ml/predict, GET /api/ml/health
│   │   │   ├── agent.py       # POST /api/agent/diagnose
│   │   │   ├── policy.py      # GET/PUT /api/policies, POST /api/policies/evaluate
│   │   │   ├── webhooks.py    # POST /api/webhooks/razorpay
│   │   │   ├── dashboard.py   # GET /api/dashboard/*, GET /api/audit
│   │   │   └── simulation.py  # POST /api/simulation/run
│   │   ├── core/              # Config, database
│   │   ├── models/            # SQLAlchemy models (7 tables)
│   │   ├── schemas/           # Pydantic schemas
│   │   ├── services/          # Business logic
│   │   │   ├── ml_service.py        # ML inference
│   │   │   ├── agent_service.py     # AI agent orchestration
│   │   │   ├── agent_tools.py       # Controlled backend tools
│   │   │   ├── policy_engine.py     # Deterministic policy validation
│   │   │   ├── recovery_service.py  # Recovery execution
│   │   │   ├── razorpay_service.py  # Razorpay integration
│   │   │   ├── dashboard_service.py # Dashboard aggregates
│   │   │   └── simulation_service.py # Batch simulation
│   │   └── utils/             # Errors, logging
│   ├── tests/                 # 11 test files, 280+ tests
│   ├── alembic/               # Database migrations
│   └── requirements.txt
├── frontend/                   # React SPA
│   ├── src/
│   │   ├── pages/             # 6 page components
│   │   ├── layouts/           # Layout with sidebar
│   │   ├── services/          # API client (axios)
│   │   └── App.jsx            # Router config
│   ├── vercel.json            # Vercel deployment
│   └── package.json
├── ml/                         # ML pipeline
│   ├── config.py              # Constants, thresholds
│   ├── data_generator.py      # Synthetic data generation
│   ├── preprocessing.py       # Feature engineering
│   ├── model.py               # Training, evaluation
│   ├── predictor.py           # Inference
│   └── saved_models/          # Trained model artifacts
├── data/                       # Synthetic dataset
├── render.yaml                 # Render deployment
├── .env.example                # Environment template
└── .gitignore
```

---

## Data Flow

### Complete Recovery Flow

```
1. Payment fails (webhook/manual)
2. Revenue Risk Case created
3. ML Model predicts recovery probability
4. AI Agent diagnoses failure, recommends action
5. Policy Engine validates recommendation
   → APPROVED: proceed to step 6
   → BLOCKED: stop, audit, notify
   → ESCALATED: human review required
6. Recovery Service executes approved action
7. Razorpay Test Mode processes payment
8. Webhook receives payment outcome
9. Case status updated
10. Audit event recorded
11. Dashboard metrics refresh
```

---

## ML Methodology

- **Algorithm:** RandomForestClassifier (200 estimators, max_depth=12)
- **Features:** 20 engineered features from transaction + customer data
- **Target:** Binary recovery outcome (recovered / not recovered)
- **Training Data:** 6,000 synthetic transactions with realistic patterns
- **Evaluation Metrics:**
  - Precision, Recall, F1-Score, ROC-AUC
  - Revenue-weighted recall and precision
  - Revenue recovery rate

### Feature Categories

| Category | Features |
|----------|----------|
| Transaction | amount, payment_method, failure_reason, attempt_number, is_retry |
| Time | hour_of_day, day_of_week, days_since_last_transaction |
| Customer | total_transactions, success_rate, lifetime_value, avg_amount |
| History | recent_failure_count, recent_success_count, amount_vs_avg_ratio |

---

## AI Agent Architecture

- **Structured Output:** Pydantic-validated recommendations
- **Controlled Actions:** 7 possible recovery actions (enum-controlled)
- **Tool Authorization:** Agent recommends, Policy Engine authorizes
- **Fallback:** Safe escalation when LLM is unavailable or output is invalid

### Recovery Actions

| Action | Description |
|--------|-------------|
| NO_ACTION | No recovery action needed |
| RETRY | Retry the failed payment |
| CREATE_PAYMENT_LINK | Create Razorpay payment link |
| SEND_PAYMENT_REMINDER | Send reminder to customer |
| WAIT_AND_RETRY | Wait and retry later |
| ESCALATE_TO_HUMAN | Escalate to human review |
| MARK_UNRECOVERABLE | Mark as unrecoverable |

---

## Deterministic Policy Engine

The Policy Engine validates every AI recommendation before execution.

### Policy Rules

| Rule | Default | Description |
|------|---------|-------------|
| MAX_RETRIES | 2 | Maximum retry attempts per case |
| MAX_REMINDERS | 2 | Maximum payment reminders per case |
| MAX_RECOVERY_ATTEMPTS | 3 | Maximum total recovery actions |
| AUTONOMOUS_AMOUNT_LIMIT | ₹10,000 | Maximum amount for autonomous action |
| MINIMUM_AI_CONFIDENCE | 0.3 | Minimum confidence required |
| MINIMUM_RECOVERY_PROBABILITY | 0.2 | Minimum recovery probability |
| CASE_LIFETIME_DAYS | 7 | Maximum case age before expiry |
| ESCALATION_THRESHOLD | 0.7 | Confidence above which escalation triggers |

### Decisions

- **APPROVED:** All checks passed, action allowed
- **BLOCKED:** Critical check failed, action prevented
- **ESCALATED:** Threshold exceeded, human review required

---

## Razorpay Integration

- **Mode:** Test Mode ONLY (never Live Mode)
- **Payment Links:** Created via Razorpay API
- **Webhook Handling:** Verifies signature, prevents duplicates
- **Mock Mode:** Falls back to mock when credentials not configured

### Webhook Events Handled

- `payment.authorized` / `payment.captured` / `payment_link.paid` → Success
- `payment.failed` / `payment_link.expired` → Failure
- Unknown events → Logged and acknowledged

---

## Simulation

The simulation engine runs synthetic transactions through the complete pipeline.

### Running a Simulation

```bash
curl -X POST http://localhost:8000/api/simulation/run \
  -H "Content-Type: application/json" \
  -d '{"num_transactions": 1000, "seed": 42}'
```

### Simulation Output

```json
{
  "simulation_id": "SIM-XXXXXXXXXXXX",
  "status": "COMPLETED",
  "label": "SIMULATED",
  "num_transactions_processed": 1000,
  "revenue_at_risk": 1234567.89,
  "simulated_revenue_recovered": 890123.45,
  "recovery_rate": 72.1,
  "processing_duration_seconds": 45.23,
  "metrics": { ... },
  "ml_prediction_stats": { ... }
}
```

**All simulation output is labeled `SIMULATED`.** It is not real customer revenue.

---

## Business Metrics

| Metric | Formula |
|--------|---------|
| Revenue at Risk | SUM(amount) WHERE status != RECOVERED |
| Revenue Recovered | SUM(recovered_amount) WHERE recovered > 0 |
| Recovery Rate | Revenue Recovered / Revenue at Risk × 100 |
| Outstanding Revenue | Revenue at Risk - Revenue Recovered |
| Escalated Cases | COUNT(status = ESCALATED) |
| Policy Blocked | COUNT(execution_status = BLOCKED_BY_POLICY) |

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/cases` | List cases (paginated, filterable) |
| GET | `/api/cases/{id}` | Get case detail |
| GET | `/api/cases/{id}/detail` | Full case with relations |
| POST | `/api/ml/predict` | ML recovery prediction |
| POST | `/api/ml/predict/batch` | Batch ML predictions |
| GET | `/api/ml/model` | Model information |
| GET | `/api/ml/health` | ML model health |
| POST | `/api/agent/diagnose` | AI agent diagnosis |
| GET | `/api/policies` | Get policy config |
| PUT | `/api/policies` | Update policy config |
| POST | `/api/policies/evaluate` | Evaluate action against policy |
| POST | `/api/webhooks/razorpay` | Razorpay webhook handler |
| GET | `/api/dashboard/stats` | Dashboard statistics |
| GET | `/api/dashboard/charts/status` | Status chart data |
| GET | `/api/dashboard/charts/priority` | Priority chart data |
| GET | `/api/dashboard/charts/actions` | Actions chart data |
| GET | `/api/dashboard/charts/daily-cases` | Daily cases data |
| GET | `/api/dashboard/charts/daily-recovered` | Daily recovered data |
| GET | `/api/audit` | Audit events (paginated) |
| POST | `/api/simulation/run` | Run batch simulation |

---

## Local Setup

### Prerequisites

- Python 3.11+
- Node.js 18+
- PostgreSQL (or use SQLite for testing)

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Copy and configure environment
cp ../.env.example .env
# Edit .env with your database URL and Razorpay test keys

# Run tests
python -m pytest tests/ -v

# Start server
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev     # Development: http://localhost:5173
npm run build   # Production build
```

### ML Pipeline

```bash
cd backend
python -m ml.pipeline  # Trains model and saves artifacts
```

### Simulation

```bash
# Via API
curl -X POST http://localhost:8000/api/simulation/run \
  -H "Content-Type: application/json" \
  -d '{"num_transactions": 1000, "seed": 42}'
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `RAZORPAY_KEY_ID` | No | Razorpay Test Mode key |
| `RAZORPAY_KEY_SECRET` | No | Razorpay Test Mode secret |
| `RAZORPAY_WEBHOOK_SECRET` | No | Razorpay webhook secret |
| `LLM_API_KEY` | No | LLM API key (mock used if empty) |
| `DEBUG` | No | Enable debug mode |
| `CORS_ORIGINS` | No | Allowed CORS origins |

---

## Testing

### Run All Tests

```bash
cd backend
python -m pytest tests/ -v
```

### Test Categories

| File | Tests | Coverage |
|------|-------|----------|
| `test_models.py` | 16 | Database models, enums, relationships |
| `test_api.py` | 11 | Health, cases API |
| `test_ml.py` | 15 | ML preprocessing, training, prediction |
| `test_ml_inference_integration.py` | 30 | ML API integration, validation |
| `test_agent.py` | 42 | AI agent tools, service, API |
| `test_policy.py` | 35 | Policy engine, checks, evaluation |
| `test_payment.py` | 25 | Razorpay, webhooks, recovery |
| `test_dashboard.py` | 15 | Dashboard stats, charts, audit |
| `test_simulation.py` | 29 | Simulation service, API |
| `test_e2e_scenarios.py` | 18 | Critical end-to-end scenarios |

### Critical E2E Scenarios

1. **Successful Recovery** — Full flow from failure to recovery
2. **Failed Recovery** — Stopping rules and escalation
3. **Policy Block** — Payment tool NOT executed when blocked
4. **Low Confidence** — Safe escalation, no unsafe action

---

## Deployment

### Frontend (Vercel)

1. Push to GitHub
2. Connect repository to Vercel
3. Set build command: `npm run build`
4. Set output directory: `dist`
5. Configure API proxy rewrite to backend URL

### Backend (Render/Railway)

1. Push to GitHub
2. Create new Web Service
3. Set build command: `pip install -r backend/requirements.txt`
4. Set start command: `cd backend && uvicorn app.main:app --host 0.0.0.0 --port $PORT`
5. Add environment variables (DATABASE_URL, Razorpay keys, etc.)
6. Health check path: `/api/health`

### Database (Cloud PostgreSQL)

1. Create PostgreSQL instance (Render, Supabase, Neon, etc.)
2. Copy connection string to `DATABASE_URL` env var
3. Run Alembic migrations: `cd backend && alembic upgrade head`

### Post-Deployment

1. Verify health endpoint: `GET /api/health`
2. Run simulation: `POST /api/simulation/run`
3. Check dashboard metrics
4. Configure Razorpay webhook URL: `https://your-backend.onrender.com/api/webhooks/razorpay`

---

## Demo Instructions

### Prerequisites
- Backend running on port 8000
- Frontend running on port 5173
- Database connected

### Demo Flow

1. **Open Dashboard** — Show overview metrics (empty initially)
2. **Run Simulation** — Execute 1000 transaction simulation
3. **Refresh Dashboard** — Show populated metrics and charts
4. **View Cases** — Browse simulated revenue risk cases
5. **Case Detail** — Show ML prediction, AI diagnosis, audit trail
6. **Policy Settings** — Show and modify policy configuration
7. **Agent Monitor** — Show audit events from simulation

### Demonstrating Key Scenarios

**Successful Recovery:**
- Filter cases by status "RECOVERED"
- Show full timeline: failure → ML → AI → Policy → Recovery → Success

**Policy Block:**
- Create case with amount > ₹10,000
- Show policy blocks the action
- Verify no payment link created

**Escalation:**
- Show cases with low confidence
- Verify escalation to human review
- Show audit trail

---

## Security Considerations

- ✅ No secrets in frontend code
- ✅ `.env` excluded from Git
- ✅ `.env.example` contains only placeholders
- ✅ Razorpay Test Mode only (never Live Mode)
- ✅ Razorpay secret keys only on backend
- ✅ Webhook signature verification enabled
- ✅ Duplicate webhook protection
- ✅ Policy limits enforced on all actions
- ✅ LLM cannot bypass Policy Engine
- ✅ Amounts come from trusted database, not LLM
- ✅ Frontend cannot execute payment operations
- ✅ Simulation labeled as SIMULATED

---

## Limitations

1. **LLM is mocked** — Uses deterministic rules instead of real LLM API
2. **Payment links are simulated** — No actual Razorpay payment links created without credentials
3. **No real webhook processing** — Requires Razorpay Test Mode account for real webhooks
4. **Single-tenant** — No merchant authentication/authorization
5. **No real-time updates** — Dashboard requires manual refresh
6. **SQLite for tests** — Production uses PostgreSQL

## Future Improvements

1. Real LLM integration (OpenAI/Anthropic)
2. Real Razorpay Test Mode payment links
3. Webhook endpoint for live payment updates
4. Multi-merchant support with authentication
5. Real-time WebSocket updates
6. Email/SMS notifications
7. Adaptive policy learning
8. Multi-agent architecture
9. Subscription recovery automation
10. Checkout abandonment recovery

---

## License

This is an MVP project for demonstration purposes.
