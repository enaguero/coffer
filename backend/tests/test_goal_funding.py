"""Goal funding schedules: linked-account auto-tracking, required-monthly,
on-track status, and this-month contribution counting."""

from datetime import date, timedelta
from io import BytesIO


def _account(client, headers, name="Savings", type_="savings") -> int:
    r = client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": name, "type": type_, "currency": "GBP", "opening_balance": "0"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _import_contributions(client, headers, account_id: int, rows: list[tuple[date, str]]) -> None:
    # UK day-first dates — the heuristic parser reads ambiguous dates as dd/mm.
    csv = "Date,Description,Amount\n" + "".join(
        f"{d.strftime('%d/%m/%Y')},STANDING ORDER SAVINGS,{amount}\n" for d, amount in rows
    )
    r = client.post(
        "/api/v1/imports/upload",
        headers=headers,
        files={"file": ("s.csv", BytesIO(csv.encode()), "text/csv")},
        data={"account_id": str(account_id)},
    )
    assert r.status_code == 201, r.text


def test_linked_goal_auto_tracks_account_balance(auth_client) -> None:
    client, headers, _ = auth_client
    account_id = _account(client, headers)
    today = date.today()
    _import_contributions(
        client,
        headers,
        account_id,
        [(today - timedelta(days=40), "500"), (today.replace(day=1), "250")],
    )

    r = client.post(
        "/api/v1/goals",
        headers=headers,
        json={
            "name": "Emergency fund",
            "target_amount": "3000",
            "account_id": account_id,
            "monthly_contribution": "250",
            "target_date": (today + timedelta(days=300)).isoformat(),
        },
    )
    assert r.status_code == 201, r.text
    goal = r.json()
    assert goal["auto_tracked"] is True
    assert float(goal["current_amount"]) == 750.0  # derived from the account, not hand-typed 0
    assert float(goal["funded_this_month"]) == 250.0
    assert goal["required_monthly"] is not None
    # Needs (3000-750)/~9.86 months ≈ £228/mo; committing £250 keeps it on track.
    assert goal["on_track"] is True
    assert goal["projected_date"] is not None


def test_unlinked_goal_keeps_manual_progress(auth_client) -> None:
    client, headers, _ = auth_client
    r = client.post(
        "/api/v1/goals",
        headers=headers,
        json={"name": "Holiday", "target_amount": "1200", "current_amount": "300"},
    )
    goal = r.json()
    assert goal["auto_tracked"] is False
    assert float(goal["current_amount"]) == 300.0
    assert goal["funded_this_month"] is None
    assert goal["on_track"] is None  # no date, no verdict


def test_behind_when_contribution_below_required(auth_client) -> None:
    client, headers, _ = auth_client
    today = date.today()
    r = client.post(
        "/api/v1/goals",
        headers=headers,
        json={
            "name": "House deposit",
            "target_amount": "20000",
            "current_amount": "1000",
            "monthly_contribution": "100",
            "target_date": (today + timedelta(days=365)).isoformat(),
        },
    )
    goal = r.json()
    # Needs ~£1,585/mo; committing £100 is far behind.
    assert goal["on_track"] is False


def test_met_goal_is_on_track_regardless(auth_client) -> None:
    client, headers, _ = auth_client
    r = client.post(
        "/api/v1/goals",
        headers=headers,
        json={"name": "Done", "target_amount": "500", "current_amount": "600"},
    )
    assert r.json()["on_track"] is True
    assert r.json()["progress"] == 1.0


def test_goal_account_must_be_owned(auth_client) -> None:
    client, headers, _ = auth_client
    account_id = _account(client, headers)
    r = client.post("/api/v1/auth/signup", json={"email": "other3@coffer.dev", "password": "other-password-3"})
    other_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    client.cookies.clear()
    r = client.post(
        "/api/v1/goals",
        headers=other_headers,
        json={"name": "Sneaky", "target_amount": "1", "account_id": account_id},
    )
    assert r.status_code == 404
