from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.deps import CurrentUser, DbSession
from app.models.debt import Debt
from app.schemas.debt import DebtCreate, DebtOut, DebtUpdate

router = APIRouter(prefix="/debts", tags=["debts"])


class DebtSummary(BaseModel):
    total_owed: Decimal
    by_debt: list[DebtOut]


@router.get("", response_model=list[DebtOut])
def list_debts(current: CurrentUser, db: DbSession) -> list[Debt]:
    return list(db.scalars(select(Debt).where(Debt.user_id == current.id).order_by(Debt.name)))


@router.get("/summary", response_model=DebtSummary)
def debt_summary(current: CurrentUser, db: DbSession) -> DebtSummary:
    debts = list(db.scalars(select(Debt).where(Debt.user_id == current.id).order_by(Debt.name)))
    total = db.scalar(select(func.coalesce(func.sum(Debt.current_balance), 0)).where(Debt.user_id == current.id))
    return DebtSummary(total_owed=Decimal(total or 0), by_debt=debts)


@router.post("", response_model=DebtOut, status_code=status.HTTP_201_CREATED)
def create_debt(payload: DebtCreate, current: CurrentUser, db: DbSession) -> Debt:
    debt = Debt(user_id=current.id, **payload.model_dump())
    db.add(debt)
    db.commit()
    db.refresh(debt)
    return debt


def _get_owned(db, current, debt_id: int) -> Debt:
    debt = db.get(Debt, debt_id)
    if debt is None or debt.user_id != current.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Debt not found")
    return debt


@router.patch("/{debt_id}", response_model=DebtOut)
def update_debt(debt_id: int, payload: DebtUpdate, current: CurrentUser, db: DbSession) -> Debt:
    debt = _get_owned(db, current, debt_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(debt, key, value)
    db.commit()
    db.refresh(debt)
    return debt


@router.delete("/{debt_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_debt(debt_id: int, current: CurrentUser, db: DbSession) -> None:
    debt = _get_owned(db, current, debt_id)
    db.delete(debt)
    db.commit()
