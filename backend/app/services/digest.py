"""The weekly wire: one plain-text email that keeps working when the app is
never opened.

Deadline-driven insights (promo-APR cliffs, renewals, low-balance days) only
save money if seen before the deadline — a pull-only dashboard structurally
can't deliver them. The digest compresses each feature to a line or two:
data freshness, promo cliffs, upcoming bills, projected low balance, and
exactly one suggested action.

Sending is optional (SMTP settings); composition works regardless, so the
same body backs the in-app preview endpoint.
"""

from __future__ import annotations

import smtplib
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from email.message import EmailMessage

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.account import Account
from app.models.debt import Debt
from app.models.transaction import Transaction
from app.models.user import User
from app.services.account_loader import load_account_data, load_txn_lites
from app.services.analytics.debt_plan import DebtInput
from app.services.analytics.forecast import project
from app.services.analytics.net_worth import current_balance
from app.services.analytics.recurring import detect_raises, detect_recurring
from app.services.analytics.surplus import rank_allocations, summarize_month

STALE_AFTER_DAYS = 35
PROMO_WARNING_DAYS = 60
BILL_LOOKAHEAD_DAYS = 7
RENEWAL_LOOKAHEAD_DAYS = 14
LIQUID_TYPES = {"checking", "savings", "cash"}


@dataclass
class Digest:
    subject: str
    body: str
    item_count: int


def _fmt(amount: Decimal) -> str:
    return f"{amount:,.2f}"


def compose_digest(db: Session, user: User, today: date | None = None) -> Digest:
    today = today or date.today()
    sections: list[str] = []

    # --- Data freshness -------------------------------------------------------
    last_txn_by_account = dict(
        db.execute(
            select(Transaction.account_id, func.max(Transaction.posted_on))
            .where(Transaction.user_id == user.id)
            .group_by(Transaction.account_id)
        ).all()
    )
    accounts = list(db.scalars(select(Account).where(Account.user_id == user.id)))
    stale = []
    for a in accounts:
        last = last_txn_by_account.get(a.id)
        if last is None or (today - last).days > STALE_AFTER_DAYS:
            stale.append(f"  - {a.name}: {'no data yet' if last is None else f'nothing since {last}'}")
    if stale:
        sections.append("STATEMENTS NEEDED — every number below is only as fresh as its data:\n" + "\n".join(stale))

    # --- Promo-APR cliffs -----------------------------------------------------
    debts = list(db.scalars(select(Debt).where(Debt.user_id == user.id)))
    cliffs = [
        d
        for d in debts
        if d.promo_ends_on is not None
        and d.current_balance > 0
        and today <= d.promo_ends_on <= today + timedelta(days=PROMO_WARNING_DAYS)
    ]
    if cliffs:
        lines = [
            f"  - {d.name}: promo rate ends {d.promo_ends_on} with {_fmt(d.current_balance)} owed"
            + (f", reverting to {d.interest_rate_apr}%" if d.interest_rate_apr else "")
            for d in cliffs
        ]
        sections.append("PROMO RATE CLIFFS — clear these before the rate reverts:\n" + "\n".join(lines))

    # --- Upcoming bills + renewals -------------------------------------------
    txns = load_txn_lites(db, user.id)
    items = detect_recurring(txns, today=today)
    active = [i for i in items if i.active]
    bills = [
        i for i in active if not i.is_income and today <= i.next_expected <= today + timedelta(days=BILL_LOOKAHEAD_DAYS)
    ]
    renewals = [
        i
        for i in active
        if not i.is_income
        and i.cadence in {"quarterly", "annual"}
        and today <= i.next_expected <= today + timedelta(days=RENEWAL_LOOKAHEAD_DAYS)
    ]
    if bills:
        total = sum((-i.typical_amount for i in bills), Decimal("0"))
        lines = [f"  - {i.next_expected}: {i.description} ({_fmt(-i.typical_amount)})" for i in bills[:8]]
        sections.append(f"NEXT {BILL_LOOKAHEAD_DAYS} DAYS — {_fmt(total)} of known bills:\n" + "\n".join(lines))
    if renewals:
        lines = [
            f"  - {i.next_expected}: {i.description} renews ({_fmt(-i.typical_amount)}, {i.cadence}) — still worth it?"
            for i in renewals
        ]
        sections.append("RENEWALS COMING — cancel before they charge:\n" + "\n".join(lines))

    # --- Projected low balance ------------------------------------------------
    account_data = load_account_data(db, user.id)
    start_balance = sum(
        (current_balance(a).balance for a in account_data if a.type.value in LIQUID_TYPES),
        Decimal("0"),
    )
    forecast = project(start_balance, items, days=30, today=today)
    if forecast.first_below_zero is not None:
        sections.append(
            f"LOW BALANCE WARNING — projected to go below zero on {forecast.first_below_zero} "
            f"(low point {_fmt(forecast.min_balance)} on {forecast.min_balance_date})."
        )

    # --- One suggested action -------------------------------------------------
    raises = detect_raises(items)
    action: str | None = None
    if raises:
        r = raises[0]
        half = (r.monthly_delta / 2).quantize(Decimal("0.01"))
        action = (
            f"Pay rise detected ({r.description}: +{_fmt(r.monthly_delta)}/month). "
            f"Commit half of it — {_fmt(half)}/month — to a debt or goal before it becomes lifestyle."
        )
    else:
        txn_tuples = [(t.posted_on, t.amount, t.category_id) for t in txns]
        months = sorted({(t.posted_on.year, t.posted_on.month) for t in txns})
        complete = [m for m in months if m < (today.year, today.month)]
        if complete:
            year, month = complete[-1]
            summary = summarize_month(txn_tuples, year, month)
            if summary.surplus > 0:
                # Debt-first for the emailed action; the full ranked list
                # (including goals and runway) lives on the Dashboard.
                options = rank_allocations(
                    summary.surplus,
                    [_debt_input(d) for d in debts if d.current_balance > 0],
                    [],
                    None,
                    today=today,
                )
                if options:
                    o = options[0]
                    action = (
                        f"Last month closed with {_fmt(summary.surplus)} spare. Best destination: {o.name} — {o.note}."
                    )
                else:
                    action = (
                        f"Last month closed with {_fmt(summary.surplus)} spare — put it to work before it evaporates."
                    )
    if action:
        sections.append("ONE THING TO DO THIS WEEK:\n  " + action)

    if not sections:
        body = "All quiet — nothing needs your attention this week. Keep importing statements."
    else:
        body = "\n\n".join(sections)
    body += "\n\n— Coffer"

    count = len(sections)
    subject = (
        f"Coffer weekly: {count} thing{'s' if count != 1 else ''} to look at" if count else "Coffer weekly: all quiet"
    )
    return Digest(subject=subject, body=body, item_count=count)


def _debt_input(d: Debt) -> DebtInput:
    return DebtInput(
        id=d.id,
        name=d.name,
        balance=d.current_balance,
        apr=d.interest_rate_apr,
        promo_apr=d.promo_apr,
        promo_ends_on=d.promo_ends_on,
        minimum_payment=d.minimum_payment,
    )


def send_email(to: str, subject: str, body: str) -> None:
    """Send via configured SMTP. Raises RuntimeError when SMTP is unset."""
    if not settings.smtp_configured:
        raise RuntimeError("SMTP is not configured (set SMTP_HOST in .env)")
    msg = EmailMessage()
    msg["From"] = settings.smtp_from or settings.smtp_username or "coffer@localhost"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_username and settings.smtp_password:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(msg)
