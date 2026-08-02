"""Backup CLI: create / restore / verify / list Coffer archives.

    docker compose exec -T backend uv run python -m app.backup create
    docker compose exec -T backend uv run python -m app.backup verify
    docker compose exec -T backend uv run python -m app.backup restore <archive.zip> --yes
    docker compose exec -T backend uv run python -m app.backup list

Schedule with host cron (weekly drill):

    0 3 * * SUN  cd /path/to/coffer && docker compose exec -T backend \
        uv run python -m app.backup create && docker compose exec -T backend \
        uv run python -m app.backup verify

`verify` restores the newest archive into a scratch database and checks the
manifest's invariants — exit 0 only when the restore actually works.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine

from app.core.config import settings
from app.services.archive import (
    create_archive,
    latest_archive,
    read_meta,
    restore_archive,
    verify_archive,
    write_meta,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.backup")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_create = sub.add_parser("create")
    p_create.add_argument("--tag", default=None)
    p_restore = sub.add_parser("restore")
    p_restore.add_argument("archive")
    p_restore.add_argument("--yes", action="store_true")
    p_restore.add_argument("--force", action="store_true", help="allow alembic revision mismatch")
    p_verify = sub.add_parser("verify")
    p_verify.add_argument("archive", nargs="?", default=None)
    sub.add_parser("list")
    args = parser.parse_args(argv)

    backup_dir = Path(settings.backup_dir)
    uploads_dir = Path(settings.upload_dir)

    if args.cmd == "create":
        engine = create_engine(settings.database_url, future=True)
        try:
            out = create_archive(engine, backup_dir, uploads_dir, tag=args.tag, keep=settings.backup_keep)
        finally:
            engine.dispose()
        write_meta(backup_dir, last_created=datetime.now(UTC).isoformat(), last_archive=out.name)
        print(f"created {out} ({out.stat().st_size // 1024} KiB)")
        return 0

    if args.cmd == "list":
        for p in sorted(backup_dir.glob("coffer-archive-*.zip")):
            print(f"{p.name}  {p.stat().st_size // 1024} KiB")
        meta = read_meta(backup_dir)
        if meta:
            print(f"meta: {meta}")
        return 0

    if args.cmd == "verify":
        path = Path(args.archive) if args.archive else latest_archive(backup_dir)
        if path is None or not path.exists():
            print("no archive to verify — run `create` first", file=sys.stderr)
            return 1
        result = verify_archive(settings.database_url, path)
        write_meta(
            backup_dir,
            last_verified=result.checked_at,
            last_verify_ok=result.ok,
            last_verify_archive=result.archive,
            last_verify_problems=result.problems,
        )
        if result.ok:
            print(f"VERIFIED {path.name}: restore drill passed ({sum(result.row_counts.values())} rows)")
            return 0
        print(f"FAILED {path.name}:", file=sys.stderr)
        for p in result.problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    if args.cmd == "restore":
        path = Path(args.archive)
        if not path.exists():
            print(f"{path} not found", file=sys.stderr)
            return 1
        if not args.yes:
            print("restore WIPES the current database. Re-run with --yes to proceed.", file=sys.stderr)
            return 1
        engine = create_engine(settings.database_url, future=True)
        try:
            counts = restore_archive(engine, path, uploads_dir=uploads_dir, force=args.force)
        finally:
            engine.dispose()
        print(f"restored {sum(counts.values())} rows from {path.name}")
        return 0

    raise AssertionError("unreachable: argparse enforces a subcommand")


if __name__ == "__main__":
    raise SystemExit(main())
