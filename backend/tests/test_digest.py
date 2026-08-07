"""Weekly digest: composition (preview endpoint) and send gating."""

from datetime import date, timedelta
from io import BytesIO

import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def _smtp_unconfigured(monkeypatch, tmp_path):
    """Force SMTP off and pin backup state so tests never depend on ambient
    container state (a real backup_meta.json would change digest content)."""
    monkeypatch.setattr(settings, "smtp_host", None)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    (backup_dir / "backup_meta.json").write_text(
        '{"last_verified": "2099-01-01T00:00:00+00:00", "last_verify_ok": true}'
    )
    monkeypatch.setattr(settings, "backup_dir", str(backup_dir))


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


def test_digest_backup_warnings(auth_client, monkeypatch, tmp_path) -> None:
    from app.core.config import settings as cfg

    # No backups at all: stay quiet — a fresh instance must not be nagged weekly.
    empty = tmp_path / "no-backups"
    empty.mkdir()
    monkeypatch.setattr(cfg, "backup_dir", str(empty))
    assert "BACKUP" not in client_get_preview(auth_client)["body"]

    # Archives exist but never verified: warn.
    created = tmp_path / "created"
    created.mkdir()
    (created / "backup_meta.json").write_text('{"last_created": "2026-08-01T00:00:00+00:00"}')
    monkeypatch.setattr(cfg, "backup_dir", str(created))
    assert "BACKUPS UNVERIFIED" in client_get_preview(auth_client)["body"]

    # Drill failed: loud warning.
    failed = tmp_path / "failed"
    failed.mkdir()
    (failed / "backup_meta.json").write_text('{"last_created": "x", "last_verified": "y", "last_verify_ok": false}')
    monkeypatch.setattr(cfg, "backup_dir", str(failed))
    assert "BACKUP PROBLEM" in client_get_preview(auth_client)["body"]


def client_get_preview(auth_client):
    client, headers, _ = auth_client
    r = client.get("/api/v1/insights/digest/preview", headers=headers)
    assert r.status_code == 200
    return r.json()


def test_owed_text_converts_foreign_balance_or_flags_missing_rate() -> None:
    """A foreign-currency balance converts at the saved rate before entering
    display-currency text; with no rate it is excluded and flagged — the
    native magnitude never leaks into a display-denominated sentence."""
    from decimal import Decimal

    from app.models.debt import Debt
    from app.services.digest import _owed_text

    d = Debt(name="Chile loan", current_balance=Decimal("1000000.00"), currency="CLP")
    assert _owed_text(d, "GBP", {"CLP": Decimal("0.00082")}) == "with 820.00 owed (converted from CLP)"
    assert _owed_text(d, "GBP", {}) == "— balance held in CLP (no FX rate saved)"
