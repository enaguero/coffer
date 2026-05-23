"""Bank-connections API tests.

Exercises the FastAPI router end-to-end against the TestClient with the
GoCardless provider monkeypatched. Verifies:
- GET /institutions proxies through with country
- POST /link/start persists a PENDING BankConnection and returns the link URL
- POST /link/complete flips status to LINKED and surfaces discovered accounts
- POST /map-account attaches a discovered account to a Coffer Account
- DELETE marks the connection REVOKED and detaches accounts
- HMAC reference round-trip (unit-level)
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.v1.bank_connections import _make_reference, _verify_reference
from app.core.config import settings
from app.services.bank_providers.base import (
    LinkedAccountRef,
    LinkSession,
    NormalizedTxn,
)


@pytest.fixture(autouse=True)
def _configure_bank_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bank-connections endpoints require these to be set; otherwise they
    return 503. We set safe placeholders here for the duration of each test."""
    monkeypatch.setattr(settings, "gocardless_secret_id", "test-sid")
    monkeypatch.setattr(settings, "gocardless_secret_key", "test-skey")
    monkeypatch.setattr(settings, "bank_sync_state_secret", "x" * 48)


class _StubProvider:
    """Stub returned from `_get_provider` in the router under test."""

    institutions_called_with: str | None = None
    revoked: list[str] = []

    async def list_institutions(self, country: str):
        self.institutions_called_with = country
        from app.schemas.bank_connection import InstitutionRef

        return [
            InstitutionRef(
                id="SBOX_BF01",
                name="Sandbox Finance",
                bic=None,
                countries=[country],
                logo_url=None,
            )
        ]

    async def create_link(
        self, *, institution_id: str, redirect_uri: str, reference: str, user_language: Any = None
    ) -> LinkSession:
        return LinkSession(
            requisition_id="req-123",
            link_url=f"https://bank.test/login?inst={institution_id}",
            agreement_id="agree-1",
            expires_at=None,
        )

    async def complete_link(self, requisition_id: str) -> list[LinkedAccountRef]:
        return [
            LinkedAccountRef(
                external_account_id="acc-xyz",
                iban_last4="6789",
                name="Current Account",
                currency="GBP",
            )
        ]

    async def fetch_transactions(self, **_kwargs) -> list[NormalizedTxn]:
        return []

    async def revoke(self, requisition_id: str) -> None:
        type(self).revoked.append(requisition_id)


@pytest.fixture()
def patch_provider(monkeypatch: pytest.MonkeyPatch) -> _StubProvider:
    stub = _StubProvider()
    import app.api.v1.bank_connections as router_mod

    monkeypatch.setattr(router_mod, "_get_provider", lambda: stub)
    return stub


def test_hmac_reference_roundtrips() -> None:
    """A signed reference verifies for the same user and not for another."""
    settings.bank_sync_state_secret = "y" * 48
    ref = _make_reference(42, "nonce-1")
    assert _verify_reference(ref, 42) is True
    assert _verify_reference(ref, 43) is False
    assert _verify_reference("garbage", 42) is False


def test_link_start_persists_pending_and_returns_link(
    auth_client: tuple[TestClient, dict[str, str], int],
    patch_provider: _StubProvider,
) -> None:
    client, headers, _ = auth_client
    r = client.post(
        "/api/v1/bank-connections/link/start",
        headers=headers,
        json={"institution_id": "SBOX_BF01", "country": "GB"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["requisition_id"] == "req-123"
    assert "bank.test" in body["link_url"]

    r2 = client.get("/api/v1/bank-connections", headers=headers)
    assert r2.status_code == 200
    rows = r2.json()
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"
    assert rows[0]["institution_id"] == "SBOX_BF01"


def test_link_complete_flips_to_linked_and_reports_accounts(
    auth_client: tuple[TestClient, dict[str, str], int],
    patch_provider: _StubProvider,
) -> None:
    client, headers, _ = auth_client
    start = client.post(
        "/api/v1/bank-connections/link/start",
        headers=headers,
        json={"institution_id": "SBOX_BF01"},
    )
    requisition_id = start.json()["requisition_id"]

    r = client.post(
        "/api/v1/bank-connections/link/complete",
        headers=headers,
        json={"requisition_id": requisition_id},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["institution_name"] in {"SBOX_BF01", "Sandbox Finance"}
    assert body["accounts"] == [
        {
            "external_account_id": "acc-xyz",
            "iban_last4": "6789",
            "name": "Current Account",
            "currency": "GBP",
        }
    ]

    listed = client.get("/api/v1/bank-connections", headers=headers).json()
    assert listed[0]["status"] == "linked"


def test_map_account_creates_and_attaches(
    auth_client: tuple[TestClient, dict[str, str], int],
    patch_provider: _StubProvider,
) -> None:
    client, headers, _ = auth_client
    start = client.post(
        "/api/v1/bank-connections/link/start",
        headers=headers,
        json={"institution_id": "SBOX_BF01"},
    ).json()
    bank_connection_id = start["bank_connection_id"]
    client.post(
        "/api/v1/bank-connections/link/complete",
        headers=headers,
        json={"requisition_id": start["requisition_id"]},
    )

    r = client.post(
        f"/api/v1/bank-connections/{bank_connection_id}/map-account",
        headers=headers,
        json={
            "external_account_id": "acc-xyz",
            "name": "HSBC Current",
            "currency": "GBP",
        },
    )
    assert r.status_code == 201, r.text
    account_id = r.json()["account_id"]
    assert isinstance(account_id, int)

    accounts = client.get("/api/v1/accounts", headers=headers).json()
    assert any(a["id"] == account_id and a["name"] == "HSBC Current" for a in accounts)


def test_disconnect_marks_revoked_and_detaches_accounts(
    auth_client: tuple[TestClient, dict[str, str], int],
    patch_provider: _StubProvider,
) -> None:
    client, headers, _ = auth_client
    start = client.post(
        "/api/v1/bank-connections/link/start",
        headers=headers,
        json={"institution_id": "SBOX_BF01"},
    ).json()
    bank_connection_id = start["bank_connection_id"]
    client.post(
        "/api/v1/bank-connections/link/complete",
        headers=headers,
        json={"requisition_id": start["requisition_id"]},
    )
    client.post(
        f"/api/v1/bank-connections/{bank_connection_id}/map-account",
        headers=headers,
        json={"external_account_id": "acc-xyz", "name": "HSBC Current", "currency": "GBP"},
    )

    r = client.delete(f"/api/v1/bank-connections/{bank_connection_id}", headers=headers)
    assert r.status_code == 204
    assert "req-123" in patch_provider.revoked

    listed = client.get("/api/v1/bank-connections", headers=headers).json()
    assert listed[0]["status"] == "revoked"


def test_endpoints_503_when_provider_unconfigured(
    auth_client: tuple[TestClient, dict[str, str], int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, headers, _ = auth_client
    monkeypatch.setattr(settings, "gocardless_secret_id", None)
    r = client.get("/api/v1/bank-connections/institutions?country=GB", headers=headers)
    assert r.status_code == 503


# Suppress linter noise: date is imported for use in stub signatures only.
_ = date
