from sqlalchemy import Boolean, String, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255))
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    # Currency all cross-account aggregates are displayed in. NULL = fall back
    # to the most-common currency across the user's accounts.
    display_currency: Mapped[str | None] = mapped_column(String(3))
    # Opt-in: fetch FX rates from the external feed (services/fx_feed.py).
    # Off by default — manual rates remain the only source until enabled.
    fx_auto_refresh: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())

    accounts: Mapped[list["Account"]] = relationship(back_populates="user", cascade="all, delete-orphan")  # noqa: F821
    categories: Mapped[list["Category"]] = relationship(back_populates="user", cascade="all, delete-orphan")  # noqa: F821
    category_rules: Mapped[list["CategoryRule"]] = relationship(back_populates="user", cascade="all, delete-orphan")  # noqa: F821
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="user", cascade="all, delete-orphan")  # noqa: F821
    debts: Mapped[list["Debt"]] = relationship(back_populates="user", cascade="all, delete-orphan")  # noqa: F821
    budget_entries: Mapped[list["BudgetEntry"]] = relationship(back_populates="user", cascade="all, delete-orphan")  # noqa: F821
    cashflow_lines: Mapped[list["CashflowLine"]] = relationship(back_populates="user", cascade="all, delete-orphan")  # noqa: F821
    goals: Mapped[list["Goal"]] = relationship(back_populates="user", cascade="all, delete-orphan")  # noqa: F821
    statement_imports: Mapped[list["StatementImport"]] = relationship(  # noqa: F821
        back_populates="user", cascade="all, delete-orphan"
    )
