from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class GoalBase(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    target_amount: Decimal
    current_amount: Decimal = Decimal("0")
    target_date: date | None = None
    account_id: int | None = None
    monthly_contribution: Decimal | None = Field(default=None, ge=0)
    notes: str | None = None


class GoalCreate(GoalBase):
    pass


class GoalUpdate(BaseModel):
    name: str | None = None
    target_amount: Decimal | None = None
    current_amount: Decimal | None = None
    target_date: date | None = None
    account_id: int | None = None
    monthly_contribution: Decimal | None = Field(default=None, ge=0)
    notes: str | None = None


class GoalOut(GoalBase):
    id: int
    progress: float
    # Funding view. current_amount reflects the linked account's balance when
    # account_id is set (auto-tracked), the stored value otherwise.
    auto_tracked: bool = False
    # (target - current) / months until target_date; None without a future date
    # or when the goal is already met.
    required_monthly: Decimal | None = None
    # monthly_contribution vs required_monthly; None when either is unknown.
    on_track: bool | None = None
    # Positive transactions into the linked account this calendar month.
    funded_this_month: Decimal | None = None
    # Arrival date at the committed monthly_contribution rate.
    projected_date: date | None = None

    model_config = {"from_attributes": True}
