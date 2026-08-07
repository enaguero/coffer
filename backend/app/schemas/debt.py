from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.debt import DebtRepaymentType

# The DB column is Numeric(6, 3) — anything past 999.999 (or negative) would
# 500 at insert, so the schemas reject it with a 422 instead.
APR_MAX = Decimal("999.999")


def _upper_currency(value: str | None) -> str | None:
    # Uppercased at the edge so FX conversion and display-currency filters
    # (exact-string comparisons) can't be defeated by a lowercase code.
    return value.upper() if value is not None else None


def repayment_type_violation(
    repayment_type: DebtRepaymentType,
    *,
    installment_amount: Decimal | None,
    ends_on: date | None,
    original_principal: Decimal | None,
    current_balance: Decimal | None,
    creating: bool = True,
) -> str | None:
    """The cross-field rules per repayment type; None when the shape is valid.

    Shared by DebtCreate's validator and the PATCH handler (which must check
    the *merged* debt — a partial update can't see the missing half, and
    passes creating=False). The statement_only balance rule differs by phase:
    a NEW statement-only debt needs a positive balance (the rate is inferred
    from it), but an update may set it to 0 — paying one off must not 422 —
    while a negative balance is always rejected."""
    repayment_type = DebtRepaymentType(repayment_type)
    if repayment_type == DebtRepaymentType.REVOLVING:
        return None
    if installment_amount is None:
        return f"{repayment_type.value} debts require installment_amount"
    if ends_on is None:
        return f"{repayment_type.value} debts require ends_on"
    if repayment_type == DebtRepaymentType.FLAT and (original_principal is None or original_principal <= 0):
        return "flat debts require original_principal > 0 (interest is computed on it)"
    if repayment_type == DebtRepaymentType.STATEMENT_ONLY:
        if creating and (current_balance is None or current_balance <= 0):
            return "statement_only debts require current_balance > 0"
        if current_balance is None or current_balance < 0:
            return "statement_only debts require current_balance >= 0"
    return None


class DebtBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    account_id: int | None = None
    original_principal: Decimal = Decimal("0")
    current_balance: Decimal = Decimal("0")
    interest_rate_apr: Decimal | None = Field(default=None, ge=0, le=APR_MAX)
    promo_apr: Decimal | None = Field(default=None, ge=0, le=APR_MAX)
    promo_ends_on: date | None = None
    minimum_payment: Decimal | None = None
    repayment_type: DebtRepaymentType = DebtRepaymentType.REVOLVING
    # NULL means the user's display currency.
    currency: str | None = Field(default=None, pattern=r"^[A-Za-z]{3}$")
    installment_amount: Decimal | None = Field(default=None, gt=0)
    due_day_of_month: int | None = Field(default=None, ge=1, le=31)
    starts_on: date | None = None
    ends_on: date | None = None
    notes: str | None = None

    _upper_currency = field_validator("currency")(_upper_currency)


class DebtCreate(DebtBase):
    @model_validator(mode="after")
    def _check_repayment_type_shape(self) -> "DebtCreate":
        violation = repayment_type_violation(
            self.repayment_type,
            installment_amount=self.installment_amount,
            ends_on=self.ends_on,
            original_principal=self.original_principal,
            current_balance=self.current_balance,
        )
        if violation:
            raise ValueError(violation)
        return self


class DebtUpdate(BaseModel):
    name: str | None = None
    account_id: int | None = None
    original_principal: Decimal | None = None
    current_balance: Decimal | None = None
    interest_rate_apr: Decimal | None = Field(default=None, ge=0, le=APR_MAX)
    promo_apr: Decimal | None = Field(default=None, ge=0, le=APR_MAX)
    promo_ends_on: date | None = None
    minimum_payment: Decimal | None = None
    repayment_type: DebtRepaymentType | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Za-z]{3}$")
    installment_amount: Decimal | None = Field(default=None, gt=0)
    due_day_of_month: int | None = Field(default=None, ge=1, le=31)
    starts_on: date | None = None
    ends_on: date | None = None
    notes: str | None = None

    _upper_currency = field_validator("currency")(_upper_currency)


class DebtOut(DebtBase):
    id: int

    model_config = {"from_attributes": True}


class Snowflake(BaseModel):
    """A one-off extra payment in a given plan month (1 = next month)."""

    month: int = Field(ge=1, le=600)
    amount: Decimal = Field(gt=0)


class PlanRequest(BaseModel):
    extra_monthly: Decimal = Field(default=Decimal("0"), ge=0)
    snowflakes: list[Snowflake] = Field(default_factory=list)


class DebtPlanDebtOut(BaseModel):
    id: int
    name: str
    payoff_date: date | None
    interest_paid: Decimal
    # The debt's own currency; None = the user's display currency. Simulation
    # figures are display-denominated (converted once at plan start).
    currency: str | None = None


class PromoCliffOut(BaseModel):
    debt_id: int
    name: str
    promo_ends_on: date
    balance_at_expiry: Decimal
    reverting_apr: Decimal
    extra_yearly_interest: Decimal


class PlanSeriesPoint(BaseModel):
    on: date
    balance: Decimal


class SchedulePaymentOut(BaseModel):
    debt_id: int
    amount: Decimal


class ScheduleMonthOut(BaseModel):
    month: date
    payments: list[SchedulePaymentOut]
    # Budget the month couldn't place: only flat loans still open (prepaying
    # them saves nothing), or everything already cleared.
    uncommitted: Decimal


class PlanOut(BaseModel):
    strategy: str
    months: int
    debt_free_date: date | None
    total_interest: Decimal
    total_paid: Decimal
    monthly_budget: Decimal
    interest_saved_vs_minimum: Decimal | None = None
    months_saved_vs_minimum: int | None = None
    debts: list[DebtPlanDebtOut]
    balance_series: list[PlanSeriesPoint]
    promo_cliffs: list[PromoCliffOut]
    assumptions: list[str]
    unpayable: bool
    # Per-debt monthly payments. Populated only for the optimal plan — the
    # comparison strategies return an empty list to keep payloads bounded.
    schedule: list[ScheduleMonthOut] = Field(default_factory=list)


class PlanCompareOut(BaseModel):
    minimum: PlanOut
    snowball: PlanOut
    avalanche: PlanOut
    optimal: PlanOut
    # Debt currencies with no saved FX rate — those debts sit outside every
    # simulation above (honest conversion, mirroring net worth).
    excluded_currencies: list[str] = Field(default_factory=list)
