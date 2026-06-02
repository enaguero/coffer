"""Seed cashflow data for the demo user.

Populates Jan 2026 → Dec 2027 (24 months) using the user's real Flujos cashflow
spreadsheet as the source of truth. Lines are split by country:
- GB / GBP: salary plus UK loans and cards
- CL / CLP: Chilean loans, credit cards, and family obligations

Empty cells in the spreadsheet (e.g. Lloyds card after Dec 2026) are intentionally
omitted from the entry list so the grid shows blank rather than a misleading zero.
Jan/Feb/Mar 2026 are not in the original sheet — we copy April 2026's value so the
full calendar year is renderable.

Run inside the container:
    docker compose exec backend uv run python -m app.seed_cashflow
    docker compose exec backend uv run python -m app.seed_cashflow --reset
    docker compose exec backend uv run python -m app.seed_cashflow --email someone@coffer.dev
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.cashflow import CashflowEntry, CashflowKind, CashflowLine
from app.models.user import User

DEMO_EMAIL = "demo@coffer.dev"

# Months covered by the seeded entries, in order.
# Apr 2026 → Dec 2027 = 21 months (matches the source spreadsheet).
_CSV_MONTHS: list[tuple[int, int]] = [
    (2026, 4), (2026, 5), (2026, 6), (2026, 7), (2026, 8), (2026, 9),
    (2026, 10), (2026, 11), (2026, 12),
    (2027, 1), (2027, 2), (2027, 3), (2027, 4), (2027, 5), (2027, 6),
    (2027, 7), (2027, 8), (2027, 9), (2027, 10), (2027, 11), (2027, 12),
]

# Months that come BEFORE the spreadsheet starts; we copy the April 2026 value
# into these so the demo grid is contiguous from Jan 2026.
_PRE_2026_MONTHS: list[tuple[int, int]] = [(2026, 1), (2026, 2), (2026, 3)]


# Sentinel for "no entry" cells — these stay blank in the grid.
_BLANK = None


def _N(*values: float | int | None) -> list[Decimal | None]:
    """Shorthand for the 21-column row; converts None straight through."""
    if len(values) != 21:
        raise ValueError(f"Expected 21 monthly cells, got {len(values)}")
    return [Decimal(str(v)) if v is not None else _BLANK for v in values]


# (name, kind, country, currency, sort_order, 21 monthly amounts Apr26→Dec27)
LINES: list[tuple[str, CashflowKind, str, str, int, list[Decimal | None]]] = [
    # ---- GB / GBP
    ("Hurdle", CashflowKind.INCOME, "GB", "GBP", 0, _N(
        5200, 5100, 5080, 5080, 5080, 5080, 4950, 4950, 4950,
        4850, 4850, 4850, 4850, 4850, 4850, 4850, 4850, 4850, 4850, 4850, 4850,
    )),
    ("House", CashflowKind.EXPENSE, "GB", "GBP", 10, _N(
        1600, 1600, 1600, 1600, 1600, 1600, 1600, 1600, 1600,
        1600, 1600, 1600, 1600, 1600, 1600, 1600, 1600, 1600, 1600, 1600, 1600,
    )),
    ("Personal expenses", CashflowKind.EXPENSE, "GB", "GBP", 11, _N(
        700, 740, 740, 750, 750, 750, 750, 750, 750,
        850, 850, 850, 850, 850, 850, 850, 850, 850, 850, 850, 850,
    )),
    ("Lloyds Loan 01", CashflowKind.EXPENSE, "GB", "GBP", 12, _N(
        960, 960, 1250, 1250, 1250, 400, 0, 0, 0,
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    )),
    ("Lloyds card", CashflowKind.EXPENSE, "GB", "GBP", 13, _N(
        100, 100, 100, 100, 100, 100, 100, 100, 100,
        None, None, None, None, None, None, None, None, None, None, None, None,
    )),
    ("Barclays card", CashflowKind.EXPENSE, "GB", "GBP", 14, _N(
        200, 200, 200, 200, 200, 200, 200, 200, 150,
        1500, 1500, 1500, 500, None, None, None, None, None, None, None, None,
    )),
    ("Overdraft Lloyds", CashflowKind.EXPENSE, "GB", "GBP", 15, _N(
        1040, None, 200, 200, 200, None, None, None, None,
        None, None, None, None, None, None, None, None, None, None, None, None,
    )),
    # ---- CL / CLP
    ("CAE", CashflowKind.EXPENSE, "CL", "CLP", 20, _N(
        100, 0, 100, 100, 100, 100, 100, 100, 100,
        100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100,
    )),
    ("CMR Falabella", CashflowKind.EXPENSE, "CL", "CLP", 21, _N(
        0, 650, 600, 600, 600, 600, 600, 600, 600,
        450, 450, 450, 450, 450, 450, 450, 450, 450, 450, 450, 450,
    )),
    ("Linea Credito Falabella", CashflowKind.EXPENSE, "CL", "CLP", 22, _N(
        0, 0, 50, 50, 50, 0, None, None, None,
        None, None, None, None, None, None, None, None, None, None, None, None,
    )),
    ("Banco Chile Prestamo", CashflowKind.EXPENSE, "CL", "CLP", 23, _N(
        150, 150, 150, 150, 150, 150, 150, 150, 150,
        150, 150, 150, 150, 150, 150, 150, 150, 150, 150, 150, 150,
    )),
    ("Tarjeta Banco Chile", CashflowKind.EXPENSE, "CL", "CLP", 24, _N(
        100, 100, 150, 150, 150, 150, 150, 150, 150,
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    )),
    ("Linea Credito Banco Chile", CashflowKind.EXPENSE, "CL", "CLP", 25, _N(
        None, 0, 50, 50, 50, 0, 0, 0, 0,
        None, None, None, None, None, None, None, None, None, None, None, None,
    )),
    ("Fondo Solidario", CashflowKind.EXPENSE, "CL", "CLP", 26, _N(
        0, 0, 0, 0, 0, 0, 0, 0, 1600,
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2000,
    )),
    ("Dad residence", CashflowKind.EXPENSE, "CL", "CLP", 27, _N(
        100, 100, 100, 100, 100, 100, 0, 0, 0,
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    )),
]


def _resolve_user(db: Session, email: str) -> User:
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        raise SystemExit(
            f"User {email} not found. Run `make seed` first (or pass --email)."
        )
    return user


def wipe(db: Session, user_id: int) -> int:
    """Remove every CashflowLine (and cascaded entries) for the user. Returns count."""
    lines = list(db.scalars(select(CashflowLine).where(CashflowLine.user_id == user_id)))
    for line in lines:
        db.delete(line)
    db.commit()
    return len(lines)


def seed(db: Session, user_id: int) -> tuple[int, int]:
    """Idempotent: skips if any cashflow line already exists for the user.

    Returns (lines_created, entries_created).
    """
    existing = db.scalar(
        select(CashflowLine).where(CashflowLine.user_id == user_id).limit(1)
    )
    if existing is not None:
        print("Cashflow lines already exist for this user. Pass --reset to recreate.")
        return (0, 0)

    lines_created = 0
    entries_created = 0
    for name, kind, country, currency, sort_order, amounts in LINES:
        line = CashflowLine(
            user_id=user_id,
            name=name,
            kind=kind,
            country=country,
            currency=currency,
            sort_order=sort_order,
        )
        db.add(line)
        db.flush()
        lines_created += 1

        # Pre-2026 months copy the first non-blank value (April 2026) so the
        # calendar year is visually complete.
        april = amounts[0]
        if april is not None:
            for year, month in _PRE_2026_MONTHS:
                db.add(CashflowEntry(
                    user_id=user_id,
                    line_id=line.id,
                    year=year,
                    month=month,
                    amount=april,
                ))
                entries_created += 1

        for (year, month), amount in zip(_CSV_MONTHS, amounts, strict=True):
            if amount is None:
                continue
            db.add(CashflowEntry(
                user_id=user_id,
                line_id=line.id,
                year=year,
                month=month,
                amount=amount,
            ))
            entries_created += 1

    db.commit()
    return (lines_created, entries_created)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed Coffer cashflow demo data")
    parser.add_argument("--email", default=DEMO_EMAIL, help="User to seed (default: demo@coffer.dev)")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Wipe the user's existing cashflow lines first",
    )
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        user = _resolve_user(db, args.email)
        if args.reset:
            removed = wipe(db, user.id)
            print(f"Removed {removed} existing cashflow line(s) for {user.email}")
        lines_created, entries_created = seed(db, user.id)
        if lines_created:
            print(
                f"Seeded {lines_created} cashflow line(s) "
                f"and {entries_created} entries for {user.email}"
            )
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
