"""UK tax-year allowance metering for wrapped accounts (ISA / LISA / pension).

The UK tax year runs 6 April to 5 April. Annual allowances (verify against
HMRC when a Budget changes them):

- ISA: £20,000 across all ISA types
- LISA: £4,000 — and LISA contributions ALSO count toward the £20,000 ISA total
- Pension annual allowance: £60,000 gross. Statement credits understate this:
  relief-at-source arrives net of basic-rate relief, and employer/net-pay
  contributions never appear on a bank statement — the UI says so.

"Contributions" are positive transactions into wrapper-tagged accounts within
the tax year — the statement-first approximation. Transfers between your own
ISAs would be miscounted as new contributions, and rows described as interest
are excluded by the caller; the UI notes both limitations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.models.account import UkWrapper

ISA_ALLOWANCE = Decimal("20000")
LISA_ALLOWANCE = Decimal("4000")
PENSION_ANNUAL_ALLOWANCE = Decimal("60000")

TAX_YEAR_START_MONTH = 4
TAX_YEAR_START_DAY = 6


def tax_year_bounds(today: date) -> tuple[date, date]:
    """(first day, last day) of the UK tax year containing `today`."""
    start_this_calendar_year = date(today.year, TAX_YEAR_START_MONTH, TAX_YEAR_START_DAY)
    if today >= start_this_calendar_year:
        start = start_this_calendar_year
    else:
        start = date(today.year - 1, TAX_YEAR_START_MONTH, TAX_YEAR_START_DAY)
    # Day before next year's start — robust however the start constants change.
    end = date(start.year + 1, start.month, start.day) - timedelta(days=1)
    return start, end


@dataclass
class AllowanceMeter:
    wrapper: UkWrapper
    allowance: Decimal
    used: Decimal
    remaining: Decimal
    # ISA meter only: how much of the used total came from LISA contributions
    # (they count against both limits).
    lisa_portion: Decimal = Decimal("0")


def compute_allowances(
    contributions: dict[UkWrapper, Decimal],
) -> list[AllowanceMeter]:
    """Meters for the wrappers present in `contributions` (tax-year totals)."""
    meters: list[AllowanceMeter] = []
    isa_direct = contributions.get(UkWrapper.ISA, Decimal("0"))
    lisa = contributions.get(UkWrapper.LISA, Decimal("0"))
    pension = contributions.get(UkWrapper.PENSION, Decimal("0"))

    if UkWrapper.ISA in contributions or UkWrapper.LISA in contributions:
        # LISA money consumes the shared £20k ISA allowance too.
        isa_used = isa_direct + lisa
        meters.append(
            AllowanceMeter(
                wrapper=UkWrapper.ISA,
                allowance=ISA_ALLOWANCE,
                used=isa_used,
                remaining=max(ISA_ALLOWANCE - isa_used, Decimal("0")),
                lisa_portion=lisa,
            )
        )
    if UkWrapper.LISA in contributions:
        meters.append(
            AllowanceMeter(
                wrapper=UkWrapper.LISA,
                allowance=LISA_ALLOWANCE,
                used=lisa,
                remaining=max(LISA_ALLOWANCE - lisa, Decimal("0")),
            )
        )
    if UkWrapper.PENSION in contributions:
        meters.append(
            AllowanceMeter(
                wrapper=UkWrapper.PENSION,
                allowance=PENSION_ANNUAL_ALLOWANCE,
                used=pension,
                remaining=max(PENSION_ANNUAL_ALLOWANCE - pension, Decimal("0")),
            )
        )
    return meters
