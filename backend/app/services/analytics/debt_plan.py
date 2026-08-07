"""Debt payoff simulation: avalanche / snowball / minimum-only / fixed-priority,
promo-APR aware, mechanics-aware per repayment type, with one-off extra
payments ("snowflakes").

Model (standard for payoff calculators):
- Interest accrues monthly per the debt's mechanics (`monthly_interest`):
  effective rate × running balance for revolving/amortized, rate × original
  principal for flat (stopping at ends_on), inferred rate × balance for
  statement-only (the rate is inferred once at plan start and reused).
- The total monthly budget is fixed: sum of starting contractual payments
  (`expected_payment` — installments for fixed-installment types, resolved
  minimums for revolving) plus the chosen extra. When a debt clears, its
  payment rolls into the pool — that's what makes snowball/avalanche
  outperform paying minimums forever.
- The pool's remainder (after contractual payments) goes to one target debt:
  highest current rate (avalanche), smallest balance (snowball), or the first
  open debt in an explicit `priority` list (strategy="fixed" — the optimizer's
  seam). Flat loans are never targeted: their interest is fixed on the
  original principal, so prepaying saves nothing; when only flat loans remain
  open the remainder is reported as per-month uncommitted surplus instead.
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
# The repayment types whose contractual payment is a fixed installment that
# supersedes minimum_payment everywhere downstream (see models/debt.py).
FIXED_INSTALLMENT_TYPES = frozenset({"amortized", "flat", "statement_only"})
# Bracket ceiling for statement-only rate inference, % APR. An implied rate
# above this is treated as degenerate input, not a plausible loan.
STATEMENT_APR_CEILING = Decimal("200")


@dataclass
class DebtInput:
    id: int
    name: str
    balance: Decimal
    apr: Decimal | None  # reverting/standard APR, e.g. 24.9
    promo_apr: Decimal | None = None
    promo_ends_on: date | None = None
    minimum_payment: Decimal | None = None
    repayment_type: str = "revolving"
    installment: Decimal | None = None
    original_principal: Decimal | None = None
    ends_on: date | None = None
    currency: str | None = None  # None = the user's display currency

    @classmethod
    def from_model(cls, d) -> DebtInput:
        """Build from a Debt ORM row (duck-typed — keeps this module ORM-free)."""
        return cls(
            id=d.id,
            name=d.name,
            balance=d.current_balance,
            apr=d.interest_rate_apr,
            promo_apr=d.promo_apr,
            promo_ends_on=d.promo_ends_on,
            minimum_payment=d.minimum_payment,
            repayment_type=str(d.repayment_type),
            installment=d.installment_amount,
            original_principal=d.original_principal,
            ends_on=d.ends_on,
            currency=d.currency,
        )


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
class ScheduleMonth:
    """One simulated month: what each debt was paid, and what the budget
    couldn't place (only flat loans still open, or everything cleared)."""

    month: date
    payments: dict[int, Decimal]  # debt_id -> amount paid this month (absent = nothing paid)
    uncommitted: Decimal = Decimal("0")


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
    # Per-debt per-month payments (uncommitted surplus rides on each row).
    schedule: list[ScheduleMonth] = field(default_factory=list)
    # True when the run was abandoned early because its running total interest
    # exceeded the optimizer's incumbent bound — every other field is partial.
    pruned: bool = False


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


# ---- Repayment-type mechanics (see the behavior matrix in docs/plans) ---------


def _months_between(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + (end.month - start.month)


def _annuity_installment(balance: Decimal, monthly_rate: Decimal, months: int) -> Decimal:
    """The level payment that amortizes `balance` over `months` at `monthly_rate`
    (a fraction per month). Strictly increasing in the rate — the property the
    bisection in infer_statement_rate relies on."""
    if monthly_rate == 0:
        return balance / months
    growth = (Decimal("1") + monthly_rate) ** months
    return balance * monthly_rate * growth / (growth - 1)


def infer_statement_rate(debt: DebtInput, on: date) -> tuple[Decimal, str]:
    """Solve the annuity equation for the constant rate implied by (current
    balance, installment, months remaining to ends_on) — bisection on the
    monthly rate, returned as % APR with an assumption string labelling it
    estimated. Degenerate input (no positive rate satisfies the equation, or a
    non-positive term) returns a zero-or-clamped best effort with an explicit
    assumption — never an exception."""
    balance, installment = debt.balance, debt.installment
    if installment is None or installment <= 0 or balance is None or balance <= 0:
        return Decimal("0"), f"{debt.name}: no installment/balance to infer a rate from — estimated 0% APR"
    months = _months_between(on, debt.ends_on) if debt.ends_on is not None else 0
    if months <= 0:
        return Decimal("0"), f"{debt.name}: no remaining term to infer a rate from — estimated 0% APR"

    at_zero = _annuity_installment(balance, Decimal("0"), months)
    if installment <= at_zero:
        if installment == at_zero:
            return Decimal("0"), f"{debt.name}: rate estimated at 0% APR from balance, installment, and remaining term"
        return Decimal("0"), (
            f"{debt.name}: installment {installment} is too small to amortize {balance} "
            f"over {months} months at any rate — estimated 0% APR"
        )

    low = Decimal("0")
    high = STATEMENT_APR_CEILING / Decimal("100") / Decimal("12")
    if installment >= _annuity_installment(balance, high, months):
        return STATEMENT_APR_CEILING, (
            f"{debt.name}: entered terms imply a rate above {STATEMENT_APR_CEILING}% APR — "
            f"estimated {STATEMENT_APR_CEILING}% APR (check the balance, installment, and end date)"
        )
    for _ in range(80):
        mid = (low + high) / 2
        if _annuity_installment(balance, mid, months) < installment:
            low = mid
        else:
            high = mid
        if high - low < Decimal("1e-9"):
            break
    apr = ((low + high) / 2 * Decimal("12") * Decimal("100")).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return apr, f"{debt.name}: rate estimated at {apr}% APR from balance, installment, and remaining term"


def monthly_interest(
    debt: DebtInput,
    balance: Decimal,
    on: date,
    statement_apr: Decimal | None = None,
) -> Decimal:
    """One month of interest on `balance` per the debt's mechanics:

    - revolving / amortized: effective (promo-aware) rate × current balance
    - flat: rate × ORIGINAL principal — installments never shrink — and accrual
      stops entirely once past ends_on, so a residual balance can't spiral
    - statement_only: inferred rate × current balance (pass `statement_apr` to
      reuse a rate inferred once at plan start; otherwise inferred here)
    """
    if debt.repayment_type == "flat":
        if debt.ends_on is not None and on > debt.ends_on:
            return Decimal("0.00")
        principal = debt.original_principal if debt.original_principal is not None else Decimal("0")
        apr = debt.apr if debt.apr is not None else Decimal("0")
        return (principal * apr / Decimal("100") / Decimal("12")).quantize(TWO_DP, rounding=ROUND_HALF_UP)
    if debt.repayment_type == "statement_only":
        apr = statement_apr if statement_apr is not None else infer_statement_rate(debt, on)[0]
        return (balance * apr / Decimal("100") / Decimal("12")).quantize(TWO_DP, rounding=ROUND_HALF_UP)
    rate = effective_apr(debt, on) / Decimal("100") / Decimal("12")
    return (balance * rate).quantize(TWO_DP, rounding=ROUND_HALF_UP)


def expected_payment(debt: DebtInput) -> tuple[Decimal, str | None]:
    """The month's contractual payment, following _resolved_minimum's
    (value, assumption) shape. Fixed-installment types pay the installment —
    superseding minimum_payment — and keep paying it past ends_on until the
    balance clears; revolving debts pay the resolved minimum."""
    if debt.repayment_type in FIXED_INSTALLMENT_TYPES and debt.installment is not None and debt.installment > 0:
        return debt.installment, None
    m, assumed = _resolved_minimum(debt)
    if not assumed:
        return m, None
    missing = "installment" if debt.repayment_type in FIXED_INSTALLMENT_TYPES else "minimum payment"
    return m, f"{debt.name}: no {missing} set — assumed max(2% of balance, £25) = {m}"


def ends_on_overrun_assumption(debt: DebtInput, payoff_on: date | None) -> str | None:
    """'Entered terms imply payoff N months after the stated end date' — the
    reconciliation warning for a fixed-installment debt whose balance outlasts
    installment × remaining term. None when the terms reconcile (payoff at or
    before ends_on), for revolving debts, or when no end date is stated."""
    if debt.repayment_type not in FIXED_INSTALLMENT_TYPES or debt.ends_on is None or payoff_on is None:
        return None
    overrun = _months_between(debt.ends_on, payoff_on)
    if overrun <= 0:
        return None
    return (
        f"{debt.name}: entered terms imply payoff {overrun} month{'s' if overrun != 1 else ''} "
        f"after the stated end date ({debt.ends_on.isoformat()})"
    )


def marginal_rate(debt: DebtInput, on: date) -> Decimal:
    """What a prepaid pound earns, per the debt's mechanics — the
    mechanics-aware replacement for effective_apr in allocation ranking.
    Flat loans return zero: interest is fixed on the original principal, so
    prepayment saves nothing (v1 — no early-settlement rebate modelling).
    Statement-only debts return the inferred (estimated) rate."""
    if debt.repayment_type == "flat":
        return Decimal("0")
    if debt.repayment_type == "statement_only":
        return infer_statement_rate(debt, on)[0]
    return effective_apr(debt, on)


def _cascade_rate(debt: DebtInput, on: date, statement_rates: dict[int, Decimal]) -> Decimal:
    """Avalanche's targeting rate: mechanics-aware, reusing the rates inferred
    at plan start for statement-only debts (flat never reaches the cascade)."""
    if debt.repayment_type == "statement_only":
        return statement_rates.get(debt.id, Decimal("0"))
    return effective_apr(debt, on)


def simulate_payoff(
    debts: list[DebtInput],
    strategy: str = "avalanche",  # "avalanche" | "snowball" | "minimum" | "fixed"
    extra_monthly: Decimal = Decimal("0"),
    snowflakes: dict[int, Decimal] | None = None,  # 1-based month offset -> amount
    start: date | None = None,
    *,
    priority: list[int] | None = None,  # strategy="fixed": debt ids, first attacked first
    interest_bound: Decimal | None = None,  # abort once running interest exceeds this (optimizer pruning)
) -> PlanResult:
    start = start or date.today()
    snowflakes = snowflakes or {}
    live = [d for d in debts if d.balance > 0]

    payments_due: dict[int, Decimal] = {}
    assumptions: list[str] = []
    for d in live:
        pay, note = expected_payment(d)
        payments_due[d.id] = pay
        if note:
            assumptions.append(note)

    # Statement-only rates are inferred once at plan start and reused every
    # month — the entered terms don't change mid-plan.
    statement_rates: dict[int, Decimal] = {}
    for d in live:
        if d.repayment_type == "statement_only":
            rate, note = infer_statement_rate(d, start)
            statement_rates[d.id] = rate
            assumptions.append(note)

    if strategy != "minimum":
        for d in live:
            if d.repayment_type == "flat":
                assumptions.append(
                    f"{d.name}: flat interest is charged on the original principal — prepaying saves no interest"
                )

    priority_rank = {debt_id: i for i, debt_id in enumerate(priority)} if priority else {}

    budget = sum(payments_due.values(), Decimal("0")) + (extra_monthly if strategy != "minimum" else Decimal("0"))
    balances = {d.id: d.balance for d in live}
    interest_paid = {d.id: Decimal("0") for d in live}
    payoff_month: dict[int, int] = {}
    series: list[tuple[date, Decimal]] = [(start, sum(balances.values(), Decimal("0")))]
    schedule: list[ScheduleMonth] = []
    total_interest = Decimal("0")
    total_paid = Decimal("0")
    cliffs: dict[int, PromoCliff] = {}
    pruned = False

    month = 0
    while any(b > 0 for b in balances.values()) and month < MAX_MONTHS:
        month += 1
        month_date = add_months(start, month)

        # 1) Accrue interest per the debt's mechanics.
        for d in live:
            if balances[d.id] <= 0:
                continue
            interest = monthly_interest(d, balances[d.id], month_date, statement_apr=statement_rates.get(d.id))
            balances[d.id] += interest
            interest_paid[d.id] += interest
            total_interest += interest

        # Running interest only grows, so once past the incumbent bound this
        # ordering can never win — abandon it (the caller discards the result).
        if interest_bound is not None and total_interest > interest_bound:
            pruned = True
            break

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

        # 2) Pay each debt's contractual amount (installment or minimum).
        pool = budget + (snowflakes.get(month, Decimal("0")) if strategy != "minimum" else Decimal("0"))
        paid_this_month: dict[int, Decimal] = {}
        for d in live:
            if balances[d.id] <= 0:
                continue
            pay = min(payments_due[d.id], balances[d.id], pool)
            balances[d.id] -= pay
            pool -= pay
            total_paid += pay
            if pay > 0:
                paid_this_month[d.id] = pay
            if balances[d.id] <= 0:
                payoff_month.setdefault(d.id, month)

        # 3) Remainder attacks the target debt (then cascades to the next).
        # Flat loans are never targeted — prepaying them saves no interest — so
        # when only flat loans remain open the remainder stays in the pool and
        # lands on the schedule row as uncommitted surplus.
        if strategy != "minimum":
            while pool > 0:
                open_debts = [d for d in live if balances[d.id] > 0 and d.repayment_type != "flat"]
                if not open_debts:
                    break
                if strategy == "snowball":
                    target = min(open_debts, key=lambda d: (balances[d.id], d.id))
                elif strategy == "fixed":
                    target = min(open_debts, key=lambda d: priority_rank.get(d.id, len(priority_rank)))
                else:  # avalanche
                    target = max(
                        open_debts, key=lambda d: (_cascade_rate(d, month_date, statement_rates), -balances[d.id])
                    )
                pay = min(pool, balances[target.id])
                balances[target.id] -= pay
                pool -= pay
                total_paid += pay
                paid_this_month[target.id] = paid_this_month.get(target.id, Decimal("0")) + pay
                if balances[target.id] <= 0:
                    payoff_month.setdefault(target.id, month)

        schedule.append(
            ScheduleMonth(
                month=month_date,
                payments={debt_id: amount.quantize(TWO_DP) for debt_id, amount in paid_this_month.items()},
                uncommitted=pool.quantize(TWO_DP),
            )
        )
        series.append((month_date, sum((b for b in balances.values() if b > 0), Decimal("0"))))

        # If nothing can ever be paid off (interest outruns budget), bail out.
        if strategy == "minimum" and month > 1 and series[-1][1] >= series[-2][1] > 0:
            outstanding = series[-1][1]
            if outstanding > series[1][1]:  # growing since the start
                break

    unpayable = any(b > 0 for b in balances.values())
    debt_free = None if unpayable else add_months(start, month)

    # Post-ends_on overruns: entered terms that imply payoff after the stated
    # end date get the reconciliation warning.
    for d in live:
        note = ends_on_overrun_assumption(d, add_months(start, payoff_month[d.id]) if d.id in payoff_month else None)
        if note:
            assumptions.append(note)

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
        schedule=schedule,
        pruned=pruned,
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
