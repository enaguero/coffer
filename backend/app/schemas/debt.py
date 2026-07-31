from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class DebtBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    account_id: int | None = None
    original_principal: Decimal = Decimal("0")
    current_balance: Decimal = Decimal("0")
    interest_rate_apr: Decimal | None = None
    promo_apr: Decimal | None = None
    promo_ends_on: date | None = None
    minimum_payment: Decimal | None = None
    due_day_of_month: int | None = Field(default=None, ge=1, le=31)
    starts_on: date | None = None
    ends_on: date | None = None
    notes: str | None = None


class DebtCreate(DebtBase):
    pass


class DebtUpdate(BaseModel):
    name: str | None = None
    account_id: int | None = None
    original_principal: Decimal | None = None
    current_balance: Decimal | None = None
    interest_rate_apr: Decimal | None = None
    promo_apr: Decimal | None = None
    promo_ends_on: date | None = None
    minimum_payment: Decimal | None = None
    due_day_of_month: int | None = Field(default=None, ge=1, le=31)
    starts_on: date | None = None
    ends_on: date | None = None
    notes: str | None = None


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


class PlanCompareOut(BaseModel):
    minimum: PlanOut
    snowball: PlanOut
    avalanche: PlanOut
