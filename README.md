# Coffer

Personal finance tracker. Monthly budget views, debt balances, savings, and weekly bank statement imports.

## Stack

- **Database**: PostgreSQL 16
- **Backend**: Python 3.12 + FastAPI + SQLAlchemy 2 + Alembic, managed with `uv`
- **Frontend**: React + TypeScript + Vite + Tailwind
- **Auth**: JWT (multi-user)
- **Imports**: CSV + PDF (pdfplumber)

## Quick start

```bash
cp .env.example .env
docker compose up -d
make seed          # optional: load the demo user with debts at 40% of salary
```

Open:

- Frontend: http://localhost:5173
- Backend docs: http://localhost:8000/docs
- Postgres: localhost:5432 (user/password from `.env`)

The seed creates `demo@coffer.dev` / `demo1234` with monthly income $5,000,
seven debts whose minimum payments sum to exactly $2,000 (40%), the matching
budget entries and categories, a salary deposit + debt payments for the
current month, and three goals.

## Layout

```
coffer/
├── docker-compose.yml
├── backend/        FastAPI app + Alembic migrations
├── frontend/       Vite + React app
└── db/init/        Optional SQL init scripts run on first db boot
```

## Useful commands

```bash
make up              # start all services
make logs            # tail logs
make migrate         # apply migrations
make makemigration   # autogenerate a new migration
make seed            # load demo data (skips if already seeded)
make seed-reset      # wipe the demo user and reseed
make fresh           # NUKE the DB, restart everything, run migrations, seed
make bash-db         # psql into the database
make clean           # tear down + remove volumes
```

## Domain model

- **User** – account holder (JWT auth).
- **Account** – a bank account, credit card, loan, or savings vehicle.
- **Category** – a budget bucket (e.g. House, Personal expenses, CMR Falabella).
- **Transaction** – a line item from a statement, tied to an account + category.
- **Debt** – outstanding balance, interest rate, minimum payment, due day.
- **BudgetEntry** – planned amount per category per month.
- **Goal** – savings target with deadline + current progress.
- **StatementImport** – record of an uploaded CSV/PDF and parsed rows.

## Volumes

- `postgres_data` – database files
- `backend_uploads` – uploaded statement files
- `backend_venv` – uv virtualenv (avoids macOS<->Linux clashes via bind mount)
- `frontend_node_modules` – isolated from host
