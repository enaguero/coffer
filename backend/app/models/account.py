from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Enum, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class AccountType(StrEnum):
    CHECKING = "checking"
    SAVINGS = "savings"
    CREDIT_CARD = "credit_card"
    LOAN = "loan"
    OVERDRAFT = "overdraft"
    CASH = "cash"
    OTHER = "other"


class Account(Base, TimestampMixin):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    type: Mapped[AccountType] = mapped_column(
        Enum(AccountType, name="account_type", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    institution: Mapped[str | None] = mapped_column(String(120))
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    opening_balance: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0"), nullable=False
    )
    # Bank-sync linkage: set when this account is backed by a BankConnection.
    # Manual accounts leave both NULL.
    bank_connection_id: Mapped[int | None] = mapped_column(
        ForeignKey("bank_connections.id", ondelete="SET NULL"), index=True
    )
    external_account_id: Mapped[str | None] = mapped_column(String(120))

    user: Mapped["User"] = relationship(back_populates="accounts")  # noqa: F821
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="account", cascade="all, delete-orphan")  # noqa: F821
    debt: Mapped["Debt | None"] = relationship(back_populates="account", uselist=False, cascade="all, delete-orphan")  # noqa: F821
    bank_connection: Mapped["BankConnection | None"] = relationship(back_populates="accounts")  # noqa: F821
