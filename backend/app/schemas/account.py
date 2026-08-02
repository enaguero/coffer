from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.models.account import AccountType, UkWrapper
from app.services.import_engine.catalog import get_bank


def _check_bank_id(value: str | None) -> str | None:
    if value is not None and get_bank(value) is None:
        raise ValueError(f"Unknown bank id: {value!r}")
    return value


def _upper_currency(value: str | None) -> str | None:
    # Uppercased at the edge so FX conversion and display-currency filters
    # (exact-string comparisons) can't be defeated by a lowercase code.
    return value.upper() if value is not None else None


class AccountBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: AccountType
    institution: str | None = None
    bank_id: str | None = None
    uk_wrapper: UkWrapper | None = None
    currency: str = Field(default="USD", pattern=r"^[A-Za-z]{3}$")
    opening_balance: Decimal = Decimal("0")
    # "private" | "household" — household-visible accounts appear read-only
    # to the other members of the owner's household.
    visibility: str = Field(default="private", pattern=r"^(private|household)$")

    _validate_bank_id = field_validator("bank_id")(_check_bank_id)
    _upper_currency = field_validator("currency")(_upper_currency)


class AccountCreate(AccountBase):
    pass


class AccountUpdate(BaseModel):
    name: str | None = None
    type: AccountType | None = None
    institution: str | None = None
    bank_id: str | None = None
    uk_wrapper: UkWrapper | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Za-z]{3}$")
    opening_balance: Decimal | None = None
    visibility: str | None = Field(default=None, pattern=r"^(private|household)$")

    _validate_bank_id = field_validator("bank_id")(_check_bank_id)
    _upper_currency = field_validator("currency")(_upper_currency)


class AccountOut(AccountBase):
    id: int

    model_config = {"from_attributes": True}


class BalanceSnapshotIn(BaseModel):
    as_of: date
    balance: Decimal


class BalanceSnapshotOut(BaseModel):
    id: int
    account_id: int
    as_of: date
    balance: Decimal
    source: str

    model_config = {"from_attributes": True}
