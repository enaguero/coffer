"""Assembles AccountData (accounts + transactions + snapshots) for analytics.

The one sanctioned place where account balance inputs are fetched — analytics
modules stay pure, and API routers share this instead of reaching into each
other. Pass `account_ids` to scope the queries to just the accounts you need;
omitting it loads the user's full portfolio (net worth needs that, a single
goal does not).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.account import LIQUID_ACCOUNT_TYPES, Account
from app.models.balance_snapshot import BalanceSnapshot
from app.models.transaction import Transaction
from app.models.user import User
from app.services.analytics.fx import most_common_currency
from app.services.analytics.net_worth import AccountData
from app.services.analytics.recurring import TxnLite


def load_txn_lites(db: Session, user_id: int, account_ids: Iterable[int] | None = None) -> list[TxnLite]:
    """Every transaction as the lightweight shape recurring-detection wants.
    Pass `account_ids` to filter in SQL instead of discarding rows in Python."""
    query = (
        select(
            Transaction.account_id,
            Transaction.posted_on,
            Transaction.description,
            Transaction.amount,
            Transaction.category_id,
        )
        .where(Transaction.user_id == user_id)
        .order_by(Transaction.posted_on)
    )
    if account_ids is not None:
        ids = set(account_ids)
        if not ids:
            return []
        query = query.where(Transaction.account_id.in_(ids))
    return [TxnLite(*row) for row in db.execute(query).all()]


def resolve_display_currency(user: User, accounts: list[AccountData]) -> str | None:
    """The user's setting, else the most common currency among liquid accounts
    (they carry the cashflow), else among all accounts, else None. Liquid-first
    keeps a portfolio dominated by foreign debt/valuation accounts from electing
    a currency the forecast has no liquid accounts in."""
    if user.display_currency:
        return user.display_currency.upper()
    liquid = [a.currency for a in accounts if a.type in LIQUID_ACCOUNT_TYPES]
    return most_common_currency(liquid) or most_common_currency([a.currency for a in accounts])


def load_forecast_scope(
    db: Session, user: User
) -> tuple[list[AccountData], str | None, list[AccountData], list[str]]:
    """The single-currency forecast's inputs, shared by /insights/forecast and
    the weekly digest so the two projections can never disagree: (all accounts,
    display currency, the liquid display-currency accounts that feed the
    projection, the liquid currencies excluded from it)."""
    accounts = load_account_data(db, user.id)
    display = resolve_display_currency(user, accounts)
    liquid = [a for a in accounts if a.type in LIQUID_ACCOUNT_TYPES]
    in_display = [a for a in liquid if display is None or a.currency == display]
    excluded = sorted({a.currency for a in liquid if display is not None and a.currency != display})
    return accounts, display, in_display, excluded


def sum_positive_inflows(
    db: Session,
    user_id: int,
    account_ids: Iterable[int],
    start: date,
    end: date,
    exclude_description_like: str | None = None,
) -> dict[int, Decimal]:
    """Per-account sum of positive transactions in [start, end], zero-prefilled
    so 'no inflows' reads as £0 rather than unknown. Shared by goal funding
    (month-to-date) and allowance metering (tax year)."""
    ids = set(account_ids)
    if not ids:
        return {}
    totals: dict[int, Decimal] = {account_id: Decimal("0") for account_id in ids}
    query = (
        select(Transaction.account_id, func.sum(Transaction.amount))
        .where(
            Transaction.user_id == user_id,
            Transaction.account_id.in_(ids),
            Transaction.amount > 0,
            Transaction.posted_on >= start,
            Transaction.posted_on <= end,
        )
        .group_by(Transaction.account_id)
    )
    if exclude_description_like:
        query = query.where(~Transaction.description.ilike(exclude_description_like))
    totals.update(dict(db.execute(query).all()))
    return totals


def load_account_data(db: Session, user_id: int, account_ids: Iterable[int] | None = None) -> list[AccountData]:
    ids = set(account_ids) if account_ids is not None else None
    if ids is not None and not ids:
        return []

    account_q = select(Account).where(Account.user_id == user_id)
    txn_q = (
        select(Transaction.account_id, Transaction.posted_on, Transaction.amount)
        .where(Transaction.user_id == user_id)
        .order_by(Transaction.posted_on)
    )
    snap_q = (
        select(
            BalanceSnapshot.account_id,
            BalanceSnapshot.as_of,
            BalanceSnapshot.balance,
            BalanceSnapshot.source,
        )
        .where(BalanceSnapshot.user_id == user_id)
        .order_by(BalanceSnapshot.as_of)
    )
    if ids is not None:
        account_q = account_q.where(Account.id.in_(ids))
        txn_q = txn_q.where(Transaction.account_id.in_(ids))
        snap_q = snap_q.where(BalanceSnapshot.account_id.in_(ids))

    accounts = list(db.scalars(account_q))

    txns_by_account: dict[int, list[tuple[date, Decimal]]] = {}
    for account_id, posted_on, amount in db.execute(txn_q).all():
        txns_by_account.setdefault(account_id, []).append((posted_on, amount))
    snaps_by_account: dict[int, list[tuple[date, Decimal, str]]] = {}
    for account_id, as_of, balance, source in db.execute(snap_q).all():
        snaps_by_account.setdefault(account_id, []).append((as_of, balance, str(source.value)))

    return [
        AccountData(
            id=a.id,
            name=a.name,
            type=a.type,
            # Normalized so currency comparisons (convert, display filters)
            # can't be defeated by a legacy lowercase code.
            currency=a.currency.upper(),
            opening_balance=a.opening_balance,
            txns=txns_by_account.get(a.id, []),
            snapshots=snaps_by_account.get(a.id, []),
        )
        for a in accounts
    ]
