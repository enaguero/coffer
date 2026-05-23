"""Bank-sync orchestration.

Takes a BankConnection and a set of Accounts, calls the provider for new
transactions per account, dedupes against existing rows by `external_id`, and
persists. Per-account state is recorded on `SyncJob` rows so the UI can show
running/success/failed with counts.

Concurrency note: each sync runs from a fresh DB session because BackgroundTasks
fire after the originating request's session has closed. The orchestrator owns
its session lifecycle here.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.account import Account
from app.models.bank_connection import BankConnection, BankConnectionStatus
from app.models.sync_job import SyncJob, SyncJobStatus
from app.models.transaction import Transaction
from app.services.bank_providers.base import (
    BankProvider,
    BankProviderError,
    NormalizedTxn,
)

log = logging.getLogger(__name__)

# Re-fetch this many days behind the latest known txn on each sync, so
# delayed-booking transactions get picked up without creating duplicates
# (dedup catches them).
LOOKBACK_DAYS = 3
# Hard floor for first sync of a new account — GoCardless's default agreement
# allows up to 90 days of history.
DEFAULT_HISTORY_DAYS = 90


def _synth_external_id(txn: NormalizedTxn) -> str:
    """Same `date|desc|amount` formula as services/csv_parser.py so a transaction
    that arrives once via upload and once via sync still deduplicates."""
    return f"{txn.posted_on.isoformat()}|{txn.description[:80]}|{txn.amount}"


def _resolve_date_range(db: Session, account_id: int) -> tuple[date, date]:
    today = date.today()
    latest = db.scalar(
        select(Transaction.posted_on)
        .where(Transaction.account_id == account_id)
        .order_by(Transaction.posted_on.desc())
        .limit(1)
    )
    if latest is None:
        return today - timedelta(days=DEFAULT_HISTORY_DAYS), today
    return max(latest - timedelta(days=LOOKBACK_DAYS), today - timedelta(days=DEFAULT_HISTORY_DAYS)), today


def _existing_external_ids(db: Session, user_id: int, account_id: int) -> set[str]:
    return set(
        db.scalars(
            select(Transaction.external_id).where(
                Transaction.user_id == user_id,
                Transaction.account_id == account_id,
                Transaction.external_id.isnot(None),
            )
        )
    )


async def _sync_account(
    db: Session,
    *,
    provider: BankProvider,
    job: SyncJob,
    account: Account,
) -> None:
    if not account.external_account_id:
        raise BankProviderError(
            f"Account {account.id} is not linked to an external bank account"
        )

    date_from, date_to = _resolve_date_range(db, account.id)
    txns = await provider.fetch_transactions(
        external_account_id=account.external_account_id,
        date_from=date_from,
        date_to=date_to,
    )
    job.transactions_fetched = len(txns)

    existing = _existing_external_ids(db, account.user_id, account.id)
    imported = 0
    for t in txns:
        ext_id = t.bank_transaction_id or _synth_external_id(t)
        if ext_id in existing:
            continue
        db.add(
            Transaction(
                user_id=account.user_id,
                account_id=account.id,
                category_id=None,
                statement_import_id=None,
                posted_on=t.posted_on,
                description=t.description,
                amount=t.amount,
                external_id=ext_id,
            )
        )
        existing.add(ext_id)
        imported += 1
    job.transactions_imported = imported


async def run_sync(
    *,
    user_id: int,
    bank_connection_id: int,
    account_ids: list[int],
    provider: BankProvider,
) -> list[int]:
    """Run a sync for the given accounts under the given connection.

    Spins up its own DB session because this is intended to be called from a
    BackgroundTask (after the originating request closes its session). Returns
    the IDs of the SyncJob rows it wrote (one per account).
    """
    db = SessionLocal()
    job_ids: list[int] = []
    try:
        connection = db.get(BankConnection, bank_connection_id)
        if connection is None or connection.user_id != user_id:
            log.warning(
                "run_sync: bank_connection %s missing or not owned by user %s",
                bank_connection_id,
                user_id,
            )
            return job_ids
        if connection.status != BankConnectionStatus.LINKED:
            log.info(
                "run_sync: skipping connection %s in status %s",
                bank_connection_id,
                connection.status,
            )
            return job_ids

        accounts = list(
            db.scalars(
                select(Account).where(
                    Account.user_id == user_id,
                    Account.bank_connection_id == bank_connection_id,
                    Account.id.in_(account_ids),
                )
            )
        )

        for account in accounts:
            job = SyncJob(
                user_id=user_id,
                bank_connection_id=bank_connection_id,
                account_id=account.id,
                status=SyncJobStatus.RUNNING,
            )
            db.add(job)
            db.flush()
            try:
                await _sync_account(db, provider=provider, job=job, account=account)
                job.status = SyncJobStatus.SUCCESS
            except Exception as exc:  # noqa: BLE001 — capture-all is intentional
                log.exception("sync failed for account %s", account.id)
                job.status = SyncJobStatus.FAILED
                job.error_message = str(exc)[:1000]
            finally:
                job.completed_at = datetime.now(UTC)
                db.commit()
                job_ids.append(job.id)
    finally:
        db.close()
    return job_ids


def run_sync_sync(
    *,
    user_id: int,
    bank_connection_id: int,
    account_ids: list[int],
    provider: BankProvider,
) -> list[int]:
    """Blocking wrapper for BackgroundTasks (which runs sync callables in a
    thread). Avoids leaking `asyncio.run` semantics into the router."""
    return asyncio.run(
        run_sync(
            user_id=user_id,
            bank_connection_id=bank_connection_id,
            account_ids=account_ids,
            provider=provider,
        )
    )
