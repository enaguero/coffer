"""Coffer Archive: create → verify (restore drill) round-trip, corruption
detection, and the per-user export endpoint."""

import json
import zipfile
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy.engine import Engine

from app.core.config import settings
from app.services.archive import (
    check_checksums,
    create_archive,
    latest_archive,
    read_manifest,
    restore_archive,
    verify_archive,
)
from tests.conftest import TEST_DATABASE_URL


@pytest.fixture(autouse=True)
def _isolated_dirs(monkeypatch, tmp_path):
    """Pin uploads/backups to tmp dirs: tests must not read the dev container's
    real uploads volume (env-dependent locally, absent in CI) nor pollute it."""
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(settings, "upload_dir", str(uploads))
    monkeypatch.setattr(settings, "backup_dir", str(tmp_path / "backup-meta"))


def _seed_some_data(client, headers) -> None:
    r = client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": "Current", "type": "checking", "currency": "GBP", "opening_balance": "0"},
    )
    account_id = r.json()["id"]
    today = date.today()
    csv = "Date,Description,Amount\n" + "".join(
        f"{(today - timedelta(days=30 * i)).strftime('%d/%m/%Y')},COFFEE SHOP,-3.50\n" for i in range(3)
    )
    r = client.post(
        "/api/v1/imports/upload",
        headers=headers,
        files={"file": ("s.csv", BytesIO(csv.encode()), "text/csv")},
        data={"account_id": str(account_id)},
    )
    assert r.status_code == 201, r.text


@pytest.fixture()
def committed_engine(engine: Engine):
    """Archive functions open their own connections, so data must be COMMITTED
    — the per-test SAVEPOINT fixture won't do. Clean up rows manually."""
    yield engine
    from sqlalchemy import text

    with engine.begin() as conn:
        for table in [
            "transactions",
            "statement_imports",
            "balance_snapshots",
            "import_profiles",
            "goals",
            "debts",
            "accounts",
            "users",
        ]:
            conn.execute(text(f"DELETE FROM {table}"))


def test_archive_round_trip_and_verify(client_committed, tmp_path, committed_engine) -> None:
    client, headers = client_committed
    _seed_some_data(client, headers)

    backup_dir = tmp_path / "backups"
    out = create_archive(committed_engine, backup_dir, Path(settings.upload_dir), keep=3)
    assert out.exists()
    assert latest_archive(backup_dir) == out

    manifest = read_manifest(out)
    assert manifest.row_counts["transactions"] == 3
    assert manifest.row_counts["users"] >= 1
    assert any(m.startswith("uploads/") for m in manifest.checksums)  # statement file included
    assert check_checksums(out) == []

    # The restore drill: scratch database, reload, invariants.
    result = verify_archive(TEST_DATABASE_URL, out)
    assert result.ok, result.problems
    assert result.row_counts["transactions"] == 3


def test_verify_detects_corruption(client_committed, tmp_path, committed_engine) -> None:
    client, headers = client_committed
    _seed_some_data(client, headers)
    out = create_archive(committed_engine, tmp_path, Path(settings.upload_dir), keep=3)

    # Tamper with a member: rewrite the zip with modified transaction data.
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(out) as zin, zipfile.ZipFile(tampered, "w") as zout:
        for item in zin.namelist():
            data = zin.read(item)
            if item == "data/transactions.jsonl":
                data = data.replace(b"-3.50", b"-9.99")
            zout.writestr(item, data)

    assert check_checksums(tampered) == ["data/transactions.jsonl"]
    result = verify_archive(TEST_DATABASE_URL, tampered)
    assert not result.ok
    assert any("checksum" in p for p in result.problems)


def test_prune_keeps_newest(tmp_path, committed_engine) -> None:
    for _ in range(4):
        create_archive(committed_engine, tmp_path, Path(settings.upload_dir), keep=2)
    assert len(list(tmp_path.glob("coffer-archive-*.zip"))) <= 2


def test_personal_export_contains_only_own_data(auth_client) -> None:
    client, headers, user_id = auth_client
    client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": "Mine", "type": "checking", "currency": "GBP"},
    )
    # A second user with their own account must not appear in the export.
    r = client.post("/api/v1/auth/signup", json={"email": "other9@coffer.dev", "password": "other-password-9"})
    other_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    client.cookies.clear()
    client.post(
        "/api/v1/accounts",
        headers=other_headers,
        json={"name": "Not yours", "type": "checking", "currency": "GBP"},
    )

    resp = client.get("/api/v1/backup/export", headers=headers)
    assert resp.status_code == 200
    with zipfile.ZipFile(BytesIO(resp.content)) as zf:
        accounts = [json.loads(line) for line in zf.read("data/accounts.jsonl").decode().splitlines() if line]
        users = [json.loads(line) for line in zf.read("data/users.jsonl").decode().splitlines() if line]
        manifest = json.loads(zf.read("manifest.json"))
    assert [a["name"] for a in accounts] == ["Mine"]
    assert len(users) == 1 and users[0]["id"] == user_id
    assert manifest["row_counts"]["accounts"] == 1


def test_backup_status_endpoint_is_sanitized(auth_client) -> None:
    client, headers, _ = auth_client
    meta_dir = Path(settings.backup_dir)
    meta_dir.mkdir(parents=True, exist_ok=True)
    (meta_dir / "backup_meta.json").write_text(
        '{"last_verified": "2026-08-01T00:00:00+00:00", "last_verify_ok": false, '
        '"last_verify_problems": ["secret /app/uploads/7/x.csv path"]}'
    )
    r = client.get("/api/v1/backup/status", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["last_verify_ok"] is False
    assert body["problem_count"] == 1
    assert "secret" not in json.dumps(body)  # raw problem strings never leave the box


def test_restore_replaces_existing_data_and_uploads(client_committed, tmp_path, committed_engine) -> None:
    client, headers = client_committed
    _seed_some_data(client, headers)
    out = create_archive(committed_engine, tmp_path / "b", Path(settings.upload_dir), keep=3)

    # Post-archive drift: an extra account and a stray upload file.
    client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": "After the backup", "type": "cash", "currency": "GBP"},
    )
    stray = Path(settings.upload_dir) / "1" / "stray.csv"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_text("not in the archive")

    counts = restore_archive(committed_engine, out, uploads_dir=Path(settings.upload_dir))
    assert counts["accounts"] == 1  # only the archived account survives
    from sqlalchemy import text

    with committed_engine.connect() as conn:
        names = [r[0] for r in conn.execute(text("SELECT name FROM accounts"))]
    assert names == ["Current"]
    assert not stray.exists()  # stale files cleared by restore
    archived_files = list(Path(settings.upload_dir).rglob("*.csv"))
    assert len(archived_files) == 1  # the archived statement is back


def test_restore_rejects_zip_slip(tmp_path, committed_engine) -> None:
    import hashlib as _hl

    evil = tmp_path / "evil.zip"
    payload = b"pwned"
    member = "uploads/../escaped.txt"
    manifest = {
        "format_version": 1,
        "created_at": "2026-01-01T00:00:00+00:00",
        "alembic_revision": None,
        "row_counts": {},
        "transaction_amount_sum": "0",
        "checksums": {member: _hl.sha256(payload).hexdigest()},
    }
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr(member, payload)
        zf.writestr("manifest.json", json.dumps(manifest))

    with pytest.raises(ValueError, match="escapes uploads dir"):
        restore_archive(committed_engine, evil, uploads_dir=tmp_path / "up")
    assert not (tmp_path / "escaped.txt").exists()


def test_restore_refuses_revision_mismatch(tmp_path, committed_engine) -> None:
    out = create_archive(committed_engine, tmp_path, Path(settings.upload_dir), keep=3)
    # Fake a revision mismatch by stamping the live DB.
    from sqlalchemy import text

    with committed_engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32))"))
        conn.execute(text("DELETE FROM alembic_version"))
        conn.execute(text("INSERT INTO alembic_version VALUES ('deadbeef')"))
    try:
        with pytest.raises(ValueError, match="alembic revision"):
            restore_archive(committed_engine, out, uploads_dir=None)
        # force=True bypasses
        restore_archive(committed_engine, out, uploads_dir=None, force=True)
    finally:
        with committed_engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
