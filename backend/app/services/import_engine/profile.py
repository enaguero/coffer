"""Declarative CSV parsing driven by an ImportProfileConfig.

A profile captures everything bank-specific about a CSV export — column
positions/names, date order, sign convention — as data. Presets in
catalog.py and user-saved profiles (models/import_profile.py) are both just
instances of this config, so "support for bank X" is a dict, not a class.
"""

from __future__ import annotations

import csv
from datetime import date, datetime
from io import StringIO

from dateutil import parser as dateparser
from pydantic import BaseModel, Field, model_validator

from app.services.csv_parser import DetectedLayout, ParsedRow, _parse_date, _parse_decimal

# Column references are either a 0-based index or a header name
# (case-insensitive, stripped).
ColumnRef = int | str


class ImportProfileConfig(BaseModel):
    delimiter: str | None = Field(default=None, min_length=1, max_length=1)
    # Lines to skip before the header (or before data when has_header=False).
    skip_rows: int = Field(default=0, ge=0, le=50)
    has_header: bool = True

    date_column: ColumnRef
    # Joined with " — " when more than one (e.g. Starling's Counter Party + Reference).
    description_columns: list[ColumnRef] = Field(min_length=1)
    amount_column: ColumnRef | None = None
    debit_column: ColumnRef | None = None
    credit_column: ColumnRef | None = None
    # Stable per-transaction id column when the bank provides one (e.g. Monzo).
    external_id_column: ColumnRef | None = None
    # Running/closing balance column when the statement carries one — feeds
    # BalanceSnapshot attestations at import.
    balance_column: ColumnRef | None = None

    # strptime format; None falls back to dateutil with day_first.
    date_format: str | None = None
    day_first: bool = True
    # Flip signs for exports that list charges as positive (typical credit cards).
    invert_amount: bool = False
    encoding: str = "utf-8-sig"

    @model_validator(mode="after")
    def _amount_source_present(self) -> ImportProfileConfig:
        if self.amount_column is None and self.debit_column is None and self.credit_column is None:
            raise ValueError("Profile needs amount_column or debit/credit columns")
        if not self.has_header:
            refs = [
                self.date_column,
                *self.description_columns,
                self.amount_column,
                self.debit_column,
                self.credit_column,
                self.external_id_column,
                self.balance_column,
            ]
            if any(isinstance(r, str) for r in refs if r is not None):
                raise ValueError("Header-name column refs require has_header=true")
        return self

    @classmethod
    def from_detected_layout(cls, layout: DetectedLayout) -> ImportProfileConfig:
        """Build a profile from what the heuristic sniffer found, using header
        names (more robust than indexes if the bank reorders columns)."""

        def name(idx: int | None) -> str | None:
            if idx is None:
                return None
            header_name = layout.header[idx] if idx < len(layout.header) else ""
            return header_name or None

        return cls(
            delimiter=layout.delimiter,
            skip_rows=layout.header_row,
            has_header=True,
            date_column=name(layout.date_column) or layout.date_column,
            description_columns=[name(layout.description_column) or layout.description_column],
            amount_column=name(layout.amount_column) if layout.amount_column is not None else layout.amount_column,
            debit_column=name(layout.debit_column) if layout.debit_column is not None else layout.debit_column,
            credit_column=name(layout.credit_column) if layout.credit_column is not None else layout.credit_column,
            balance_column=name(layout.balance_column) if layout.balance_column is not None else layout.balance_column,
        )


class ProfileParseError(ValueError):
    """The file doesn't match the profile (missing columns, no data rows)."""


def _resolve_ref(ref: ColumnRef | None, header: list[str] | None) -> int | None:
    if ref is None:
        return None
    if isinstance(ref, int):
        return ref
    if header is None:
        raise ProfileParseError(f"Column {ref!r} referenced by name but file has no header")
    lowered = [h.strip().lower() for h in header]
    want = ref.strip().lower()
    if want in lowered:
        return lowered.index(want)
    raise ProfileParseError(f"Column {ref!r} not found in header: {header}")


def _cell(row: list[str], idx: int | None) -> str:
    if idx is None or idx >= len(row):
        return ""
    return (row[idx] or "").strip()


def _parse_profile_date(value: str, config: ImportProfileConfig) -> date | None:
    if config.date_format:
        try:
            return datetime.strptime(value.strip(), config.date_format).date()
        except ValueError:
            return None
    if config.day_first:
        return _parse_date(value)
    if not value:
        return None
    try:
        return dateparser.parse(value, dayfirst=False, fuzzy=True).date()
    except (ValueError, TypeError, OverflowError):
        return None


def parse_with_profile(content: bytes, config: ImportProfileConfig) -> tuple[list[ParsedRow], int]:
    """Return (rows, skipped). Raises ProfileParseError when the file shape
    doesn't match the profile at all — callers fall back to the heuristic."""
    text = content.decode(config.encoding, errors="replace")
    lines = text.splitlines()[config.skip_rows :]
    if not lines:
        raise ProfileParseError("File has no rows after skip_rows")

    delimiter = config.delimiter
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff("\n".join(lines[:50]), delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","

    reader = csv.reader(StringIO("\n".join(lines)), delimiter=delimiter)
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        raise ProfileParseError("File has no data rows")

    header: list[str] | None = None
    if config.has_header:
        header, rows = rows[0], rows[1:]

    date_idx = _resolve_ref(config.date_column, header)
    desc_idxs = [_resolve_ref(ref, header) for ref in config.description_columns]
    amount_idx = _resolve_ref(config.amount_column, header)
    debit_idx = _resolve_ref(config.debit_column, header)
    credit_idx = _resolve_ref(config.credit_column, header)
    ext_id_idx = _resolve_ref(config.external_id_column, header)
    # A stale profile whose balance column vanished must not fail the import —
    # balance capture is best-effort, unlike the load-bearing columns above.
    try:
        balance_idx = _resolve_ref(config.balance_column, header)
    except ProfileParseError:
        balance_idx = None

    parsed: list[ParsedRow] = []
    skipped = 0
    for raw in rows:
        posted = _parse_profile_date(_cell(raw, date_idx), config)
        description = " — ".join(p for p in (_cell(raw, i) for i in desc_idxs) if p)
        if posted is None or not description:
            skipped += 1
            continue

        amount = _parse_decimal(_cell(raw, amount_idx)) if amount_idx is not None else None
        if amount is None and (debit_idx is not None or credit_idx is not None):
            debit_val = _parse_decimal(_cell(raw, debit_idx))
            credit_val = _parse_decimal(_cell(raw, credit_idx))
            if credit_val is not None and credit_val != 0:
                amount = abs(credit_val)
            elif debit_val is not None:
                amount = -abs(debit_val)
        if amount is None:
            skipped += 1
            continue
        if config.invert_amount:
            amount = -amount

        description = description[:500]
        bank_row_id = _cell(raw, ext_id_idx)
        parsed.append(
            ParsedRow(
                posted_on=posted,
                description=description,
                amount=amount,
                balance=_parse_decimal(_cell(raw, balance_idx)) if balance_idx is not None else None,
                external_id=bank_row_id or f"{posted.isoformat()}|{description[:80]}|{amount}",
            )
        )

    if not parsed and skipped:
        # Every row failed — the profile doesn't fit this file.
        raise ProfileParseError(f"Profile matched no rows ({skipped} skipped)")
    return parsed, skipped
