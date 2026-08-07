"""API tests for debt CRUD: per-repayment-type conditional validation,
per-debt currency, the legacy (pre-mechanics) revolving shape, the payoff
planner (optimal + schedule, honest FX conversion), and the summary totals."""

from decimal import Decimal


def _create_debt(client, headers, **fields) -> dict:
    payload = {"name": "Test debt", **fields}
    return client.post("/api/v1/debts", headers=headers, json=payload)


def test_create_revolving_defaults(auth_client) -> None:
    """Today's shape keeps working: no new fields required for revolving."""
    client, headers, _ = auth_client
    r = _create_debt(
        client,
        headers,
        name="Barclaycard",
        current_balance="1200.00",
        interest_rate_apr="24.9",
        minimum_payment="35.00",
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["repayment_type"] == "revolving"
    assert body["currency"] is None
    assert body["installment_amount"] is None


def test_create_one_debt_of_each_type(auth_client) -> None:
    client, headers, _ = auth_client
    cases = [
        {
            "name": "Card",
            "repayment_type": "revolving",
            "current_balance": "500.00",
            "minimum_payment": "25.00",
        },
        {
            "name": "Car loan",
            "repayment_type": "amortized",
            "current_balance": "8000.00",
            "installment_amount": "250.00",
            "ends_on": "2029-06-01",
            "interest_rate_apr": "7.5",
        },
        {
            "name": "Flat loan",
            "repayment_type": "flat",
            "original_principal": "5000.00",
            "current_balance": "3000.00",
            "installment_amount": "180.00",
            "ends_on": "2028-01-01",
            "interest_rate_apr": "12.0",
            "currency": "clp",
        },
        {
            "name": "Statement loan",
            "repayment_type": "statement_only",
            "current_balance": "2400.00",
            "installment_amount": "120.00",
            "ends_on": "2028-06-01",
        },
    ]
    for case in cases:
        r = _create_debt(client, headers, **case)
        assert r.status_code == 201, f"{case['repayment_type']}: {r.text}"
        body = r.json()
        assert body["repayment_type"] == case["repayment_type"]
        if "installment_amount" in case:
            assert body["installment_amount"] == case["installment_amount"]
        if "ends_on" in case:
            assert body["ends_on"] == case["ends_on"]
    # Currency is uppercased at the edge.
    listed = client.get("/api/v1/debts", headers=headers).json()
    flat = next(d for d in listed if d["name"] == "Flat loan")
    assert flat["currency"] == "CLP"


def test_amortized_requires_installment_and_ends_on(auth_client) -> None:
    client, headers, _ = auth_client
    r = _create_debt(
        client,
        headers,
        repayment_type="amortized",
        current_balance="8000.00",
        ends_on="2029-06-01",
    )
    assert r.status_code == 422
    assert "installment_amount" in r.text

    r = _create_debt(
        client,
        headers,
        repayment_type="amortized",
        current_balance="8000.00",
        installment_amount="250.00",
    )
    assert r.status_code == 422
    assert "ends_on" in r.text


def test_flat_requires_positive_original_principal(auth_client) -> None:
    client, headers, _ = auth_client
    for principal in (None, "0"):
        fields = {
            "repayment_type": "flat",
            "current_balance": "3000.00",
            "installment_amount": "180.00",
            "ends_on": "2028-01-01",
        }
        if principal is not None:
            fields["original_principal"] = principal
        r = _create_debt(client, headers, **fields)
        assert r.status_code == 422, r.text
        assert "original_principal" in r.text


def test_statement_only_requires_current_balance(auth_client) -> None:
    client, headers, _ = auth_client
    # Missing (defaults to 0) and explicit zero both fail at CREATE — the rate
    # is inferred from the balance, so a new statement-only debt needs one.
    for balance in (None, "0"):
        fields = {
            "repayment_type": "statement_only",
            "installment_amount": "120.00",
            "ends_on": "2028-06-01",
        }
        if balance is not None:
            fields["current_balance"] = balance
        r = _create_debt(client, headers, **fields)
        assert r.status_code == 422, r.text
        assert "current_balance" in r.text


def test_bad_currency_code_rejected(auth_client) -> None:
    client, headers, _ = auth_client
    for bad in ("GBPX", "G1P", "£"):
        r = _create_debt(client, headers, current_balance="100.00", currency=bad)
        assert r.status_code == 422, f"{bad!r}: {r.text}"


def test_patch_to_amortized_enforces_required_fields(auth_client) -> None:
    """Switching type via a partial PATCH must validate the merged debt."""
    client, headers, _ = auth_client
    r = _create_debt(client, headers, current_balance="1000.00", minimum_payment="30.00")
    assert r.status_code == 201, r.text
    debt_id = r.json()["id"]

    r = client.patch(f"/api/v1/debts/{debt_id}", headers=headers, json={"repayment_type": "amortized"})
    assert r.status_code == 422
    assert "installment_amount" in r.text

    # The failed PATCH must not have half-applied: the debt is still revolving.
    listed = client.get("/api/v1/debts", headers=headers).json()
    assert next(d for d in listed if d["id"] == debt_id)["repayment_type"] == "revolving"

    # Supplying the required fields in the same PATCH succeeds.
    r = client.patch(
        f"/api/v1/debts/{debt_id}",
        headers=headers,
        json={"repayment_type": "amortized", "installment_amount": "150.00", "ends_on": "2028-06-01"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["repayment_type"] == "amortized"


def test_patch_statement_only_balance_to_zero_allowed(auth_client) -> None:
    """Paying a statement-only debt off must not 422: the > 0 rule applies at
    CREATE only — updates allow 0 and reject only negatives."""
    client, headers, _ = auth_client
    r = _create_debt(
        client,
        headers,
        repayment_type="statement_only",
        current_balance="500.00",
        installment_amount="120.00",
        ends_on="2028-06-01",
    )
    assert r.status_code == 201, r.text
    debt_id = r.json()["id"]

    r = client.patch(f"/api/v1/debts/{debt_id}", headers=headers, json={"current_balance": "0"})
    assert r.status_code == 200, r.text
    assert Decimal(r.json()["current_balance"]) == Decimal("0")

    r = client.patch(f"/api/v1/debts/{debt_id}", headers=headers, json={"current_balance": "-1"})
    assert r.status_code == 422
    assert "current_balance" in r.text


def test_patch_to_flat_requires_original_principal(auth_client) -> None:
    """Switching type via PATCH validates the merged debt: flat needs a
    positive original_principal (interest is computed on it)."""
    client, headers, _ = auth_client
    r = _create_debt(client, headers, current_balance="3000.00", minimum_payment="90.00")
    assert r.status_code == 201, r.text
    debt_id = r.json()["id"]

    r = client.patch(
        f"/api/v1/debts/{debt_id}",
        headers=headers,
        json={"repayment_type": "flat", "installment_amount": "180.00", "ends_on": "2028-01-01"},
    )
    assert r.status_code == 422
    assert "original_principal" in r.text

    # Nothing half-applied, and supplying the principal in the same PATCH works.
    listed = client.get("/api/v1/debts", headers=headers).json()
    assert next(d for d in listed if d["id"] == debt_id)["repayment_type"] == "revolving"
    r = client.patch(
        f"/api/v1/debts/{debt_id}",
        headers=headers,
        json={
            "repayment_type": "flat",
            "installment_amount": "180.00",
            "ends_on": "2028-01-01",
            "original_principal": "5000.00",
        },
    )
    assert r.status_code == 200, r.text


# ---- POST /debts/plan (optimal + schedule, honest conversion) -----------------


def test_plan_mixed_portfolio_returns_optimal_with_schedule(auth_client) -> None:
    client, headers, _ = auth_client
    cases = [
        {
            "name": "Card",
            "repayment_type": "revolving",
            "current_balance": "3000.00",
            "interest_rate_apr": "24.9",
            "minimum_payment": "75.00",
        },
        {
            "name": "Car loan",
            "repayment_type": "amortized",
            "current_balance": "8000.00",
            "installment_amount": "250.00",
            "ends_on": "2029-06-01",
            "interest_rate_apr": "7.5",
        },
        {
            "name": "Flat loan",
            "repayment_type": "flat",
            "original_principal": "5000.00",
            "current_balance": "3000.00",
            "installment_amount": "180.00",
            "ends_on": "2028-01-01",
            "interest_rate_apr": "12.0",
        },
        {
            "name": "Statement loan",
            "repayment_type": "statement_only",
            "current_balance": "2400.00",
            "installment_amount": "120.00",
            "ends_on": "2028-06-01",
        },
    ]
    for case in cases:
        r = _create_debt(client, headers, **case)
        assert r.status_code == 201, f"{case['name']}: {r.text}"

    r = client.post("/api/v1/debts/plan", headers=headers, json={"extra_monthly": "200"})
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["excluded_currencies"] == []

    optimal = plan["optimal"]
    assert optimal["strategy"] == "optimal"
    assert optimal["unpayable"] is False
    assert len(optimal["debts"]) == 4
    assert all(d["currency"] is None for d in optimal["debts"])
    # Optimal is never worse than any displayed strategy.
    assert float(optimal["total_interest"]) <= float(plan["avalanche"]["total_interest"])
    # The statement-only rate is inferred, and the plan says so.
    assert any("estimated" in a for a in optimal["assumptions"])

    # Only the optimal plan carries the per-debt monthly schedule.
    assert optimal["schedule"], "optimal plan must carry a schedule"
    first = optimal["schedule"][0]
    assert set(first) == {"month", "payments", "uncommitted"}
    assert first["payments"], "month 1 pays the contractual amounts"
    assert set(first["payments"][0]) == {"debt_id", "amount"}
    for strategy in ("minimum", "snowball", "avalanche"):
        assert plan[strategy]["schedule"] == []


def test_plan_excludes_foreign_debt_without_rate(auth_client) -> None:
    client, headers, _ = auth_client
    client.patch("/api/v1/auth/me", headers=headers, json={"display_currency": "GBP"})
    r = _create_debt(
        client,
        headers,
        name="UK card",
        current_balance="1000.00",
        interest_rate_apr="24.9",
        minimum_payment="50.00",
    )
    assert r.status_code == 201, r.text
    r = _create_debt(
        client,
        headers,
        name="Chile loan",
        current_balance="1000000.00",
        interest_rate_apr="30.0",
        minimum_payment="50000.00",
        currency="CLP",
    )
    assert r.status_code == 201, r.text
    clp_id = r.json()["id"]

    r = client.post("/api/v1/debts/plan", headers=headers, json={"extra_monthly": "100"})
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["excluded_currencies"] == ["CLP"]
    # The CLP debt sits outside every simulation, and each plan says so.
    for strategy in ("minimum", "snowball", "avalanche", "optimal"):
        assert clp_id not in {d["id"] for d in plan[strategy]["debts"]}
        assert any("unconverted (CLP)" in a for a in plan[strategy]["assumptions"])

    # With a saved rate the debt joins the pool, converted at today's rate.
    client.put("/api/v1/fx", headers=headers, json=[{"currency": "CLP", "rate": 0.001}])
    plan = client.post("/api/v1/debts/plan", headers=headers, json={}).json()
    assert plan["excluded_currencies"] == []
    clp = next(d for d in plan["optimal"]["debts"] if d["id"] == clp_id)
    assert clp["currency"] == "CLP"
    assert any("converted from CLP" in a for a in plan["optimal"]["assumptions"])


# ---- GET /debts/summary (display-currency totals) ------------------------------


def test_summary_converts_rated_and_excludes_unrated_currencies(auth_client) -> None:
    client, headers, _ = auth_client
    client.patch("/api/v1/auth/me", headers=headers, json={"display_currency": "GBP"})
    _create_debt(client, headers, name="UK card", current_balance="100.00")
    r = _create_debt(client, headers, name="Chile loan", current_balance="1000000.00", currency="CLP")
    assert r.status_code == 201, r.text
    clp_id = r.json()["id"]

    # No CLP rate: the CLP balance must never be summed raw into the total.
    s = client.get("/api/v1/debts/summary", headers=headers).json()
    assert Decimal(s["total_owed"]) == Decimal("100.00")
    assert s["excluded_currencies"] == ["CLP"]
    by_id = {d["id"]: d for d in s["by_debt"]}
    assert by_id[clp_id]["converted"] is False
    # Still listed with its raw balance in its own currency.
    assert Decimal(by_id[clp_id]["current_balance"]) == Decimal("1000000.00")
    assert by_id[clp_id]["currency"] == "CLP"

    client.put("/api/v1/fx", headers=headers, json=[{"currency": "CLP", "rate": 0.00082}])
    s = client.get("/api/v1/debts/summary", headers=headers).json()
    assert Decimal(s["total_owed"]) == Decimal("920.00")  # 100 + 1,000,000 × 0.00082
    assert s["excluded_currencies"] == []
    assert all(d["converted"] for d in s["by_debt"])


def test_legacy_debt_lists_as_revolving_with_null_currency(auth_client, db_session) -> None:
    """A debt row written without the new fields (pre-migration shape) reads
    back as revolving with no currency and no installment."""
    from app.models.debt import Debt

    client, headers, user_id = auth_client
    db_session.add(Debt(user_id=user_id, name="Old loan"))
    db_session.commit()

    listed = client.get("/api/v1/debts", headers=headers).json()
    old = next(d for d in listed if d["name"] == "Old loan")
    assert old["repayment_type"] == "revolving"
    assert old["currency"] is None
    assert old["installment_amount"] is None
