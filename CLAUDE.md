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
make backup                # create a Coffer Archive (DB + statements + manifest)
make backup-verify         # restore-drill the newest archive into a scratch DB
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
- `api/v1/router.py` aggregates per-resource routers under `/api/v1`: `accounts`, `auth`, `backup`, `banks`, `budgets`, `cashflow`, `categories`, `category_rules`, `debts`, `fx`, `goals`, `imports`, `insights`, `transactions`. `insights` serves the analytics endpoints (recurring, forecast, networth, allowances, surplus, digest preview/send).
- **Multi-currency**: `users.display_currency` (set via `PATCH /auth/me`; NULL = automatic — most-common currency among liquid accounts, then all accounts, ties alphabetical) + user-maintained `FxRate` rows (`/api/v1/fx`; 1 unit of currency = rate units of display currency — no external FX API; changing the display currency **deletes** saved rates, they were defined against the old target). `services/analytics/fx.py::convert` returns `None` when no rate exists; net worth then excludes that account from totals, flags it `converted=false`, and lists its currency in `excluded_currencies` — never silently mixed. Forecast, digest, and surplus are single-currency by design: `account_loader.load_forecast_scope` / display-currency filters keep only display-currency accounts' balances and transactions in those aggregates. Register figures (debts, budgets, manual goal amounts) carry no currency column and are display-denominated by convention.
- `core/deps.py` exports `CurrentUser` (JWT-authenticated `User`) and `DbSession`. **Auth is dual-mode**: `get_current_user` reads the HttpOnly session cookie first (`COOKIE_NAME` in `core/cookies.py`), then falls back to the OAuth2 Bearer header — browsers use the cookie, `/docs` and API clients use Bearer. Every per-user query filters by `current.id`.
- `core/config.py` — pydantic settings. `assert_production_safe()` runs at import and refuses to boot if `JWT_SECRET` is unset, a known placeholder, or under 32 chars, unless `COFFER_ENV` is `dev` or `test`.
- `core/security.py` — bcrypt password hashing + PyJWT encode/decode (`sub = user_id`).
- `core/rate_limit.py` — slowapi `Limiter` with in-process storage. Disabled when `COFFER_ENV in {test}`. `/auth/login` is 10/min and `/auth/signup` is 5/hour per IP.
- `models/` — SQLAlchemy declarative models, all inheriting `Base` (+ `TimestampMixin`). All money columns are `Mapped[Decimal]` over `Numeric(14,2)` — do not introduce `Mapped[float]`.
- `schemas/` — Pydantic v2 request/response models. Money fields are `Decimal`; JSON output serializes through pydantic's Decimal handling.
- `services/csv_parser.py` / `services/pdf_parser.py` — heuristic parsing (the fallback layer), return normalized rows + a `external_id` synthesized as `date|desc|amount` (used for per-account dedup on import). `parse_csv_detailed` also reports the detected column layout so it can be saved as an import profile.
- `services/import_engine/` — the statement import engine. `resolver.resolve_and_parse` picks the parse strategy for an upload: OFX/QIF by extension (`formats/`), then the account's saved `ImportProfile`, then the UK bank catalog preset/adapter for the account's `(bank_id, type)` (`catalog.py`, `adapters/`), then the heuristic sniffer. Presets are declarative `ImportProfileConfig`s (data, not classes) — a mismatching preset/profile degrades to the next layer with a user-visible warning, never a failed upload. Code adapters (registry in `adapters/base.py`) exist only where parsing needs logic (e.g. Revolut's state filtering). **Adding a UK bank = adding a data entry to `catalog.py`.**
- `services/categorization.py` — compile + match user-defined `CategoryRule`s against transaction descriptions. Called both during import and from the manual catch-up endpoint.
- `services/analytics/` — pure financial computation, no DB/FastAPI (unit-testable with plain values). `debt_plan.py`: promo-APR-aware avalanche/snowball amortization with snowflake extras (POST `/debts/plan` compares vs a minimums-only baseline; an `unpayable` truncated baseline reports no savings comparison). `recurring.py`: recurring-transaction detection (the enabling primitive) + salary-raise detection. `forecast.py`: day-by-day balance projection from recurring items with reserve-threshold warnings; debt due-days are calendar annotations only (projecting them would double-count detected payments). `net_worth.py`: balance-at-date anchored on the latest BalanceSnapshot then applying later transactions; drift = attested minus derived (missing-data signal). `surplus.py`: monthly cash surplus + marginal-pound allocation ranking (debt APR = guaranteed return, goal months-earlier, runway gained). Served by `api/v1/insights.py` and `/accounts/coverage` (per-account data-freshness).
- **Balance attestations**: statement parsers capture the bank's own running/closing balance (CSV `Balance` columns via presets/profiles/heuristic, OFX `<LEDGERBAL>`) into `BalanceSnapshot` at import — one per (account, day), manual valuations (pension/property on `other`-type accounts) share the table via `POST /accounts/{id}/snapshots`.
- `seed.py` — `python -m app.seed` (`--reset` wipes first). The demo user's debt minimums sum to exactly 40% of $5,000 salary; preserve that invariant.

### Statement import flow (`api/v1/imports.py`)
Accepted uploads: `.csv`, `.ofx`/`.qfx`, `.qif`, `.pdf`. Three entry surfaces:

- **Quick (`POST /imports/upload`)** — parse + dedup + auto-categorize + commit in one request. Used by the legacy/simple path.
- **Preview-then-commit** — `POST /imports/preview` parses, stores rows as JSONB on the `StatementImport` row with `status="preview"`, returns the rows annotated with `suggested_category_id` and `is_duplicate`, plus how the file was parsed (`source`, `warnings`) and — when the heuristic sniffer ran — an `inferred_config` the client can save as the account's import profile. The user reviews in the UI, then `POST /imports/{id}/confirm` commits the selected subset (with optional per-row category overrides). `DELETE /imports/{id}` discards a preview.

- **Statement inbox** — files wait in `<INBOX_DIR>/<user_id>/pending/` until reviewed. They arrive via `POST /imports/inbox` (the PWA share-sheet: the service worker in `frontend/public/sw.js` parks shared files in the Cache API and the Import page drains them with the real session) or dropped directly into the host-bind-mounted `./inbox/` folder (Syncthing/NAS — the listing reads the directory live, no daemon). Previewing an inbox file feeds the normal preview→confirm pipeline and archives it to `processed/` (pruned to the last 20; `uploads/` keeps the canonical copy).

In the upload flows, the file is parsed **before** being written to disk so a parse failure can't leave an orphan upload, and inserted transactions are deduped by `external_id` per-account (bank-native ids — OFX `FITID`, Monzo `Transaction ID` — when available, the synthesized key otherwise).

Import profiles are one-per-account (`models/import_profile.py`, JSONB config) managed via `GET/PUT/DELETE /accounts/{id}/import-profile`. The UK bank list for the account picker is served by `GET /api/v1/banks` from `services/import_engine/catalog.py`; `accounts.bank_id` stores the chosen catalog slug and drives preset selection on import.

### Frontend (`frontend/src/`)
Vite + React 18 + TypeScript + Tailwind + React Router 6 + React Query + axios + recharts + lucide-react.

- `api/client.ts` — axios instance with `withCredentials: true`. **No bearer-token plumbing on the frontend** — the HttpOnly session cookie set by `/auth/login` is invisible to JS and is sent automatically. The single response interceptor redirects to `/login` on 401.
- `api/types.ts` — shared response types mirroring `backend/app/schemas/`.
- `contexts/AuthContext.tsx` + `contexts/useAuth.ts` — split for the react-refresh ESLint rule. `AuthProvider` exposes `user`, `login`, `signup`, `logout`; bootstraps from `/api/v1/auth/me`.
- `App.tsx` — `/login` and `/signup` are public; everything else nests inside `ProtectedRoute` → `Layout`. Pages are one file each in `pages/`.
- `lib/format.ts` — currency/percent formatters.
- `lib/useCurrency.ts` — `useUserCurrency` returns the user's saved `display_currency` when set, else the most common currency across their accounts (ties alphabetical, mirroring the backend); `useAccountCurrencyMap` maps `account_id → currency` for per-row formatting (Transactions, the recurring table). Pages that render converted insights (`NetWorth`, `Forecast`) prefer the response's `display_currency` field over the hook. Used by Dashboard, Budget, Debts, Goals, Transactions — pass the result as the second arg to `fmtMoney` / `fmtMoneySigned`.

API base URL comes from `VITE_API_URL` (defaults to `http://localhost:8000` in dev).

## Configuration

`.env` (copied from `.env.example`) drives both compose and the backend's pydantic `Settings`. Keys:
- `POSTGRES_*`, `BACKEND_PORT`, `FRONTEND_PORT`
- `JWT_SECRET` — must be unique and ≥ 32 chars in production, or set `COFFER_ENV=dev`
- `ACCESS_TOKEN_EXPIRE_MINUTES`, `CORS_ORIGINS` (comma-separated; split by `settings.cors_origin_list`)
- `VITE_API_URL`
- `COFFER_ENV` — `dev` / `test` to relax JWT-secret and rate-limit guards. Anything else is production.
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_STARTTLS` — weekly digest email delivery; empty `SMTP_HOST` disables sending (the in-app preview still works). **These must also be passed through `docker-compose.yml`'s backend `environment:` block — the container does not read the root `.env` directly.**
- `BACKUP_DIR` (default `/app/backups`, a named volume), `BACKUP_KEEP` (archive generations to retain) — also passed through the compose `environment:` block.
- `INBOX_DIR` (default `/app/inbox`, bind-mounted to `./inbox` on the host so sync tools can reach it) — statement inbox location.

**Backups (Coffer Archive)**: the compose start command runs `python -m app.prestart`, which snapshots a `pre-upgrade` archive whenever pending migrations are detected on a non-empty DB, then migrates. The archive is a zip of per-table JSONL (readable without Coffer) + the original statement files + a checksummed manifest; a bare `pg_dump` misses the statement files. `python -m app.backup create|verify|restore|list` — `verify` is a real restore drill into a scratch database (schedule `create && verify` with weekly host cron). Restore refuses alembic-revision mismatches without `--force`. `GET /backup/export` is the per-user portability zip; instance restore is CLI-only.

The weekly digest sender is `docker compose exec -T backend uv run python -m app.digest` (one email per user, to their login address) — schedule it with host cron. `GET /api/v1/insights/digest/preview` renders the same content in-app without SMTP.
