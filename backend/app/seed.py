"""Seed demo data.

Creates a demo user with:
- Monthly income of $5,000
- Debts whose minimum payments sum to exactly 40% of income ($2,000)
- Matching accounts, categories, budget entries, sample transactions, and goals

Idempotent: if the demo user already exists, exits without changes unless
invoked with --reset, which wipes that user (and cascades everything they own)
before re-seeding.

Run inside the container:
    docker compose exec backend uv run python -m app.seed
    docker compose exec backend uv run python -m app.seed --reset
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.account import Account, AccountType
from app.models.budget import BudgetEntry
from app.models.category import Category, CategoryKind
from app.models.debt import Debt
from app.models.fx_rate import FxRate
from app.models.goal import Goal
from app.models.transaction import Transaction
from app.models.user import User

DEMO_EMAIL = "demo@coffer.dev"
DEMO_PASSWORD = "demo1234"
DEMO_NAME = "Demo User"

MONTHLY_INCOME = Decimal("5000")
DEBT_BUDGET = MONTHLY_INCOME * Decimal("0.40")  # = 2000, exactly 40%

# (name, account_type, current_balance, original_principal, apr, minimum_payment, due_day)
DEBTS = [
    ("Lloyds Loan 01",        AccountType.LOAN,        Decimal("12000"), Decimal("18000"), Decimal("8.5"),  Decimal("500"), 5),
    ("CMR Falabella",         AccountType.CREDIT_CARD, Decimal("4500"),  Decimal("4500"),  Decimal("38.0"), Decimal("450"), 15),
    ("Banco Chile Prestamo",  AccountType.LOAN,        Decimal("5000"),  Decimal("6000"),  Decimal("12.0"), Decimal("200"), 10),
    ("Tarjeta Banco Chile",   AccountType.CREDIT_CARD, Decimal("1500"),  Decimal("1500"),  Decimal("32.0"), Decimal("150"), 20),
    ("Barclays card",         AccountType.CREDIT_CARD, Decimal("3500"),  Decimal("3500"),  Decimal("22.9"), Decimal("250"), 25),
    ("Lloyds card",           AccountType.CREDIT_CARD, Decimal("2000"),  Decimal("2000"),  Decimal("21.9"), Decimal("150"), 28),
    ("Overdraft Lloyds",      AccountType.OVERDRAFT,   Decimal("1500"),  Decimal("1500"),  Decimal("39.9"), Decimal("300"), 1),
]
# Sum of minimum_payment: 500+450+200+150+250+150+300 = 2000 (40% of 5000)

# Non-debt expenses + saving — total + debts must <= income (here exactly 5000)
OTHER_BUDGET: list[tuple[str, CategoryKind, Decimal]] = [
    ("House",             CategoryKind.EXPENSE, Decimal("1600")),
    ("Personal expenses", CategoryKind.EXPENSE, Decimal("700")),
    ("Saving",            CategoryKind.SAVING,  Decimal("700")),
]
# House 1600 + Personal 700 + Saving 700 + Debts 2000 = 5000 = income

GOALS = [
    ("Emergency fund",  Decimal("10000"), Decimal("2000"), 365),
    ("Debt-free by EOY", Decimal("30000"), Decimal("12000"), 365),
    ("Vacation",        Decimal("3000"),  Decimal("500"),  180),
]


def wipe_user(db: Session) -> None:
    existing = db.scalar(select(User).where(User.email == DEMO_EMAIL))
    if existing is not None:
        db.delete(existing)
        db.commit()
        print(f"Removed existing demo user {DEMO_EMAIL}")


def seed(db: Session) -> None:
    if db.scalar(select(User).where(User.email == DEMO_EMAIL)) is not None:
        print(f"Demo user {DEMO_EMAIL} already exists. Pass --reset to recreate.")
        return

    today = date.today()
    year, month = today.year, today.month
    month_start = today.replace(day=1)

    # ---- User
    user = User(
        email=DEMO_EMAIL,
        full_name=DEMO_NAME,
        hashed_password=hash_password(DEMO_PASSWORD),
        display_currency="GBP",
    )
    db.add(user)
    db.flush()

    # Manual FX: 1 CLP in GBP (demo rate — the user maintains this).
    db.add(FxRate(user_id=user.id, currency="CLP", rate=Decimal("0.00082"), as_of=today))

    # ---- Operating accounts
    checking = Account(
        user_id=user.id,
        name="Lloyds Checking",
        type=AccountType.CHECKING,
        institution="Lloyds",
        currency="GBP",
        opening_balance=Decimal("1200"),
    )
    savings = Account(
        user_id=user.id,
        name="Lloyds Savings",
        type=AccountType.SAVINGS,
        institution="Lloyds",
        currency="GBP",
        opening_balance=Decimal("2000"),
    )
    db.add_all([checking, savings])
    db.flush()

    # ---- Debt accounts + Debt records + debt-payment categories
    income_cat = Category(user_id=user.id, name="Hurdle", kind=CategoryKind.INCOME, color="#22c55e")
    db.add(income_cat)

    for name, kind, planned in OTHER_BUDGET:
        cat = Category(user_id=user.id, name=name, kind=kind)
        db.add(cat)
        db.flush()
        db.add(BudgetEntry(
            user_id=user.id, category_id=cat.id, year=year, month=month, planned_amount=planned,
        ))

    db.flush()
    db.add(BudgetEntry(
        user_id=user.id, category_id=income_cat.id, year=year, month=month, planned_amount=MONTHLY_INCOME,
    ))

    debt_accounts: dict[str, Account] = {}
    for dname, dtype, balance, principal, apr, min_pay, due in DEBTS:
        acct = Account(
            user_id=user.id,
            name=dname,
            type=dtype,
            institution=dname.split()[0],
            # Chilean debts are CLP; UK ones GBP — a genuinely dual-currency household.
            currency="CLP" if dname.split()[0] in {"CMR", "Banco", "Tarjeta"} else "GBP",
            opening_balance=-balance,
        )
        db.add(acct)
        db.flush()
        debt_accounts[dname] = acct

        db.add(Debt(
            user_id=user.id,
            account_id=acct.id,
            name=dname,
            original_principal=principal,
            current_balance=balance,
            interest_rate_apr=apr,
            minimum_payment=min_pay,
            due_day_of_month=due,
        ))

        cat = Category(user_id=user.id, name=dname, kind=CategoryKind.DEBT_PAYMENT)
        db.add(cat)
        db.flush()
        db.add(BudgetEntry(
            user_id=user.id, category_id=cat.id, year=year, month=month, planned_amount=min_pay,
        ))

        # Sample transaction: this month's payment, already made
        payment_day = min(due, 28)
        try:
            payment_date = month_start.replace(day=payment_day)
        except ValueError:
            payment_date = month_start
        if payment_date > today:
            payment_date = today
        db.add(Transaction(
            user_id=user.id,
            account_id=checking.id,
            category_id=cat.id,
            posted_on=payment_date,
            description=f"Payment to {dname}",
            amount=-min_pay,
        ))

    # ---- Salary on the 1st of the month
    db.add(Transaction(
        user_id=user.id,
        account_id=checking.id,
        category_id=income_cat.id,
        posted_on=month_start,
        description="Salary deposit",
        amount=MONTHLY_INCOME,
    ))

    # ---- A few non-debt expense transactions
    cats_by_name = {c.name: c for c in db.scalars(select(Category).where(Category.user_id == user.id))}
    sample_expenses = [
        ("House",             "Rent",                Decimal("1600"), 2),
        ("Personal expenses", "Groceries",           Decimal("180"),  3),
        ("Personal expenses", "Restaurants",         Decimal("85"),   6),
        ("Personal expenses", "Phone",               Decimal("45"),   8),
        ("Personal expenses", "Transport",           Decimal("60"),   10),
        ("Saving",            "Auto-transfer to savings", Decimal("400"), 5),
    ]
    for cat_name, desc, amount, day in sample_expenses:
        cat = cats_by_name.get(cat_name)
        if cat is None:
            continue
        try:
            txn_date = month_start.replace(day=min(day, 28))
        except ValueError:
            txn_date = month_start
        if txn_date > today:
            txn_date = today - timedelta(days=1)
        sign = Decimal("1") if cat.kind == CategoryKind.SAVING else Decimal("-1")
        db.add(Transaction(
            user_id=user.id,
            account_id=checking.id if cat.kind != CategoryKind.SAVING else savings.id,
            category_id=cat.id,
            posted_on=txn_date,
            description=desc,
            amount=amount * sign,
        ))

    # ---- Goals
    for gname, target, current, days_out in GOALS:
        db.add(Goal(
            user_id=user.id,
            name=gname,
            target_amount=target,
            current_amount=current,
            target_date=today + timedelta(days=days_out),
        ))

    db.commit()

    total_min = sum((d[5] for d in DEBTS), start=Decimal("0"))
    print(f"Seeded demo user: {DEMO_EMAIL} / {DEMO_PASSWORD}")
    print(f"Monthly income:      ${MONTHLY_INCOME}")
    print(f"Debt min-payments:   ${total_min}  ({total_min / MONTHLY_INCOME:.0%} of income)")
    print(f"Other expenses:      ${sum((b[2] for b in OTHER_BUDGET if b[1] == CategoryKind.EXPENSE), start=Decimal('0'))}")
    print(f"Planned savings:     ${sum((b[2] for b in OTHER_BUDGET if b[1] == CategoryKind.SAVING), start=Decimal('0'))}")
    print(f"Goals seeded:        {len(GOALS)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed Coffer demo data")
    parser.add_argument("--reset", action="store_true", help="Delete the demo user first")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        if args.reset:
            wipe_user(db)
        seed(db)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
