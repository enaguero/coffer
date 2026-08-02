"""Data portability and backup status.

- GET /backup/export — the signed-in user's OWN data as a zip of JSONL files
  plus their statement originals: readable by any tool, no Coffer required.
  (Instance-wide backup/restore/verify is the operator CLI `python -m
  app.backup` — an API endpoint must never hand one user every user's data.)
- GET /backup/status — when the operator archive was last created and last
  passed its restore drill, for surfacing in the UI.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.core.config import settings
from app.core.deps import CurrentUser, DbSession
from app.core.rate_limit import limiter
from app.models import Base
from app.services.archive import read_meta, rows_to_jsonl

router = APIRouter(prefix="/backup", tags=["backup"])

# Every table that carries per-user rows has a user_id column; global tables
# (alembic bookkeeping) have none and are skipped for the personal export.


@router.get("/export")
@limiter.limit("5/hour")
def export_my_data(request: Request, current: CurrentUser, db: DbSession) -> StreamingResponse:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        exported: dict[str, int] = {}
        for table in Base.metadata.sorted_tables:
            if table.name == "users":
                # Own row only, and never the password hash — an export lands in
                # Downloads/cloud sync where a crackable hash doesn't belong.
                cols = [c for c in table.columns if c.name != "hashed_password"]
                rows = db.execute(select(*cols).where(table.columns["id"] == current.id)).mappings().all()
            elif "user_id" in table.columns:
                rows = db.execute(select(table).where(table.columns["user_id"] == current.id)).mappings().all()
            else:
                continue
            payload = rows_to_jsonl(rows)
            zf.writestr(f"data/{table.name}.jsonl", payload)
            exported[table.name] = len(rows)

        user_uploads = Path(settings.upload_dir) / str(current.id)
        if user_uploads.is_dir():
            for f in sorted(user_uploads.rglob("*")):
                if f.is_file():
                    # zf.write streams from disk in chunks — no whole-file buffer.
                    zf.write(f, arcname=f"statements/{f.relative_to(user_uploads)}")

        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "exported_at": datetime.now(UTC).isoformat(),
                    "user_email": current.email,
                    "row_counts": exported,
                    "note": "Personal data export — JSONL per table, plus original statements.",
                },
                indent=1,
            ),
        )
    buf.seek(0)
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="coffer-export-{stamp}.zip"'},
    )


class BackupStatusOut(BaseModel):
    """Sanitized: timestamps and verdicts only — verify problem strings carry
    instance paths and totals that don't belong to every authenticated user."""

    last_created: str | None = None
    last_verified: str | None = None
    last_verify_ok: bool | None = None
    problem_count: int = 0


@router.get("/status", response_model=BackupStatusOut)
def backup_status(current: CurrentUser) -> BackupStatusOut:
    meta = read_meta(Path(settings.backup_dir))
    return BackupStatusOut(
        last_created=meta.get("last_created"),
        last_verified=meta.get("last_verified"),
        last_verify_ok=meta.get("last_verify_ok"),
        problem_count=len(meta.get("last_verify_problems") or []),
    )
