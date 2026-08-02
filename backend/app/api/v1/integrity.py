"""Statements-as-ground-truth endpoints: coverage gaps, balance-chain
continuity, and read-only replay of the stored original statement files
against the ledger. Nothing here mutates data — drift is reported, and fixing
it (re-importing, editing) stays an explicit user action."""

from dataclasses import asdict
from datetime import date

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import extract, select

from app.core.deps import CurrentUser, DbSession
from app.models.account import Account
from app.models.transaction import Transaction
from app.schemas.integrity import (
    AccountIntegrityOut,
    IntegrityOut,
    ReplayOut,
    ReplayReportOut,
)
from app.services.account_loader import load_account_data
from app.services.ground_truth import (
    LedgerIndex,
    chain_breaks,
    committed_statements,
    month_gaps,
    replay_statement,
    resolve_stored_file,
)
from app.services.import_engine import load_profile_config
from app.services.import_engine.profile import ImportProfileConfig

router = APIRouter(prefix="/integrity", tags=["integrity"])

# The lists are for reading; the counts stay exact. One mis-parsed date must
# not render a thousand gap chips, nor a systematic offset a banner per month.
MAX_GAP_MONTHS = 36
MAX_CHAIN_BREAKS = 6


def _months_in_period(start: date, end: date) -> set[tuple[int, int]]:
    months: set[tuple[int, int]] = set()
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.add((year, month))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return months


@router.get("", response_model=IntegrityOut)
def integrity_summary(current: CurrentUser, db: DbSession) -> IntegrityOut:
    account_data = load_account_data(db, current.id)
    by_account: dict[int, list] = {}
    for s in committed_statements(db, current.id):
        by_account.setdefault(s.account_id, []).append(s)

    # Documented months come from the statements' own periods — a quiet or
    # all-duplicate statement still documents its range. Legacy statements
    # (no stored period) fall back to their linked transactions' months.
    months_by_account: dict[int, set[tuple[int, int]]] = {}
    legacy_ids = []
    for account_id, records in by_account.items():
        months = months_by_account.setdefault(account_id, set())
        for s in records:
            if s.period_start and s.period_end:
                months |= _months_in_period(s.period_start, s.period_end)
            else:
                legacy_ids.append(s.id)
    if legacy_ids:
        legacy_rows = db.execute(
            select(
                Transaction.account_id,
                extract("year", Transaction.posted_on),
                extract("month", Transaction.posted_on),
            )
            .where(Transaction.user_id == current.id, Transaction.statement_import_id.in_(legacy_ids))
            .distinct()
        ).all()
        for account_id, year, month in legacy_rows:
            months_by_account.setdefault(account_id, set()).add((int(year), int(month)))

    out: list[AccountIntegrityOut] = []
    for a in account_data:
        acct_statements = by_account.get(a.id, [])
        months = months_by_account.get(a.id, set())
        first_doc = last_doc = None
        if months:
            (fy, fm), (ly, lm) = min(months), max(months)
            first_doc, last_doc = date(fy, fm, 1), date(ly, lm, 1)
        gaps = month_gaps(months)
        statement_snaps = [(as_of, balance) for as_of, balance, source in a.snapshots if source == "statement"]
        breaks = chain_breaks(statement_snaps, a.txns)
        out.append(
            AccountIntegrityOut(
                account_id=a.id,
                name=a.name,
                currency=a.currency,
                statement_count=len(acct_statements),
                files_missing=sum(1 for s in acct_statements if resolve_stored_file(s) is None),
                first_documented=first_doc,
                last_documented=last_doc,
                missing_months=gaps[:MAX_GAP_MONTHS],
                missing_month_count=len(gaps),
                chain_breaks=[asdict(b) for b in breaks[:MAX_CHAIN_BREAKS]],
                chain_break_count=len(breaks),
            )
        )
    return IntegrityOut(accounts=out)


@router.post("/replay", response_model=ReplayOut)
def replay(current: CurrentUser, db: DbSession, account_id: int | None = None) -> ReplayOut:
    """Re-parse stored originals and diff them against the ledger. The UI
    replays account-by-account so no single request re-parses everything."""
    if account_id is not None:
        account = db.get(Account, account_id)
        if account is None or account.user_id != current.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    statements = committed_statements(db, current.id, account_id)
    accounts = {a.id: a for a in db.scalars(select(Account).where(Account.user_id == current.id))}

    reports: list[ReplayReportOut] = []
    ledger_cache: dict[int, LedgerIndex] = {}
    profile_cache: dict[int, ImportProfileConfig | None] = {}
    for record in statements:
        acct = accounts.get(record.account_id)
        if acct is None:
            continue
        if record.account_id not in ledger_cache:
            rows = db.execute(
                select(Transaction.posted_on, Transaction.amount, Transaction.external_id).where(
                    Transaction.user_id == current.id,
                    Transaction.account_id == record.account_id,
                    Transaction.external_id.is_not(None),
                )
            ).all()
            ledger_cache[record.account_id] = LedgerIndex([(p, amt, ext) for p, amt, ext in rows])
            profile_cache[record.account_id] = load_profile_config(db, record.account_id)
        report = replay_statement(
            record, acct, profile_cache[record.account_id], ledger_cache[record.account_id]
        )
        reports.append(ReplayReportOut(**asdict(report)))

    return ReplayOut(
        files=reports,
        files_ok=sum(1 for r in reports if r.status == "ok"),
        files_with_drift=sum(1 for r in reports if r.status == "drift"),
        files_missing=sum(1 for r in reports if r.status == "file_missing"),
        files_failed=sum(1 for r in reports if r.status == "parse_failed"),
    )
