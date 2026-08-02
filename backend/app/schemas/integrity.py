from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class ChainBreakOut(BaseModel):
    prev_as_of: date
    as_of: date
    attested: Decimal
    expected: Decimal
    delta: Decimal


class AccountIntegrityOut(BaseModel):
    account_id: int
    name: str
    currency: str
    statement_count: int
    files_missing: int
    first_documented: date | None
    last_documented: date | None
    # Lists are capped server-side; the counts are exact.
    missing_months: list[str]
    missing_month_count: int
    chain_breaks: list[ChainBreakOut]
    chain_break_count: int


class IntegrityOut(BaseModel):
    accounts: list[AccountIntegrityOut]


class RowDiffOut(BaseModel):
    external_id: str
    posted_on: date
    description: str
    amount: Decimal
    ledger_posted_on: date | None = None
    ledger_amount: Decimal | None = None


class ReplayReportOut(BaseModel):
    statement_id: int
    account_id: int
    filename: str
    status: str  # "ok" | "drift" | "file_missing" | "parse_failed"
    source: str  # which parser layer replayed the file
    parsed_rows: int
    matched: int
    missing_count: int
    altered_count: int
    skipped: int  # rows the user deselected at preview-confirm — not drift
    # Capped at MAX_DETAIL_ROWS server-side; the counts above are exact.
    missing_from_ledger: list[RowDiffOut]
    altered: list[RowDiffOut]
    error: str | None = None


class ReplayOut(BaseModel):
    files: list[ReplayReportOut]
    files_ok: int
    files_with_drift: int
    files_missing: int
    files_failed: int
