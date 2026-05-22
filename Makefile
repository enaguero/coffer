.PHONY: up down logs ps build rebuild restart bash-backend bash-frontend bash-db migrate makemigration seed seed-reset fresh test-backend clean

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

ps:
	docker compose ps

build:
	docker compose build

rebuild:
	docker compose build --no-cache

restart:
	docker compose restart

bash-backend:
	docker compose exec backend sh

bash-frontend:
	docker compose exec frontend sh

bash-db:
	docker compose exec db psql -U coffer -d coffer

migrate:
	docker compose exec backend uv run alembic upgrade head

makemigration:
	@read -p "Migration message: " msg; \
	docker compose exec backend uv run alembic revision --autogenerate -m "$$msg"

seed:
	docker compose exec backend uv run python -m app.seed

seed-reset:
	docker compose exec backend uv run python -m app.seed --reset

# Wipes the database volume, rebuilds images, restarts everything, runs migrations, loads seed.
fresh:
	@echo "==> Stopping services and removing volumes…"
	docker compose down -v
	@echo "==> Building images (picks up pyproject.toml / package.json changes)…"
	docker compose build
	@echo "==> Starting services (postgres, backend, frontend)…"
	docker compose up -d
	@echo "==> Waiting for backend to finish migrations and answer /health…"
	@for i in $$(seq 1 90); do \
		if docker compose exec -T backend curl -fs http://localhost:8000/health >/dev/null 2>&1; then \
			echo "    backend is up"; break; \
		fi; \
		sleep 2; \
		if [ "$$i" = "90" ]; then echo "Backend did not come up in 180s"; exit 1; fi; \
	done
	@echo "==> Loading seed data…"
	docker compose exec -T backend uv run python -m app.seed
	@echo ""
	@echo "Done. Sign in at http://localhost:5173"
	@echo "  Email:    demo@coffer.dev"
	@echo "  Password: demo1234"

test-backend:
	docker compose exec backend uv run pytest

clean:
	docker compose down -v
