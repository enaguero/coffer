from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class TransactionBase(BaseModel):
    account_id: int
    category_id: int | None = None
    posted_on: date
    description: str = Field(min_length=1, max_length=500)
    amount: Decimal
    notes: str | None = None
    external_id: str | None = None


class TransactionCreate(TransactionBase):
    pass


class TransactionUpdate(BaseModel):
    account_id: int | None = None
    category_id: int | None = None
    posted_on: date | None = None
    description: str | None = None
    amount: Decimal | None = None
    notes: str | None = None


class TransactionOut(TransactionBase):
    id: int
    statement_import_id: int | None = None

    model_config = {"from_attributes": True}


class CategoryMonthlySpend(BaseModel):
    category_id: int | None
    category_name: str | None
    total: Decimal


class MonthlySummary(BaseModel):
    year: int
    month: int
    income: Decimal
    expenses: Decimal
    saving: Decimal
    by_category: list[CategoryMonthlySpend]
