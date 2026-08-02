"""Container prestart: snapshot before migrating, then migrate.

Replaces the bare `alembic upgrade head` in the compose start command. When
pending migrations are detected on a database that already has history, an
archive tagged `pre-upgrade` is created first — so a botched migration can
always be rolled back with `python -m app.backup restore <archive> --yes`
plus the previous image.
"""

from __future__ import annotations

import sys
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from alembic import command
from app.core.config import settings
from app.services.archive import create_archive


def main() -> int:
    engine = create_engine(settings.database_url, future=True)
    try:
        try:
            script = ScriptDirectory.from_config(Config("alembic.ini"))
            with engine.connect() as conn:
                current = MigrationContext.configure(conn).get_current_revision()
            pending = current not in set(script.get_heads())
            fresh_db = current is None
        except Exception:
            pending, fresh_db = True, True

        if pending and not fresh_db:
            try:
                out = create_archive(
                    engine,
                    Path(settings.backup_dir),
                    Path(settings.upload_dir),
                    tag="pre-upgrade",
                    keep=settings.backup_keep,
                )
                print(f"prestart: pending migrations — snapshot {out.name} taken")
            except Exception as exc:  # noqa: BLE001 — a failed snapshot must not block boot forever
                print(
                    f"prestart: snapshot failed ({exc.__class__.__name__}: {exc}) — continuing",
                    file=sys.stderr,
                )
    finally:
        engine.dispose()

    command.upgrade(Config("alembic.ini"), "head")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
