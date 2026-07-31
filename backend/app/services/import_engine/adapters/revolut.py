"""Revolut CSV export.

Needs code rather than a declarative preset because rows must be filtered by
State (pending/reverted rows would double-import later) and the fee is a
separate column that has to be folded into the amount.

Export headers: Type, Product, Started Date, Completed Date, Description,
Amount, Fee, Currency, State, Balance. Dates are ISO ("2024-01-15 12:30:41").
"""

from __future__ import annotations

import csv
from datetime import date
from io import StringIO

from app.services.csv_parser import ParsedRow, _parse_decimal
from app.services.import_engine.adapters.base import register_adapter


def _parse_iso_date(value: str) -> date | None:
    # Revolut dates are ISO ("2026-03-02 08:00:41") — parse them as such;
    # a day-first parser would flip "2026-03-02" into February 3rd.
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


@register_adapter("revolut_csv")
def parse_revolut_csv(content: bytes) -> tuple[list[ParsedRow], int]:
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(StringIO(text))
    parsed: list[ParsedRow] = []
    skipped = 0
    for row in reader:
        cells = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        if cells.get("state", "").upper() != "COMPLETED":
            skipped += 1
            continue
        posted = _parse_iso_date(cells.get("completed date") or cells.get("started date") or "")
        amount = _parse_decimal(cells.get("amount") or "")
        fee = _parse_decimal(cells.get("fee") or "") or 0
        description = cells.get("description", "")[:500]
        if posted is None or amount is None or not description:
            skipped += 1
            continue
        # Fee is reported positive and charged on top of the (signed) amount.
        amount = amount - abs(fee)
        parsed.append(
            ParsedRow(
                posted_on=posted,
                description=description,
                amount=amount,
                balance=_parse_decimal(cells.get("balance") or ""),
                external_id=f"{posted.isoformat()}|{description[:80]}|{amount}",
            )
        )
    return parsed, skipped
