"""API tests for debt CRUD: per-repayment-type conditional validation,
per-debt currency, and the legacy (pre-mechanics) revolving shape."""


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
    r = _create_debt(
        client,
        headers,
        repayment_type="statement_only",
        installment_amount="120.00",
        ends_on="2028-06-01",
    )
    assert r.status_code == 422
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
