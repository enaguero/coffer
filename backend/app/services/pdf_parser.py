"""Heuristic PDF statement parser.

PDF statements are unreliable; we attempt table extraction first, then fall
back to regex-line parsing. Both approaches look for a date at the start of a
line and an amount at the end. Anything ambiguous is skipped.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from io import BytesIO

import pdfplumber

from app.services.csv_parser import ParsedRow, _parse_date, _parse_decimal

# Match a date at line start (1-2 digit day, 1-2 digit month, 2-4 digit year, common separators)
DATE_RE = re.compile(r"^\s*(\d{1,2}[/\-\.\s][A-Za-z0-9]{1,9}[/\-\.\s]\d{2,4})")
# Money at end of line: -123.45 / 1,234.56 / (123.45)
AMOUNT_RE = re.compile(r"(-?\(?\$?\s?-?\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})\)?)\s*$")


def parse_pdf(content: bytes) -> tuple[list[ParsedRow], int]:
    parsed: list[ParsedRow] = []
    skipped = 0

    with pdfplumber.open(BytesIO(content)) as pdf:
        for page in pdf.pages:
            tables = []
            try:
                tables = page.extract_tables() or []
            except Exception:
                tables = []

            for table in tables:
                if not table or len(table) < 2:
                    continue
                header = [(c or "").strip().lower() for c in table[0]]
                date_idx = next((i for i, h in enumerate(header) if "date" in h or "fecha" in h), None)
                desc_idx = next(
                    (i for i, h in enumerate(header) if "desc" in h or "detail" in h or "concept" in h), None
                )
                amt_idx = next(
                    (i for i, h in enumerate(header) if "amount" in h or "monto" in h or "importe" in h), None
                )
                if date_idx is None or desc_idx is None or amt_idx is None:
                    continue
                for row in table[1:]:
                    if not row or any(cell is None for cell in (row[date_idx], row[desc_idx], row[amt_idx])):
                        skipped += 1
                        continue
                    posted = _parse_date(row[date_idx])
                    amount = _parse_decimal(row[amt_idx])
                    desc = (row[desc_idx] or "").strip()
                    if posted is None or amount is None or not desc:
                        skipped += 1
                        continue
                    parsed.append(
                        ParsedRow(
                            posted_on=posted,
                            description=desc[:500],
                            amount=amount,
                            external_id=f"{posted.isoformat()}|{desc[:80]}|{amount}",
                        )
                    )

            if not tables:
                text = page.extract_text() or ""
                for line in text.splitlines():
                    date_match = DATE_RE.match(line)
                    amount_match = AMOUNT_RE.search(line)
                    if not (date_match and amount_match):
                        continue
                    posted = _parse_date(date_match.group(1))
                    amount = _parse_decimal(amount_match.group(1))
                    if posted is None or amount is None:
                        skipped += 1
                        continue
                    desc = line[date_match.end() : amount_match.start()].strip()
                    if not desc:
                        skipped += 1
                        continue
                    parsed.append(
                        ParsedRow(
                            posted_on=posted,
                            description=desc[:500],
                            amount=amount,
                            external_id=f"{posted.isoformat()}|{desc[:80]}|{amount}",
                        )
                    )
    return parsed, skipped


# Re-export to silence unused-import warning on InvalidOperation; kept for parity.
__all__ = ["parse_pdf", "Decimal", "InvalidOperation"]
