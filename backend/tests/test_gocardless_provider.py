"""GoCardlessProvider unit tests.

We swap the provider's httpx transport for an `httpx.MockTransport` and assert
on the request shape sent to the GoCardless API and the normalization of
responses. No real network calls.
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.services.bank_providers.base import BankProviderError, BankProviderNotConfigured
from app.services.bank_providers.gocardless import GoCardlessProvider


def _provider(handler) -> GoCardlessProvider:
    return GoCardlessProvider(
        secret_id="sid-123",
        secret_key="skey-456",
        base_url="https://example.test/api/v2",
        transport=httpx.MockTransport(handler),
    )


def test_not_configured_raises() -> None:
    with pytest.raises(BankProviderNotConfigured):
        GoCardlessProvider(secret_id=None, secret_key=None, base_url="https://x")


def test_list_institutions_normalizes_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token/new/"):
            return httpx.Response(200, json={"access": "tok", "access_expires": 86400})
        if request.url.path.endswith("/institutions/"):
            assert request.url.params.get("country") == "GB"
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "SBOX_BF01",
                        "name": "Sandbox Finance",
                        "bic": "SBOXGB1",
                        "countries": ["GB"],
                        "logo": "https://example.test/logo.png",
                    }
                ],
            )
        return httpx.Response(404)

    provider = _provider(handler)
    out = asyncio.run(provider.list_institutions("GB"))
    assert len(out) == 1
    assert out[0].id == "SBOX_BF01"
    assert out[0].name == "Sandbox Finance"
    assert out[0].countries == ["GB"]
    assert out[0].logo_url == "https://example.test/logo.png"


def test_fetch_transactions_normalizes_booked_and_drops_undated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token/new/"):
            return httpx.Response(200, json={"access": "tok", "access_expires": 86400})
        if "/transactions/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "transactions": {
                        "booked": [
                            {
                                "transactionId": "tx-abc",
                                "bookingDate": "2026-05-20",
                                "transactionAmount": {"amount": "-12.50", "currency": "EUR"},
                                "remittanceInformationUnstructured": "Coffee shop",
                            },
                            {
                                # No transactionId → falls back to internalTransactionId
                                "internalTransactionId": "int-xyz",
                                "bookingDate": "2026-05-21",
                                "transactionAmount": {"amount": "2500.00", "currency": "EUR"},
                                "creditorName": "Acme Payroll",
                            },
                            {
                                # Missing date → dropped
                                "transactionAmount": {"amount": "5.00", "currency": "EUR"},
                            },
                        ],
                        "pending": [
                            # Pending list is intentionally ignored
                            {
                                "transactionId": "should-be-ignored",
                                "bookingDate": "2026-05-22",
                                "transactionAmount": {"amount": "1.00", "currency": "EUR"},
                            }
                        ],
                    }
                },
            )
        return httpx.Response(404)

    provider = _provider(handler)
    txns = asyncio.run(
        provider.fetch_transactions(
            external_account_id="acc-1",
            date_from=date(2026, 5, 1),
            date_to=date(2026, 5, 31),
        )
    )
    assert len(txns) == 2
    assert txns[0].bank_transaction_id == "tx-abc"
    assert txns[0].amount == Decimal("-12.50")
    assert txns[0].description == "Coffee shop"
    assert txns[1].bank_transaction_id == "int-xyz"
    assert txns[1].description == "Acme Payroll"


def test_provider_error_on_5xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token/new/"):
            return httpx.Response(200, json={"access": "tok"})
        return httpx.Response(500, json={"detail": "boom"})

    provider = _provider(handler)
    with pytest.raises(BankProviderError):
        asyncio.run(provider.list_institutions("GB"))


def test_revoke_swallows_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token/new/"):
            return httpx.Response(200, json={"access": "tok"})
        # Already-revoked / never-existed requisitions 404 — that's fine.
        return httpx.Response(404)

    provider = _provider(handler)
    asyncio.run(provider.revoke("missing-req"))  # must not raise


def test_create_link_posts_eua_then_requisition_with_signed_reference() -> None:
    seen: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = {}
        if request.content:
            import json
            body = json.loads(request.content)
        seen.append((request.method, request.url.path, body))
        if request.url.path.endswith("/token/new/"):
            return httpx.Response(200, json={"access": "tok"})
        if request.url.path.endswith("/agreements/enduser/"):
            return httpx.Response(200, json={"id": "agree-1"})
        if request.url.path.endswith("/requisitions/"):
            return httpx.Response(
                200,
                json={"id": "req-1", "link": "https://bank.test/login?r=req-1"},
            )
        return httpx.Response(404)

    provider = _provider(handler)
    session = asyncio.run(
        provider.create_link(
            institution_id="SBOX_BF01",
            redirect_uri="http://localhost:5173/banks/callback",
            reference="u42.nonce.macsig",
        )
    )
    assert session.requisition_id == "req-1"
    assert session.link_url.startswith("https://bank.test/")
    paths = [p for _, p, _ in seen]
    assert any(p.endswith("/agreements/enduser/") for p in paths)
    assert any(p.endswith("/requisitions/") for p in paths)
    requisition_body = next(b for _, p, b in seen if p.endswith("/requisitions/"))
    assert requisition_body["reference"] == "u42.nonce.macsig"
    assert requisition_body["agreement"] == "agree-1"
