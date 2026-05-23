"""Heuristic CSV statement parser.

Bank CSVs vary wildly. We sniff the dialect, look for date/description/amount
columns by common names, and fall back to debit/credit pairs. Anything we can't
confidently parse is skipped and reported in the response.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from io import StringIO

from dateutil import parser as dateparser

DATE_COLUMN_HINTS = ("date", "posted", "transaction date", "trans date", "fecha", "post date")
DESC_COLUMN_HINTS = ("description", "details", "narration", "memo", "payee", "concepto", "detalle")
AMOUNT_COLUMN_HINTS = ("amount", "value", "monto", "importe")
DEBIT_HINTS = ("debit", "withdrawal", "cargo", "egreso", "out")
CREDIT_HINTS = ("credit", "deposit", "abono", "ingreso", "in")


@dataclass
class ParsedRow:
    posted_on: date
    description: str
    amount: Decimal
    external_id: str | None = None


def _find_column(header: list[str], hints: Iterable[str]) -> int | None:
    lowered = [h.strip().lower() for h in header]
    for hint in hints:
        for i, name in enumerate(lowered):
            if hint == name or hint in name:
                return i
    return None


def _parse_decimal(value: str) -> Decimal | None:
    if value is None:
        return None
    cleaned = value.strip().replace("$", "").replace(" ", "")
    if not cleaned:
        return None
    # Handle parentheses for negatives, e.g. (123.45)
    negative = False
    if cleaned.startswith("(") and cleaned.endswith(")"):
        negative = True
        cleaned = cleaned[1:-1]
    # Locale: if comma comes after the last dot, treat comma as decimal separator
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        # Treat lone comma as decimal separator
        cleaned = cleaned.replace(",", ".")
    try:
        result = Decimal(cleaned)
    except InvalidOperation:
        return None
    return -result if negative else result


def _parse_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return dateparser.parse(value, dayfirst=True, fuzzy=True).date()
    except (ValueError, TypeError, OverflowError):
        return None


def parse_csv(content: bytes) -> tuple[list[ParsedRow], int]:
    """Return (rows, skipped_rows)."""
    text = content.decode("utf-8-sig", errors="replace")
    try:
        sample = text[:4096]
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel

    reader = csv.reader(StringIO(text), dialect=dialect)
    rows = list(reader)
    if not rows:
        return [], 0

    # Skip leading metadata lines until we find a plausible header (>= 3 cells).
    header_idx = 0
    for i, row in enumerate(rows):
        if len(row) >= 3 and any(cell.strip() for cell in row):
            header_idx = i
            break
    header = rows[header_idx]

    date_col = _find_column(header, DATE_COLUMN_HINTS)
    desc_col = _find_column(header, DESC_COLUMN_HINTS)
    amount_col = _find_column(header, AMOUNT_COLUMN_HINTS)
    debit_col = _find_column(header, DEBIT_HINTS)
    credit_col = _find_column(header, CREDIT_HINTS)

    if date_col is None or desc_col is None or (amount_col is None and debit_col is None and credit_col is None):
        return [], len(rows) - header_idx - 1

    parsed: list[ParsedRow] = []
    skipped = 0
    for raw in rows[header_idx + 1 :]:
        if not any(cell.strip() for cell in raw):
            continue
        try:
            posted = _parse_date(raw[date_col])
            description = raw[desc_col].strip() if desc_col < len(raw) else ""
        except IndexError:
            skipped += 1
            continue
        if posted is None or not description:
            skipped += 1
            continue

        amount: Decimal | None = None
        if amount_col is not None and amount_col < len(raw):
            amount = _parse_decimal(raw[amount_col])
        if amount is None and (debit_col is not None or credit_col is not None):
            debit_val = _parse_decimal(raw[debit_col]) if debit_col is not None and debit_col < len(raw) else None
            credit_val = _parse_decimal(raw[credit_col]) if credit_col is not None and credit_col < len(raw) else None
            if credit_val is not None:
                amount = credit_val
            elif debit_val is not None:
                amount = -abs(debit_val)
        if amount is None:
            skipped += 1
            continue

        parsed.append(
            ParsedRow(
                posted_on=posted,
                description=description[:500],
                amount=amount,
                external_id=f"{posted.isoformat()}|{description[:80]}|{amount}",
            )
        )
    return parsed, skipped
