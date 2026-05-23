# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Working environment

Everything runs in Docker via `docker-compose.yml` (services: `db`, `backend`, `frontend`). Source is bind-mounted, but Python's `.venv` and Node's `node_modules` live in named volumes (`backend_venv`, `frontend_node_modules`) so macOS host files don't collide with Linux container builds. **Do not run `uv`, `pytest`, `alembic`, `npm`, or `eslint` directly on the host** — always go through `make` or `docker compose exec`.

After changing `pyproject.toml` / `package.json`, run `make fresh` (or `docker compose build`) so the named-volume deps are rebuilt; a plain `make up` won't pick up new dependencies.

## Common commands

```bash
make up                    # start all services
make logs                  # tail logs
make migrate               # alembic upgrade head (inside backend container)
make makemigration         # prompts for message, runs alembic revision --autogenerate
make seed                  # load demo user (demo@coffer.dev / demo1234)
make seed-reset            # wipe demo user and reseed
make fresh                 # NUKE db volume, rebuild, restart, migrate, seed
make test-backend          # pytest inside backend container
make bash-backend          # shell into backend container
make bash-db               # psql into the database
```

Single backend test: `docker compose exec backend uv run pytest tests/path/to/test.py::test_name`

Backend lint (ruff, configured in `backend/pyproject.toml` — line-length 120, target py312, rules `E F I UP B`; per-file ignores for `app/models/*`, `app/seed.py`, `app/api/v1/*`; `alembic/versions/` is excluded):
```bash
docker compose exec backend uv run ruff check .
docker compose exec backend uv run ruff format .
```

Frontend lint / type-check / build:
```bash
docker compose exec frontend npm run lint        # ESLint (zero warnings allowed)
docker compose exec frontend npm run build       # tsc -b && vite build
```

## CI

`.github/workflows/ci.yml` runs on push to `main` and on PRs. Two jobs:
- **backend** — boots a real postgres service, `uv sync --frozen`, `ruff check`, `pytest`.
- **frontend** — `npm ci`, `npm run lint`, `npm run build`.

Test database (`coffer_test`) is created at session start by the conftest, schema built via `Base.metadata.create_all`. Tests run inside a SAVEPOINT and roll back per-function, so seed/demo state never leaks in. `conftest.py` forces `COFFER_ENV=test` to disable the slowapi rate limiter (which is in-memory and would carry state across tests).

## Architecture

### Backend (`backend/app/`)
FastAPI + SQLAlchemy 2 (declarative `Mapped[...]`) + Alembic, Python 3.12 managed by `uv`.

- `main.py` mounts CORS, slowapi middleware, and the `api_router`. Health at `/health`.
- `api/v1/router.py` aggregates per-resource routers under `/api/v1`: `accounts`, `auth`, `budgets`, `categories`, `category_rules`, `debts`, `goals`, `imports`, `transactions`.
- `core/deps.py` exports `CurrentUser` (JWT-authenticated `User`) and `DbSession`. **Auth is dual-mode**: `get_current_user` reads the HttpOnly session cookie first (`COOKIE_NAME` in `core/cookies.py`), then falls back to the OAuth2 Bearer header — browsers use the cookie, `/docs` and API clients use Bearer. Every per-user query filters by `current.id`.
- `core/config.py` — pydantic settings. `assert_production_safe()` runs at import and refuses to boot if `JWT_SECRET` is unset, a known placeholder, or under 32 chars, unless `COFFER_ENV` is `dev` or `test`.
- `core/security.py` — bcrypt password hashing + PyJWT encode/decode (`sub = user_id`).
- `core/rate_limit.py` — slowapi `Limiter` with in-process storage. Disabled when `COFFER_ENV in {test}`. `/auth/login` is 10/min and `/auth/signup` is 5/hour per IP.
- `models/` — SQLAlchemy declarative models, all inheriting `Base` (+ `TimestampMixin`). All money columns are `Mapped[Decimal]` over `Numeric(14,2)` — do not introduce `Mapped[float]`.
- `schemas/` — Pydantic v2 request/response models. Money fields are `Decimal`; JSON output serializes through pydantic's Decimal handling.
- `services/csv_parser.py` / `services/pdf_parser.py` — pure parsing, return normalized rows + a `external_id` synthesized as `date|desc|amount` (used for per-account dedup on import).
- `services/categorization.py` — compile + match user-defined `CategoryRule`s against transaction descriptions. Called both during import and from the manual catch-up endpoint.
- `seed.py` — `python -m app.seed` (`--reset` wipes first). The demo user's debt minimums sum to exactly 40% of $5,000 salary; preserve that invariant.

### Statement import flow (`api/v1/imports.py`)
Two flows live side-by-side:

- **Quick (`POST /imports/upload`)** — parse + dedup + auto-categorize + commit in one request. Used by the legacy/simple path.
- **Preview-then-commit** — `POST /imports/preview` parses, stores rows as JSONB on the `StatementImport` row with `status="preview"`, returns the rows annotated with `suggested_category_id` and `is_duplicate`. The user reviews in the UI, then `POST /imports/{id}/confirm` commits the selected subset (with optional per-row category overrides). `DELETE /imports/{id}` discards a preview.

In both flows, the file is parsed **before** being written to disk so a parse failure can't leave an orphan upload, and inserted transactions are deduped by `external_id` per-account.

### Frontend (`frontend/src/`)
Vite + React 18 + TypeScript + Tailwind + React Router 6 + React Query + axios + recharts + lucide-react.

- `api/client.ts` — axios instance with `withCredentials: true`. **No bearer-token plumbing on the frontend** — the HttpOnly session cookie set by `/auth/login` is invisible to JS and is sent automatically. The single response interceptor redirects to `/login` on 401.
- `api/types.ts` — shared response types mirroring `backend/app/schemas/`.
- `contexts/AuthContext.tsx` + `contexts/useAuth.ts` — split for the react-refresh ESLint rule. `AuthProvider` exposes `user`, `login`, `signup`, `logout`; bootstraps from `/api/v1/auth/me`.
- `App.tsx` — `/login` and `/signup` are public; everything else nests inside `ProtectedRoute` → `Layout`. Pages are one file each in `pages/`.
- `lib/format.ts` — currency/percent formatters.
- `lib/useCurrency.ts` — `useUserCurrency` returns the most common currency across the user's accounts; `useAccountCurrencyMap` maps `account_id → currency` for per-row formatting on the Transactions page. Used by Dashboard, Budget, Debts, Goals, Transactions — pass the result as the second arg to `fmtMoney` / `fmtMoneySigned`.

API base URL comes from `VITE_API_URL` (defaults to `http://localhost:8000` in dev).

## Configuration

`.env` (copied from `.env.example`) drives both compose and the backend's pydantic `Settings`. Keys:
- `POSTGRES_*`, `BACKEND_PORT`, `FRONTEND_PORT`
- `JWT_SECRET` — must be unique and ≥ 32 chars in production, or set `COFFER_ENV=dev`
- `ACCESS_TOKEN_EXPIRE_MINUTES`, `CORS_ORIGINS` (comma-separated; split by `settings.cors_origin_list`)
- `VITE_API_URL`
- `COFFER_ENV` — `dev` / `test` to relax JWT-secret and rate-limit guards. Anything else is production.
