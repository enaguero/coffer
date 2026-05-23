"""Bank-sync orchestrator tests.

We exercise the orchestrator with a stub BankProvider that returns a
deterministic list of NormalizedTxn rows, plus a real test DB session. The HTTP
layer is not exercised here — see test_gocardless_provider.py for that.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

import app.core.database as database
from app.models.account import Account, AccountType
from app.models.bank_connection import BankConnection, BankConnectionStatus, BankProvider
from app.models.sync_job import SyncJob, SyncJobStatus
from app.models.transaction import Transaction
from app.models.user import User
from app.services.bank_providers.base import NormalizedTxn
from app.services.bank_sync import run_sync_sync


class _StubProvider:
    """A bare-bones BankProvider stand-in used by the orchestrator."""

    def __init__(self, txns: list[NormalizedTxn]) -> None:
        self._txns = txns
        self.calls: list[tuple[str, date, date]] = []

    async def fetch_transactions(
        self, *, external_account_id: str, date_from: date, date_to: date
    ) -> list[NormalizedTxn]:
        self.calls.append((external_account_id, date_from, date_to))
        return self._txns

    # Unused by the orchestrator but kept so the Protocol shape is satisfied.
    async def list_institutions(self, country: str):  # pragma: no cover
        return []

    async def create_link(self, **_kwargs):  # pragma: no cover
        raise NotImplementedError

    async def complete_link(self, requisition_id: str):  # pragma: no cover
        return []

    async def revoke(self, requisition_id: str) -> None:  # pragma: no cover
        return None


@pytest.fixture()
def linked_setup(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> tuple[int, int, int]:
    """A user with a LINKED BankConnection and one mapped Account.

    The orchestrator opens a fresh session via SessionLocal — we redirect that
    factory to the test's session so SAVEPOINT rollback still covers the work.
    """

    class _FakeSessionFactory:
        def __call__(self) -> Session:
            return _NoCloseSession(db_session)

    class _NoCloseSession:
        """Proxy that delegates to the test session and swallows .close().

        The orchestrator calls `db.close()` at the end; we don't want that to
        close the long-lived test connection. Every other method is delegated.
        """

        def __init__(self, inner: Session) -> None:
            self._inner = inner

        def __getattr__(self, item: str):
            return getattr(self._inner, item)

        def close(self) -> None:  # noqa: A003
            pass

    monkeypatch.setattr(database, "SessionLocal", _FakeSessionFactory())
    import app.services.bank_sync as bank_sync

    monkeypatch.setattr(bank_sync, "SessionLocal", _FakeSessionFactory())

    user = User(email="sync@coffer.dev", full_name=None, hashed_password="x")
    db_session.add(user)
    db_session.flush()

    connection = BankConnection(
        user_id=user.id,
        provider=BankProvider.GOCARDLESS,
        institution_id="SBOX_BF01",
        institution_name="Sandbox Finance",
        requisition_id="req-1",
        agreement_id="agree-1",
        requisition_expires_at=datetime.now(UTC) + timedelta(days=89),
        status=BankConnectionStatus.LINKED,
    )
    db_session.add(connection)
    db_session.flush()

    account = Account(
        user_id=user.id,
        name="HSBC Current",
        type=AccountType.CHECKING,
        currency="GBP",
        bank_connection_id=connection.id,
        external_account_id="acc-1",
    )
    db_session.add(account)
    db_session.commit()
    return user.id, connection.id, account.id


def test_sync_imports_new_txns_and_dedupes_subsequent_runs(
    db_session: Session, linked_setup: tuple[int, int, int]
) -> None:
    user_id, conn_id, account_id = linked_setup
    txns = [
        NormalizedTxn(
            posted_on=date(2026, 5, 20),
            description="Coffee shop",
            amount=Decimal("-4.50"),
            bank_transaction_id="tx-1",
        ),
        NormalizedTxn(
            posted_on=date(2026, 5, 21),
            description="Payroll",
            amount=Decimal("2500.00"),
            bank_transaction_id="tx-2",
        ),
    ]
    provider = _StubProvider(txns)

    job_ids = run_sync_sync(
        user_id=user_id,
        bank_connection_id=conn_id,
        account_ids=[account_id],
        provider=provider,
    )
    assert len(job_ids) == 1
    job = db_session.get(SyncJob, job_ids[0])
    assert job is not None
    assert job.status == SyncJobStatus.SUCCESS
    assert job.transactions_fetched == 2
    assert job.transactions_imported == 2

    rows = db_session.query(Transaction).filter(Transaction.account_id == account_id).all()
    assert {r.external_id for r in rows} == {"tx-1", "tx-2"}

    # Run again with the same response — everything dedupes.
    job_ids_2 = run_sync_sync(
        user_id=user_id,
        bank_connection_id=conn_id,
        account_ids=[account_id],
        provider=provider,
    )
    job2 = db_session.get(SyncJob, job_ids_2[0])
    assert job2 is not None
    assert job2.status == SyncJobStatus.SUCCESS
    assert job2.transactions_imported == 0


def test_sync_falls_back_to_synth_id_when_provider_lacks_one(
    db_session: Session, linked_setup: tuple[int, int, int]
) -> None:
    user_id, conn_id, account_id = linked_setup
    txns = [
        NormalizedTxn(
            posted_on=date(2026, 5, 20),
            description="Cash withdrawal",
            amount=Decimal("-100.00"),
            bank_transaction_id=None,
        ),
    ]
    job_ids = run_sync_sync(
        user_id=user_id,
        bank_connection_id=conn_id,
        account_ids=[account_id],
        provider=_StubProvider(txns),
    )
    rows = db_session.query(Transaction).filter(Transaction.account_id == account_id).all()
    assert len(rows) == 1
    assert rows[0].external_id == "2026-05-20|Cash withdrawal|-100.00"
    job = db_session.get(SyncJob, job_ids[0])
    assert job is not None and job.status == SyncJobStatus.SUCCESS


def test_sync_marks_job_failed_when_provider_raises(
    db_session: Session, linked_setup: tuple[int, int, int]
) -> None:
    user_id, conn_id, account_id = linked_setup

    class _BoomProvider(_StubProvider):
        async def fetch_transactions(self, **_kwargs):
            raise RuntimeError("network down")

    job_ids = run_sync_sync(
        user_id=user_id,
        bank_connection_id=conn_id,
        account_ids=[account_id],
        provider=_BoomProvider([]),
    )
    job = db_session.get(SyncJob, job_ids[0])
    assert job is not None
    assert job.status == SyncJobStatus.FAILED
    assert job.error_message and "network down" in job.error_message
