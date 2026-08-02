"""Multi-currency: fx conversion, net-worth conversion/exclusion, the fx-rates
API, the display-currency setting, and the single-currency forecast filter."""

from datetime import date
from decimal import Decimal

from app.models.account import AccountType
from app.services.analytics.fx import convert
from app.services.analytics.net_worth import AccountData, compute_net_worth

# ---------------------------------------------------------------- fx.convert


def test_convert_same_currency_is_identity() -> None:
    assert convert(Decimal("123.45"), "GBP", "GBP", {}) == Decimal("123.45")


def test_convert_applies_rate_and_quantizes() -> None:
    rates = {"CLP": Decimal("0.00082")}
    assert convert(Decimal("1000000"), "CLP", "GBP", rates) == Decimal("820.00")


def test_convert_missing_or_bad_rate_returns_none() -> None:
    assert convert(Decimal("10"), "CLP", "GBP", {}) is None
    assert convert(Decimal("10"), "CLP", "GBP", {"CLP": Decimal("0")}) is None
    assert convert(Decimal("10"), "CLP", "GBP", {"CLP": Decimal("-1")}) is None


# ------------------------------------------------- compute_net_worth with FX


def _acc(id_: int, currency: str, opening: str, type_: AccountType = AccountType.CHECKING) -> AccountData:
    return AccountData(id=id_, name=f"a{id_}", type=type_, currency=currency, opening_balance=Decimal(opening))


def test_net_worth_converts_with_rate() -> None:
    accounts = [_acc(1, "GBP", "1000"), _acc(2, "CLP", "1000000")]
    report = compute_net_worth(
        accounts, [], months=3, today=date(2026, 6, 15),
        display_currency="GBP", rates={"CLP": Decimal("0.00082")},
    )
    assert report.display_currency == "GBP"
    assert report.excluded_currencies == []
    assert report.assets == Decimal("1820.00")  # 1000 + 1,000,000 * 0.00082
    assert all(b.converted for b in report.accounts)
    # Balances stay native — conversion applies to totals only.
    clp = next(b for b in report.accounts if b.id == 2)
    assert clp.balance == Decimal("1000000.00")


def test_net_worth_excludes_unconvertible_accounts() -> None:
    accounts = [_acc(1, "GBP", "1000"), _acc(2, "CLP", "1000000")]
    report = compute_net_worth(
        accounts, [], months=3, today=date(2026, 6, 15), display_currency="GBP", rates={},
    )
    assert report.excluded_currencies == ["CLP"]
    assert report.assets == Decimal("1000.00")
    clp = next(b for b in report.accounts if b.id == 2)
    assert clp.converted is False
    # Series must exclude the unconvertible account too.
    assert report.series[-1].assets == Decimal("1000.00")


def test_net_worth_no_display_keeps_legacy_mixed_sum() -> None:
    accounts = [_acc(1, "GBP", "1000"), _acc(2, "CLP", "500")]
    report = compute_net_worth(accounts, [], months=3, today=date(2026, 6, 15))
    assert report.display_currency is None
    assert report.excluded_currencies == []
    assert report.assets == Decimal("1500.00")


def test_net_worth_converts_liability_accounts() -> None:
    accounts = [_acc(1, "GBP", "1000"), _acc(2, "CLP", "-1000000", AccountType.CREDIT_CARD)]
    report = compute_net_worth(
        accounts, [], months=3, today=date(2026, 6, 15),
        display_currency="GBP", rates={"CLP": Decimal("0.00082")},
    )
    assert report.liabilities == Decimal("820.00")
    assert report.net == Decimal("180.00")


# ------------------------------------------------------------- fx rates API


def test_fx_crud_roundtrip(auth_client) -> None:
    client, headers, _ = auth_client
    assert client.get("/api/v1/fx", headers=headers).json() == []

    r = client.put(
        "/api/v1/fx", headers=headers,
        json=[{"currency": "clp", "rate": 0.00082, "as_of": "2026-06-01"}],
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert rows[0]["currency"] == "CLP"  # uppercased on write
    assert Decimal(rows[0]["rate"]) == Decimal("0.00082")

    # Upsert overwrites in place — no duplicate row.
    r = client.put("/api/v1/fx", headers=headers, json=[{"currency": "CLP", "rate": 0.0009}])
    rows = r.json()
    assert len(rows) == 1
    assert Decimal(rows[0]["rate"]) == Decimal("0.0009")

    assert client.delete("/api/v1/fx/clp", headers=headers).status_code == 204
    assert client.delete("/api/v1/fx/CLP", headers=headers).status_code == 404
    assert client.get("/api/v1/fx", headers=headers).json() == []


def test_fx_rejects_nonpositive_rate_and_bad_currency(auth_client) -> None:
    client, headers, _ = auth_client
    assert client.put("/api/v1/fx", headers=headers, json=[{"currency": "CLP", "rate": 0}]).status_code == 422
    assert client.put("/api/v1/fx", headers=headers, json=[{"currency": "CL", "rate": 1}]).status_code == 422


def test_fx_rates_are_per_user(auth_client) -> None:
    client, headers, _ = auth_client
    client.put("/api/v1/fx", headers=headers, json=[{"currency": "CLP", "rate": 0.00082}])

    r = client.post("/api/v1/auth/signup", json={"email": "other@coffer.dev", "password": "other-pw-1234"})
    other = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get("/api/v1/fx", headers=other).json() == []
    assert client.delete("/api/v1/fx/CLP", headers=other).status_code == 404


# ------------------------------------------------------ display currency API


def test_patch_me_sets_display_currency(auth_client) -> None:
    client, headers, _ = auth_client
    assert client.get("/api/v1/auth/me", headers=headers).json()["display_currency"] is None

    r = client.patch("/api/v1/auth/me", headers=headers, json={"display_currency": "gbp"})
    assert r.status_code == 200, r.text
    assert r.json()["display_currency"] == "GBP"
    assert client.get("/api/v1/auth/me", headers=headers).json()["display_currency"] == "GBP"


def test_patch_me_rejects_bad_length(auth_client) -> None:
    client, headers, _ = auth_client
    assert client.patch("/api/v1/auth/me", headers=headers, json={"display_currency": "POUND"}).status_code == 422


# ----------------------------------------------------- endpoints end-to-end


def _account(client, headers, *, name: str, currency: str, opening: str, type_: str = "checking") -> int:
    r = client.post(
        "/api/v1/accounts", headers=headers,
        json={"name": name, "type": type_, "currency": currency, "opening_balance": opening},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_networth_endpoint_converts_and_flags(auth_client) -> None:
    client, headers, _ = auth_client
    _account(client, headers, name="UK", currency="GBP", opening="1000")
    clp_id = _account(client, headers, name="Chile", currency="CLP", opening="1000000")
    client.patch("/api/v1/auth/me", headers=headers, json={"display_currency": "GBP"})

    nw = client.get("/api/v1/insights/networth", headers=headers).json()
    assert nw["display_currency"] == "GBP"
    assert nw["excluded_currencies"] == ["CLP"]
    assert Decimal(nw["assets"]) == Decimal("1000.00")
    assert next(a for a in nw["accounts"] if a["id"] == clp_id)["converted"] is False

    client.put("/api/v1/fx", headers=headers, json=[{"currency": "CLP", "rate": 0.00082}])
    nw = client.get("/api/v1/insights/networth", headers=headers).json()
    assert nw["excluded_currencies"] == []
    assert Decimal(nw["assets"]) == Decimal("1820.00")


def test_networth_endpoint_defaults_to_most_common_currency(auth_client) -> None:
    client, headers, _ = auth_client
    _account(client, headers, name="A", currency="GBP", opening="100")
    _account(client, headers, name="B", currency="GBP", opening="200")
    _account(client, headers, name="C", currency="CLP", opening="900")

    nw = client.get("/api/v1/insights/networth", headers=headers).json()
    assert nw["display_currency"] == "GBP"
    assert nw["excluded_currencies"] == ["CLP"]
    assert Decimal(nw["assets"]) == Decimal("300.00")


def test_forecast_endpoint_is_single_currency(auth_client) -> None:
    client, headers, _ = auth_client
    _account(client, headers, name="UK", currency="GBP", opening="1000")
    _account(client, headers, name="Chile", currency="CLP", opening="1000000")
    client.patch("/api/v1/auth/me", headers=headers, json={"display_currency": "GBP"})

    f = client.get("/api/v1/insights/forecast", headers=headers).json()
    assert f["display_currency"] == "GBP"
    assert f["excluded_currencies"] == ["CLP"]
    assert Decimal(f["start_balance"]) == Decimal("1000.00")
