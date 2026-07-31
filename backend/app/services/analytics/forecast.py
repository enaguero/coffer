"""Forward cashflow projection from detected recurring items.

Projects the combined liquid balance day by day, applying each active
recurring item's expected occurrences. Warnings fire at a user-set reserve
threshold, not at zero — by the time a balance touches zero the fees have
already landed.

Debt due-days are listed in the bill calendar for awareness but are NOT
projected: real debt payments already appear in history and are therefore
captured as recurring items — projecting the register too would double-count.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from app.services.analytics.recurring import RecurringItem

MIN_CONFIDENCE = 0.45


@dataclass
class ForecastEvent:
    on: date
    description: str
    amount: Decimal
    cadence: str
    is_income: bool


@dataclass
class DueMarker:
    """A debt due-day annotation (not part of the projection)."""

    on: date
    name: str
    minimum_payment: Decimal | None


@dataclass
class ForecastResult:
    start_balance: Decimal
    reserve: Decimal
    days: int
    series: list[tuple[date, Decimal]] = field(default_factory=list)
    events: list[ForecastEvent] = field(default_factory=list)
    due_markers: list[DueMarker] = field(default_factory=list)
    min_balance: Decimal = Decimal("0")
    min_balance_date: date | None = None
    first_below_reserve: date | None = None
    first_below_zero: date | None = None
    safe_to_commit: Decimal = Decimal("0")


def _occurrences(item: RecurringItem, start: date, end: date) -> list[date]:
    step = timedelta(days=item.cadence_days)
    on = item.next_expected
    while on < start:
        on += step
    out = []
    while on <= end:
        out.append(on)
        on += step
    return out


def next_due_date(due_day: int, today: date) -> date:
    """Next calendar occurrence of a day-of-month, clamping short months."""
    year, month = today.year, today.month
    while True:
        last_day = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)).day
        candidate = date(year, month, min(due_day, last_day))
        if candidate >= today:
            return candidate
        month += 1
        if month > 12:
            month, year = 1, year + 1


def project(
    start_balance: Decimal,
    items: list[RecurringItem],
    days: int = 60,
    reserve: Decimal = Decimal("0"),
    today: date | None = None,
    debt_due_days: list[tuple[str, int, Decimal | None]] | None = None,  # (name, day, minimum)
) -> ForecastResult:
    today = today or date.today()
    end = today + timedelta(days=days)

    usable = [i for i in items if i.active and i.confidence >= MIN_CONFIDENCE]
    events: list[ForecastEvent] = []
    for item in usable:
        for on in _occurrences(item, today + timedelta(days=1), end):
            events.append(
                ForecastEvent(
                    on=on,
                    description=item.description,
                    amount=item.typical_amount,
                    cadence=item.cadence,
                    is_income=item.is_income,
                )
            )
    events.sort(key=lambda e: (e.on, -abs(e.amount)))

    by_day: dict[date, Decimal] = {}
    for e in events:
        by_day[e.on] = by_day.get(e.on, Decimal("0")) + e.amount

    series: list[tuple[date, Decimal]] = [(today, start_balance)]
    balance = start_balance
    min_balance, min_date = start_balance, today
    first_below_reserve: date | None = None
    first_below_zero: date | None = None
    for offset in range(1, days + 1):
        on = today + timedelta(days=offset)
        balance += by_day.get(on, Decimal("0"))
        series.append((on, balance.quantize(Decimal("0.01"))))
        if balance < min_balance:
            min_balance, min_date = balance, on
        if first_below_reserve is None and balance < reserve:
            first_below_reserve = on
        if first_below_zero is None and balance < 0:
            first_below_zero = on

    markers = [
        DueMarker(on=next_due_date(day, today), name=name, minimum_payment=minimum)
        for name, day, minimum in (debt_due_days or [])
        if 1 <= day <= 31
    ]
    markers.sort(key=lambda m: m.on)

    return ForecastResult(
        start_balance=start_balance,
        reserve=reserve,
        days=days,
        series=series,
        events=events,
        due_markers=markers,
        min_balance=min_balance.quantize(Decimal("0.01")),
        min_balance_date=min_date,
        first_below_reserve=first_below_reserve,
        first_below_zero=first_below_zero,
        safe_to_commit=max(Decimal("0"), (min_balance - reserve).quantize(Decimal("0.01"))),
    )
