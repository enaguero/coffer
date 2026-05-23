"""Verify money columns return Decimal, not float — the whole point of task #5."""

from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.transaction import Transaction


def test_account_opening_balance_is_decimal(
    auth_client: tuple[TestClient, dict[str, str], int], db_session: Session
) -> None:
    client, headers, _ = auth_client
    r = client.post(
        "/api/v1/accounts",
        headers=headers,
        json={
            "name": "Test",
            "type": "checking",
            "currency": "USD",
            "opening_balance": "1234.56",
        },
    )
    assert r.status_code == 201
    account_id = r.json()["id"]

    account = db_session.get(Account, account_id)
    assert account is not None
    assert isinstance(account.opening_balance, Decimal)
    assert account.opening_balance == Decimal("1234.56")


def test_transaction_amount_round_trips_without_float_loss(
    auth_client: tuple[TestClient, dict[str, str], int], db_session: Session
) -> None:
    """Classic float footgun: 0.10 + 0.20 != 0.30 in binary fp.
    Decimal must round-trip cleanly through the DB."""
    client, headers, _ = auth_client
    acct = client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": "A", "type": "checking", "currency": "USD", "opening_balance": "0"},
    ).json()
    for amount in ("0.10", "0.20", "0.30", "1234567890.12"):
        r = client.post(
            "/api/v1/transactions",
            headers=headers,
            json={
                "account_id": acct["id"],
                "posted_on": "2024-01-01",
                "description": f"t {amount}",
                "amount": amount,
            },
        )
        assert r.status_code == 201, r.text

    txns = db_session.query(Transaction).filter_by(account_id=acct["id"]).all()
    amounts = {t.amount for t in txns}
    assert Decimal("0.10") in amounts
    assert Decimal("0.30") in amounts
    assert Decimal("1234567890.12") in amounts
    assert all(isinstance(t.amount, Decimal) for t in txns)
