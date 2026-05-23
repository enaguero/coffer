from datetime import datetime

from pydantic import BaseModel, Field

from app.models.bank_connection import BankConnectionStatus, BankProvider


class InstitutionRef(BaseModel):
    """A bank as returned by the provider's institution list."""

    id: str
    name: str
    bic: str | None = None
    countries: list[str] = []
    logo_url: str | None = None


class LinkStartRequest(BaseModel):
    institution_id: str = Field(min_length=1, max_length=120)
    # Two-letter country code so we can re-fetch institutions if needed; not strictly required.
    country: str | None = Field(default=None, min_length=2, max_length=2)


class LinkStartResponse(BaseModel):
    bank_connection_id: int
    requisition_id: str
    link_url: str


class LinkCompleteRequest(BaseModel):
    requisition_id: str = Field(min_length=1, max_length=120)


class DiscoveredAccount(BaseModel):
    """One bank-side account discovered after the user finished linking."""

    external_account_id: str
    iban_last4: str | None = None
    name: str | None = None
    currency: str | None = None


class LinkCompleteResponse(BaseModel):
    bank_connection_id: int
    institution_name: str
    accounts: list[DiscoveredAccount]


class BankConnectionOut(BaseModel):
    id: int
    provider: BankProvider
    institution_id: str
    institution_name: str
    status: BankConnectionStatus
    requisition_expires_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class MapAccountRequest(BaseModel):
    """Attach a discovered bank-side account to an existing or new Coffer Account."""

    external_account_id: str = Field(min_length=1, max_length=120)
    # If account_id is set, we attach to that existing Account; otherwise we create
    # a new one using name/type/currency.
    account_id: int | None = None
    name: str | None = Field(default=None, max_length=120)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
