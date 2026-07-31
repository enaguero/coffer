"""Unit tests for the analytics services — pure arithmetic, no DB."""

from datetime import date
from decimal import Decimal

from app.models.account import AccountType
from app.services.analytics.debt_plan import DebtInput, compare_strategies, simulate_payoff
from app.services.analytics.forecast import next_due_date, project
from app.services.analytics.net_worth import AccountData, balance_at, compute_net_worth, current_balance
from app.services.analytics.recurring import TxnLite, detect_raises, detect_recurring
from app.services.analytics.surplus import rank_allocations, summarize_month

START = date(2026, 8, 1)


def _card(balance="3000", apr="24.9", minimum="75", **kw) -> DebtInput:
    return DebtInput(
        id=kw.get("id", 1),
        name=kw.get("name", "Card"),
        balance=Decimal(balance),
        apr=Decimal(apr) if apr is not None else None,
        minimum_payment=Decimal(minimum) if minimum else None,
        promo_apr=kw.get("promo_apr"),
        promo_ends_on=kw.get("promo_ends_on"),
    )


# ---- Debt simulator -----------------------------------------------------------


def test_avalanche_beats_minimum_only() -> None:
    debts = [_card(), _card(id=2, name="Loan", balance="8000", apr="6.5", minimum="160")]
    results = compare_strategies(debts, extra_monthly=Decimal("200"), start=START)
    assert results["avalanche"].total_interest < results["minimum"].total_interest
    assert results["avalanche"].months < results["minimum"].months
    # Avalanche targets the higher APR first, so it never pays more interest
    # than snowball on the same inputs.
    assert results["avalanche"].total_interest <= results["snowball"].total_interest


def test_snowflake_accelerates_payoff() -> None:
    debts = [_card()]
    plain = simulate_payoff(debts, "avalanche", Decimal("50"), start=START)
    with_snowflake = simulate_payoff(debts, "avalanche", Decimal("50"), snowflakes={2: Decimal("500")}, start=START)
    assert with_snowflake.months < plain.months
    assert with_snowflake.total_interest < plain.total_interest


def test_promo_cliff_reported_when_balance_survives_expiry() -> None:
    debts = [
        _card(
            balance="4000",
            apr="24.9",
            minimum="40",
            promo_apr=Decimal("0"),
            promo_ends_on=date(2026, 12, 31),
        )
    ]
    result = simulate_payoff(debts, "avalanche", Decimal("0"), start=START)
    assert len(result.promo_cliffs) == 1
    cliff = result.promo_cliffs[0]
    assert cliff.promo_ends_on == date(2026, 12, 31)
    assert cliff.balance_at_expiry > 0
    assert cliff.reverting_apr == Decimal("24.9")


def test_promo_rate_accrues_no_interest_during_window() -> None:
    debts = [_card(balance="1000", apr="24.9", minimum="500", promo_apr=Decimal("0"), promo_ends_on=date(2027, 12, 31))]
    result = simulate_payoff(debts, "avalanche", Decimal("0"), start=START)
    assert result.total_interest == Decimal("0.00")  # cleared inside the 0% window


def test_unpayable_when_interest_outruns_payment() -> None:
    debts = [_card(balance="10000", apr="40", minimum="25")]
    result = simulate_payoff(debts, "minimum", start=START)
    assert result.unpayable


def test_missing_minimum_gets_assumption() -> None:
    debts = [_card(minimum=None)]
    result = simulate_payoff(debts, "avalanche", Decimal("100"), start=START)
    assert result.assumptions and "no minimum payment" in result.assumptions[0]


# ---- Recurring detection ------------------------------------------------------


def _monthly_txns(desc: str, amount: str, months: int, day: int = 15) -> list[TxnLite]:
    out = []
    for i in range(months):
        month_index = 3 + i  # Apr..Jul 2026
        out.append(
            TxnLite(
                account_id=1,
                posted_on=date(2026, month_index + 1, day),
                description=desc,
                amount=Decimal(amount),
            )
        )
    return out


def test_detect_monthly_subscription_and_next_expected() -> None:
    txns = _monthly_txns("NETFLIX.COM 123", "-9.99", 4)
    items = detect_recurring(txns, today=date(2026, 8, 1))
    assert len(items) == 1
    item = items[0]
    assert item.cadence == "monthly"
    assert item.typical_amount == Decimal("-9.99")
    assert item.active
    assert item.next_expected >= date(2026, 8, 1)


def test_irregular_transactions_not_detected() -> None:
    txns = [
        TxnLite(1, date(2026, 4, 2), "COFFEE SHOP", Decimal("-3.50")),
        TxnLite(1, date(2026, 4, 9), "COFFEE SHOP", Decimal("-3.50")),
        TxnLite(1, date(2026, 6, 27), "COFFEE SHOP", Decimal("-3.50")),
    ]
    assert detect_recurring(txns, today=date(2026, 8, 1)) == []


def test_raise_detected_on_sustained_salary_step_up() -> None:
    txns = _monthly_txns("ACME LTD SALARY", "2500", 2) + [
        TxnLite(1, date(2026, 6, 15), "ACME LTD SALARY", Decimal("2650")),
        TxnLite(1, date(2026, 7, 15), "ACME LTD SALARY", Decimal("2650")),
    ]
    items = detect_recurring(txns, today=date(2026, 8, 1))
    raises = detect_raises(items)
    assert len(raises) == 1
    assert raises[0].previous_amount == Decimal("2500")
    assert raises[0].new_amount == Decimal("2650")


def test_one_off_bonus_is_not_a_raise() -> None:
    txns = _monthly_txns("ACME LTD SALARY", "2500", 3) + [
        TxnLite(1, date(2026, 7, 15), "ACME LTD SALARY", Decimal("4000")),
    ]
    items = detect_recurring(txns, today=date(2026, 8, 1))
    assert detect_raises(items) == []  # only the latest occurrence stepped up


# ---- Forecast -----------------------------------------------------------------


def test_projection_applies_events_and_reserve_warning() -> None:
    today = date(2026, 8, 1)
    items = detect_recurring(
        _monthly_txns("RENT PAYMENT", "-800", 4, day=20) + _monthly_txns("ACME LTD SALARY", "1000", 4, day=15),
        today=today,
    )
    # Start below the reserve floor: the projection sits at £100 until the next
    # salary (~Aug 14), so the reserve is breached from the first projected day.
    result = project(Decimal("100"), items, days=60, reserve=Decimal("200"), today=today)
    assert len(result.series) == 61
    assert result.events  # rent + salary occurrences inside the window
    assert result.first_below_reserve == date(2026, 8, 2)
    assert result.first_below_zero is None
    assert result.min_balance == Decimal("100")
    assert result.safe_to_commit == Decimal("0")


def test_next_due_date_clamps_short_months() -> None:
    assert next_due_date(31, date(2026, 2, 10)) == date(2026, 2, 28)
    assert next_due_date(5, date(2026, 8, 10)) == date(2026, 9, 5)


# ---- Net worth ----------------------------------------------------------------


def _acc(**kw) -> AccountData:
    return AccountData(
        id=kw.get("id", 1),
        name=kw.get("name", "Current"),
        type=kw.get("type", AccountType.CHECKING),
        currency="GBP",
        opening_balance=Decimal(kw.get("opening", "0")),
        txns=kw.get("txns", []),
        snapshots=kw.get("snapshots", []),
    )


def test_balance_anchors_on_snapshot_then_applies_later_txns() -> None:
    acc = _acc(
        opening="100",
        txns=[(date(2026, 6, 1), Decimal("50")), (date(2026, 7, 10), Decimal("-30"))],
        snapshots=[(date(2026, 6, 30), Decimal("500"), "statement")],
    )
    # Snapshot says 500 on Jun 30; the July txn applies after it.
    assert balance_at(acc, date(2026, 7, 31)) == Decimal("470")
    # Before the snapshot: derived from opening + txns.
    assert balance_at(acc, date(2026, 6, 15)) == Decimal("150")


def test_drift_surfaces_missing_transactions() -> None:
    acc = _acc(
        opening="0",
        txns=[(date(2026, 6, 1), Decimal("100"))],
        snapshots=[(date(2026, 6, 30), Decimal("250"), "statement")],
    )
    bal = current_balance(acc, today=date(2026, 7, 1))
    assert bal.drift == Decimal("150")  # bank says 250, ledger only explains 100


def test_net_worth_combines_assets_liabilities_and_register() -> None:
    accounts = [
        _acc(id=1, opening="1000"),
        _acc(id=2, name="Card", type=AccountType.CREDIT_CARD, opening="-400"),
        _acc(id=3, name="Pension", type=AccountType.OTHER, snapshots=[(date(2026, 6, 30), Decimal("20000"), "manual")]),
    ]
    report = compute_net_worth(
        accounts,
        register_debts=[(9, "Student loan", Decimal("5000"), None)],
        months=6,
        today=date(2026, 8, 1),
    )
    assert report.assets == Decimal("21000.00")
    assert report.liabilities == Decimal("5400.00")
    assert report.net == Decimal("15600.00")
    assert report.series[-1].net == Decimal("15600.00")


def test_linked_register_debt_not_double_counted() -> None:
    accounts = [_acc(id=2, name="Card", type=AccountType.CREDIT_CARD, opening="-400")]
    report = compute_net_worth(
        accounts,
        register_debts=[(9, "Card", Decimal("400"), 2)],  # linked to account 2
        months=3,
        today=date(2026, 8, 1),
    )
    assert report.liabilities == Decimal("400.00")


# ---- Surplus ------------------------------------------------------------------


def test_summarize_month_and_uncategorized_visibility() -> None:
    txns = [
        (date(2026, 6, 1), Decimal("2500"), 1),
        (date(2026, 6, 5), Decimal("-800"), 2),
        (date(2026, 6, 9), Decimal("-40"), None),
        (date(2026, 7, 1), Decimal("999"), 1),  # other month, excluded
    ]
    s = summarize_month(txns, 2026, 6)
    assert s.income == Decimal("2500.00")
    assert s.outflows == Decimal("840.00")
    assert s.surplus == Decimal("1660.00")
    assert s.uncategorized_count == 1
    assert s.uncategorized_amount == Decimal("40.00")


def test_rank_allocations_orders_debts_by_effective_apr() -> None:
    today = date(2026, 8, 1)
    debts = [
        _card(id=1, name="Cheap loan", balance="5000", apr="6.5", minimum="100"),
        _card(id=2, name="Expensive card", balance="2000", apr="29.9", minimum="50"),
    ]
    options = rank_allocations(
        Decimal("300"),
        debts,
        goals=[(7, "Holiday", Decimal("1200"), Decimal("200"), date(2027, 8, 1))],
        monthly_floor=Decimal("1500"),
        today=today,
    )
    assert options[0].name == "Expensive card"
    assert options[0].yearly_interest_saved == Decimal("89.70")  # 300 * 29.9%
    kinds = [o.kind for o in options]
    assert kinds == ["debt", "debt", "goal", "runway"]
    goal_opt = options[2]
    assert goal_opt.months_earlier is not None and goal_opt.months_earlier > 0
