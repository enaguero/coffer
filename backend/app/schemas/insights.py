from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.account import AccountType


class RecurringItemOut(BaseModel):
    account_id: int
    description: str
    cadence: str
    cadence_days: int
    typical_amount: Decimal
    monthly_equivalent: Decimal
    occurrences: int
    first_seen: date
    last_seen: date
    next_expected: date
    confidence: float
    active: bool
    is_income: bool
    category_id: int | None


class SeriesPoint(BaseModel):
    on: date
    balance: Decimal


class ForecastEventOut(BaseModel):
    on: date
    description: str
    amount: Decimal
    cadence: str
    is_income: bool


class DueMarkerOut(BaseModel):
    on: date
    name: str
    minimum_payment: Decimal | None


class ForecastOut(BaseModel):
    display_currency: str | None = None
    # Liquid-account currencies excluded from the projection (no conversion).
    excluded_currencies: list[str] = Field(default_factory=list)
    start_balance: Decimal
    reserve: Decimal
    days: int
    series: list[SeriesPoint]
    events: list[ForecastEventOut]
    due_markers: list[DueMarkerOut]
    min_balance: Decimal
    min_balance_date: date | None
    first_below_reserve: date | None
    first_below_zero: date | None
    safe_to_commit: Decimal


class AccountBalanceOut(BaseModel):
    id: int
    name: str
    type: AccountType
    currency: str
    balance: Decimal  # in the account's own currency
    as_of: date | None
    source: str
    drift: Decimal | None
    converted: bool = True


class RegisterDebtOut(BaseModel):
    id: int
    name: str
    balance: Decimal  # in the debt's own currency
    currency: str | None = None  # None = display currency by convention
    # Included in liability totals? False when no FX rate is saved.
    converted: bool = True
    # Payoff at contractual minimums; None = never clears or unconvertible.
    payoff_date: date | None = None


class NetWorthPointOut(BaseModel):
    on: date
    assets: Decimal
    liabilities: Decimal
    net: Decimal


class NetWorthOut(BaseModel):
    display_currency: str | None = None
    excluded_currencies: list[str] = Field(default_factory=list)
    accounts: list[AccountBalanceOut]
    register_debts: list[RegisterDebtOut]
    assets: Decimal
    liabilities: Decimal
    net: Decimal
    series: list[NetWorthPointOut]


class AllocationOptionOut(BaseModel):
    kind: str
    target_id: int | None
    name: str
    apr: Decimal | None
    yearly_interest_saved: Decimal | None
    months_earlier: Decimal | None
    runway_months_gained: Decimal | None
    note: str


class RaiseOut(BaseModel):
    description: str
    account_id: int
    cadence: str
    previous_amount: Decimal
    new_amount: Decimal
    monthly_delta: Decimal


class SurplusOut(BaseModel):
    year: int
    month: int
    income: Decimal
    outflows: Decimal
    surplus: Decimal
    txn_count: int
    uncategorized_count: int
    uncategorized_amount: Decimal
    # The amount the allocation options were priced against (the positive
    # surplus, or the explicit ?amount override).
    amount_considered: Decimal
    options: list[AllocationOptionOut]
    raises_detected: list[RaiseOut]


class AllowanceMeterOut(BaseModel):
    wrapper: str
    allowance: Decimal
    used: Decimal
    remaining: Decimal
    lisa_portion: Decimal


class AllowancesOut(BaseModel):
    tax_year_start: date
    tax_year_end: date
    # Inclusive of today — 1 on the final day of the tax year.
    days_left: int
    meters: list[AllowanceMeterOut]
    # How many GBP accounts are wrapper-tagged (0 = feature not set up).
    wrapped_account_count: int


class DigestOut(BaseModel):
    subject: str
    body: str
    item_count: int
    smtp_configured: bool


class DigestSendOut(BaseModel):
    sent_to: str
    subject: str


class AccountCoverageOut(BaseModel):
    account_id: int
    name: str
    type: AccountType
    last_txn_on: date | None
    txn_count: int
    last_import_at: datetime | None
    last_snapshot_on: date | None
