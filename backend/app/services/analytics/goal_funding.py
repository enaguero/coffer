"""Pure goal-funding math: required monthly contribution, on-track verdict,
and projected arrival date. Shared by the goals API and the surplus allocator
so the two surfaces can never disagree."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_CEILING, Decimal

from app.services.analytics.debt_plan import add_months

DAYS_PER_MONTH = Decimal("30.44")
# Beyond this the projection is meaningless (and unbounded month counts would
# overflow `date`): report "no arrival date" instead.
MAX_PROJECTED_MONTHS = 600


@dataclass
class GoalFunding:
    remaining: Decimal
    # (target - current) / months to target date; rounds UP — "needed per
    # month" understated by rounding would fake an on-track verdict.
    required_monthly: Decimal | None
    # True when met; False when the committed contribution falls short of the
    # requirement OR the deadline has passed unmet; None when unknowable.
    on_track: bool | None
    projected_date: date | None


def compute_funding(
    target: Decimal,
    current: Decimal,
    target_date: date | None,
    monthly_contribution: Decimal | None,
    today: date,
) -> GoalFunding:
    remaining = target - current

    required_monthly: Decimal | None = None
    if remaining > 0 and target_date is not None and target_date > today:
        months_left = Decimal((target_date - today).days) / DAYS_PER_MONTH
        if months_left > 0:
            required_monthly = (remaining / months_left).quantize(Decimal("0.01"), rounding=ROUND_CEILING)

    on_track: bool | None = None
    if remaining <= 0:
        on_track = True
    elif target_date is not None and target_date <= today:
        on_track = False  # deadline passed, target unmet — overdue is "behind"
    elif required_monthly is not None and monthly_contribution is not None:
        on_track = monthly_contribution >= required_monthly

    projected_date: date | None = None
    if remaining > 0 and monthly_contribution and monthly_contribution > 0:
        months = (remaining / monthly_contribution).quantize(Decimal("1"), rounding=ROUND_CEILING)
        if months <= MAX_PROJECTED_MONTHS:
            projected_date = add_months(today, int(months))

    return GoalFunding(
        remaining=remaining,
        required_monthly=required_monthly,
        on_track=on_track,
        projected_date=projected_date,
    )
