from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Date, Enum, ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class BalanceSource(StrEnum):
    STATEMENT = "statement"  # closing/running balance read from an imported statement
    MANUAL = "manual"  # user-entered valuation (pension, property, ISA, ...)


class BalanceSnapshot(Base, TimestampMixin):
    """A dated point-in-time balance for an account.

    Statement snapshots are attestations — the bank's own printed balance,
    captured automatically at import. Manual snapshots let non-transactional
    accounts (pension, house) carry a valuation history. One snapshot per
    account per day; a re-import or manual correction overwrites in place.
    """

    __tablename__ = "balance_snapshots"
    __table_args__ = (UniqueConstraint("account_id", "as_of", name="uq_balance_snapshot_account_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    source: Mapped[BalanceSource] = mapped_column(
        Enum(BalanceSource, name="balance_source", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=BalanceSource.MANUAL,
    )

    account: Mapped["Account"] = relationship()  # noqa: F821
