from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class DebtBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    account_id: int | None = None
    original_principal: Decimal = Decimal("0")
    current_balance: Decimal = Decimal("0")
    interest_rate_apr: Decimal | None = None
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
    minimum_payment: Decimal | None = None
    due_day_of_month: int | None = Field(default=None, ge=1, le=31)
    starts_on: date | None = None
    ends_on: date | None = None
    notes: str | None = None


class DebtOut(DebtBase):
    id: int

    model_config = {"from_attributes": True}
