"""Bank-aggregator integrations.

Currently: GoCardless Bank Account Data (free EU/UK PSD2 Open Banking).
The `BankProvider` Protocol in `base.py` defines the surface every provider
must implement so additional aggregators (Plaid, Teller, Belvo, ...) can be
slotted in without touching the API layer.
"""

from app.services.bank_providers.base import (
    BankProviderError,
    BankProviderNotConfigured,
    LinkedAccountRef,
    LinkSession,
    NormalizedTxn,
)
from app.services.bank_providers.gocardless import GoCardlessProvider

__all__ = [
    "BankProviderError",
    "BankProviderNotConfigured",
    "LinkedAccountRef",
    "LinkSession",
    "NormalizedTxn",
    "GoCardlessProvider",
]
