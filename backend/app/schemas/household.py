from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class HouseholdCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


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
    token: str
    expires_at: datetime


class JoinRequest(BaseModel):
    token: str = Field(min_length=16, max_length=64)


class SharedAccountOut(BaseModel):
    account_id: int
    owner_user_id: int
    owner_name: str
    name: str
    type: str
    currency: str
    balance: Decimal
    as_of: str | None  # ISO date of the freshest information used
    source: str


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
