"""Statements as ground truth: replay stored originals against the ledger,
map statement coverage gaps, and check balance-chain continuity.

The stored statement files are the source documents; the ledger is derived
state that can drift — rows deleted or edited after import, parser changes,
never-confirmed previews. Everything here is read-only: replay re-parses the
original bytes with the current parser stack and reports differences, it never
mutates the ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.account import Account
from app.models.statement import StatementImport, StatementImportStatus
from app.models.transaction import Transaction
from app.services.import_engine import resolve_and_parse

TWO_DP = Decimal("0.01")
# Detail rows are capped per statement; counts stay exact.
MAX_DETAIL_ROWS = 20


# ---------------------------------------------------------------- pure checks


def month_gaps(documented_months: set[tuple[int, int]]) -> list[str]:
    """Months with no statement-documented activity between the first and last
    documented month, as "YYYY-MM" strings."""
    if len(documented_months) < 2:
        return []
    ordered = sorted(documented_months)
    (year, month), last = ordered[0], ordered[-1]
    gaps: list[str] = []
    while (year, month) < last:
        month += 1
        if month == 13:
            year, month = year + 1, 1
        if (year, month) != last and (year, month) not in documented_months:
            gaps.append(f"{year:04d}-{month:02d}")
    return gaps


@dataclass
class ChainBreak:
    prev_as_of: date
    as_of: date
    prev_balance: Decimal
    attested: Decimal
    expected: Decimal
    delta: Decimal  # attested minus expected — money the ledger can't explain


def chain_breaks(
    snapshots: list[tuple[date, Decimal]], txns: list[tuple[date, Decimal]]
) -> list[ChainBreak]:
    """Consecutive statement attestations must chain: the next statement's
    balance should equal the previous one plus the ledger activity between
    them. A non-zero delta pinpoints WHERE data is missing — between these two
    dates — which the single net-worth drift number cannot do.

    `snapshots` are (as_of, balance) statement attestations, `txns` are the
    account's (posted_on, amount), both in any order."""
    ordered = sorted(snapshots)
    breaks: list[ChainBreak] = []
    for (prev_on, prev_bal), (curr_on, curr_bal) in zip(ordered, ordered[1:], strict=False):
        between = sum((amt for on, amt in txns if prev_on < on <= curr_on), Decimal("0"))
        expected = (prev_bal + between).quantize(TWO_DP)
        delta = (curr_bal - expected).quantize(TWO_DP)
        if abs(delta) >= TWO_DP:
            breaks.append(
                ChainBreak(
                    prev_as_of=prev_on,
                    as_of=curr_on,
                    prev_balance=prev_bal,
                    attested=curr_bal,
                    expected=expected,
                    delta=delta,
                )
            )
    return breaks


# ---------------------------------------------------------------- file replay


@dataclass
class RowDiff:
    external_id: str
    posted_on: date
    description: str
    amount: Decimal
    # For altered rows: what the ledger has instead.
    ledger_posted_on: date | None = None
    ledger_amount: Decimal | None = None


@dataclass
class ReplayReport:
    statement_id: int
    account_id: int
    filename: str
    # "ok" | "drift" | "file_missing" | "parse_failed"
    status: str
    parsed_rows: int = 0
    matched: int = 0
    missing_count: int = 0
    altered_count: int = 0
    unverifiable: int = 0  # parsed rows without an external_id (can't be diffed)
    missing_from_ledger: list[RowDiff] = field(default_factory=list)
    altered: list[RowDiff] = field(default_factory=list)
    error: str | None = None


def _resolve_stored_file(record: StatementImport) -> Path | None:
    """The DB stores the absolute path at write time; if the upload dir moved
    (docker vs host, restored backup), fall back to the current setting."""
    path = Path(record.stored_path)
    if path.is_file():
        return path
    fallback = Path(settings.upload_dir) / str(record.user_id) / path.name
    return fallback if fallback.is_file() else None


def replay_statement(
    record: StatementImport,
    account: Account,
    profile_config,
    ledger_by_external_id: dict[str, Transaction],
) -> ReplayReport:
    report = ReplayReport(
        statement_id=record.id,
        account_id=record.account_id,
        filename=record.filename,
        status="ok",
    )
    path = _resolve_stored_file(record)
    if path is None:
        report.status = "file_missing"
        report.error = "stored original not found on disk"
        return report

    suffix = Path(record.filename).suffix.lower() or f".{record.format.value}"
    try:
        outcome = resolve_and_parse(path.read_bytes(), suffix, account, profile_config)
    except Exception as exc:  # a source document that no longer parses IS the finding
        report.status = "parse_failed"
        report.error = f"{exc.__class__.__name__}: {exc}"
        return report

    report.parsed_rows = len(outcome.rows)
    for row in outcome.rows:
        if not row.external_id:
            report.unverifiable += 1
            continue
        txn = ledger_by_external_id.get(row.external_id)
        if txn is None:
            report.missing_count += 1
            if len(report.missing_from_ledger) < MAX_DETAIL_ROWS:
                report.missing_from_ledger.append(
                    RowDiff(
                        external_id=row.external_id,
                        posted_on=row.posted_on,
                        description=row.description,
                        amount=row.amount,
                    )
                )
        elif txn.posted_on != row.posted_on or txn.amount != row.amount:
            report.altered_count += 1
            if len(report.altered) < MAX_DETAIL_ROWS:
                report.altered.append(
                    RowDiff(
                        external_id=row.external_id,
                        posted_on=row.posted_on,
                        description=row.description,
                        amount=row.amount,
                        ledger_posted_on=txn.posted_on,
                        ledger_amount=txn.amount,
                    )
                )
        else:
            report.matched += 1

    if report.missing_count or report.altered_count:
        report.status = "drift"
    return report


def committed_statements(db: Session, user_id: int, account_id: int | None = None) -> list[StatementImport]:
    query = (
        select(StatementImport)
        .where(
            StatementImport.user_id == user_id,
            StatementImport.status == StatementImportStatus.COMMITTED,
        )
        .order_by(StatementImport.account_id, StatementImport.id)
    )
    if account_id is not None:
        query = query.where(StatementImport.account_id == account_id)
    return list(db.scalars(query))
