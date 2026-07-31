"""Recurring-transaction detection from imported history.

Groups transactions by (account, normalized merchant, direction), then checks
whether the gaps between occurrences fit a known cadence (weekly, fortnightly,
four-weekly/monthly, quarterly, annual) with tolerable jitter. Amounts may
drift (utilities); heavy drift lowers confidence rather than rejecting.

This is the enabling primitive for the forecast, the bill calendar, surplus
insights, and raise detection — it must stay deterministic and explainable.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

MIN_OCCURRENCES = 3

# (label, canonical days, min gap, max gap, jitter tolerance in days)
_CADENCES = (
    ("weekly", 7, 5, 9, 2),
    ("fortnightly", 14, 12, 17, 3),
    ("four-weekly", 28, 25, 29, 3),
    ("monthly", 30, 28, 33, 5),
    ("quarterly", 91, 80, 100, 14),
    ("annual", 365, 340, 395, 21),
)

_NORMALIZE_RE = re.compile(r"[\d]+|[^\w\s]")
_WS_RE = re.compile(r"\s+")


def normalize_merchant(description: str) -> str:
    cleaned = _NORMALIZE_RE.sub(" ", description.upper())
    return _WS_RE.sub(" ", cleaned).strip()[:32]


@dataclass
class TxnLite:
    account_id: int
    posted_on: date
    description: str
    amount: Decimal
    category_id: int | None = None


@dataclass
class RecurringItem:
    account_id: int
    merchant_key: str
    description: str  # most recent raw description
    cadence: str
    cadence_days: int
    typical_amount: Decimal  # median, signed
    monthly_equivalent: Decimal  # signed, normalized to per-month
    occurrences: int
    first_seen: date
    last_seen: date
    next_expected: date
    confidence: float  # 0..1
    active: bool
    is_income: bool
    category_id: int | None
    amounts: list[Decimal]  # chronological, for raise detection


def _match_cadence(intervals: list[int]) -> tuple[str, int, int] | None:
    med = statistics.median(intervals)
    for label, canonical, lo, hi, tolerance in _CADENCES:
        if lo <= med <= hi:
            fitting = sum(1 for iv in intervals if abs(iv - med) <= tolerance)
            if fitting / len(intervals) >= 0.6:
                return label, canonical, round(med)
    return None


def detect_recurring(transactions: list[TxnLite], today: date | None = None) -> list[RecurringItem]:
    today = today or date.today()
    groups: dict[tuple[int, str, bool], list[TxnLite]] = {}
    for t in transactions:
        key = normalize_merchant(t.description)
        if not key:
            continue
        groups.setdefault((t.account_id, key, t.amount > 0), []).append(t)

    items: list[RecurringItem] = []
    for (account_id, key, is_income), txns in groups.items():
        # One occurrence per day: same-day rows (e.g. split card payments to
        # the same merchant) collapse into a single dated amount.
        by_day: dict[date, Decimal] = {}
        latest_desc: dict[date, str] = {}
        cat_counts: dict[int | None, int] = {}
        for t in txns:
            by_day[t.posted_on] = by_day.get(t.posted_on, Decimal("0")) + t.amount
            latest_desc[t.posted_on] = t.description
            cat_counts[t.category_id] = cat_counts.get(t.category_id, 0) + 1
        if len(by_day) < MIN_OCCURRENCES:
            continue

        days = sorted(by_day)
        intervals = [(b - a).days for a, b in zip(days, days[1:], strict=False)]
        matched = _match_cadence(intervals)
        if matched is None:
            continue
        label, _canonical, median_days = matched

        amounts = [by_day[d] for d in days]
        abs_amounts = sorted(abs(a) for a in amounts)
        typical_abs = abs_amounts[len(abs_amounts) // 2]
        if typical_abs == 0:
            continue
        spread = float((abs_amounts[-1] - abs_amounts[0]) / typical_abs)
        amount_consistency = 1.0 if spread <= 0.15 else (0.6 if spread <= 0.5 else 0.3)

        med = statistics.median(intervals)
        fitting = sum(1 for iv in intervals if abs(iv - med) <= 5)
        interval_consistency = fitting / len(intervals)

        confidence = round(
            0.5 * min(len(days) / 6, 1.0) + 0.3 * interval_consistency + 0.2 * amount_consistency,
            2,
        )

        typical = sorted(amounts, key=abs)[len(amounts) // 2]
        last_seen = days[-1]
        next_expected = last_seen + timedelta(days=median_days)
        while next_expected < today:
            next_expected += timedelta(days=median_days)

        items.append(
            RecurringItem(
                account_id=account_id,
                merchant_key=key,
                description=latest_desc[last_seen],
                cadence=label,
                cadence_days=median_days,
                typical_amount=typical,
                monthly_equivalent=(typical * Decimal("30.44") / Decimal(median_days)).quantize(Decimal("0.01")),
                occurrences=len(days),
                first_seen=days[0],
                last_seen=last_seen,
                next_expected=next_expected,
                confidence=confidence,
                active=(today - last_seen).days <= 2 * median_days + 5,
                is_income=is_income,
                category_id=max(cat_counts, key=lambda k: cat_counts[k]),
                amounts=amounts,
            )
        )

    items.sort(key=lambda i: abs(i.monthly_equivalent), reverse=True)
    return items


@dataclass
class DetectedRaise:
    description: str
    account_id: int
    cadence: str
    previous_amount: Decimal
    new_amount: Decimal
    monthly_delta: Decimal


def detect_raises(items: list[RecurringItem]) -> list[DetectedRaise]:
    """Sustained step-ups in recurring income (the Save-More-Tomorrow trigger).

    Requires the new level to hold for the 2 most recent occurrences and to
    exceed the prior median by >2% and >£10 — filters one-off bonuses and
    pay-date noise, though a UK April tax-code change can still look like a
    small raise (the UI should say so).
    """
    raises: list[DetectedRaise] = []
    for item in items:
        if not item.is_income or not item.active or item.occurrences < 4:
            continue
        amounts = item.amounts
        recent = amounts[-2:]
        prior = sorted(amounts[:-2])
        baseline = prior[len(prior) // 2]
        threshold = baseline * Decimal("1.02") + Decimal("10")
        if all(a >= threshold for a in recent) and min(recent) > baseline:
            new_level = min(recent)
            per_month = Decimal("30.44") / Decimal(item.cadence_days)
            raises.append(
                DetectedRaise(
                    description=item.description,
                    account_id=item.account_id,
                    cadence=item.cadence,
                    previous_amount=baseline,
                    new_amount=new_level,
                    monthly_delta=((new_level - baseline) * per_month).quantize(Decimal("0.01")),
                )
            )
    return raises
