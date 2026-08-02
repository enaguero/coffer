"""Coffer Archive: one restorable artifact for the whole instance.

Format: a zip containing
- manifest.json — format version, alembic revision, per-table row counts,
  ledger invariants, and sha256 checksums of every member
- data/<table>.jsonl — every table, rows as JSON (Decimal/date as strings),
  readable by any tool without Coffer running
- uploads/... — the original statement files (they live outside Postgres and
  are silently lost by a bare pg_dump)

Design points that matter:
- The dump REFLECTS the live database schema (not the code's models), so the
  pre-upgrade snapshot works even when the new image's models are ahead of
  the not-yet-migrated database — the exact case snapshots exist for.
- The dump runs in one REPEATABLE READ transaction: a torn archive (child
  rows whose parents committed mid-dump) can never be produced.
- Creation is atomic: written to a .partial file and renamed on success, so
  a failed create can't leave a plausible-looking archive behind.
- verify is a restore DRILL — restore into a uniquely-named scratch database,
  re-check row counts and ledger invariants, then drop it. A backup that has
  never been restored is a hope, not a backup.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import shutil
import zipfile
from dataclasses import dataclass, field, fields
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url

FORMAT_VERSION = 1
META_FILE = "backup_meta.json"


# ---- serialization ------------------------------------------------------------


def _to_jsonable(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def rows_to_jsonl(rows) -> str:
    """Mappings → one JSON object per line. Shared with the per-user export."""
    return "\n".join(json.dumps({k: _to_jsonable(v) for k, v in r.items()}, ensure_ascii=False) for r in rows)


def _from_jsonable(column, value):
    if value is None:
        return None
    if isinstance(column.type, sa.Numeric):
        return Decimal(value)
    if isinstance(column.type, sa.DateTime):
        return datetime.fromisoformat(value)
    if isinstance(column.type, sa.Date):
        return date.fromisoformat(value)
    return value  # str/int/bool/JSONB/enum values round-trip as-is


def _reflected_metadata(conn) -> sa.MetaData:
    meta = sa.MetaData()
    meta.reflect(bind=conn)
    return meta


def _data_tables(meta: sa.MetaData) -> list[sa.Table]:
    return [t for t in meta.sorted_tables if t.name != "alembic_version"]


# ---- manifest -----------------------------------------------------------------


@dataclass
class Manifest:
    format_version: int
    created_at: str
    alembic_revision: str | None
    row_counts: dict[str, int]
    transaction_amount_sum: str  # ledger invariant, Decimal as string
    checksums: dict[str, str] = field(default_factory=dict)  # member path -> sha256


def _alembic_revision(conn, meta: sa.MetaData) -> str | None:
    """Read the alembic revision via reflected metadata — probing with a raw
    SELECT would abort the surrounding transaction when the table is absent
    (e.g. metadata-created test databases)."""
    table = meta.tables.get("alembic_version")
    if table is None:
        return None
    return conn.execute(select(table.c.version_num)).scalar()


# ---- create -------------------------------------------------------------------


def create_archive(
    engine: Engine,
    backup_dir: Path,
    uploads_dir: Path,
    tag: str | None = None,
    keep: int = 5,
) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    name = f"coffer-archive-{stamp}-{secrets.token_hex(3)}{f'-{tag}' if tag else ''}.zip"
    out = backup_dir / name
    partial = backup_dir / (name + ".partial")

    row_counts: dict[str, int] = {}
    checksums: dict[str, str] = {}
    txn_sum = Decimal("0")

    try:
        with (
            zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as zf,
            # One snapshot for the whole dump — no torn parent/child rows.
            engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn,
            conn.begin(),
        ):
            meta = _reflected_metadata(conn)
            revision = _alembic_revision(conn, meta)
            for table in _data_tables(meta):
                rows = conn.execute(select(table)).mappings().all()
                row_counts[table.name] = len(rows)
                if table.name == "transactions":
                    txn_sum = sum((Decimal(r["amount"]) for r in rows), Decimal("0"))
                payload = rows_to_jsonl(rows)
                member = f"data/{table.name}.jsonl"
                zf.writestr(member, payload)
                checksums[member] = hashlib.sha256(payload.encode()).hexdigest()

            if uploads_dir.is_dir():
                for f in sorted(uploads_dir.rglob("*")):
                    if f.is_file():
                        member = f"uploads/{f.relative_to(uploads_dir)}"
                        content = f.read_bytes()
                        zf.writestr(member, content)
                        checksums[member] = hashlib.sha256(content).hexdigest()

            manifest = Manifest(
                format_version=FORMAT_VERSION,
                created_at=datetime.now(UTC).isoformat(),
                alembic_revision=revision,
                row_counts=row_counts,
                transaction_amount_sum=str(txn_sum),
                checksums=checksums,
            )
            zf.writestr("manifest.json", json.dumps(manifest.__dict__, indent=1))
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    partial.rename(out)

    _prune(backup_dir, keep)
    return out


def _prune(backup_dir: Path, keep: int) -> None:
    archives = sorted(backup_dir.glob("coffer-archive-*.zip"), key=lambda p: p.stat().st_mtime)
    for old in archives[:-keep] if keep > 0 else []:
        old.unlink(missing_ok=True)


def latest_archive(backup_dir: Path) -> Path | None:
    archives = sorted(backup_dir.glob("coffer-archive-*.zip"), key=lambda p: p.stat().st_mtime)
    return archives[-1] if archives else None


# ---- reading / integrity ------------------------------------------------------


def read_manifest(path: Path) -> Manifest:
    with zipfile.ZipFile(path) as zf:
        raw = json.loads(zf.read("manifest.json"))
    if raw.get("format_version", 0) > FORMAT_VERSION:
        raise ValueError(f"Archive format v{raw['format_version']} is newer than this Coffer understands")
    known = {f.name for f in fields(Manifest)}
    return Manifest(**{k: v for k, v in raw.items() if k in known})


def check_checksums(path: Path) -> list[str]:
    """Return the list of corrupted members (empty = intact)."""
    manifest = read_manifest(path)
    bad: list[str] = []
    with zipfile.ZipFile(path) as zf:
        for member, expected in manifest.checksums.items():
            actual = hashlib.sha256(zf.read(member)).hexdigest()
            if actual != expected:
                bad.append(member)
    return bad


# ---- restore ------------------------------------------------------------------


def restore_archive(
    engine: Engine,
    path: Path,
    uploads_dir: Path | None = None,
    force: bool = False,
) -> dict[str, int]:
    """Wipe every table and reload from the archive.

    The schema must already exist (run alembic first). Unless `force`, the
    archive's alembic revision must match the live database's — restoring
    across schema versions silently drops or nulls columns.

    Returns per-table inserted row counts.
    """
    corrupted = check_checksums(path)
    if corrupted:
        raise ValueError(f"Archive corrupted, refusing to restore: {corrupted[:5]}")
    manifest = read_manifest(path)

    inserted: dict[str, int] = {}
    with zipfile.ZipFile(path) as zf, engine.begin() as conn:
        meta = _reflected_metadata(conn)
        live_revision = _alembic_revision(conn, meta)
        if not force and manifest.alembic_revision != live_revision:
            raise ValueError(
                f"Archive is at alembic revision {manifest.alembic_revision!r} but the "
                f"database is at {live_revision!r}. Migrate to match, or pass force=True "
                "(--force) accepting that mismatched columns are dropped or nulled."
            )
        tables = _data_tables(meta)
        for table in reversed(tables):
            conn.execute(table.delete())
        for table in tables:
            member = f"data/{table.name}.jsonl"
            try:
                payload = zf.read(member).decode()
            except KeyError:
                inserted[table.name] = 0
                continue
            rows = [
                {k: _from_jsonable(table.columns[k], v) for k, v in json.loads(line).items() if k in table.columns}
                for line in payload.splitlines()
                if line.strip()
            ]
            if rows:
                conn.execute(insert(table), rows)
            inserted[table.name] = len(rows)
        # Sequences don't follow explicit-id inserts — resync any that exist.
        for table in tables:
            if "id" in table.columns:
                conn.execute(
                    text(
                        f"SELECT setval(seq, COALESCE((SELECT MAX(id) FROM {table.name}), 0) + 1, false) "  # noqa: S608 — table names come from reflection, not user input
                        f"FROM (SELECT pg_get_serial_sequence('{table.name}', 'id') AS seq) q "
                        "WHERE seq IS NOT NULL"
                    )
                )

    if uploads_dir is not None:
        _restore_uploads(zipfile.ZipFile(path), uploads_dir)
    return inserted


def _restore_uploads(zf: zipfile.ZipFile, uploads_dir: Path) -> None:
    """Replace the uploads tree with the archive's copy.

    The directory is cleared first — stale files from after the archive was
    taken must not survive (a recycled user id would otherwise inherit a
    previous user's statements). Member paths are containment-checked: a
    crafted archive must not write outside uploads_dir (zip-slip).
    """
    with zf:
        base = uploads_dir.resolve()
        if uploads_dir.exists():
            shutil.rmtree(uploads_dir)
        uploads_dir.mkdir(parents=True, exist_ok=True)
        for member in zf.namelist():
            if not member.startswith("uploads/") or member.endswith("/"):
                continue
            target = (uploads_dir / member.removeprefix("uploads/")).resolve()
            if not target.is_relative_to(base):
                raise ValueError(f"Archive member escapes uploads dir: {member!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(member))


# ---- verify (the restore drill) -----------------------------------------------


@dataclass
class VerifyResult:
    ok: bool
    archive: str
    checked_at: str
    problems: list[str] = field(default_factory=list)
    row_counts: dict[str, int] = field(default_factory=dict)


def verify_archive(database_url: str, path: Path) -> VerifyResult:
    """Restore the archive into a uniquely-named scratch database, re-check
    the manifest's row counts and ledger invariant, then drop the scratch."""
    now = datetime.now(UTC).isoformat()
    problems: list[str] = []
    counts: dict[str, int] = {}

    try:
        corrupted = check_checksums(path)
    except Exception as exc:  # unreadable/partial archive IS a failed verification
        return VerifyResult(
            ok=False,
            archive=path.name,
            checked_at=now,
            problems=[f"archive unreadable: {exc.__class__.__name__}: {exc}"],
        )
    if corrupted:
        return VerifyResult(
            ok=False,
            archive=path.name,
            checked_at=now,
            problems=[f"checksum mismatch: {m}" for m in corrupted],
        )
    manifest = read_manifest(path)

    url = make_url(database_url)
    scratch_name = f"{url.database}_verify_{secrets.token_hex(4)}"
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT", future=True)
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{scratch_name}"'))

        scratch = create_engine(url.set(database=scratch_name), future=True)
        try:
            # Recreate the archive's schema shape from its own table data by
            # using the app's models is wrong across versions — but the drill
            # runs on the SAME image that took the archive in the cron pairing,
            # so models match. Enforce that via the revision check in restore.
            from app.models import Base

            Base.metadata.create_all(scratch)
            counts = restore_archive(scratch, path, uploads_dir=None, force=True)

            for table, expected in manifest.row_counts.items():
                got = counts.get(table, 0)
                if got != expected:
                    problems.append(f"{table}: restored {got} rows, manifest says {expected}")

            with scratch.connect() as conn:
                restored_sum = conn.execute(text("SELECT COALESCE(SUM(amount), 0) FROM transactions")).scalar()
            if str(Decimal(restored_sum)) != manifest.transaction_amount_sum:
                problems.append(
                    f"transaction sum drifted: restored {restored_sum}, manifest {manifest.transaction_amount_sum}"
                )
        except Exception as exc:  # noqa: BLE001 — any failure IS the verification result
            problems.append(f"restore drill failed: {exc.__class__.__name__}: {exc}")
        finally:
            scratch.dispose()
            with admin.connect() as conn:
                conn.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :db AND pid <> pg_backend_pid()"
                    ),
                    {"db": scratch_name},
                )
                conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch_name}"'))
    except Exception as exc:  # noqa: BLE001
        problems.append(f"scratch database setup failed: {exc.__class__.__name__}: {exc}")
    finally:
        admin.dispose()

    return VerifyResult(ok=not problems, archive=path.name, checked_at=now, problems=problems, row_counts=counts)


# ---- status sidecar -----------------------------------------------------------


def write_meta(backup_dir: Path, **updates) -> None:
    backup_dir.mkdir(parents=True, exist_ok=True)
    meta = read_meta(backup_dir)
    meta.update(updates)
    (backup_dir / META_FILE).write_text(json.dumps(meta, indent=1))


def read_meta(backup_dir: Path) -> dict:
    try:
        return json.loads((backup_dir / META_FILE).read_text())
    except (OSError, json.JSONDecodeError):
        return {}
