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
import ssl
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.debt import Debt
from app.models.user import User
from app.services.account_loader import load_forecast_scope, load_fx_rates, load_txn_lites
from app.services.analytics.debt_plan import DebtInput
from app.services.analytics.forecast import project
from app.services.analytics.fx import convert_optional
from app.services.analytics.net_worth import current_balance
from app.services.analytics.recurring import detect_raises, detect_recurring
from app.services.analytics.surplus import latest_complete_month, rank_allocations, summarize_month
from app.services.archive import read_meta

STALE_AFTER_DAYS = 35
PROMO_WARNING_DAYS = 60
BILL_LOOKAHEAD_DAYS = 7
RENEWAL_LOOKAHEAD_DAYS = 14
MAX_BILL_LINES = 8


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

    # One data load feeds freshness, the accounts list, and the forecast start.
    # The projection shares the forecast endpoint's single-currency scope so
    # the emailed warning can never disagree with the in-app forecast.
    account_data, display, in_display, _excluded = load_forecast_scope(db, user)
    included_ids = {a.id for a in in_display}
    # Foreign-currency debt amounts convert at the user's saved rates before
    # they enter display-currency text (exclude-and-flag when no rate exists).
    fx_rates = load_fx_rates(db, user.id)

    # --- Data freshness -------------------------------------------------------
    # Fresh = a recent transaction OR a recent balance snapshot: valuation-only
    # accounts (house, pension) are maintained by snapshots, not statements.
    stale = []
    for a in account_data:
        last_txn = a.txns[-1][0] if a.txns else None
        last_snap = a.snapshots[-1][0] if a.snapshots else None
        last = max(d for d in (last_txn, last_snap) if d is not None) if (last_txn or last_snap) else None
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
            f"  - {d.name}: promo rate ends {d.promo_ends_on} {_owed_text(d, display, fx_rates)}"
            + (f", reverting to {d.interest_rate_apr}%" if d.interest_rate_apr else "")
            for d in cliffs
        ]
        sections.append("PROMO RATE CLIFFS — clear these before the rate reverts:\n" + "\n".join(lines))

    # --- Upcoming bills + renewals -------------------------------------------
    txns = load_txn_lites(db, user.id, account_ids=included_ids)
    items = detect_recurring(txns, today=today)
    active = [i for i in items if i.active]
    bills = [
        i for i in active if not i.is_income and today <= i.next_expected <= today + timedelta(days=BILL_LOOKAHEAD_DAYS)
    ]
    # Renewals due within the bill window are already listed above — don't
    # count one charge as two things.
    renewals = [
        i
        for i in active
        if not i.is_income
        and i.cadence in {"quarterly", "annual"}
        and today + timedelta(days=BILL_LOOKAHEAD_DAYS)
        < i.next_expected
        <= today + timedelta(days=RENEWAL_LOOKAHEAD_DAYS)
    ]
    if bills:
        total = sum((-i.typical_amount for i in bills), Decimal("0"))
        lines = [f"  - {i.next_expected}: {i.description} ({_fmt(-i.typical_amount)})" for i in bills[:MAX_BILL_LINES]]
        if len(bills) > MAX_BILL_LINES:
            hidden = sum((-i.typical_amount for i in bills[MAX_BILL_LINES:]), Decimal("0"))
            lines.append(f"  - ... and {len(bills) - MAX_BILL_LINES} more totalling {_fmt(hidden)}")
        sections.append(f"NEXT {BILL_LOOKAHEAD_DAYS} DAYS — {_fmt(total)} of known bills:\n" + "\n".join(lines))
    if renewals:
        lines = [
            f"  - {i.next_expected}: {i.description} renews ({_fmt(-i.typical_amount)}, {i.cadence}) — still worth it?"
            for i in renewals
        ]
        sections.append("RENEWALS COMING — cancel before they charge:\n" + "\n".join(lines))

    # --- Projected low balance ------------------------------------------------
    start_balance = sum((current_balance(a).balance for a in in_display), Decimal("0"))
    forecast = project(start_balance, items, days=30, today=today)
    if forecast.first_below_zero is not None:
        sections.append(
            f"LOW BALANCE WARNING — projected to go below zero on {forecast.first_below_zero} "
            f"(low point {_fmt(forecast.min_balance)} on {forecast.min_balance_date})."
        )

    # --- Backup health ---------------------------------------------------------
    # Only nag when backups are actually in a bad state: the drill failed, or
    # archives exist but have never been verified. A fresh instance with no
    # backups at all stays quiet here (the status endpoint covers discovery).
    meta = read_meta(Path(settings.backup_dir))
    if meta.get("last_verify_ok") is False:
        sections.append(
            "BACKUP PROBLEM — the last restore drill FAILED. Run `python -m app.backup verify` and check the archive."
        )
    elif meta.get("last_created") and not meta.get("last_verified"):
        sections.append(
            "BACKUPS UNVERIFIED — archives exist but no restore drill has run. "
            "`python -m app.backup verify` proves your backup restores."
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
        picked = latest_complete_month([t.posted_on for t in txns], today)
        if picked is not None:
            year, month = picked
            summary = summarize_month(txn_tuples, year, month)
            if summary.surplus > 0:
                # Debt-first for the emailed action; the full ranked list
                # (including goals and runway) lives on the Dashboard.
                options = rank_allocations(
                    summary.surplus,
                    [DebtInput.from_model(d) for d in debts if d.current_balance > 0],
                    [],
                    None,
                    today=today,
                    display_currency=display,
                    rates=fx_rates,
                )
                if options:
                    o = options[0]
                    action = (
                        f"Last month closed with {_fmt(summary.surplus)} spare. "
                        f"Highest-interest debt: {o.name} — {o.note}. "
                        "(The Dashboard also ranks your goals and emergency runway.)"
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


def _owed_text(d: Debt, display: str | None, rates: dict[str, Decimal]) -> str:
    """The 'X owed' fragment for a debt line, converting foreign-currency
    balances to the display currency — a missing rate excludes the figure and
    flags why, never mixing a native magnitude into display-currency text."""
    converted = convert_optional(d.current_balance, d.currency, display, rates)
    if converted is None:
        return f"— balance held in {d.currency} (no FX rate saved)"
    if converted is d.current_balance:  # passthrough — already display-denominated
        return f"with {_fmt(converted)} owed"
    return f"with {_fmt(converted)} owed (converted from {d.currency})"


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
            # Explicit context: smtplib's default skips certificate verification,
            # which would hand credentials to any on-path attacker.
            smtp.starttls(context=ssl.create_default_context())
        if settings.smtp_username and settings.smtp_password:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(msg)
