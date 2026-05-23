from datetime import date
from decimal import Decimal

import pytest

from app.services.csv_parser import _parse_decimal, parse_csv


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,234.56", Decimal("1234.56")),
        ("1.234,56", Decimal("1234.56")),  # European locale
        ("(123.45)", Decimal("-123.45")),  # accounting negative
        ("-50.00", Decimal("-50.00")),
        ("$ 75", Decimal("75")),
        ("0,50", Decimal("0.50")),  # comma-only decimal
        ("", None),
        ("not a number", None),
    ],
)
def test_parse_decimal(raw: str, expected: Decimal | None) -> None:
    assert _parse_decimal(raw) == expected


def test_parse_csv_standard_amount_column() -> None:
    csv = b"Date,Description,Amount\n2024-01-15,Coffee,-4.50\n2024-01-16,Payroll,2500.00\n"
    rows, skipped = parse_csv(csv)
    assert skipped == 0
    assert len(rows) == 2
    assert rows[0].posted_on == date(2024, 1, 15)
    assert rows[0].description == "Coffee"
    assert rows[0].amount == Decimal("-4.50")
    # Type, not just value — proves Decimal flows through.
    assert isinstance(rows[0].amount, Decimal)
    assert rows[1].amount == Decimal("2500.00")


def test_parse_csv_debit_credit_columns() -> None:
    csv = (
        b"Date,Description,Debit,Credit\n"
        b"2024-02-01,ATM,40.00,\n"
        b"2024-02-02,Refund,,15.00\n"
    )
    rows, skipped = parse_csv(csv)
    assert skipped == 0
    assert rows[0].amount == Decimal("-40.00")  # debit -> negative
    assert rows[1].amount == Decimal("15.00")


def test_parse_csv_skips_unparseable_rows() -> None:
    csv = b"Date,Description,Amount\nnot-a-date,X,1.00\n2024-01-01,,1.00\n2024-01-02,Y,abc\n"
    rows, skipped = parse_csv(csv)
    assert rows == []
    assert skipped == 3


def test_parse_csv_external_id_is_stable() -> None:
    csv = b"Date,Description,Amount\n2024-01-15,Coffee,-4.50\n"
    rows1, _ = parse_csv(csv)
    rows2, _ = parse_csv(csv)
    assert rows1[0].external_id == rows2[0].external_id
    assert rows1[0].external_id is not None
