from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255))
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    accounts: Mapped[list["Account"]] = relationship(back_populates="user", cascade="all, delete-orphan")  # noqa: F821
    categories: Mapped[list["Category"]] = relationship(back_populates="user", cascade="all, delete-orphan")  # noqa: F821
    category_rules: Mapped[list["CategoryRule"]] = relationship(back_populates="user", cascade="all, delete-orphan")  # noqa: F821
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="user", cascade="all, delete-orphan")  # noqa: F821
    debts: Mapped[list["Debt"]] = relationship(back_populates="user", cascade="all, delete-orphan")  # noqa: F821
    budget_entries: Mapped[list["BudgetEntry"]] = relationship(back_populates="user", cascade="all, delete-orphan")  # noqa: F821
    cashflow_lines: Mapped[list["CashflowLine"]] = relationship(back_populates="user", cascade="all, delete-orphan")  # noqa: F821
    goals: Mapped[list["Goal"]] = relationship(back_populates="user", cascade="all, delete-orphan")  # noqa: F821
    statement_imports: Mapped[list["StatementImport"]] = relationship(back_populates="user", cascade="all, delete-orphan")  # noqa: F821
    bank_connections: Mapped[list["BankConnection"]] = relationship(back_populates="user", cascade="all, delete-orphan")  # noqa: F821
