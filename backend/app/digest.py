"""Send the weekly digest to every user. Intended for cron on the host:

    0 8 * * MON  cd /path/to/coffer && docker compose exec -T backend uv run python -m app.digest

Requires SMTP_* settings in .env; exits non-zero if unconfigured so a broken
cron surfaces instead of silently doing nothing.
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
    sent = 0
    try:
        for user in db.scalars(select(User)):
            digest = compose_digest(db, user)
            send_email(user.email, digest.subject, digest.body)
            print(f"sent digest to {user.email} ({digest.item_count} items)")
            sent += 1
    finally:
        db.close()
    return 0 if sent else 1


if __name__ == "__main__":
    raise SystemExit(main())
