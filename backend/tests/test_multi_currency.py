"""Multi-currency: fx conversion, net-worth conversion/exclusion, the fx-rates
API, the display-currency setting, and the single-currency forecast filter."""

from datetime import date, timedelta
from decimal import Decimal

from app.models.account import AccountType
from app.services.analytics.fx import convert, most_common_currency
from app.services.analytics.net_worth import AccountData, compute_net_worth

# ---------------------------------------------------------------- fx.convert


def test_most_common_currency_breaks_ties_alphabetically() -> None:
    assert most_common_currency([]) is None
    assert most_common_currency(["GBP", "CLP", "GBP"]) == "GBP"
    # A tie must resolve the same on backend and frontend: alphabetical.
    assert most_common_currency(["GBP", "CLP"]) == "CLP"


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
    # Non-alphabetic codes would be unreachable via the /fx/{currency} path.
    assert client.put("/api/v1/fx", headers=headers, json=[{"currency": "A/B", "rate": 1}]).status_code == 422
    # Too small to store in Numeric(18,8) (would round to 0 = "no rate"),
    # and too large for its 10 integer digits.
    for bad_rate in ["0.000000001", "10000000000"]:
        r = client.put("/api/v1/fx", headers=headers, json=[{"currency": "SAT", "rate": bad_rate}])
        assert r.status_code == 422, bad_rate


def test_fx_duplicate_currency_in_one_payload_last_wins(auth_client) -> None:
    client, headers, _ = auth_client
    r = client.put(
        "/api/v1/fx", headers=headers,
        json=[{"currency": "clp", "rate": 0.0008}, {"currency": "CLP", "rate": 0.0009}],
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 1
    assert Decimal(rows[0]["rate"]) == Decimal("0.0009")


def test_fx_rate_precision_survives_string_input(auth_client) -> None:
    client, headers, _ = auth_client
    r = client.put(
        "/api/v1/fx", headers=headers,
        json=[{"currency": "XAU", "rate": "1234567890.12345678"}],
    )
    assert r.status_code == 200, r.text
    assert Decimal(r.json()[0]["rate"]) == Decimal("1234567890.12345678")


def test_fx_update_without_as_of_stamps_today(auth_client) -> None:
    client, headers, _ = auth_client
    client.put("/api/v1/fx", headers=headers, json=[{"currency": "CLP", "rate": 0.0008, "as_of": "2026-06-01"}])
    r = client.put("/api/v1/fx", headers=headers, json=[{"currency": "CLP", "rate": 0.0009}])
    # A rate saved without an explicit date is "as of when it was saved" —
    # never silently null.
    assert r.json()[0]["as_of"] == date.today().isoformat()


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
    assert client.patch("/api/v1/auth/me", headers=headers, json={"display_currency": "12%"}).status_code == 422


def test_patch_me_null_clears_display_currency(auth_client) -> None:
    client, headers, _ = auth_client
    client.patch("/api/v1/auth/me", headers=headers, json={"display_currency": "GBP"})
    r = client.patch("/api/v1/auth/me", headers=headers, json={"display_currency": None})
    assert r.status_code == 200, r.text
    assert r.json()["display_currency"] is None


def test_display_change_deletes_saved_rates(auth_client) -> None:
    client, headers, _ = auth_client
    client.patch("/api/v1/auth/me", headers=headers, json={"display_currency": "GBP"})
    client.put("/api/v1/fx", headers=headers, json=[{"currency": "CLP", "rate": 0.00082}])
    assert len(client.get("/api/v1/fx", headers=headers).json()) == 1

    # Same value → rates survive.
    client.patch("/api/v1/auth/me", headers=headers, json={"display_currency": "GBP"})
    assert len(client.get("/api/v1/fx", headers=headers).json()) == 1

    # New target → rates were defined against the old one and must go.
    client.patch("/api/v1/auth/me", headers=headers, json={"display_currency": "USD"})
    assert client.get("/api/v1/fx", headers=headers).json() == []


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


def test_forecast_fallback_prefers_liquid_currency(auth_client) -> None:
    """Foreign debt accounts outnumbering liquid ones must not elect a display
    currency the forecast has no liquid accounts in (start would be 0)."""
    client, headers, _ = auth_client
    _account(client, headers, name="UK", currency="GBP", opening="2000")
    _account(client, headers, name="Card A", currency="CLP", opening="-100", type_="credit_card")
    _account(client, headers, name="Card B", currency="CLP", opening="-100", type_="credit_card")

    f = client.get("/api/v1/insights/forecast", headers=headers).json()
    assert f["display_currency"] == "GBP"
    assert Decimal(f["start_balance"]) == Decimal("2000.00")
    assert f["excluded_currencies"] == []


def test_forecast_due_markers_skip_foreign_currency_debts(auth_client) -> None:
    client, headers, _ = auth_client
    _account(client, headers, name="UK", currency="GBP", opening="1000")
    clp_id = _account(client, headers, name="Chile Card", currency="CLP", opening="-500000", type_="credit_card")
    client.patch("/api/v1/auth/me", headers=headers, json={"display_currency": "GBP"})

    for name, account_id in [("CLP card", clp_id), ("GBP loan", None)]:
        r = client.post(
            "/api/v1/debts", headers=headers,
            json={
                "name": name, "current_balance": "500", "minimum_payment": "50",
                "due_day_of_month": 15, "account_id": account_id,
            },
        )
        assert r.status_code == 201, r.text

    f = client.get("/api/v1/insights/forecast", headers=headers).json()
    names = {m["name"] for m in f["due_markers"]}
    # The register debt has no currency → display by convention; the CLP-linked
    # one would mislabel its minimum on a GBP calendar.
    assert names == {"GBP loan"}


def test_surplus_counts_only_display_currency_transactions(auth_client) -> None:
    client, headers, _ = auth_client
    gbp_id = _account(client, headers, name="UK", currency="GBP", opening="0")
    clp_id = _account(client, headers, name="Chile", currency="CLP", opening="0")
    client.patch("/api/v1/auth/me", headers=headers, json={"display_currency": "GBP"})

    first_of_prev = (date.today().replace(day=1) - timedelta(days=1)).replace(day=15)
    for account_id, amount in [(gbp_id, "3000"), (gbp_id, "-1000"), (clp_id, "500000")]:
        r = client.post(
            "/api/v1/transactions", headers=headers,
            json={
                "account_id": account_id, "posted_on": first_of_prev.isoformat(),
                "description": "row", "amount": amount,
            },
        )
        assert r.status_code == 201, r.text

    s = client.get("/api/v1/insights/surplus", headers=headers).json()
    # The 500,000-CLP inflow must not pollute the GBP surplus.
    assert Decimal(s["income"]) == Decimal("3000.00")
    assert Decimal(s["surplus"]) == Decimal("2000.00")


def test_account_currency_normalized_to_uppercase(auth_client) -> None:
    client, headers, _ = auth_client
    r = client.post(
        "/api/v1/accounts", headers=headers,
        json={"name": "Lower", "type": "checking", "currency": "gbp", "opening_balance": "10"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["currency"] == "GBP"
