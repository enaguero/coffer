"""Net worth from transaction-derived balances, statement attestations, and
manual valuations.

Balance at a date anchors on the latest snapshot at or before that date, then
applies transactions after the snapshot: `snap.balance + sum(txns in (snap.as_of,
d])`. With no snapshot it falls back to `opening_balance + sum(txns <= d)`.
Statement snapshots therefore self-correct histories with missing months.

Drift = latest statement balance minus the transaction-derived balance on the
same day. Non-zero drift means transactions are missing from the ledger — a
data-integrity signal, surfaced rather than hidden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from app.models.account import AccountType
from app.services.analytics.fx import convert

ASSET_TYPES = {AccountType.CHECKING, AccountType.SAVINGS, AccountType.CASH, AccountType.OTHER}
LIABILITY_TYPES = {AccountType.CREDIT_CARD, AccountType.LOAN, AccountType.OVERDRAFT}


@dataclass
class AccountData:
    id: int
    name: str
    type: AccountType
    currency: str
    opening_balance: Decimal
    # chronological (posted_on, amount)
    txns: list[tuple[date, Decimal]] = field(default_factory=list)
    # chronological (as_of, balance, source)
    snapshots: list[tuple[date, Decimal, str]] = field(default_factory=list)


@dataclass
class AccountBalance:
    id: int
    name: str
    type: AccountType
    currency: str
    balance: Decimal  # in the account's own currency
    as_of: date | None  # date of the freshest information used
    source: str  # "statement" | "manual" | "derived"
    drift: Decimal | None = None  # attested minus derived, when both exist
    # Included in the display-currency totals? False when no FX rate exists.
    converted: bool = True


@dataclass
class NetWorthPoint:
    on: date
    assets: Decimal
    liabilities: Decimal
    net: Decimal


def _derived_at(acc: AccountData, on: date) -> Decimal:
    total = acc.opening_balance
    for posted, amount in acc.txns:
        if posted > on:
            break
        total += amount
    return total


def balance_at(acc: AccountData, on: date) -> Decimal:
    snaps = [s for s in acc.snapshots if s[0] <= on]
    if snaps:
        as_of, bal, _source = snaps[-1]
        after = sum((amt for posted, amt in acc.txns if as_of < posted <= on), Decimal("0"))
        return bal + after
    return _derived_at(acc, on)


def current_balance(acc: AccountData, today: date | None = None) -> AccountBalance:
    today = today or date.today()
    last_txn = acc.txns[-1][0] if acc.txns else None
    last_snap = acc.snapshots[-1] if acc.snapshots else None

    drift: Decimal | None = None
    if last_snap is not None and acc.txns:
        drift = (last_snap[1] - _derived_at(acc, last_snap[0])).quantize(Decimal("0.01"))

    balance = balance_at(acc, today).quantize(Decimal("0.01"))
    if last_snap is not None and (last_txn is None or last_snap[0] >= last_txn):
        source, as_of = last_snap[2], last_snap[0]
    elif last_txn is not None:
        source, as_of = "derived", last_txn
    else:
        source, as_of = "opening", None
    return AccountBalance(
        id=acc.id,
        name=acc.name,
        type=acc.type,
        currency=acc.currency,
        balance=balance,
        as_of=as_of,
        source=source,
        drift=drift if drift is not None and abs(drift) >= Decimal("0.01") else None,
    )


def _month_ends(today: date, months: int) -> list[date]:
    out: list[date] = []
    year, month = today.year, today.month
    for _ in range(months):
        first = date(year, month, 1)
        out.append(first - timedelta(days=1))
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    out.reverse()
    out.append(today)
    return out


@dataclass
class NetWorthReport:
    accounts: list[AccountBalance]
    # Debts from the register that are NOT linked to a tracked account
    # (linked ones are already counted through their account's balance).
    register_debts: list[tuple[int, str, Decimal]]
    assets: Decimal
    liabilities: Decimal
    net: Decimal
    series: list[NetWorthPoint]
    display_currency: str | None = None
    # Currencies with accounts that could NOT be converted (no rate saved).
    excluded_currencies: list[str] = field(default_factory=list)


def compute_net_worth(
    accounts: list[AccountData],
    register_debts: list[tuple[int, str, Decimal, int | None]],  # (id, name, balance, account_id)
    months: int = 24,
    today: date | None = None,
    display_currency: str | None = None,
    rates: dict[str, Decimal] | None = None,
) -> NetWorthReport:
    """When `display_currency` is set, every balance is converted with the
    user-maintained `rates` before summing; accounts whose currency has no
    rate are excluded from totals and flagged — never silently mixed.
    (The series applies today's rates across history: a trend approximation,
    not a historical-FX reconstruction.)"""
    today = today or date.today()
    rates = rates or {}

    def to_display(amount: Decimal, currency: str) -> Decimal | None:
        if display_currency is None:
            return amount  # legacy mixed-sum behavior when no display set
        return convert(amount, currency, display_currency, rates)

    convertible = {
        a.id: display_currency is None or to_display(Decimal("0"), a.currency) is not None
        for a in accounts
    }
    excluded = sorted({a.currency for a in accounts if not convertible[a.id]})
    tracked_ids = {a.id for a in accounts}
    unlinked = [
        (d_id, name, bal) for d_id, name, bal, acc_id in register_debts if acc_id is None or acc_id not in tracked_ids
    ]
    unlinked_total = sum((bal for _, _, bal in unlinked), Decimal("0"))

    balances = [current_balance(a, today) for a in accounts]
    currency_by_id = {a.id: a.currency for a in accounts}
    for b in balances:
        b.converted = convertible[b.id]

    def display_balance(b: AccountBalance) -> Decimal:
        got = to_display(b.balance, currency_by_id[b.id])
        return got if got is not None else Decimal("0")

    assets = sum((display_balance(b) for b in balances if b.type in ASSET_TYPES and b.converted), Decimal("0"))
    # Liability accounts usually carry negative balances; count their magnitude.
    account_liabilities = sum(
        (-display_balance(b) for b in balances if b.type in LIABILITY_TYPES and b.converted), Decimal("0")
    )
    # Register debts carry no currency of their own — treated as display currency.
    liabilities = account_liabilities + unlinked_total

    series: list[NetWorthPoint] = []

    def series_balance(acc: AccountData, on: date) -> Decimal:
        got = to_display(balance_at(acc, on), acc.currency)
        return got if got is not None else Decimal("0")

    for on in _month_ends(today, months):
        a = sum(
            (series_balance(acc, on) for acc in accounts if acc.type in ASSET_TYPES and convertible[acc.id]),
            Decimal("0"),
        )
        acc_liab = sum(
            (-series_balance(acc, on) for acc in accounts if acc.type in LIABILITY_TYPES and convertible[acc.id]),
            Decimal("0"),
        )
        # Register debts have no dated history — held flat at today's balance.
        liab = acc_liab + unlinked_total
        series.append(
            NetWorthPoint(
                on=on,
                assets=a.quantize(Decimal("0.01")),
                liabilities=liab.quantize(Decimal("0.01")),
                net=(a - liab).quantize(Decimal("0.01")),
            )
        )
    # Drop leading months with no information at all (flat zero before data).
    first_signal = next((i for i, p in enumerate(series) if p.assets != 0 or p.liabilities != unlinked_total), 0)
    series = series[max(0, first_signal - 1) :]

    return NetWorthReport(
        accounts=balances,
        register_debts=unlinked,
        assets=assets.quantize(Decimal("0.01")),
        liabilities=liabilities.quantize(Decimal("0.01")),
        net=(assets - liabilities).quantize(Decimal("0.01")),
        series=series,
        display_currency=display_currency,
        excluded_currencies=excluded,
    )
