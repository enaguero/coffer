"""Monthly surplus and the marginal-pound allocation ranking.

Surplus is cash-basis: inflows minus outflows over a calendar month, computed
from imported transactions. The allocation ranking then prices each possible
destination for a spare pound in outcome terms:

- a debt: its effective APR is a guaranteed return — £X/year interest avoided
- a goal: months moved closer to the target date
- emergency runway: months of essential spending gained

Uncategorized activity is reported alongside so the number's honesty is
visible (a half-categorized month produces a suspicious surplus, and the user
should see why).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.services.analytics.debt_plan import DebtInput, marginal_rate
from app.services.analytics.fx import convert

TWO_DP = Decimal("0.01")


def latest_complete_month(dates: list[date], today: date) -> tuple[int, int] | None:
    """The most recent (year, month) strictly before today's month that has
    data, or None. Shared by the surplus endpoint and the digest so both talk
    about the same month."""
    months = sorted({(d.year, d.month) for d in dates})
    complete = [m for m in months if m < (today.year, today.month)]
    return complete[-1] if complete else None


@dataclass
class MonthSummary:
    year: int
    month: int
    income: Decimal
    outflows: Decimal
    surplus: Decimal
    txn_count: int
    uncategorized_count: int
    uncategorized_amount: Decimal  # absolute sum of uncategorized rows


def summarize_month(
    txns: list[tuple[date, Decimal, int | None]],  # (posted_on, amount, category_id)
    year: int,
    month: int,
) -> MonthSummary:
    income = outflows = uncat_amount = Decimal("0")
    count = uncat = 0
    for posted, amount, category_id in txns:
        if posted.year != year or posted.month != month:
            continue
        count += 1
        if amount > 0:
            income += amount
        else:
            outflows += -amount
        if category_id is None:
            uncat += 1
            uncat_amount += abs(amount)
    return MonthSummary(
        year=year,
        month=month,
        income=income.quantize(TWO_DP),
        outflows=outflows.quantize(TWO_DP),
        surplus=(income - outflows).quantize(TWO_DP),
        txn_count=count,
        uncategorized_count=uncat,
        uncategorized_amount=uncat_amount.quantize(TWO_DP),
    )


@dataclass
class AllocationOption:
    kind: str  # "debt" | "goal" | "runway"
    target_id: int | None
    name: str
    # For debts: APR and first-year interest avoided by allocating `amount`.
    apr: Decimal | None
    yearly_interest_saved: Decimal | None
    # For goals: how much sooner the target date arrives.
    months_earlier: Decimal | None
    # For runway: months of essential spending gained.
    runway_months_gained: Decimal | None
    note: str


def rank_allocations(
    amount: Decimal,
    debts: list[DebtInput],
    goals: list[tuple[int, str, Decimal, Decimal, date | None]],  # (id, name, target, current, target_date)
    monthly_floor: Decimal | None,
    today: date | None = None,
    display_currency: str | None = None,
    rates: dict[str, Decimal] | None = None,
) -> list[AllocationOption]:
    today = today or date.today()
    rates = rates or {}
    options: list[AllocationOption] = []
    unconverted: list[AllocationOption] = []

    rate_of = {d.id: marginal_rate(d, today) for d in debts}
    for d in sorted(debts, key=lambda d: rate_of[d.id], reverse=True):
        if d.balance <= 0:
            continue
        # Foreign-currency balances convert at the saved rate; without one the
        # debt is excluded from the figures and flagged — never silently mixed.
        balance = d.balance
        converted_suffix = ""
        if d.currency is not None and display_currency is not None and d.currency != display_currency:
            converted = convert(d.balance, d.currency, display_currency, rates)
            if converted is None:
                unconverted.append(
                    AllocationOption(
                        kind="debt",
                        target_id=d.id,
                        name=d.name,
                        apr=None,
                        yearly_interest_saved=None,
                        months_earlier=None,
                        runway_months_gained=None,
                        note=f"Balance held in {d.currency} with no saved FX rate — excluded from the figures",
                    )
                )
                continue
            balance = converted
            converted_suffix = f" (balance converted from {d.currency})"
        apr = rate_of[d.id]
        applied = min(amount, balance)
        saved = (applied * apr / Decimal("100")).quantize(TWO_DP, rounding=ROUND_HALF_UP)
        if d.repayment_type == "flat":
            note = "Flat-rate loan — interest is fixed on the original principal, so prepaying saves no interest"
        elif d.repayment_type == "statement_only":
            note = (
                f"Estimated {apr}% return (rate inferred from installment and term) — avoids ~£{saved}/year in interest"
            )
        elif d.promo_apr is not None and d.promo_ends_on is not None and today <= d.promo_ends_on:
            note = (
                f"0%/promo until {d.promo_ends_on.isoformat()} — clearing before the cliff "
                f"avoids reverting to {d.apr or 0}%"
            )
        else:
            note = f"Guaranteed {apr}% return — avoids ~£{saved}/year in interest"
        options.append(
            AllocationOption(
                kind="debt",
                target_id=d.id,
                name=d.name,
                apr=apr,
                yearly_interest_saved=saved,
                months_earlier=None,
                runway_months_gained=None,
                note=note + converted_suffix,
            )
        )
    options.extend(unconverted)  # flagged, after the ranked debts

    for goal_id, name, target, current, target_date in goals:
        remaining = target - current
        if remaining <= 0:
            continue
        months_earlier: Decimal | None = None
        if target_date is not None and target_date > today:
            months_left = Decimal((target_date - today).days) / Decimal("30.44")
            if months_left > 0:
                required_monthly = remaining / months_left
                if required_monthly > 0:
                    months_earlier = (min(amount, remaining) / required_monthly).quantize(Decimal("0.1"))
        options.append(
            AllocationOption(
                kind="goal",
                target_id=goal_id,
                name=name,
                apr=None,
                yearly_interest_saved=None,
                months_earlier=months_earlier,
                runway_months_gained=None,
                note=(
                    f"Reaches the target ~{months_earlier} months sooner"
                    if months_earlier is not None
                    else "Progress toward target"
                ),
            )
        )

    if monthly_floor is not None and monthly_floor > 0:
        gained = (amount / monthly_floor).quantize(Decimal("0.01"))
        options.append(
            AllocationOption(
                kind="runway",
                target_id=None,
                name="Emergency runway",
                apr=None,
                yearly_interest_saved=None,
                months_earlier=None,
                runway_months_gained=gained,
                note=f"Adds {gained} months of essential-spending cover",
            )
        )

    return options
