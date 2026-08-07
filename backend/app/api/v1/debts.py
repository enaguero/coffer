from dataclasses import asdict
from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.deps import CurrentUser, DbSession
from app.models.debt import Debt
from app.schemas.debt import (
    DebtCreate,
    DebtOut,
    DebtUpdate,
    PlanCompareOut,
    PlanOut,
    PlanRequest,
    repayment_type_violation,
)
from app.services.analytics.debt_plan import DebtInput, PlanResult, compare_strategies

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


def _plan_out(result: PlanResult, baseline: PlanResult | None) -> PlanOut:
    return PlanOut(
        strategy=result.strategy,
        months=result.months,
        debt_free_date=result.debt_free_date,
        total_interest=result.total_interest,
        total_paid=result.total_paid,
        monthly_budget=result.monthly_budget,
        # A truncated (unpayable) baseline has meaningless totals — comparing
        # against it would claim negative savings, so report no comparison.
        interest_saved_vs_minimum=(
            baseline.total_interest - result.total_interest
            if baseline and not baseline.unpayable and not result.unpayable
            else None
        ),
        months_saved_vs_minimum=(
            baseline.months - result.months if baseline and not baseline.unpayable and not result.unpayable else None
        ),
        debts=[asdict(d) for d in result.debts],
        balance_series=[{"on": on, "balance": b} for on, b in result.balance_series],
        promo_cliffs=[asdict(c) for c in result.promo_cliffs],
        assumptions=result.assumptions,
        unpayable=result.unpayable,
    )


@router.post("/plan", response_model=PlanCompareOut)
def plan_payoff(payload: PlanRequest, current: CurrentUser, db: DbSession) -> PlanCompareOut:
    """Simulate paying off all open debts: minimums-only baseline vs snowball
    vs avalanche with the requested extra budget and one-off payments."""
    debts = list(db.scalars(select(Debt).where(Debt.user_id == current.id)))
    inputs = [DebtInput.from_model(d) for d in debts]
    snowflakes = {s.month: s.amount for s in payload.snowflakes}
    results = compare_strategies(inputs, payload.extra_monthly, snowflakes)
    baseline = results["minimum"]
    return PlanCompareOut(
        minimum=_plan_out(baseline, None),
        snowball=_plan_out(results["snowball"], baseline),
        avalanche=_plan_out(results["avalanche"], baseline),
    )


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
    updates = payload.model_dump(exclude_unset=True)

    # Cross-field type rules must hold on the *merged* debt — a partial PATCH
    # (e.g. revolving → amortized without an installment) can't be validated
    # from the payload alone. Checked before mutating so a violation leaves
    # the debt untouched.
    def _merged(field: str):
        return updates[field] if field in updates else getattr(debt, field)

    violation = repayment_type_violation(
        _merged("repayment_type"),
        installment_amount=_merged("installment_amount"),
        ends_on=_merged("ends_on"),
        original_principal=_merged("original_principal"),
        current_balance=_merged("current_balance"),
    )
    if violation:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=violation)
    for key, value in updates.items():
        setattr(debt, key, value)
    db.commit()
    db.refresh(debt)
    return debt


@router.delete("/{debt_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_debt(debt_id: int, current: CurrentUser, db: DbSession) -> None:
    debt = _get_owned(db, current, debt_id)
    db.delete(debt)
    db.commit()
