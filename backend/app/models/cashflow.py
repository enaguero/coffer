from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class CashflowKind(StrEnum):
    INCOME = "income"
    EXPENSE = "expense"


class CashflowLine(Base, TimestampMixin):
    """A named planning row in the cashflow grid (e.g. 'Hurdle', 'Lloyds Loan 01').

    Lines carry their own country and currency so a single user can plan across
    jurisdictions without forcing FX conversion. `account_id` / `category_id` are
    optional hooks for matching actual transactions later.
    """

    __tablename__ = "cashflow_lines"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_cashflow_line_user_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[CashflowKind] = mapped_column(
        Enum(CashflowKind, name="cashflow_kind", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    country: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text)

    user: Mapped["User"] = relationship(back_populates="cashflow_lines")  # noqa: F821
    entries: Mapped[list["CashflowEntry"]] = relationship(
        back_populates="line", cascade="all, delete-orphan"
    )


class CashflowEntry(Base, TimestampMixin):
    """Month-bucketed amount for a single cashflow line."""

    __tablename__ = "cashflow_entries"
    __table_args__ = (
        UniqueConstraint("line_id", "year", "month", name="uq_cashflow_entry_line_period"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    line_id: Mapped[int] = mapped_column(
        ForeignKey("cashflow_lines.id", ondelete="CASCADE"), index=True
    )
    year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    month: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0"))

    line: Mapped["CashflowLine"] = relationship(back_populates="entries")
