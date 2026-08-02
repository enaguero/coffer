"""Shared test fixtures.

Strategy:
- One throwaway database (`coffer_test`) is created at session start and dropped
  at the end. Schema is built with `Base.metadata.create_all` — we don't run
  Alembic in tests because migration tests belong elsewhere and metadata-create
  is faster + version-agnostic.
- Each test runs inside a SAVEPOINT on a long-lived connection, so rows
  inserted by one test never leak into another and we don't pay create/drop
  cost per test.
- The FastAPI app's `get_db` dependency is overridden so route code shares the
  same in-test session/connection.
"""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker

# Force a known-good config before importing the app, so the production-safety
# check in core/config.py doesn't refuse a placeholder JWT secret. Use forceful
# assignment (not setdefault): docker-compose.yml ships COFFER_ENV=dev, and we
# need to override it so the rate limiter is disabled during the test suite.
os.environ["COFFER_ENV"] = "test"
os.environ.setdefault("JWT_SECRET", "test-secret-not-used-anywhere-real-32chars")

_DEFAULT_URL = os.environ.get("DATABASE_URL", "postgresql+psycopg://coffer:coffer@db:5432/coffer")
TEST_DB_NAME = "coffer_test"
TEST_DATABASE_URL = os.environ.get(
    "COFFER_TEST_DATABASE_URL",
    _DEFAULT_URL.rsplit("/", 1)[0] + f"/{TEST_DB_NAME}",
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

from app.core.config import settings  # noqa: E402
from app.core.database import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_dirs(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every filesystem-touching setting at a per-test tmp dir. The
    defaults live under /app, which only exists inside the Docker image — on a
    bare CI runner they'd fail with FileNotFoundError/PermissionError, and even
    in Docker they'd leak state between tests."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "inbox_dir", str(tmp_path / "inbox"))
    monkeypatch.setattr(settings, "backup_dir", str(tmp_path / "backups"))


def _recreate_test_database() -> None:
    """Drop and recreate the test DB via an autocommit admin connection."""
    admin_url = _DEFAULT_URL.rsplit("/", 1)[0] + "/postgres"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT", future=True)
    with admin.connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :db AND pid <> pg_backend_pid()"
            ),
            {"db": TEST_DB_NAME},
        )
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"'))
        conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    admin.dispose()


@pytest.fixture(scope="session")
def engine() -> Generator[Engine, None, None]:
    _recreate_test_database()
    eng = create_engine(TEST_DATABASE_URL, future=True)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def db_connection(engine: Engine) -> Generator[Connection, None, None]:
    """A connection with an outer transaction that always rolls back."""
    conn = engine.connect()
    outer = conn.begin()
    try:
        yield conn
    finally:
        outer.rollback()
        conn.close()


@pytest.fixture()
def db_session(db_connection: Connection) -> Generator[Session, None, None]:
    """A session bound to the rollback-on-teardown connection."""
    Maker = sessionmaker(bind=db_connection, autoflush=False, autocommit=False, future=True)
    session = Maker()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """TestClient with get_db overridden to share the test's session."""

    def _override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def client_committed(engine: Engine) -> Generator[tuple[TestClient, dict[str, str]], None, None]:
    """A client whose writes COMMIT for real — needed by code that opens its
    own DB connections (e.g. archive create/verify). Tests using this must
    clean up (see test_backup.py's committed_engine fixture)."""
    Maker = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = Maker()

    def _override() -> Generator[Session, None, None]:
        yield session

    app.dependency_overrides[get_db] = _override
    try:
        with TestClient(app) as c:
            r = c.post(
                "/api/v1/auth/signup",
                json={"email": "committed@coffer.dev", "password": "committed-pw-1234"},
            )
            assert r.status_code == 201, r.text
            headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
            yield c, headers
    finally:
        session.close()
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def auth_client(client: TestClient) -> tuple[TestClient, dict[str, str], int]:
    """Signed-up + authenticated client, plus headers and the new user id."""
    email = "test@coffer.dev"
    password = "test-password-1234"
    r = client.post("/api/v1/auth/signup", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/v1/auth/me", headers=headers).json()
    return client, headers, me["id"]
