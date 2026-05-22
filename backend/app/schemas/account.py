from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.account import AccountType


class AccountBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: AccountType
    institution: str | None = None
    currency: str = Field(default="USD", min_length=3, max_length=3)
    opening_balance: Decimal = Decimal("0")


class AccountCreate(AccountBase):
    pass


class AccountUpdate(BaseModel):
    name: str | None = None
    type: AccountType | None = None
    institution: str | None = None
    currency: str | None = None
    opening_balance: Decimal | None = None


class AccountOut(AccountBase):
    id: int

    model_config = {"from_attributes": True}
