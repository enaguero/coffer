from enum import StrEnum

from sqlalchemy import Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class CategoryKind(StrEnum):
    INCOME = "income"
    EXPENSE = "expense"
    DEBT_PAYMENT = "debt_payment"
    SAVING = "saving"


class Category(Base, TimestampMixin):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_category_user_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[CategoryKind] = mapped_column(
        Enum(CategoryKind, name="category_kind", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=CategoryKind.EXPENSE,
    )
    color: Mapped[str | None] = mapped_column(String(9))

    user: Mapped["User"] = relationship(back_populates="categories")  # noqa: F821
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="category")  # noqa: F821
    budget_entries: Mapped[list["BudgetEntry"]] = relationship(back_populates="category", cascade="all, delete-orphan")  # noqa: F821
