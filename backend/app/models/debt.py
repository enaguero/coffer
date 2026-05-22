from datetime import date

from sqlalchemy import Date, ForeignKey, Numeric, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Debt(Base, TimestampMixin):
    """An outstanding obligation: loan, credit card, overdraft."""

    __tablename__ = "debts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), unique=True
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    original_principal: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    current_balance: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    interest_rate_apr: Mapped[float | None] = mapped_column(Numeric(6, 3))
    minimum_payment: Mapped[float | None] = mapped_column(Numeric(14, 2))
    due_day_of_month: Mapped[int | None] = mapped_column(SmallInteger)
    starts_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(String(1000))

    user: Mapped["User"] = relationship(back_populates="debts")  # noqa: F821
    account: Mapped["Account | None"] = relationship(back_populates="debt")  # noqa: F821
