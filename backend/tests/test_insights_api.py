"""API tests for the financial-outcomes features: balance capture on import,
debt planning, net worth, coverage, recurring/forecast/surplus insights."""

from datetime import date, timedelta
from io import BytesIO


def _account(client, headers, *, name="Current", type_="checking", bank_id=None, opening="0") -> int:
    r = client.post(
        "/api/v1/accounts",
        headers=headers,
        json={
            "name": name,
            "type": type_,
            "currency": "GBP",
            "opening_balance": opening,
            "bank_id": bank_id,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _recent_months(n: int) -> list[date]:
    """The 15th of the last n complete-ish months, oldest first."""
    today = date.today()
    first_of_month = today.replace(day=1)
    months: list[date] = []
    for _ in range(n):
        prev_end = first_of_month - timedelta(days=1)
        months.append(prev_end.replace(day=15))
        first_of_month = prev_end.replace(day=1)
    months.reverse()
    return months


def _lloyds_csv(months: list[date]) -> bytes:
    """A Lloyds-style statement: salary in, rent + Netflix out, running balance."""
    header = (
        "Transaction Date,Transaction Type,Sort Code,Account Number,"
        "Transaction Description,Debit Amount,Credit Amount,Balance\n"
    )
    rows = []
    balance = 1000.0
    salary = [2500, 2500, 2650, 2650]  # a raise halfway through
    for i, m in enumerate(months):
        pay = salary[i] if i < len(salary) else 2650
        balance += pay
        rows.append(f"{m.strftime('%d/%m/%Y')},FPI,'11-22-33,123,ACME LTD SALARY,,{pay}.00,{balance:.2f}\n")
        balance -= 800
        rows.append(f"{m.replace(day=20).strftime('%d/%m/%Y')},DD,'11-22-33,123,RENT PAYMENT,800.00,,{balance:.2f}\n")
        balance -= 9.99
        rows.append(f"{m.replace(day=22).strftime('%d/%m/%Y')},DD,'11-22-33,123,NETFLIX.COM,9.99,,{balance:.2f}\n")
    return (header + "".join(rows)).encode()


def _import_statement(client, headers, account_id: int, content: bytes) -> dict:
    r = client.post(
        "/api/v1/imports/upload",
        headers=headers,
        files={"file": ("statement.csv", BytesIO(content), "text/csv")},
        data={"account_id": str(account_id)},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_statement_balance_captured_as_snapshot(auth_client) -> None:
    client, headers, _ = auth_client
    account_id = _account(client, headers, bank_id="lloyds")
    months = _recent_months(4)
    _import_statement(client, headers, account_id, _lloyds_csv(months))

    snaps = client.get(f"/api/v1/accounts/{account_id}/snapshots", headers=headers).json()
    assert len(snaps) == 1
    assert snaps[0]["source"] == "statement"
    assert snaps[0]["as_of"] == months[-1].replace(day=22).isoformat()

    nw = client.get("/api/v1/insights/networth", headers=headers).json()
    acc = next(a for a in nw["accounts"] if a["id"] == account_id)
    assert acc["source"] == "statement"
    # Statement balance agrees with derived (opening 0 + txns... it won't — the
    # CSV starts from a prior balance of 1000), so drift must be reported.
    assert acc["drift"] is not None


def test_manual_valuation_and_net_worth(auth_client) -> None:
    client, headers, _ = auth_client
    pension_id = _account(client, headers, name="Pension", type_="other")
    r = client.post(
        f"/api/v1/accounts/{pension_id}/snapshots",
        headers=headers,
        json={"as_of": date.today().isoformat(), "balance": "20000"},
    )
    assert r.status_code == 201, r.text

    nw = client.get("/api/v1/insights/networth", headers=headers).json()
    assert float(nw["assets"]) >= 20000
    acc = next(a for a in nw["accounts"] if a["id"] == pension_id)
    assert acc["source"] == "manual"


def test_debt_plan_avalanche_saves_interest(auth_client) -> None:
    client, headers, _ = auth_client
    promo_end = (date.today() + timedelta(days=90)).isoformat()
    client.post(
        "/api/v1/debts",
        headers=headers,
        json={
            "name": "Costly card",
            "current_balance": "3000",
            "interest_rate_apr": "24.9",
            "minimum_payment": "75",
        },
    )
    client.post(
        "/api/v1/debts",
        headers=headers,
        json={
            "name": "0% transfer card",
            "current_balance": "4000",
            "interest_rate_apr": "22.9",
            "promo_apr": "0",
            "promo_ends_on": promo_end,
            "minimum_payment": "100",
        },
    )

    r = client.post("/api/v1/debts/plan", headers=headers, json={"extra_monthly": "150"})
    assert r.status_code == 200, r.text
    plan = r.json()
    assert float(plan["avalanche"]["total_interest"]) < float(plan["minimum"]["total_interest"])
    assert float(plan["avalanche"]["interest_saved_vs_minimum"]) > 0
    assert plan["avalanche"]["debt_free_date"] is not None
    # The 0% card outlives its promo window at this budget → cliff warning.
    cliffs = plan["avalanche"]["promo_cliffs"]
    assert any(c["name"] == "0% transfer card" for c in cliffs)


def test_recurring_forecast_and_surplus_flow(auth_client) -> None:
    client, headers, _ = auth_client
    account_id = _account(client, headers, bank_id="lloyds")
    _import_statement(client, headers, account_id, _lloyds_csv(_recent_months(4)))

    recurring = client.get("/api/v1/insights/recurring", headers=headers).json()
    descriptions = {r["description"] for r in recurring}
    assert any("SALARY" in d for d in descriptions)
    assert any("NETFLIX" in d for d in descriptions)

    forecast = client.get("/api/v1/insights/forecast?days=60&reserve=100", headers=headers).json()
    assert len(forecast["series"]) == 61
    assert forecast["events"]  # projected rent/netflix/salary occurrences

    client.post(
        "/api/v1/debts",
        headers=headers,
        json={"name": "Card", "current_balance": "1000", "interest_rate_apr": "29.9", "minimum_payment": "25"},
    )
    client.post(
        "/api/v1/goals",
        headers=headers,
        json={
            "name": "Emergency fund",
            "target_amount": "5000",
            "current_amount": "500",
            "target_date": (date.today() + timedelta(days=365)).isoformat(),
        },
    )

    surplus = client.get("/api/v1/insights/surplus", headers=headers).json()
    # Salary (2500+) minus rent+netflix (~810) is decidedly positive.
    assert float(surplus["surplus"]) > 0
    kinds = [o["kind"] for o in surplus["options"]]
    assert kinds[0] == "debt"
    assert "goal" in kinds and "runway" in kinds
    # The mid-series salary step-up (2500 → 2650) is a detected raise.
    assert len(surplus["raises_detected"]) == 1
    assert float(surplus["raises_detected"][0]["new_amount"]) == 2650.0


def test_account_coverage(auth_client) -> None:
    client, headers, _ = auth_client
    fresh_id = _account(client, headers, name="Fresh", bank_id="lloyds")
    empty_id = _account(client, headers, name="Empty")
    months = _recent_months(2)
    _import_statement(client, headers, fresh_id, _lloyds_csv(months))

    r = client.get("/api/v1/accounts/coverage", headers=headers)
    assert r.status_code == 200, r.text
    by_id = {c["account_id"]: c for c in r.json()}
    assert by_id[fresh_id]["txn_count"] == 6
    assert by_id[fresh_id]["last_txn_on"] == months[-1].replace(day=22).isoformat()
    assert by_id[fresh_id]["last_import_at"] is not None
    assert by_id[fresh_id]["last_snapshot_on"] is not None
    assert by_id[empty_id]["txn_count"] == 0
    assert by_id[empty_id]["last_txn_on"] is None


def test_snapshots_are_owner_scoped(auth_client) -> None:
    client, headers, _ = auth_client
    account_id = _account(client, headers)
    r = client.post("/api/v1/auth/signup", json={"email": "other2@coffer.dev", "password": "other-password-2"})
    other_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    client.cookies.clear()  # cookie auth wins otherwise; force the Bearer identity
    r = client.post(
        f"/api/v1/accounts/{account_id}/snapshots",
        headers=other_headers,
        json={"as_of": date.today().isoformat(), "balance": "1"},
    )
    assert r.status_code == 404
