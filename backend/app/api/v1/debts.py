from dataclasses import asdict
from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.deps import CurrentUser, DbSession
from app.models.account import Account
from app.models.debt import Debt
from app.schemas.debt import (
    DebtCreate,
    DebtOut,
    DebtPlanDebtOut,
    DebtUpdate,
    PlanCompareOut,
    PlanOut,
    PlanRequest,
    ScheduleMonthOut,
    SchedulePaymentOut,
    repayment_type_violation,
)
from app.services.account_loader import load_display_and_rates
from app.services.analytics.debt_optimizer import optimize
from app.services.analytics.debt_plan import DebtInput, PlanResult, convert_debt_inputs
from app.services.analytics.fx import convert_optional

router = APIRouter(prefix="/debts", tags=["debts"])


class DebtSummaryItemOut(DebtOut):
    # False = balance held in a foreign currency with no saved FX rate — the
    # debt is listed (raw balance + currency) but excluded from total_owed.
    converted: bool = True


class DebtSummary(BaseModel):
    # Display-currency total over convertible debts only — never a raw sum
    # across currencies.
    total_owed: Decimal
    by_debt: list[DebtSummaryItemOut]
    excluded_currencies: list[str] = Field(default_factory=list)


@router.get("", response_model=list[DebtOut])
def list_debts(current: CurrentUser, db: DbSession) -> list[Debt]:
    return list(db.scalars(select(Debt).where(Debt.user_id == current.id).order_by(Debt.name)))


@router.get("/summary", response_model=DebtSummary)
def debt_summary(current: CurrentUser, db: DbSession) -> DebtSummary:
    debts = list(db.scalars(select(Debt).where(Debt.user_id == current.id).order_by(Debt.name)))
    display, rates = load_display_and_rates(db, current)
    total = Decimal("0")
    excluded: set[str] = set()
    items: list[DebtSummaryItemOut] = []
    for d in debts:
        item = DebtSummaryItemOut.model_validate(d)
        value = convert_optional(d.current_balance, d.currency, display, rates)
        if value is None:
            item.converted = False
            excluded.add(d.currency)
        else:
            total += value
        items.append(item)
    return DebtSummary(total_owed=total, by_debt=items, excluded_currencies=sorted(excluded))


def _plan_out(
    result: PlanResult,
    baseline: PlanResult | None,
    *,
    currency_by_id: dict[int, str | None] | None = None,
    extra_assumptions: list[str] | None = None,
    include_schedule: bool = False,
) -> PlanOut:
    currency_by_id = currency_by_id or {}
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
        debts=[
            DebtPlanDebtOut(
                id=d.id,
                name=d.name,
                payoff_date=d.payoff_date,
                interest_paid=d.interest_paid,
                currency=currency_by_id.get(d.id),
            )
            for d in result.debts
        ],
        balance_series=[{"on": on, "balance": b} for on, b in result.balance_series],
        promo_cliffs=[asdict(c) for c in result.promo_cliffs],
        assumptions=[*result.assumptions, *(extra_assumptions or [])],
        unpayable=result.unpayable,
        schedule=(
            [
                ScheduleMonthOut(
                    month=m.month,
                    payments=[
                        SchedulePaymentOut(debt_id=debt_id, amount=amount)
                        for debt_id, amount in sorted(m.payments.items())
                    ],
                    uncommitted=m.uncommitted,
                )
                for m in result.schedule
            ]
            if include_schedule
            else []
        ),
    )


@router.post("/plan", response_model=PlanCompareOut)
def plan_payoff(payload: PlanRequest, current: CurrentUser, db: DbSession) -> PlanCompareOut:
    """Simulate paying off all open debts: minimums-only baseline vs snowball
    vs avalanche vs the optimizer's best ordering, with the requested extra
    budget and one-off payments. The whole simulation runs in the display
    currency: foreign-currency debts convert once at plan start (saved rates),
    and debts with no rate are excluded from the pool and flagged."""
    debts = list(db.scalars(select(Debt).where(Debt.user_id == current.id)))
    display, rates = load_display_and_rates(db, current)
    pool, excluded, fx_notes = convert_debt_inputs([DebtInput.from_model(d) for d in debts], display, rates)
    snowflakes = {s.month: s.amount for s in payload.snowflakes}
    optimal, results = optimize(pool, payload.extra_monthly, snowflakes)
    baseline = results["minimum"]
    shared = {"currency_by_id": {d.id: d.currency for d in debts}, "extra_assumptions": fx_notes}
    return PlanCompareOut(
        minimum=_plan_out(baseline, None, **shared),
        snowball=_plan_out(results["snowball"], baseline, **shared),
        avalanche=_plan_out(results["avalanche"], baseline, **shared),
        optimal=_plan_out(optimal, baseline, include_schedule=True, **shared),
        excluded_currencies=sorted({d.currency for d in excluded}),
    )


def _check_account_owned(db, current, account_id: int) -> None:
    """404 unless the linked account exists AND belongs to the current user —
    mirrors _get_owned so a foreign id is indistinguishable from a missing one."""
    account = db.get(Account, account_id)
    if account is None or account.user_id != current.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")


def _commit_or_409(db) -> None:
    """Commit, mapping the one-debt-per-account unique violation to a 409."""
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That account is already linked to another debt",
        ) from e


@router.post("", response_model=DebtOut, status_code=status.HTTP_201_CREATED)
def create_debt(payload: DebtCreate, current: CurrentUser, db: DbSession) -> Debt:
    if payload.account_id is not None:
        _check_account_owned(db, current, payload.account_id)
    debt = Debt(user_id=current.id, **payload.model_dump())
    db.add(debt)
    _commit_or_409(db)
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
        creating=False,  # updates may zero a statement_only balance (paid off)
    )
    if violation:
        # List-shaped detail so PATCH 422s look like create's pydantic ones —
        # one client-side extraction path for both endpoints.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[{"loc": ["body"], "msg": violation, "type": "value_error"}],
        )
    if updates.get("account_id") is not None:
        _check_account_owned(db, current, updates["account_id"])
    for key, value in updates.items():
        setattr(debt, key, value)
    _commit_or_409(db)
    db.refresh(debt)
    return debt


@router.delete("/{debt_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_debt(debt_id: int, current: CurrentUser, db: DbSession) -> None:
    debt = _get_owned(db, current, debt_id)
    db.delete(debt)
    db.commit()
