"""Weekly digest: composition (preview endpoint) and send gating."""

from datetime import date, timedelta
from io import BytesIO

import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def _smtp_unconfigured(monkeypatch):
    """Force SMTP off so tests never depend on ambient .env or send real mail."""
    monkeypatch.setattr(settings, "smtp_host", None)


def _account(client, headers, name="Current", type_="checking") -> int:
    r = client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": name, "type": type_, "currency": "GBP", "opening_balance": "0"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _import_csv(client, headers, account_id: int, rows: list[tuple[date, str, str]]) -> None:
    csv = "Date,Description,Amount\n" + "".join(
        f"{d.strftime('%d/%m/%Y')},{desc},{amount}\n" for d, desc, amount in rows
    )
    r = client.post(
        "/api/v1/imports/upload",
        headers=headers,
        files={"file": ("s.csv", BytesIO(csv.encode()), "text/csv")},
        data={"account_id": str(account_id)},
    )
    assert r.status_code == 201, r.text


def test_digest_preview_composes_sections(auth_client) -> None:
    client, headers, _ = auth_client
    account_id = _account(client, headers)

    # Recurring rent so bills/forecast have signal, dated over recent months.
    today = date.today()
    rows = []
    for i in range(4, 0, -1):
        first = today.replace(day=1)
        for _ in range(i - 1):
            first = (first - timedelta(days=1)).replace(day=1)
        rows.append((first.replace(day=15), "RENT PAYMENT", "-800"))
        rows.append((first.replace(day=14), "ACME SALARY", "2500"))
    _import_csv(client, headers, account_id, rows)

    # A promo cliff inside the 60-day window.
    client.post(
        "/api/v1/debts",
        headers=headers,
        json={
            "name": "0% card",
            "current_balance": "2300",
            "interest_rate_apr": "24.9",
            "promo_apr": "0",
            "promo_ends_on": (today + timedelta(days=30)).isoformat(),
            "minimum_payment": "40",
        },
    )
    # A stale account with no data at all.
    _account(client, headers, name="Dusty savings", type_="savings")

    r = client.get("/api/v1/insights/digest/preview", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["item_count"] >= 2
    assert "PROMO RATE CLIFFS" in body["body"]
    assert "0% card" in body["body"]
    assert "Dusty savings" in body["body"]  # statements-needed section
    assert body["smtp_configured"] is False
    assert body["subject"].startswith("Coffer weekly")


def test_digest_preview_quiet_when_nothing_to_report(auth_client) -> None:
    client, headers, _ = auth_client
    r = client.get("/api/v1/insights/digest/preview", headers=headers)
    assert r.status_code == 200
    body = r.json()
    # A brand-new user has no accounts, debts, or data: nothing to flag.
    assert body["item_count"] == 0
    assert "All quiet" in body["body"]


def test_digest_send_returns_503_without_smtp(auth_client) -> None:
    client, headers, _ = auth_client
    r = client.post("/api/v1/insights/digest/send", headers=headers)
    assert r.status_code == 503
