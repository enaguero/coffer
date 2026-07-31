"""QIF (Quicken Interchange Format) statement parser.

Line-oriented: each record is a set of single-letter-prefixed fields ended by
`^`. We read D (date), T/U (amount), P (payee), M (memo) and ignore the rest.
QIF dates are ambiguous (dd/mm vs mm/dd) — we default to day-first, matching
the UK banks this importer targets.
"""

from __future__ import annotations

from app.services.csv_parser import ParsedRow, _parse_date, _parse_decimal


def parse_qif(content: bytes) -> tuple[list[ParsedRow], int]:
    """Return (rows, skipped_rows)."""
    text = content.decode("utf-8-sig", errors="replace")
    parsed: list[ParsedRow] = []
    skipped = 0
    record: dict[str, str] = {}

    def flush() -> None:
        nonlocal skipped
        if not record:
            return
        # Quicken writes years as 'YY after 2000, e.g. D25/01'24.
        posted = _parse_date(record.get("D", "").replace("'", "/"))
        amount = _parse_decimal(record.get("T") or record.get("U") or "")
        description = " — ".join(p for p in (record.get("P"), record.get("M")) if p)
        if posted is None or amount is None or not description:
            skipped += 1
            return
        description = description[:500]
        parsed.append(
            ParsedRow(
                posted_on=posted,
                description=description,
                amount=amount,
                external_id=f"{posted.isoformat()}|{description[:80]}|{amount}",
            )
        )

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("!"):
            continue
        if line == "^":
            flush()
            record = {}
            continue
        code, value = line[0].upper(), line[1:].strip()
        if code in {"D", "T", "U", "P", "M"} and code not in record:
            record[code] = value
    flush()  # file may omit the trailing ^
    return parsed, skipped
