"""Currency conversion over user-maintained rates.

Rates are expressed as: 1 unit of `currency` = rate units of the user\'s
display currency. Conversion returns None when no rate exists — callers must
surface "unconverted" honestly instead of silently mixing currencies.
"""

from __future__ import annotations

from decimal import Decimal

TWO_DP = Decimal("0.01")


def convert(
    amount: Decimal, currency: str, display_currency: str, rates: dict[str, Decimal]
) -> Decimal | None:
    if currency == display_currency:
        return amount
    rate = rates.get(currency)
    if rate is None or rate <= 0:
        return None
    return (amount * rate).quantize(TWO_DP)
