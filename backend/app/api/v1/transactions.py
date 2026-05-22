from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import extract, func, select

from app.core.deps import CurrentUser, DbSession
from app.models.category import Category, CategoryKind
from app.models.transaction import Transaction
from app.schemas.transaction import (
    CategoryMonthlySpend,
    MonthlySummary,
    TransactionCreate,
    TransactionOut,
    TransactionUpdate,
)

router = APIRouter(prefix="/transactions", tags=["transactions"])


@router.get("", response_model=list[TransactionOut])
def list_transactions(
    current: CurrentUser,
    db: DbSession,
    account_id: int | None = None,
    category_id: int | None = None,
    start: date | None = None,
    end: date | None = None,
    limit: int = Query(default=200, le=2000),
    offset: int = 0,
) -> list[Transaction]:
    stmt = select(Transaction).where(Transaction.user_id == current.id)
    if account_id is not None:
        stmt = stmt.where(Transaction.account_id == account_id)
    if category_id is not None:
        stmt = stmt.where(Transaction.category_id == category_id)
    if start is not None:
        stmt = stmt.where(Transaction.posted_on >= start)
    if end is not None:
        stmt = stmt.where(Transaction.posted_on <= end)
    stmt = stmt.order_by(Transaction.posted_on.desc(), Transaction.id.desc()).limit(limit).offset(offset)
    return list(db.scalars(stmt))


@router.post("", response_model=TransactionOut, status_code=status.HTTP_201_CREATED)
def create_transaction(
    payload: TransactionCreate, current: CurrentUser, db: DbSession
) -> Transaction:
    txn = Transaction(user_id=current.id, **payload.model_dump())
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn


def _get_owned(db, current, txn_id: int) -> Transaction:
    txn = db.get(Transaction, txn_id)
    if txn is None or txn.user_id != current.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")
    return txn


@router.patch("/{txn_id}", response_model=TransactionOut)
def update_transaction(
    txn_id: int, payload: TransactionUpdate, current: CurrentUser, db: DbSession
) -> Transaction:
    txn = _get_owned(db, current, txn_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(txn, key, value)
    db.commit()
    db.refresh(txn)
    return txn


@router.delete("/{txn_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_transaction(txn_id: int, current: CurrentUser, db: DbSession) -> None:
    txn = _get_owned(db, current, txn_id)
    db.delete(txn)
    db.commit()


@router.get("/summary/{year}/{month}", response_model=MonthlySummary)
def monthly_summary(
    year: int, month: int, current: CurrentUser, db: DbSession
) -> MonthlySummary:
    base = (
        select(
            Transaction.category_id,
            Category.name.label("category_name"),
            Category.kind.label("kind"),
            func.coalesce(func.sum(Transaction.amount), 0).label("total"),
        )
        .join(Category, Category.id == Transaction.category_id, isouter=True)
        .where(
            Transaction.user_id == current.id,
            extract("year", Transaction.posted_on) == year,
            extract("month", Transaction.posted_on) == month,
        )
        .group_by(Transaction.category_id, Category.name, Category.kind)
    )
    rows = db.execute(base).all()

    income = Decimal("0")
    expenses = Decimal("0")
    saving = Decimal("0")
    by_category: list[CategoryMonthlySpend] = []
    for cat_id, cat_name, kind, total in rows:
        total = Decimal(total or 0)
        by_category.append(
            CategoryMonthlySpend(category_id=cat_id, category_name=cat_name, total=total)
        )
        if kind == CategoryKind.INCOME:
            income += total
        elif kind == CategoryKind.SAVING:
            saving += total
        else:
            # Treat any non-income, non-saving category (including null) as expense
            expenses += abs(total) if total < 0 else total
    return MonthlySummary(
        year=year,
        month=month,
        income=income,
        expenses=expenses,
        saving=saving,
        by_category=by_category,
    )
