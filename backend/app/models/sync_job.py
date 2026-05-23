from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class SyncJobStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class SyncJob(Base, TimestampMixin):
    """One row per sync run. Replaces StatementImport for API-based bank syncs.

    `account_id` is nullable: a sync can span multiple accounts (one connection,
    many accounts at the bank), in which case we write one job row per account
    so per-account counts and errors are easy to reason about.
    """

    __tablename__ = "sync_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    bank_connection_id: Mapped[int] = mapped_column(
        ForeignKey("bank_connections.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[SyncJobStatus] = mapped_column(
        Enum(SyncJobStatus, name="sync_job_status", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=SyncJobStatus.RUNNING,
    )
    transactions_fetched: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    transactions_imported: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(1000))

    bank_connection: Mapped["BankConnection"] = relationship(back_populates="sync_jobs")  # noqa: F821
    account: Mapped["Account | None"] = relationship()  # noqa: F821
