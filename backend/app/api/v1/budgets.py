from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import extract, func, select
from sqlalchemy.exc import IntegrityError

from app.core.deps import CurrentUser, DbSession
from app.models.account import Account
from app.models.budget import BudgetEntry
from app.models.category import Category, CategoryKind
from app.models.transaction import Transaction
from app.schemas.budget import (
    BudgetEntryCreate,
    BudgetEntryOut,
    BudgetEntryUpdate,
    BudgetMonthCell,
    BudgetMonthView,
)
from app.services.account_loader import load_account_data, resolve_display_currency

router = APIRouter(prefix="/budgets", tags=["budgets"])


@router.get("", response_model=list[BudgetEntryOut])
def list_budget_entries(
    current: CurrentUser, db: DbSession, year: int | None = None, month: int | None = None
) -> list[BudgetEntry]:
    stmt = select(BudgetEntry).where(BudgetEntry.user_id == current.id)
    if year is not None:
        stmt = stmt.where(BudgetEntry.year == year)
    if month is not None:
        stmt = stmt.where(BudgetEntry.month == month)
    return list(db.scalars(stmt))


@router.post("", response_model=BudgetEntryOut, status_code=status.HTTP_201_CREATED)
def create_budget_entry(
    payload: BudgetEntryCreate, current: CurrentUser, db: DbSession
) -> BudgetEntry:
    entry = BudgetEntry(user_id=current.id, **payload.model_dump())
    db.add(entry)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Budget entry already exists for this category/month",
        ) from e
    db.refresh(entry)
    return entry


@router.patch("/{entry_id}", response_model=BudgetEntryOut)
def update_budget_entry(
    entry_id: int, payload: BudgetEntryUpdate, current: CurrentUser, db: DbSession
) -> BudgetEntry:
    entry = db.get(BudgetEntry, entry_id)
    if entry is None or entry.user_id != current.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Budget entry not found")
    entry.planned_amount = payload.planned_amount
    db.commit()
    db.refresh(entry)
    return entry


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_budget_entry(entry_id: int, current: CurrentUser, db: DbSession) -> None:
    entry = db.get(BudgetEntry, entry_id)
    if entry is None or entry.user_id != current.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Budget entry not found")
    db.delete(entry)
    db.commit()


@router.get("/month/{year}/{month}", response_model=BudgetMonthView)
def month_view(year: int, month: int, current: CurrentUser, db: DbSession) -> BudgetMonthView:
    """Return planned vs. actual per category for the month (the spreadsheet view)."""

    categories = list(
        db.scalars(select(Category).where(Category.user_id == current.id).order_by(Category.name))
    )
    planned_map: dict[int, Decimal] = {
        e.category_id: Decimal(e.planned_amount)
        for e in db.scalars(
            select(BudgetEntry).where(
                BudgetEntry.user_id == current.id,
                BudgetEntry.year == year,
                BudgetEntry.month == month,
            )
        )
    }
    # Budget amounts are display-currency figures; actuals only add up in the
    # same units, so transactions on other-currency accounts stay out.
    display = resolve_display_currency(current, load_account_data(db, current.id))
    actual_q = (
        select(
            Transaction.category_id,
            func.coalesce(func.sum(Transaction.amount), 0),
        )
        .where(
            Transaction.user_id == current.id,
            extract("year", Transaction.posted_on) == year,
            extract("month", Transaction.posted_on) == month,
        )
        .group_by(Transaction.category_id)
    )
    if display is not None:
        actual_q = actual_q.where(
            Transaction.account_id.in_(
                select(Account.id).where(Account.user_id == current.id, func.upper(Account.currency) == display)
            )
        )
    actual_rows = db.execute(actual_q).all()
    actual_map: dict[int | None, Decimal] = {cat_id: Decimal(total or 0) for cat_id, total in actual_rows}

    rows: list[BudgetMonthCell] = []
    income_planned = Decimal("0")
    income_actual = Decimal("0")
    expenses_planned = Decimal("0")
    expenses_actual = Decimal("0")
    saving_planned = Decimal("0")
    saving_actual = Decimal("0")

    for cat in categories:
        planned = planned_map.get(cat.id, Decimal("0"))
        actual_raw = actual_map.get(cat.id, Decimal("0"))
        if cat.kind == CategoryKind.INCOME:
            actual_value = actual_raw
            income_planned += planned
            income_actual += actual_value
        elif cat.kind == CategoryKind.SAVING:
            actual_value = actual_raw
            saving_planned += planned
            saving_actual += actual_value
        else:
            actual_value = abs(actual_raw) if actual_raw < 0 else actual_raw
            expenses_planned += planned
            expenses_actual += actual_value
        rows.append(
            BudgetMonthCell(
                category_id=cat.id,
                category_name=cat.name,
                planned=planned,
                actual=actual_value,
            )
        )

    return BudgetMonthView(
        year=year,
        month=month,
        income_planned=income_planned,
        income_actual=income_actual,
        expenses_planned=expenses_planned,
        expenses_actual=expenses_actual,
        saving_planned=saving_planned,
        saving_actual=saving_actual,
        rows=rows,
    )
