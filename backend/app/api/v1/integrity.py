"""Statements-as-ground-truth endpoints: coverage gaps, balance-chain
continuity, and read-only replay of the stored original statement files
against the ledger. Nothing here mutates data — drift is reported, and fixing
it (re-importing, editing) stays an explicit user action."""

from dataclasses import asdict
from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import extract, select

from app.core.config import settings
from app.core.deps import CurrentUser, DbSession
from app.models.account import Account
from app.models.balance_snapshot import BalanceSnapshot, BalanceSource
from app.models.transaction import Transaction
from app.schemas.integrity import (
    AccountIntegrityOut,
    IntegrityOut,
    ReplayOut,
    ReplayReportOut,
)
from app.services.ground_truth import (
    chain_breaks,
    committed_statements,
    month_gaps,
    replay_statement,
)
from app.services.import_engine import load_profile_config

router = APIRouter(prefix="/integrity", tags=["integrity"])


def _stored_file_exists(stored_path: str, user_id: int, upload_dir: str) -> bool:
    path = Path(stored_path)
    return path.is_file() or (Path(upload_dir) / str(user_id) / path.name).is_file()


@router.get("", response_model=IntegrityOut)
def integrity_summary(current: CurrentUser, db: DbSession) -> IntegrityOut:
    accounts = list(db.scalars(select(Account).where(Account.user_id == current.id).order_by(Account.name)))
    statements = committed_statements(db, current.id)
    by_account: dict[int, list] = {}
    for s in statements:
        by_account.setdefault(s.account_id, []).append(s)

    # Statement-documented months per account, from rows that came in via an
    # import (manually keyed transactions don't document coverage).
    documented_rows = db.execute(
        select(
            Transaction.account_id,
            extract("year", Transaction.posted_on),
            extract("month", Transaction.posted_on),
        )
        .where(Transaction.user_id == current.id, Transaction.statement_import_id.is_not(None))
        .distinct()
    ).all()
    months_by_account: dict[int, set[tuple[int, int]]] = {}
    for account_id, year, month in documented_rows:
        months_by_account.setdefault(account_id, set()).add((int(year), int(month)))

    snaps = db.execute(
        select(BalanceSnapshot.account_id, BalanceSnapshot.as_of, BalanceSnapshot.balance)
        .where(BalanceSnapshot.user_id == current.id, BalanceSnapshot.source == BalanceSource.STATEMENT)
        .order_by(BalanceSnapshot.as_of)
    ).all()
    snaps_by_account: dict[int, list] = {}
    for account_id, as_of, balance in snaps:
        snaps_by_account.setdefault(account_id, []).append((as_of, balance))

    txns = db.execute(
        select(Transaction.account_id, Transaction.posted_on, Transaction.amount).where(
            Transaction.user_id == current.id
        )
    ).all()
    txns_by_account: dict[int, list] = {}
    for account_id, posted_on, amount in txns:
        txns_by_account.setdefault(account_id, []).append((posted_on, amount))

    out: list[AccountIntegrityOut] = []
    for a in accounts:
        acct_statements = by_account.get(a.id, [])
        months = months_by_account.get(a.id, set())
        first_last: tuple[date | None, date | None] = (None, None)
        if months:
            (fy, fm), (ly, lm) = min(months), max(months)
            first_last = (date(fy, fm, 1), date(ly, lm, 1))
        out.append(
            AccountIntegrityOut(
                account_id=a.id,
                name=a.name,
                currency=a.currency,
                statement_count=len(acct_statements),
                files_missing=sum(
                    1
                    for s in acct_statements
                    if not _stored_file_exists(s.stored_path, current.id, settings.upload_dir)
                ),
                first_documented=first_last[0],
                last_documented=first_last[1],
                missing_months=month_gaps(months),
                chain_breaks=[
                    asdict(b)
                    for b in chain_breaks(snaps_by_account.get(a.id, []), txns_by_account.get(a.id, []))
                ],
            )
        )
    return IntegrityOut(accounts=out)


@router.post("/replay", response_model=ReplayOut)
def replay(current: CurrentUser, db: DbSession, account_id: int | None = None) -> ReplayOut:
    if account_id is not None:
        account = db.get(Account, account_id)
        if account is None or account.user_id != current.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    statements = committed_statements(db, current.id, account_id)
    accounts = {a.id: a for a in db.scalars(select(Account).where(Account.user_id == current.id))}

    reports: list[ReplayReportOut] = []
    ledger_cache: dict[int, dict[str, Transaction]] = {}
    profile_cache: dict[int, object] = {}
    for record in statements:
        acct = accounts.get(record.account_id)
        if acct is None:
            continue
        if record.account_id not in ledger_cache:
            rows = db.scalars(
                select(Transaction).where(
                    Transaction.user_id == current.id,
                    Transaction.account_id == record.account_id,
                    Transaction.external_id.is_not(None),
                )
            )
            ledger_cache[record.account_id] = {t.external_id: t for t in rows}
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
