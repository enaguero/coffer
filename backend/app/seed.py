"""Seed demo data.

Creates a demo user with:
- Monthly income of $5,000
- A four-debt portfolio covering every repayment mechanic across two
  currencies: a revolving credit card (GBP, 0% promo window), an amortized
  personal loan (GBP), a flat-interest loan (CLP — an unlinked register debt,
  converted in net worth via the seeded FX rate), and a statement-only loan
  (GBP — no APR, the engine infers it)
- Converted monthly debt commitments summing to exactly 40% of income:
  installment for the three fixed types, minimum payment for the card, the
  CLP installment counted at the seeded CLP→GBP rate
- Three months of history (current month + two prior): salary, expenses, and
  per-linked-debt payments, plus month-end BalanceSnapshots for every account,
  all mutually consistent (snapshots are computed from the seeded ledger)
- Matching accounts, categories, budget entries, and goals

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
from app.models.balance_snapshot import BalanceSnapshot, BalanceSource
from app.models.budget import BudgetEntry
from app.models.category import Category, CategoryKind
from app.models.debt import Debt, DebtRepaymentType
from app.models.fx_rate import FxRate
from app.models.goal import Goal
from app.models.transaction import Transaction
from app.models.user import User
from app.services.analytics.debt_plan import add_months

DEMO_EMAIL = "demo@coffer.dev"
DEMO_PASSWORD = "demo1234"
DEMO_NAME = "Demo User"

MONTHLY_INCOME = Decimal("5000")
DEBT_BUDGET = MONTHLY_INCOME * Decimal("0.40")  # = 2000, exactly 40%

# Manual FX: 1 CLP in GBP (demo rate — the user maintains this). Chosen so the
# CLP installment converts to exact pennies: 250,000.00 × 0.00082 = 205.00.
CLP_TO_GBP = Decimal("0.00082")

# Months of seeded history: the current month plus two prior.
HISTORY_MONTHS = 3

_RV = DebtRepaymentType.REVOLVING
_AM = DebtRepaymentType.AMORTIZED
_FL = DebtRepaymentType.FLAT
_SO = DebtRepaymentType.STATEMENT_ONLY
_CC = AccountType.CREDIT_CARD
_LN = AccountType.LOAN

# One debt per repayment mechanic. acct_type None = unlinked register debt;
# ccy None = the display currency (GBP) by register convention. Money cells are
# strings (fed through Decimal at use) to keep the table narrow.
# (name,                    rtype, acct_type, ccy,   balance,      principal,    apr,    min_pay,  installment,  due, ends_in_months, principal_per_month)
DEBTS = [
    ("Barclays Card",        _RV,  _CC,       None,  "3500.00",    "4850.00",    "22.9", "450.00", None,         15,  None, "450.00"),
    ("Lloyds Personal Loan", _AM,  _LN,       None,  "19500.00",   "22000.00",   "8.9",  None,     "620.00",     5,   36,   "475.00"),
    ("Coopeuch Consumo",     _FL,  None,      "CLP", "4500000.00", "6000000.00", "18.0", None,     "250000.00",  10,  24,   None),
    ("Zopa Car Loan",        _SO,  _LN,       None,  "8400.00",    None,         None,   None,     "725.00",     25,  12,   "700.00"),
]
# The 40% invariant, in converted (display, GBP) terms — installment for the
# three fixed types, minimum_payment for the revolving card:
#   Barclays Card          minimum         450.00 GBP
#   Lloyds Personal Loan   installment     620.00 GBP
#   Coopeuch Consumo       installment 250,000.00 CLP × 0.00082 = 205.00 GBP
#   Zopa Car Loan          installment     725.00 GBP
#   total: 450 + 620 + 205 + 725 = 2000.00 GBP = 40% of the 5000 salary
#
# Three months of payments reconcile each linked debt's current_balance via a
# fixed principal split per month (a narrative, not an amortization engine):
#   Barclays Card         4850.00 − 3 × 450.00 (0% promo → all principal)      = 3500.00
#   Lloyds Personal Loan 20925.00 − 3 × 475.00 (620.00 pay − 145.00 interest)  = 19500.00
#   Zopa Car Loan        10500.00 − 3 × 700.00 (725.00 pay − 25.00 implied)    = 8400.00
#   Coopeuch Consumo: unlinked flat loan — balance moves only on capital
#   payments, none seeded here; the register states 4,500,000.00 CLP as-is.
# Linked debt accounts therefore open at −(current_balance + 3 × principal/mo).

# The card sits inside a 0% balance-transfer window (all-principal payments in
# the seeded history) that reverts to its 22.9% APR when the promo lapses.
CARD_PROMO_APR = Decimal("0")
CARD_PROMO_MONTHS_AHEAD = 4

# Non-debt expenses + saving — total + debts must <= income (here exactly 5000)
OTHER_BUDGET: list[tuple[str, CategoryKind, Decimal]] = [
    ("House",             CategoryKind.EXPENSE, Decimal("1600")),
    ("Personal expenses", CategoryKind.EXPENSE, Decimal("700")),
    ("Saving",            CategoryKind.SAVING,  Decimal("700")),
]
# House 1600 + Personal 700 + Saving 700 + Debts 2000 = 5000 = income

# (category name, description, amount, day of month) — repeated each history month.
SAMPLE_EXPENSES = [
    ("House",             "Rent",                     Decimal("1600"), 2),
    ("Personal expenses", "Groceries",                Decimal("180"),  3),
    ("Personal expenses", "Restaurants",              Decimal("85"),   6),
    ("Personal expenses", "Phone",                    Decimal("45"),   8),
    ("Personal expenses", "Transport",                Decimal("60"),   10),
    ("Saving",            "Auto-transfer to savings", Decimal("400"),  5),
]

GOALS = [
    ("Emergency fund",  Decimal("10000"), Decimal("2000"), 365),
    ("Debt-free by EOY", Decimal("30000"), Decimal("12000"), 365),
    ("Vacation",        Decimal("3000"),  Decimal("500"),  180),
]


def _dec(value: str | None) -> Decimal | None:
    return Decimal(value) if value is not None else None


def _clamped(month_start: date, day: int, today: date) -> date:
    """Day-of-month clamped to ≤28 and never in the future (this month's
    not-yet-due items post today, 'already made' — the original convention)."""
    return min(month_start.replace(day=min(day, 28)), today)


def _display_commitment(rtype: DebtRepaymentType, ccy: str | None, min_pay: Decimal | None,
                        installment: Decimal | None) -> Decimal:
    """A debt's monthly commitment in display (GBP) terms: minimum_payment
    for revolving, installment otherwise; CLP converted at the seeded rate."""
    amount = min_pay if rtype is DebtRepaymentType.REVOLVING else installment
    assert amount is not None
    if ccy == "CLP":
        amount = (amount * CLP_TO_GBP).quantize(Decimal("0.01"))
    return amount


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
    month_start = today.replace(day=1)
    # add_months clamps the day to the target month's length — a no-op for the
    # day-1 dates seeded here.
    months = [add_months(month_start, offset) for offset in range(1 - HISTORY_MONTHS, 1)]
    # Month-end snapshot dates: the day before each seeded month begins — the
    # opening position plus the close of both fully-seeded months. All in the
    # past (the current month's own end hasn't happened yet).
    boundaries = [m - timedelta(days=1) for m in months]

    # ---- User
    user = User(
        email=DEMO_EMAIL,
        full_name=DEMO_NAME,
        hashed_password=hash_password(DEMO_PASSWORD),
        display_currency="GBP",
    )
    db.add(user)
    db.flush()

    db.add(FxRate(user_id=user.id, currency="CLP", rate=CLP_TO_GBP, as_of=today))

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
    # A genuinely foreign-currency account: 1,000,000 CLP ≈ £820 at the demo
    # rate. It exercises conversion on Net Worth and exclusion on the
    # (single-currency, GBP) forecast.
    clp_savings = Account(
        user_id=user.id,
        name="Banco de Chile Ahorro",
        type=AccountType.SAVINGS,
        institution="Banco de Chile",
        currency="CLP",
        opening_balance=Decimal("1000000"),
    )
    db.add_all([checking, savings, clp_savings])
    db.flush()
    snapshot_accounts: list[Account] = [checking, savings, clp_savings]

    # ---- Ledger bookkeeping: every seeded transaction is logged so month-end
    # snapshots can be computed from the same rows and never drift.
    txn_log: dict[int, list[tuple[date, Decimal]]] = {}

    def add_txn(account: Account, posted_on: date, description: str, amount: Decimal,
                category: Category | None = None) -> None:
        db.add(Transaction(
            user_id=user.id,
            account_id=account.id,
            category_id=category.id if category is not None else None,
            posted_on=posted_on,
            description=description,
            amount=amount,
        ))
        txn_log.setdefault(account.id, []).append((posted_on, amount))

    # ---- Categories + budget lines (entries created for each history month)
    budget_lines: list[tuple[Category, Decimal]] = []

    income_cat = Category(user_id=user.id, name="Hurdle", kind=CategoryKind.INCOME, color="#22c55e")
    db.add(income_cat)
    db.flush()
    budget_lines.append((income_cat, MONTHLY_INCOME))

    expense_cats: dict[str, Category] = {}
    for name, kind, planned in OTHER_BUDGET:
        cat = Category(user_id=user.id, name=name, kind=kind)
        db.add(cat)
        db.flush()
        expense_cats[name] = cat
        budget_lines.append((cat, planned))

    # ---- Debt accounts + Debt records + debt-payment categories
    # (name, account, category, monthly payment, due day, principal per month)
    payment_plan: list[tuple[str, Account, Category, Decimal, int, Decimal]] = []
    total_commit = Decimal("0")  # accumulated per debt — the 40% invariant printed at the end
    for dname, rtype, acct_type, ccy, balance_s, principal_s, apr_s, min_pay_s, installment_s, due, ends_in, m_principal_s in DEBTS:
        balance, principal = Decimal(balance_s), _dec(principal_s)
        min_pay, installment, m_principal = _dec(min_pay_s), _dec(installment_s), _dec(m_principal_s)

        acct: Account | None = None
        if acct_type is not None:
            # Linked debt accounts stay GBP: balances, payments from GBP
            # checking, and budget lines form one display-currency unit. The
            # foreign-currency mechanics live on the unlinked CLP flat loan —
            # register debts carry their own `currency` column now.
            acct = Account(
                user_id=user.id,
                name=dname,
                type=acct_type,
                institution=dname.split()[0],
                currency="GBP",
                # Opens where the balance stood before the seeded payments (see
                # the reconciliation table above) so ledger, snapshots, and the
                # debt register's current_balance all agree.
                opening_balance=-(balance + HISTORY_MONTHS * m_principal),
            )
            db.add(acct)
            db.flush()
            snapshot_accounts.append(acct)

        db.add(Debt(
            user_id=user.id,
            account_id=acct.id if acct is not None else None,
            name=dname,
            original_principal=principal if principal is not None else Decimal("0"),
            current_balance=balance,
            interest_rate_apr=Decimal(apr_s) if apr_s is not None else None,
            promo_apr=CARD_PROMO_APR if rtype is DebtRepaymentType.REVOLVING else None,
            promo_ends_on=(
                add_months(month_start, CARD_PROMO_MONTHS_AHEAD) - timedelta(days=1)
                if rtype is DebtRepaymentType.REVOLVING
                else None
            ),
            minimum_payment=min_pay,
            repayment_type=rtype,
            currency=ccy,
            installment_amount=installment,
            due_day_of_month=due,
            ends_on=add_months(month_start, ends_in) if ends_in is not None else None,
        ))

        # Budget lines are display-denominated by convention — the CLP loan's
        # entry carries its converted 205.00 so the debt budget still reads
        # 2000 even though no GBP transaction pays it.
        cat = Category(user_id=user.id, name=dname, kind=CategoryKind.DEBT_PAYMENT)
        db.add(cat)
        db.flush()
        commitment = _display_commitment(rtype, ccy, min_pay, installment)
        budget_lines.append((cat, commitment))
        total_commit += commitment

        if acct is not None:
            payment_plan.append((dname, acct, cat, min_pay if min_pay is not None else installment, due, m_principal))

    for cat, planned in budget_lines:
        for m_start in months:
            db.add(BudgetEntry(
                user_id=user.id, category_id=cat.id, year=m_start.year, month=m_start.month, planned_amount=planned,
            ))

    # ---- Three months of history: salary, expenses, and debt payments
    for m_start in months:
        add_txn(checking, m_start, "Salary deposit", MONTHLY_INCOME, income_cat)

        for cat_name, desc, amount, day in SAMPLE_EXPENSES:
            cat = expense_cats[cat_name]
            posted = _clamped(m_start, day, today)
            if cat.kind == CategoryKind.SAVING:
                add_txn(savings, posted, desc, amount, cat)
            else:
                add_txn(checking, posted, desc, -amount, cat)

        for dname, acct, cat, payment, due, m_principal in payment_plan:
            posted = _clamped(m_start, due, today)
            add_txn(checking, posted, f"Payment to {dname}", -payment, cat)
            # Mirror the payment on the debt account's own ledger (credit +
            # interest charge, netting to the fixed principal split) so its
            # balance history is real. Uncategorized — budget actuals count
            # only the checking-side payment.
            add_txn(acct, posted, "Payment received - thank you", payment)
            interest = payment - m_principal
            if interest > 0:
                add_txn(acct, posted, "Interest charged", -interest)

    # ---- Month-end balance snapshots, computed from the seeded ledger
    snap_count = 0
    for acct in snapshot_accounts:
        for boundary in boundaries:
            balance_at = acct.opening_balance + sum(
                (amount for posted, amount in txn_log.get(acct.id, []) if posted <= boundary), start=Decimal("0"),
            )
            db.add(BalanceSnapshot(
                user_id=user.id,
                account_id=acct.id,
                as_of=boundary,
                balance=balance_at,
                source=BalanceSource.MANUAL,
            ))
            snap_count += 1

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

    print(f"Seeded demo user: {DEMO_EMAIL} / {DEMO_PASSWORD}")
    print(f"Monthly income:      ${MONTHLY_INCOME}")
    print(f"Debt commitments:    ${total_commit}  ({total_commit / MONTHLY_INCOME:.0%} of income; CLP at {CLP_TO_GBP})")
    print(f"Debts:               {len(DEBTS)} (revolving / amortized / flat CLP / statement-only)")
    print(f"History:             {len(months)} months (salary, expenses, debt payments)")
    print(f"Balance snapshots:   {snap_count} across {len(snapshot_accounts)} accounts")
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
