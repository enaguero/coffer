"""UK tax-year allowance metering: tax-year boundaries, LISA double-counting,
and the /insights/allowances endpoint."""

from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO

from app.models.account import UkWrapper
from app.services.analytics.allowances import (
    ISA_ALLOWANCE,
    LISA_ALLOWANCE,
    compute_allowances,
    tax_year_bounds,
)

# ---- Pure math ----------------------------------------------------------------


def test_tax_year_bounds_span_april_sixth() -> None:
    # After 6 April: current calendar year starts the tax year.
    assert tax_year_bounds(date(2026, 7, 31)) == (date(2026, 4, 6), date(2027, 4, 5))
    # Before 6 April: still the previous year's tax year.
    assert tax_year_bounds(date(2026, 2, 1)) == (date(2025, 4, 6), date(2026, 4, 5))
    # Boundary days.
    assert tax_year_bounds(date(2026, 4, 6))[0] == date(2026, 4, 6)
    assert tax_year_bounds(date(2026, 4, 5))[0] == date(2025, 4, 6)


def test_lisa_contributions_count_toward_isa_allowance() -> None:
    meters = {
        m.wrapper: m for m in compute_allowances({UkWrapper.ISA: Decimal("10000"), UkWrapper.LISA: Decimal("3000")})
    }
    assert meters[UkWrapper.ISA].used == Decimal("13000")
    assert meters[UkWrapper.ISA].lisa_portion == Decimal("3000")
    assert meters[UkWrapper.ISA].remaining == ISA_ALLOWANCE - Decimal("13000")
    assert meters[UkWrapper.LISA].used == Decimal("3000")
    assert meters[UkWrapper.LISA].remaining == LISA_ALLOWANCE - Decimal("3000")


def test_overfilled_allowance_clamps_remaining_to_zero() -> None:
    meters = compute_allowances({UkWrapper.ISA: Decimal("25000")})
    assert meters[0].remaining == Decimal("0")


def test_no_wrappers_no_meters() -> None:
    assert compute_allowances({}) == []


# ---- API ----------------------------------------------------------------------


def _wrapped_account(client, headers, name: str, wrapper: str) -> int:
    r = client.post(
        "/api/v1/accounts",
        headers=headers,
        json={
            "name": name,
            "type": "savings",
            "currency": "GBP",
            "opening_balance": "0",
            "uk_wrapper": wrapper,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _import_rows(client, headers, account_id: int, rows: list[tuple[date, str]]) -> None:
    csv = "Date,Description,Amount\n" + "".join(
        f"{d.strftime('%d/%m/%Y')},TRANSFER IN,{amount}\n" for d, amount in rows
    )
    r = client.post(
        "/api/v1/imports/upload",
        headers=headers,
        files={"file": ("s.csv", BytesIO(csv.encode()), "text/csv")},
        data={"account_id": str(account_id)},
    )
    assert r.status_code == 201, r.text


def test_allowances_endpoint_meters_current_tax_year_only(auth_client) -> None:
    client, headers, _ = auth_client
    isa_id = _wrapped_account(client, headers, "S&S ISA", "isa")

    today = date.today()
    start, _end = tax_year_bounds(today)
    inside = start + timedelta(days=10)
    outside = start - timedelta(days=10)  # previous tax year
    # Ensure the in-year date isn't in the future (tax year started recently).
    inside = min(inside, today)
    _import_rows(client, headers, isa_id, [(inside, "5000"), (outside, "9999")])

    body = client.get("/api/v1/insights/allowances", headers=headers).json()
    assert body["wrapped_account_count"] == 1
    meters = {m["wrapper"]: m for m in body["meters"]}
    assert float(meters["isa"]["used"]) == 5000.0  # prior-year 9999 excluded
    assert float(meters["isa"]["remaining"]) == 15000.0
    assert body["days_left"] > 0


def test_allowances_empty_without_tagged_accounts(auth_client) -> None:
    client, headers, _ = auth_client
    body = client.get("/api/v1/insights/allowances", headers=headers).json()
    assert body["meters"] == []
    assert body["wrapped_account_count"] == 0


def test_wrapper_rejected_on_invalid_value(auth_client) -> None:
    client, headers, _ = auth_client
    r = client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": "Bad", "type": "savings", "currency": "GBP", "uk_wrapper": "sipp-x"},
    )
    assert r.status_code == 422
