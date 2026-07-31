"""Debt payoff simulation: avalanche / snowball / minimum-only, promo-APR
aware, with one-off extra payments ("snowflakes").

Model (standard for payoff calculators):
- Interest accrues monthly at effective_apr/12 on the running balance.
- The total monthly budget is fixed: sum of starting minimum payments plus the
  chosen extra. When a debt clears, its minimum rolls into the pool — that's
  what makes snowball/avalanche outperform paying minimums forever.
- The pool's remainder (after minimums) goes to one target debt: highest
  effective APR today (avalanche) or smallest balance (snowball).
- A promo window (promo_apr until promo_ends_on) applies while current; the
  simulation steps month by month, so the rate flips mid-plan exactly when the
  cliff hits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

TWO_DP = Decimal("0.01")
MAX_MONTHS = 600
# Fallback when a debt has no stored minimum payment: the greater of 2% of the
# balance at plan start or £25 — the standard UK credit-card floor.
DEFAULT_MIN_PCT = Decimal("0.02")
DEFAULT_MIN_FLOOR = Decimal("25")


@dataclass
class DebtInput:
    id: int
    name: str
    balance: Decimal
    apr: Decimal | None  # reverting/standard APR, e.g. 24.9
    promo_apr: Decimal | None = None
    promo_ends_on: date | None = None
    minimum_payment: Decimal | None = None


@dataclass
class DebtResult:
    id: int
    name: str
    payoff_date: date | None  # None = not cleared within MAX_MONTHS
    interest_paid: Decimal = Decimal("0")


@dataclass
class PromoCliff:
    debt_id: int
    name: str
    promo_ends_on: date
    balance_at_expiry: Decimal
    reverting_apr: Decimal
    extra_yearly_interest: Decimal  # what the leftover balance costs per year after the cliff


@dataclass
class PlanResult:
    strategy: str
    months: int
    debt_free_date: date | None
    total_interest: Decimal
    total_paid: Decimal
    monthly_budget: Decimal
    debts: list[DebtResult] = field(default_factory=list)
    # (month_start, total_remaining_balance) series for charting
    balance_series: list[tuple[date, Decimal]] = field(default_factory=list)
    promo_cliffs: list[PromoCliff] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    unpayable: bool = False


def add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(
        d.day,
        [
            31,
            29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
            31,
            30,
            31,
            30,
            31,
            31,
            30,
            31,
            30,
            31,
        ][month - 1],
    )
    return date(year, month, day)


def effective_apr(debt: DebtInput, on: date) -> Decimal:
    if debt.promo_apr is not None and debt.promo_ends_on is not None and on <= debt.promo_ends_on:
        return debt.promo_apr
    return debt.apr if debt.apr is not None else Decimal("0")


def _resolved_minimum(debt: DebtInput) -> tuple[Decimal, bool]:
    if debt.minimum_payment is not None and debt.minimum_payment > 0:
        return debt.minimum_payment, False
    assumed = max(
        (debt.balance * DEFAULT_MIN_PCT).quantize(TWO_DP, rounding=ROUND_HALF_UP),
        DEFAULT_MIN_FLOOR,
    )
    return assumed, True


def simulate_payoff(
    debts: list[DebtInput],
    strategy: str = "avalanche",  # "avalanche" | "snowball" | "minimum"
    extra_monthly: Decimal = Decimal("0"),
    snowflakes: dict[int, Decimal] | None = None,  # 1-based month offset -> amount
    start: date | None = None,
) -> PlanResult:
    start = start or date.today()
    snowflakes = snowflakes or {}
    live = [d for d in debts if d.balance > 0]

    minimums: dict[int, Decimal] = {}
    assumptions: list[str] = []
    for d in live:
        m, assumed = _resolved_minimum(d)
        minimums[d.id] = m
        if assumed:
            assumptions.append(f"{d.name}: no minimum payment set — assumed max(2% of balance, £25) = {m}")

    budget = sum(minimums.values(), Decimal("0")) + (extra_monthly if strategy != "minimum" else Decimal("0"))
    balances = {d.id: d.balance for d in live}
    interest_paid = {d.id: Decimal("0") for d in live}
    payoff_month: dict[int, int] = {}
    series: list[tuple[date, Decimal]] = [(start, sum(balances.values(), Decimal("0")))]
    total_interest = Decimal("0")
    total_paid = Decimal("0")
    cliffs: dict[int, PromoCliff] = {}

    month = 0
    while any(b > 0 for b in balances.values()) and month < MAX_MONTHS:
        month += 1
        month_date = add_months(start, month)

        # 1) Accrue interest.
        for d in live:
            if balances[d.id] <= 0:
                continue
            rate = effective_apr(d, month_date) / Decimal("100") / Decimal("12")
            interest = (balances[d.id] * rate).quantize(TWO_DP, rounding=ROUND_HALF_UP)
            balances[d.id] += interest
            interest_paid[d.id] += interest
            total_interest += interest

        # Record promo-cliff exposure the first month after each promo ends.
        for d in live:
            if (
                d.promo_apr is not None
                and d.promo_ends_on is not None
                and d.id not in cliffs
                and month_date > d.promo_ends_on
                and balances[d.id] > 0
            ):
                apr = d.apr if d.apr is not None else Decimal("0")
                cliffs[d.id] = PromoCliff(
                    debt_id=d.id,
                    name=d.name,
                    promo_ends_on=d.promo_ends_on,
                    balance_at_expiry=balances[d.id].quantize(TWO_DP),
                    reverting_apr=apr,
                    extra_yearly_interest=(balances[d.id] * apr / Decimal("100")).quantize(TWO_DP),
                )

        # 2) Pay minimums.
        pool = budget + (snowflakes.get(month, Decimal("0")) if strategy != "minimum" else Decimal("0"))
        for d in live:
            if balances[d.id] <= 0:
                continue
            pay = min(minimums[d.id], balances[d.id], pool)
            balances[d.id] -= pay
            pool -= pay
            total_paid += pay
            if balances[d.id] <= 0:
                payoff_month.setdefault(d.id, month)

        # 3) Remainder attacks the target debt (then cascades to the next).
        if strategy != "minimum":
            while pool > 0:
                open_debts = [d for d in live if balances[d.id] > 0]
                if not open_debts:
                    break
                if strategy == "snowball":
                    target = min(open_debts, key=lambda d: (balances[d.id], d.id))
                else:  # avalanche
                    target = max(open_debts, key=lambda d: (effective_apr(d, month_date), -balances[d.id]))
                pay = min(pool, balances[target.id])
                balances[target.id] -= pay
                pool -= pay
                total_paid += pay
                if balances[target.id] <= 0:
                    payoff_month.setdefault(target.id, month)

        series.append((month_date, sum((b for b in balances.values() if b > 0), Decimal("0"))))

        # If nothing can ever be paid off (interest outruns budget), bail out.
        if strategy == "minimum" and month > 1 and series[-1][1] >= series[-2][1] > 0:
            outstanding = series[-1][1]
            if outstanding > series[1][1]:  # growing since the start
                break

    unpayable = any(b > 0 for b in balances.values())
    debt_free = None if unpayable else add_months(start, month)

    return PlanResult(
        strategy=strategy,
        months=month,
        debt_free_date=debt_free,
        total_interest=total_interest.quantize(TWO_DP),
        total_paid=total_paid.quantize(TWO_DP),
        monthly_budget=budget.quantize(TWO_DP),
        debts=[
            DebtResult(
                id=d.id,
                name=d.name,
                payoff_date=add_months(start, payoff_month[d.id]) if d.id in payoff_month else None,
                interest_paid=interest_paid[d.id].quantize(TWO_DP),
            )
            for d in live
        ],
        balance_series=[(dt, bal.quantize(TWO_DP)) for dt, bal in series],
        promo_cliffs=sorted(cliffs.values(), key=lambda c: c.promo_ends_on),
        assumptions=assumptions,
        unpayable=unpayable,
    )


def compare_strategies(
    debts: list[DebtInput],
    extra_monthly: Decimal = Decimal("0"),
    snowflakes: dict[int, Decimal] | None = None,
    start: date | None = None,
) -> dict[str, PlanResult]:
    """Baseline (minimums only) vs snowball vs avalanche with the given extra."""
    return {
        "minimum": simulate_payoff(debts, "minimum", Decimal("0"), None, start),
        "snowball": simulate_payoff(debts, "snowball", extra_monthly, snowflakes, start),
        "avalanche": simulate_payoff(debts, "avalanche", extra_monthly, snowflakes, start),
    }
