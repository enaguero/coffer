"""Send the weekly digest to every user. Intended for cron on the host:

    0 8 * * MON  cd /path/to/coffer && docker compose exec -T backend uv run python -m app.digest

Exit codes: 0 = every digest sent (or no users exist); 1 = SMTP unconfigured
or at least one send failed. One user's failure never blocks the rest.
"""

from __future__ import annotations

import sys

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.user import User
from app.services.digest import compose_digest, send_email


def main() -> int:
    if not settings.smtp_configured:
        print("SMTP is not configured (set SMTP_HOST in .env) — digest not sent.", file=sys.stderr)
        return 1
    db = SessionLocal()
    sent = failed = 0
    try:
        for user in db.scalars(select(User)):
            try:
                digest = compose_digest(db, user)
                send_email(user.email, digest.subject, digest.body)
            except Exception as exc:  # noqa: BLE001 — one bad recipient must not block the rest
                failed += 1
                print(f"FAILED for {user.email}: {exc.__class__.__name__}: {exc}", file=sys.stderr)
                continue
            print(f"sent digest to {user.email} ({digest.item_count} items)")
            sent += 1
    finally:
        db.close()
    if failed:
        print(f"{sent} sent, {failed} failed", file=sys.stderr)
        return 1
    print(f"{sent} digest(s) sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
