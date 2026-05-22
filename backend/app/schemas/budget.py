from decimal import Decimal

from pydantic import BaseModel, Field


class BudgetEntryBase(BaseModel):
    category_id: int
    year: int = Field(ge=1900, le=3000)
    month: int = Field(ge=1, le=12)
    planned_amount: Decimal = Decimal("0")


class BudgetEntryCreate(BudgetEntryBase):
    pass


class BudgetEntryUpdate(BaseModel):
    planned_amount: Decimal


class BudgetEntryOut(BudgetEntryBase):
    id: int

    model_config = {"from_attributes": True}


class BudgetMonthCell(BaseModel):
    category_id: int
    category_name: str
    planned: Decimal
    actual: Decimal


class BudgetMonthView(BaseModel):
    year: int
    month: int
    income_planned: Decimal
    income_actual: Decimal
    expenses_planned: Decimal
    expenses_actual: Decimal
    saving_planned: Decimal
    saving_actual: Decimal
    rows: list[BudgetMonthCell]
