"""Statements as ground truth: replay stored originals against the ledger,
map statement coverage gaps, and check balance-chain continuity.

The stored statement files are the source documents; the ledger is derived
state that can drift — rows deleted or edited after import, parser changes,
never-confirmed previews. Everything here is read-only: replay re-parses the
original bytes with the current parser stack and reports differences, it never
mutates the ledger.

Honesty limits, by design:
- Replay compares dates and amounts (quantized to 2dp, the ledger's scale);
  descriptions are not compared — synthesized external_ids embed them, so a
  description edit re-keys the row instead.
- When an external_id doesn't match (typically because a saved profile or bank
  preset now parses the file through a different layer than the original
  import), a row is still matched by (date, amount) before being called
  missing — parser evolution must not read as data loss.
- Rows the user deselected at preview-confirm are recorded on the statement
  and reported as skipped, never as drift.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.account import Account
from app.models.statement import StatementImport, StatementImportStatus
from app.services.analytics.fx import TWO_DP
from app.services.import_engine import resolve_and_parse

# Detail rows are capped per statement; counts stay exact.
MAX_DETAIL_ROWS = 20


# ---------------------------------------------------------------- pure checks


def month_gaps(documented_months: set[tuple[int, int]]) -> list[str]:
    """Months with no statement-documented activity strictly between the first
    and last documented month, as "YYYY-MM" strings."""
    if len(documented_months) < 2:
        return []
    idx = {y * 12 + (m - 1) for y, m in documented_months}
    return [f"{i // 12:04d}-{i % 12 + 1:02d}" for i in range(min(idx) + 1, max(idx)) if i not in idx]


@dataclass
class ChainBreak:
    prev_as_of: date
    as_of: date
    attested: Decimal
    expected: Decimal
    delta: Decimal  # attested minus expected — money the ledger can't explain


def _chain_breaks_one_convention(
    snapshots: list[tuple[date, Decimal]],
    day_totals: dict[date, Decimal],
    days: list[date],
    prefix: list[Decimal],
) -> list[ChainBreak]:
    def between_exclusive(a: date, b: date) -> Decimal:
        # Sum of txns strictly between the two dates.
        lo, hi = bisect_right(days, a), bisect_left(days, b)
        return prefix[hi] - prefix[lo]

    breaks: list[ChainBreak] = []
    for (prev_on, prev_bal), (curr_on, curr_bal) in zip(snapshots, snapshots[1:], strict=False):
        between = between_exclusive(prev_on, curr_on)
        prev_day = day_totals.get(prev_on, Decimal("0"))
        curr_day = day_totals.get(curr_on, Decimal("0"))
        # Statements can cut mid-day, so boundary-day transactions may fall on
        # either side of an attestation. Accept the chain if ANY assignment of
        # the two boundary days explains the next balance; report the break
        # against the end-of-day reading (prev day already counted, curr day
        # counted) when none does.
        candidates = [
            prev_bal + between + (prev_day if a else Decimal("0")) + (curr_day if b else Decimal("0"))
            for a in (False, True)
            for b in (False, True)
        ]
        if any(abs(curr_bal - c) < TWO_DP for c in candidates):
            continue
        expected = (prev_bal + between + curr_day).quantize(TWO_DP)
        breaks.append(
            ChainBreak(
                prev_as_of=prev_on,
                as_of=curr_on,
                attested=curr_bal,
                expected=expected,
                delta=(curr_bal - expected).quantize(TWO_DP),
            )
        )
    return breaks


def chain_breaks(
    snapshots: list[tuple[date, Decimal]], txns: list[tuple[date, Decimal]]
) -> list[ChainBreak]:
    """Consecutive statement attestations must chain: the next statement's
    balance should equal the previous one plus the ledger activity between
    them. A non-zero delta pinpoints WHERE data is missing — between these two
    dates — which the single net-worth drift number cannot do.

    Card statements often attest the balance in bank convention (positive =
    owed) while the ledger stores charges negative; the check is run under
    both sign readings of the attested balances and the cleaner one wins —
    a sign convention must never read as missing money."""
    if len(snapshots) < 2:
        return []
    ordered = sorted(snapshots)
    day_totals: dict[date, Decimal] = {}
    for on, amt in txns:
        day_totals[on] = day_totals.get(on, Decimal("0")) + amt
    days = sorted(day_totals)
    prefix = [Decimal("0")]
    for d in days:
        prefix.append(prefix[-1] + day_totals[d])

    as_is = _chain_breaks_one_convention(ordered, day_totals, days, prefix)
    if not as_is:
        return []
    negated = _chain_breaks_one_convention(
        [(on, -bal) for on, bal in ordered], day_totals, days, prefix
    )
    return negated if len(negated) < len(as_is) else as_is


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
    source: str = ""  # which parser layer replayed the file
    parsed_rows: int = 0
    matched: int = 0
    missing_count: int = 0
    altered_count: int = 0
    skipped: int = 0  # rows the user deselected at preview-confirm
    missing_from_ledger: list[RowDiff] = field(default_factory=list)
    altered: list[RowDiff] = field(default_factory=list)
    error: str | None = None


class LedgerIndex:
    """The account's external_id'd transactions, indexed for consume-based
    matching: each ledger row can satisfy at most one parsed row per file, so
    two identical statement rows deduped to one ledger row report the second
    as missing instead of double-matching (an under-import is real drift)."""

    def __init__(self, rows: list[tuple[date, Decimal, str]]):
        # rows: (posted_on, amount, external_id) — amounts already 2dp.
        self.rows = rows
        self.by_external_id: dict[str, list[int]] = {}
        self.by_date_amount: dict[tuple[date, Decimal], list[int]] = {}
        for i, (posted_on, amount, external_id) in enumerate(rows):
            self.by_external_id.setdefault(external_id, []).append(i)
            self.by_date_amount.setdefault((posted_on, amount), []).append(i)

    def take(self, candidates: list[int], consumed: set[int]) -> int | None:
        for i in candidates:
            if i not in consumed:
                consumed.add(i)
                return i
        return None


def resolve_stored_file(record: StatementImport) -> Path | None:
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
    ledger: LedgerIndex,
) -> ReplayReport:
    report = ReplayReport(
        statement_id=record.id,
        account_id=record.account_id,
        filename=record.filename,
        status="ok",
    )
    path = resolve_stored_file(record)
    if path is None:
        report.status = "file_missing"
        report.error = "stored original not found on disk"
        return report
    try:
        content = path.read_bytes()
    except OSError:
        report.status = "file_missing"
        report.error = "stored original could not be read"
        return report

    suffix = Path(record.filename).suffix.lower() or f".{record.format.value}"
    try:
        outcome = resolve_and_parse(content, suffix, account, profile_config)
    except Exception as exc:  # noqa: BLE001 — malformed files raise arbitrary parser errors
        report.status = "parse_failed"
        # Class name only: exception text can carry server paths.
        report.error = f"re-parse raised {exc.__class__.__name__}"
        return report

    report.source = outcome.source
    report.parsed_rows = len(outcome.rows)
    if not outcome.rows and record.rows_parsed > 0:
        # Parsers degrade to zero rows instead of raising — a file that once
        # yielded rows and now yields none did NOT verify clean.
        report.status = "parse_failed"
        report.error = f"re-parse produced 0 rows; the original import parsed {record.rows_parsed}"
        return report

    skipped_ids = set(record.skipped_external_ids or [])
    consumed: set[int] = set()
    for row in outcome.rows:
        amount = row.amount.quantize(TWO_DP)
        if row.external_id and row.external_id in skipped_ids:
            report.skipped += 1
            continue
        idx = ledger.take(ledger.by_external_id.get(row.external_id or "", []), consumed)
        if idx is None:
            # A profile/preset saved after the original import re-keys
            # synthesized ids; (date, amount) still identifies the row.
            idx = ledger.take(ledger.by_date_amount.get((row.posted_on, amount), []), consumed)
            if idx is not None:
                report.matched += 1
                continue
            report.missing_count += 1
            if len(report.missing_from_ledger) < MAX_DETAIL_ROWS:
                report.missing_from_ledger.append(
                    RowDiff(
                        external_id=row.external_id or "",
                        posted_on=row.posted_on,
                        description=row.description,
                        amount=amount,
                    )
                )
            continue
        ledger_posted_on, ledger_amount, _ = ledger.rows[idx]
        if ledger_posted_on != row.posted_on or ledger_amount != amount:
            report.altered_count += 1
            if len(report.altered) < MAX_DETAIL_ROWS:
                report.altered.append(
                    RowDiff(
                        external_id=row.external_id or "",
                        posted_on=row.posted_on,
                        description=row.description,
                        amount=amount,
                        ledger_posted_on=ledger_posted_on,
                        ledger_amount=ledger_amount,
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
