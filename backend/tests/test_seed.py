"""Seed-data invariants, recomputed from app/seed.py's own constants — no DB.

CLAUDE.md: "The demo user's debt minimums sum to exactly 40% of $5,000 salary;
preserve that invariant." Anyone editing the DEBTS table breaks this test
before they break the demo."""

from decimal import Decimal

from app import seed


def test_seed_debt_commitments_sum_to_forty_percent_of_income() -> None:
    # Recompute through seed.py's own commitment helper: minimum_payment for
    # the revolving card, installment for the fixed types, CLP converted at
    # the seeded rate — exactly what _display_commitment encodes.
    total = sum(
        (
            seed._display_commitment(rtype, ccy, seed._dec(min_pay), seed._dec(installment))
            for _name, rtype, _acct, ccy, _bal, _prin, _apr, min_pay, installment, _due, _ends, _ppm in seed.DEBTS
        ),
        Decimal("0"),
    )
    assert total == Decimal("2000.00")
    assert total == seed.DEBT_BUDGET == seed.MONTHLY_INCOME * Decimal("0.40")
