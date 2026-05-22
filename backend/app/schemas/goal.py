from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class GoalBase(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    target_amount: Decimal
    current_amount: Decimal = Decimal("0")
    target_date: date | None = None
    notes: str | None = None


class GoalCreate(GoalBase):
    pass


class GoalUpdate(BaseModel):
    name: str | None = None
    target_amount: Decimal | None = None
    current_amount: Decimal | None = None
    target_date: date | None = None
    notes: str | None = None


class GoalOut(GoalBase):
    id: int
    progress: float

    model_config = {"from_attributes": True}
