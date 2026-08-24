# AI Revenue Recovery Agent

An AI-powered revenue recovery system that identifies failed payments, assesses recovery probability using machine learning, and orchestrates intelligent recovery strategies through a controlled agent architecture.

## Architecture Overview

```
Payment Failure → Revenue Risk Detection → ML Risk Assessment
→ AI Diagnosis → Recovery Strategy → Policy Validation
→ Controlled Recovery Action → Payment Result
→ Audit Trail → Revenue Recovery / Escalation → Analytics
```

**Key Principle:** The LLM never directly executes payment operations. All actions flow through:
```
LLM Recommendation → Deterministic Policy Engine → Approved/Blocked → Controlled Backend Tool → Razorpay API
```

## Technology Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React, Vite, JavaScript, Tailwind CSS, Recharts |
| Backend | Python, FastAPI, Pydantic |
| Database | PostgreSQL, SQLAlchemy |
| ML | scikit-learn, pandas, NumPy |
| AI Agent | LLM API with Structured Outputs & Tool Calling |
| Payments | Razorpay Test Mode APIs |
| Deployment | Vercel (frontend), Render/Railway (backend) |

## Project Structure

```
project-root/
├── frontend/           # React + Vite frontend
├── backend/            # FastAPI backend
│   └── app/
│       ├── main.py     # Application entry point
│       ├── core/       # Config, database setup
│       ├── api/        # API routers
│       ├── models/     # SQLAlchemy models
│       ├── schemas/    # Pydantic schemas
│       ├── services/   # Business logic
│       ├── db/         # Database session
│       └── utils/      # Logging, errors
├── ml/                 # ML models (Phase 2+)
├── data/               # Data files
├── tests/              # Backend tests
├── docs/               # Documentation
├── scripts/            # Utility scripts
├── .env.example        # Environment template
├── .gitignore
└── README.md
```

## Prerequisites

- Python 3.11+
- Node.js 18+
- PostgreSQL 14+
- npm or yarn

## Backend Setup

```bash
cd backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy environment file
cp ../.env.example ../.env
# Edit ../.env with your database credentials

# Run the backend
uvicorn app.main:app --reload --port 8000
```

The API will be available at: `http://localhost:8000`
API docs: `http://localhost:8000/docs`

## Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# Run the development server
npm run dev
```

The frontend will be available at: `http://localhost:5173`

## Environment Configuration

Copy `.env.example` to `.env` and configure:

```env
# Database (required)
DATABASE_URL=postgresql://user:password@localhost:5432/revenue_recovery

# Razorpay Test Mode (Phase 3+)
RAZORPAY_KEY_ID=
RAZORPAY_KEY_SECRET=
RAZORPAY_WEBHOOK_SECRET=

# LLM API (Phase 3+)
LLM_API_KEY=
```

**⚠️ Never commit `.env` or expose secrets to the frontend.**

## Running Locally

1. Start PostgreSQL and create the database
2. Start the backend: `cd backend && uvicorn app.main:app --reload`
3. Start the frontend: `cd frontend && npm run dev`
4. Visit `http://localhost:5173`

## Database Migrations

```bash
# Generate migration after model changes
cd backend
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head

# Rollback last migration
alembic downgrade -1
```

## Testing

```bash
# Backend tests
cd backend
pytest

# Frontend build check
cd frontend
npm run build
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check with DB status |
| GET | `/api/cases` | List revenue risk cases (paginated, filterable) |
| GET | `/api/cases/{case_id}` | Get detailed case information |

## Database Models

| Model | Description |
|-------|-------------|
| Customer | Customer information and transaction stats |
| Transaction | Payment transaction records |
| RevenueRiskCase | Failed payments and recovery tracking |
| RecoveryAction | Actions taken on risk cases |
| AuditEvent | Audit trail for all case activities |

## Development Phases

- **Phase 1**: Project foundation & core architecture
- **Phase 2** (Current): Database models, migrations & case APIs
- **Phase 3**: ML risk assessment & payment failure detection
- **Phase 4**: AI agent, policy engine & Razorpay integration
- **Phase 5**: Analytics dashboard & reporting

## License

Private — Internal use only.
