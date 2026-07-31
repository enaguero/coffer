from pydantic import BaseModel

from app.models.account import AccountType


class UkBankOut(BaseModel):
    id: str
    name: str
    account_types: list[AccountType]
    formats: list[str]
    notes: str
