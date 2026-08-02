from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.models.account import AccountType


class HouseholdCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v


class HouseholdMemberOut(BaseModel):
    user_id: int
    email: str
    full_name: str | None
    role: str
    is_me: bool


class HouseholdOut(BaseModel):
    id: int
    name: str
    my_role: str
    members: list[HouseholdMemberOut]


class InviteOut(BaseModel):
    id: int
    token: str
    expires_at: datetime


class JoinRequest(BaseModel):
    token: str = Field(min_length=16, max_length=64)


class SharedAccountOut(BaseModel):
    account_id: int
    owner_user_id: int
    owner_name: str
    name: str
    type: AccountType
    currency: str
    balance: Decimal
    as_of: date | None  # date of the freshest information used
    source: str  # "statement" | "manual" | "derived" | "opening"


class SharedCurrencyTotalOut(BaseModel):
    currency: str
    total: Decimal


class SharedViewOut(BaseModel):
    household_id: int
    household_name: str
    accounts: list[SharedAccountOut]
    # Grouped per currency — members maintain their own FX rates, so cross-
    # currency sums would silently mix units.
    totals: list[SharedCurrencyTotalOut]
