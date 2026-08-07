"""Shortest-path payoff optimizer: the best allocation over a candidate class,
never worse than any displayed strategy.

Candidate class:
- ≤ 6 optimizable (non-flat) debts: every payoff priority ordering, each
  simulated with `simulate_payoff(strategy="fixed")` and abandoned mid-run the
  moment its running interest exceeds the incumbent best's total interest.
- > 6 debts: a single greedy ordering by mechanics-aware marginal rate with a
  promo-cliff lookahead — a promo debt whose balance survives its window at
  contractual payments ranks by the reverting APR, not the teaser rate.
- Always unioned with the minimum/snowball/avalanche comparison runs: dynamic
  avalanche retargets monthly by current rate and can beat every static
  ordering on promo-cliff portfolios, so "optimal ≤ every displayed strategy"
  holds by construction.

Objective: minimize total interest; months to debt-free breaks ties. Flat
loans never receive extra allocation (their interest is fixed on the original
principal — prepaying saves no interest); an all-flat portfolio degenerates to
minimums-only with that explanation in the assumptions. Everything here is
computed arithmetic with assumptions visible, never advice.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from itertools import permutations

from app.services.analytics.debt_plan import (
    DebtInput,
    PlanResult,
    _months_between,
    compare_strategies,
    expected_payment,
    marginal_rate,
    simulate_payoff,
)

# Beyond this many optimizable debts, exhaustive ordering search (n! simulator
# runs) leaves the latency budget even with pruning — fall back to greedy.
MAX_EXHAUSTIVE_DEBTS = 6

ALL_FLAT_ASSUMPTION = (
    "All debts charge flat interest on the original principal — prepaying saves no interest, "
    "so the optimal plan pays the contractual installments only"
)


def _key(run: PlanResult) -> tuple[bool, Decimal, int]:
    """Payable beats unpayable; then least total interest; months tie-break."""
    return (run.unpayable, run.total_interest, run.months)


def _greedy_priority(optimizable: list[DebtInput], start: date) -> list[int]:
    """One ordering by marginal rate with promo-cliff lookahead: a promo debt
    whose balance outlives its window at contractual payments is ranked by the
    reverting APR (the rate a prepaid pound will actually face)."""

    def rate(d: DebtInput) -> Decimal:
        r = marginal_rate(d, start)
        if d.promo_apr is not None and d.promo_ends_on is not None and start <= d.promo_ends_on and d.apr is not None:
            months_left = max(_months_between(start, d.promo_ends_on), 0)
            pay, _ = expected_payment(d)
            if d.balance - pay * months_left > 0:
                r = max(r, d.apr)
        return r

    # Higher rate first; ties go to the smaller balance (clears sooner, rolls
    # its payment into the pool earlier).
    return [d.id for d in sorted(optimizable, key=lambda d: (rate(d), -d.balance), reverse=True)]


def optimize(
    debts: list[DebtInput],
    extra_monthly: Decimal = Decimal("0"),
    snowflakes: dict[int, Decimal] | None = None,  # 1-based month offset -> amount
    start: date | None = None,
) -> tuple[PlanResult, dict[str, PlanResult]]:
    """The winning run (strategy label "optimal") plus the standard
    minimum/snowball/avalanche comparison dict it was measured against."""
    start = start or date.today()
    comparison = compare_strategies(debts, extra_monthly, snowflakes, start)

    live = [d for d in debts if d.balance > 0]
    optimizable = [d for d in live if d.repayment_type != "flat"]

    if not optimizable:
        # Nothing prepayment can improve: the optimal plan is the minimums-only
        # baseline, with the reason on record (unless there are no open debts
        # at all — an empty pool has nothing to explain).
        base = comparison["minimum"]
        extra = [ALL_FLAT_ASSUMPTION] if live else []
        return replace(base, strategy="optimal", assumptions=[*base.assumptions, *extra]), comparison

    best = min(comparison.values(), key=_key)

    if comparison["avalanche"].unpayable:
        # The full budget can't outrun the arithmetic under the best dynamic
        # strategy — reordering priorities won't change that, and enumerating
        # n! runs of 600 truncated months would be pure waste.
        candidates: list[list[int]] = []
    elif len(optimizable) <= MAX_EXHAUSTIVE_DEBTS:
        candidates = [list(p) for p in permutations([d.id for d in optimizable])]
    else:
        candidates = [_greedy_priority(optimizable, start)]

    for priority in candidates:
        bound = None if best.unpayable else best.total_interest
        run = simulate_payoff(debts, "fixed", extra_monthly, snowflakes, start, priority=priority, interest_bound=bound)
        if run.pruned:
            continue
        if _key(run) < _key(best):
            best = run

    return replace(best, strategy="optimal"), comparison
