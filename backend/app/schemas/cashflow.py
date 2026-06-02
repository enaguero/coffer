from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.models.cashflow import CashflowKind


class CashflowLineBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: CashflowKind
    country: str = Field(min_length=2, max_length=2)
    currency: str = Field(min_length=3, max_length=3)
    account_id: int | None = None
    category_id: int | None = None
    sort_order: int = 0
    is_active: bool = True
    notes: str | None = None

    @field_validator("country")
    @classmethod
    def _country_upper(cls, v: str) -> str:
        return v.upper()

    @field_validator("currency")
    @classmethod
    def _currency_upper(cls, v: str) -> str:
        return v.upper()


class CashflowLineCreate(CashflowLineBase):
    pass


class CashflowLineUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    kind: CashflowKind | None = None
    country: str | None = Field(default=None, min_length=2, max_length=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    account_id: int | None = None
    category_id: int | None = None
    sort_order: int | None = None
    is_active: bool | None = None
    notes: str | None = None

    @field_validator("country")
    @classmethod
    def _country_upper(cls, v: str | None) -> str | None:
        return v.upper() if v else v

    @field_validator("currency")
    @classmethod
    def _currency_upper(cls, v: str | None) -> str | None:
        return v.upper() if v else v


class CashflowEntryIn(BaseModel):
    year: int = Field(ge=1900, le=3000)
    month: int = Field(ge=1, le=12)
    amount: Decimal


class CashflowEntryUpsert(CashflowEntryIn):
    line_id: int


class CashflowEntryOut(CashflowEntryIn):
    id: int
    line_id: int

    model_config = {"from_attributes": True}


class CashflowEntryBulk(BaseModel):
    entries: list[CashflowEntryUpsert]


class CashflowLineOut(CashflowLineBase):
    id: int
    entries: list[CashflowEntryIn] = []

    model_config = {"from_attributes": True}


class CashflowMonth(BaseModel):
    year: int
    month: int


class CashflowMonthTotal(BaseModel):
    year: int
    month: int
    income: Decimal
    expense: Decimal
    net: Decimal


class CashflowCurrencyTotals(BaseModel):
    currency: str
    months: list[CashflowMonthTotal]


class CashflowGridOut(BaseModel):
    months: list[CashflowMonth]
    lines: list[CashflowLineOut]
    totals_by_currency: list[CashflowCurrencyTotals]
