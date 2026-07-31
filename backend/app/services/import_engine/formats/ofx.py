"""Lenient OFX/QFX statement parser.

OFX 1.x is SGML (leaf tags have no closers), 2.x is XML. Both wrap each
transaction in <STMTTRN>...</STMTTRN>, so instead of a full document parse we
extract those blocks and read leaf values with a tolerant regex. This survives
the many almost-valid files banks actually produce.

OFX is the best import source when a bank offers it: <FITID> is the bank's own
stable transaction id, which makes dedup exact instead of synthesized.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from app.services.csv_parser import ParsedRow, _parse_decimal

_STMTTRN_RE = re.compile(r"<STMTTRN>(.*?)(?=</STMTTRN>|<STMTTRN>|</BANKTRANLIST>|\Z)", re.S | re.I)
_LEDGERBAL_RE = re.compile(r"<LEDGERBAL>(.*?)(?=</LEDGERBAL>|<AVAILBAL>|\Z)", re.S | re.I)
# Leaf value: everything after <TAG> up to the next tag or line end.
_LEAF_RES = {
    tag: re.compile(rf"<{tag}>\s*([^<\r\n]+)", re.I)
    for tag in ("DTPOSTED", "TRNAMT", "NAME", "MEMO", "FITID", "BALAMT", "DTASOF")
}


def _leaf(block: str, tag: str) -> str | None:
    m = _LEAF_RES[tag].search(block)
    if not m:
        return None
    value = m.group(1).strip()
    return value or None


def _parse_ofx_date(value: str) -> date | None:
    # DTPOSTED looks like 20240115, 20240115120000, or 20240115120000[-5:EST].
    digits = value.strip()[:8]
    if len(digits) != 8 or not digits.isdigit():
        return None
    try:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        return None


def extract_ofx_ledger_balance(content: bytes) -> tuple[date, Decimal] | None:
    """The statement's own closing balance (<LEDGERBAL>), when present.

    OFX files attest the account balance directly — better than deriving it
    from transactions, since it holds even when history is incomplete.
    """
    text = content.decode("utf-8-sig", errors="replace")
    m = _LEDGERBAL_RE.search(text)
    if not m:
        return None
    block = m.group(1)
    amount = _parse_decimal(_leaf(block, "BALAMT") or "")
    as_of = _parse_ofx_date(_leaf(block, "DTASOF") or "")
    if amount is None or as_of is None:
        return None
    return as_of, amount


def parse_ofx(content: bytes) -> tuple[list[ParsedRow], int]:
    """Return (rows, skipped_rows)."""
    text = content.decode("utf-8-sig", errors="replace")
    parsed: list[ParsedRow] = []
    skipped = 0
    for match in _STMTTRN_RE.finditer(text):
        block = match.group(1)
        posted = _parse_ofx_date(_leaf(block, "DTPOSTED") or "")
        amount = _parse_decimal(_leaf(block, "TRNAMT") or "")
        name = _leaf(block, "NAME")
        memo = _leaf(block, "MEMO")
        description = " — ".join(p for p in (name, memo) if p)
        if posted is None or amount is None or not description:
            skipped += 1
            continue
        description = description[:500]
        fitid = _leaf(block, "FITID")
        parsed.append(
            ParsedRow(
                posted_on=posted,
                description=description,
                amount=amount,
                external_id=fitid or f"{posted.isoformat()}|{description[:80]}|{amount}",
            )
        )
    return parsed, skipped
