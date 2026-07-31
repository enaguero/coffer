from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Goal(Base, TimestampMixin):
    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    target_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    current_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=Decimal("0")
    )
    target_date: Mapped[date | None] = mapped_column(Date)
    # Funding: when linked to a savings account, progress derives from that
    # account's balance instead of the hand-edited current_amount, and
    # contributions are counted from its imported transactions.
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"))
    # The user's committed monthly contribution (their plan, not a computed value).
    monthly_contribution: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    notes: Mapped[str | None] = mapped_column(String(1000))

    user: Mapped["User"] = relationship(back_populates="goals")  # noqa: F821
