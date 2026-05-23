"""Shared contracts that every bank provider implementation must satisfy."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from app.schemas.bank_connection import InstitutionRef


class BankProviderError(RuntimeError):
    """Generic provider failure surfaced as a 502 by the API layer."""


class BankProviderNotConfigured(BankProviderError):
    """Provider credentials are missing — surface as 503 from the API layer."""


@dataclass
class LinkSession:
    """What we get back from kicking off the bank-link flow."""

    requisition_id: str
    link_url: str
    agreement_id: str | None
    expires_at: datetime | None


@dataclass
class LinkedAccountRef:
    """One bank-side account discovered after the user finished authenticating."""

    external_account_id: str
    iban_last4: str | None = None
    name: str | None = None
    currency: str | None = None


@dataclass
class NormalizedTxn:
    """The shape persistence code expects from any provider.

    Mirrors `ParsedRow` in services/csv_parser.py so the dedup + insert loop
    in services/bank_sync.py can reuse the imports.py persistence pattern.
    """

    posted_on: date
    description: str
    amount: Decimal
    # Provider's stable transaction identifier when it exists; otherwise None
    # and the orchestrator falls back to the date|desc|amount synthesized id.
    bank_transaction_id: str | None = None
    raw: dict = field(default_factory=dict)


class BankProvider(Protocol):
    async def list_institutions(self, country: str) -> list[InstitutionRef]: ...

    async def create_link(
        self,
        *,
        institution_id: str,
        redirect_uri: str,
        reference: str,
        user_language: str | None = None,
    ) -> LinkSession: ...

    async def complete_link(self, requisition_id: str) -> list[LinkedAccountRef]: ...

    async def fetch_transactions(
        self,
        *,
        external_account_id: str,
        date_from: date,
        date_to: date,
    ) -> list[NormalizedTxn]: ...

    async def revoke(self, requisition_id: str) -> None: ...
