"""Read-only analytics endpoints: recurring items, cashflow forecast, net
worth, and the monthly surplus allocator. All computation lives in
services/analytics; this layer fetches rows and serializes results."""

import smtplib
from dataclasses import asdict
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select

from app.core.config import settings
from app.core.deps import CurrentUser, DbSession
from app.core.rate_limit import limiter
from app.models.account import Account, UkWrapper
from app.models.debt import Debt
from app.models.fx_rate import FxRate
from app.models.goal import Goal
from app.models.transaction import Transaction
from app.schemas.insights import (
    AllocationOptionOut,
    AllowanceMeterOut,
    AllowancesOut,
    DigestOut,
    DigestSendOut,
    ForecastOut,
    NetWorthOut,
    RecurringItemOut,
    SurplusOut,
)
from app.services.account_loader import (
    load_account_data,
    load_forecast_scope,
    load_txn_lites,
    resolve_display_currency,
    sum_positive_inflows,
)
from app.services.analytics.allowances import compute_allowances, tax_year_bounds
from app.services.analytics.debt_plan import DebtInput, convert_debt_inputs, simulate_payoff
from app.services.analytics.forecast import project
from app.services.analytics.net_worth import compute_net_worth, current_balance
from app.services.analytics.recurring import detect_raises, detect_recurring
from app.services.analytics.surplus import latest_complete_month, rank_allocations, summarize_month
from app.services.digest import compose_digest, send_email
from app.services.fx_feed import refresh_user_rates

router = APIRouter(prefix="/insights", tags=["insights"])


def _fx_rates(db, user_id: int) -> dict:
    return {r.currency: r.rate for r in db.scalars(select(FxRate).where(FxRate.user_id == user_id))}


def _debt_inputs(db, user_id: int) -> list[DebtInput]:
    debts = db.scalars(select(Debt).where(Debt.user_id == user_id)).all()
    return [DebtInput.from_model(d) for d in debts]


@router.get("/recurring", response_model=list[RecurringItemOut])
def recurring(current: CurrentUser, db: DbSession) -> list[RecurringItemOut]:
    items = detect_recurring(load_txn_lites(db, current.id))
    return [
        RecurringItemOut(**{k: v for k, v in asdict(i).items() if k not in {"merchant_key", "amounts"}}) for i in items
    ]


@router.get("/forecast", response_model=ForecastOut)
def forecast(
    current: CurrentUser,
    db: DbSession,
    days: int = Query(default=60, ge=7, le=365),
    reserve: Decimal = Query(default=Decimal("0"), ge=0),
) -> ForecastOut:
    # The projection is a single running balance, so it must be single-currency:
    # only display-currency liquid accounts feed it (shared with the digest via
    # load_forecast_scope). Recurring items come from those same accounts —
    # card-ledger charges reach the liquid balance through the card *payment*,
    # so including them directly would double-count when the payment is also
    # detected as recurring.
    account_data, display, in_display, excluded_currencies = load_forecast_scope(db, current)
    included_ids = {a.id for a in in_display}
    items = detect_recurring(load_txn_lites(db, current.id, account_ids=included_ids))
    start = sum((current_balance(a).balance for a in in_display), Decimal("0"))
    debts = db.scalars(select(Debt).where(Debt.user_id == current.id)).all()
    # Due markers share the calendar with display-currency amounts; debts tied
    # to a foreign-currency account — or carrying a foreign currency of their
    # own — would render their minimums mislabeled.
    currency_of = {a.id: a.currency for a in account_data}
    due_days = [
        (d.name, d.due_day_of_month, d.minimum_payment)
        for d in debts
        if d.due_day_of_month is not None
        and (display is None or d.currency is None or d.currency == display)
        and (display is None or d.account_id is None or currency_of.get(d.account_id, display) == display)
    ]
    result = project(start, items, days=days, reserve=reserve, debt_due_days=due_days)
    return ForecastOut(
        start_balance=result.start_balance,
        reserve=result.reserve,
        days=result.days,
        series=[{"on": on, "balance": b} for on, b in result.series],
        events=[asdict(e) for e in result.events],
        due_markers=[asdict(m) for m in result.due_markers],
        min_balance=result.min_balance,
        min_balance_date=result.min_balance_date,
        first_below_reserve=result.first_below_reserve,
        first_below_zero=result.first_below_zero,
        safe_to_commit=result.safe_to_commit,
        display_currency=display,
        excluded_currencies=excluded_currencies,
    )


def _minimums_payoff_dates(inputs: list[DebtInput], display: str | None, rates: dict[str, Decimal]) -> dict[int, date]:
    """Cheap per-debt payoff dates: ONE minimums-only run over the convertible
    debts (under minimums each debt just pays its own contractual amount, so
    excluding the unconvertible ones can't shift anyone else's date). A debt
    that never clears — or can't be converted — simply has no entry."""
    pool, _excluded, _notes = convert_debt_inputs(inputs, display, rates)
    if not pool:
        return {}
    result = simulate_payoff(pool, "minimum")
    return {d.id: d.payoff_date for d in result.debts if d.payoff_date is not None}


@router.get("/networth", response_model=NetWorthOut)
def networth(
    current: CurrentUser,
    db: DbSession,
    months: int = Query(default=24, ge=3, le=120),
) -> NetWorthOut:
    account_data = load_account_data(db, current.id)
    debts = db.scalars(select(Debt).where(Debt.user_id == current.id)).all()
    display = resolve_display_currency(current, account_data)
    # Opportunistic FX refresh, mirroring GET /fx: a no-op unless the user
    # opted in, and any failure serves last-known rates — never a 500.
    try:
        in_use = {a.currency for a in account_data} | {d.currency for d in debts if d.currency is not None}
        refresh_user_rates(db, current, in_use, display)
    except Exception:
        pass
    rates = _fx_rates(db, current.id)
    payoff_by_id = _minimums_payoff_dates([DebtInput.from_model(d) for d in debts], display, rates)
    report = compute_net_worth(
        account_data,
        [(d.id, d.name, d.current_balance, d.account_id, d.currency, payoff_by_id.get(d.id)) for d in debts],
        months=months,
        display_currency=display,
        rates=rates,
    )
    return NetWorthOut(
        accounts=[asdict(b) for b in report.accounts],
        register_debts=[asdict(d) for d in report.register_debts],
        assets=report.assets,
        liabilities=report.liabilities,
        net=report.net,
        series=[asdict(p) for p in report.series],
        display_currency=report.display_currency,
        excluded_currencies=report.excluded_currencies,
    )


@router.get("/allowances", response_model=AllowancesOut)
def allowances(current: CurrentUser, db: DbSession) -> AllowancesOut:
    """UK tax-year allowance meters, from contributions into wrapper-tagged
    accounts (positive transactions within the current tax year; rows described
    as interest are excluded — interest is not an HMRC subscription)."""
    today = date.today()
    start, end = tax_year_bounds(today)

    # Allowances are GBP-denominated; non-GBP accounts are excluded outright
    # (tagging them is also rejected at the API — belt and braces).
    wrapped = db.execute(
        select(Account.id, Account.uk_wrapper).where(
            Account.user_id == current.id,
            Account.uk_wrapper.isnot(None),
            Account.currency == "GBP",
        )
    ).all()
    wrapper_by_account = {account_id: wrapper for account_id, wrapper in wrapped}

    inflows = sum_positive_inflows(
        db, current.id, wrapper_by_account, start, end, exclude_description_like="%interest%"
    )
    contributions: dict[UkWrapper, Decimal] = {wrapper: Decimal("0") for wrapper in wrapper_by_account.values()}
    for account_id, total in inflows.items():
        contributions[wrapper_by_account[account_id]] += total

    meters = compute_allowances(contributions)
    return AllowancesOut(
        tax_year_start=start,
        tax_year_end=end,
        # Inclusive: the last day of the tax year reads "1 day left", not 0 —
        # contributions made that day still count.
        days_left=(end - today).days + 1,
        meters=[AllowanceMeterOut(**asdict(m)) for m in meters],
        wrapped_account_count=len(wrapper_by_account),
    )


@router.get("/digest/preview", response_model=DigestOut)
def digest_preview(current: CurrentUser, db: DbSession) -> DigestOut:
    """The weekly digest as it would be emailed — works without SMTP."""
    digest = compose_digest(db, current)
    return DigestOut(
        subject=digest.subject,
        body=digest.body,
        item_count=digest.item_count,
        smtp_configured=settings.smtp_configured,
    )


@router.post("/digest/send", response_model=DigestSendOut)
@limiter.limit("5/hour")
def digest_send(request: Request, current: CurrentUser, db: DbSession) -> DigestSendOut:
    """Compose and email the digest to the signed-in user's address now."""
    if not settings.smtp_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SMTP is not configured — set SMTP_HOST in .env",
        )
    digest = compose_digest(db, current)
    try:
        send_email(current.email, digest.subject, digest.body)
    except (OSError, smtplib.SMTPException) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"SMTP send failed: {exc.__class__.__name__}",
        ) from exc
    return DigestSendOut(sent_to=current.email, subject=digest.subject)


@router.get("/surplus", response_model=SurplusOut)
def surplus(
    current: CurrentUser,
    db: DbSession,
    year: int | None = Query(default=None, ge=2000, le=2100),
    month: int | None = Query(default=None, ge=1, le=12),
    amount: Decimal | None = Query(default=None, gt=0),
) -> SurplusOut:
    # Cash surplus is a single-currency figure: only display-currency accounts'
    # transactions feed it — raw amounts across currencies don't add.
    account_data = load_account_data(db, current.id)
    display = resolve_display_currency(current, account_data)
    display_ids = {a.id for a in account_data if display is None or a.currency == display}
    currency_of = {a.id: a.currency for a in account_data}
    txn_q = select(Transaction.posted_on, Transaction.amount, Transaction.category_id).where(
        Transaction.user_id == current.id
    )
    if display is not None:
        txn_q = txn_q.where(Transaction.account_id.in_(display_ids))
    txn_tuples = [(p, a, c) for p, a, c in db.execute(txn_q).all()]

    if year is None or month is None:
        # The latest complete month with data; fall back to the latest month
        # with any data at all (same helper the digest uses).
        today = date.today()
        picked = latest_complete_month([p for p, _, _ in txn_tuples], today)
        if picked is None:
            candidates = sorted({(p.year, p.month) for p, _, _ in txn_tuples})
            picked = (candidates or [(today.year, today.month)])[-1]
        year, month = picked

    summary = summarize_month(txn_tuples, year, month)

    considered = amount if amount is not None else max(summary.surplus, Decimal("0"))
    goals = db.scalars(select(Goal).where(Goal.user_id == current.id)).all()

    # Essential-spending floor: average outflows over up to 3 recent complete
    # months — a pragmatic runway denominator until spending is fully categorized.
    monthly_outflows: dict[tuple[int, int], Decimal] = {}
    for p, a, _c in txn_tuples:
        if a < 0:
            key = (p.year, p.month)
            monthly_outflows[key] = monthly_outflows.get(key, Decimal("0")) + -a
    today = date.today()
    completed = sorted(k for k in monthly_outflows if k < (today.year, today.month))[-3:]
    floor = (
        (sum((monthly_outflows[k] for k in completed), Decimal("0")) / len(completed)).quantize(Decimal("0.01"))
        if completed
        else None
    )

    # For goals funded by a linked account, the live derived balance is the
    # real "current" — the stored current_amount goes stale while auto-tracked.
    # Goals linked to a non-display-currency account are left out: their
    # balance is in different units and "months earlier" math would be fiction.
    linked_ids = {g.account_id for g in goals if g.account_id is not None}
    linked_balances = {acc.id: current_balance(acc).balance for acc in load_account_data(db, current.id, linked_ids)}
    goal_tuples = [
        (
            g.id,
            g.name,
            g.target_amount,
            linked_balances.get(g.account_id, g.current_amount) if g.account_id is not None else g.current_amount,
            g.target_date,
        )
        for g in goals
        if g.account_id is None or display is None or currency_of.get(g.account_id, display) == display
    ]

    options = (
        rank_allocations(
            considered,
            _debt_inputs(db, current.id),
            goal_tuples,
            floor,
            display_currency=display,
            rates=_fx_rates(db, current.id),
        )
        if considered > 0
        else []
    )

    items = detect_recurring(load_txn_lites(db, current.id, account_ids=display_ids if display else None))
    raises = detect_raises(items)

    return SurplusOut(
        year=year,
        month=month,
        income=summary.income,
        outflows=summary.outflows,
        surplus=summary.surplus,
        txn_count=summary.txn_count,
        uncategorized_count=summary.uncategorized_count,
        uncategorized_amount=summary.uncategorized_amount,
        amount_considered=considered,
        options=[AllocationOptionOut(**asdict(o)) for o in options],
        raises_detected=[asdict(r) for r in raises],
    )
