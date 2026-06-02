from decimal import Decimal

from fastapi.testclient import TestClient


def _new_user(client: TestClient, email: str) -> dict[str, str]:
    client.cookies.clear()
    r = client.post(
        "/api/v1/auth/signup", json={"email": email, "password": "pw-12345678"}
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_create_line_and_list(
    auth_client: tuple[TestClient, dict[str, str], int],
) -> None:
    client, headers, _ = auth_client
    r = client.post(
        "/api/v1/cashflow/lines",
        headers=headers,
        json={"name": "Hurdle", "kind": "income", "country": "gb", "currency": "gbp"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["country"] == "GB"
    assert body["currency"] == "GBP"
    assert body["kind"] == "income"
    assert body["is_active"] is True
    assert body["entries"] == []

    lst = client.get("/api/v1/cashflow/lines", headers=headers).json()
    assert len(lst) == 1
    assert lst[0]["name"] == "Hurdle"


def test_line_name_unique_per_user(
    auth_client: tuple[TestClient, dict[str, str], int],
) -> None:
    client, headers, _ = auth_client
    payload = {"name": "House", "kind": "expense", "country": "GB", "currency": "GBP"}
    assert client.post("/api/v1/cashflow/lines", headers=headers, json=payload).status_code == 201
    dup = client.post("/api/v1/cashflow/lines", headers=headers, json=payload)
    assert dup.status_code == 409


def test_entry_upsert_is_idempotent(
    auth_client: tuple[TestClient, dict[str, str], int],
) -> None:
    client, headers, _ = auth_client
    line = client.post(
        "/api/v1/cashflow/lines",
        headers=headers,
        json={"name": "House", "kind": "expense", "country": "GB", "currency": "GBP"},
    ).json()
    body = {"line_id": line["id"], "year": 2026, "month": 4, "amount": "1600.00"}
    r1 = client.put("/api/v1/cashflow/entries", headers=headers, json=body)
    assert r1.status_code == 200
    entry_id = r1.json()["id"]
    # Same period → updates in place, no new row.
    body["amount"] = "1700.00"
    r2 = client.put("/api/v1/cashflow/entries", headers=headers, json=body)
    assert r2.status_code == 200
    assert r2.json()["id"] == entry_id
    assert Decimal(r2.json()["amount"]) == Decimal("1700.00")


def test_bulk_upsert_saves_overrides(
    auth_client: tuple[TestClient, dict[str, str], int],
) -> None:
    client, headers, _ = auth_client
    a = client.post(
        "/api/v1/cashflow/lines",
        headers=headers,
        json={"name": "House", "kind": "expense", "country": "GB", "currency": "GBP"},
    ).json()
    b = client.post(
        "/api/v1/cashflow/lines",
        headers=headers,
        json={"name": "Hurdle", "kind": "income", "country": "GB", "currency": "GBP"},
    ).json()
    r = client.post(
        "/api/v1/cashflow/entries/bulk",
        headers=headers,
        json={
            "entries": [
                {"line_id": a["id"], "year": 2026, "month": 4, "amount": "1600"},
                {"line_id": a["id"], "year": 2026, "month": 5, "amount": "1600"},
                {"line_id": b["id"], "year": 2026, "month": 4, "amount": "5200"},
            ]
        },
    )
    assert r.status_code == 200
    assert len(r.json()) == 3


def test_bulk_upsert_rejects_foreign_line(
    auth_client: tuple[TestClient, dict[str, str], int],
) -> None:
    client, headers_a, _ = auth_client
    headers_b = _new_user(client, "other@coffer.dev")
    b_line = client.post(
        "/api/v1/cashflow/lines",
        headers=headers_b,
        json={"name": "Other", "kind": "income", "country": "GB", "currency": "GBP"},
    ).json()
    client.cookies.clear()
    r = client.post(
        "/api/v1/cashflow/entries/bulk",
        headers=headers_a,
        json={"entries": [{"line_id": b_line["id"], "year": 2026, "month": 4, "amount": "1"}]},
    )
    assert r.status_code == 404


def test_grid_totals_across_currencies_and_country_filter(
    auth_client: tuple[TestClient, dict[str, str], int],
) -> None:
    client, headers, _ = auth_client
    # GB income + GB expense in GBP
    hurdle = client.post(
        "/api/v1/cashflow/lines",
        headers=headers,
        json={"name": "Hurdle", "kind": "income", "country": "GB", "currency": "GBP"},
    ).json()
    house = client.post(
        "/api/v1/cashflow/lines",
        headers=headers,
        json={"name": "House", "kind": "expense", "country": "GB", "currency": "GBP"},
    ).json()
    # CL expense in CLP
    cmr = client.post(
        "/api/v1/cashflow/lines",
        headers=headers,
        json={"name": "CMR Falabella", "kind": "expense", "country": "CL", "currency": "CLP"},
    ).json()

    client.post(
        "/api/v1/cashflow/entries/bulk",
        headers=headers,
        json={
            "entries": [
                {"line_id": hurdle["id"], "year": 2026, "month": 4, "amount": "5200"},
                {"line_id": house["id"], "year": 2026, "month": 4, "amount": "1600"},
                {"line_id": cmr["id"], "year": 2026, "month": 4, "amount": "650"},
                {"line_id": cmr["id"], "year": 2026, "month": 5, "amount": "600"},
            ]
        },
    )

    grid = client.get(
        "/api/v1/cashflow/grid",
        headers=headers,
        params={"start_year": 2026, "start_month": 4, "months": 2},
    ).json()

    assert [(m["year"], m["month"]) for m in grid["months"]] == [(2026, 4), (2026, 5)]
    assert len(grid["lines"]) == 3
    # Two currencies, sorted alphabetically.
    currencies = [t["currency"] for t in grid["totals_by_currency"]]
    assert currencies == ["CLP", "GBP"]
    gbp = next(t for t in grid["totals_by_currency"] if t["currency"] == "GBP")
    assert Decimal(gbp["months"][0]["income"]) == Decimal("5200.00")
    assert Decimal(gbp["months"][0]["expense"]) == Decimal("1600.00")
    assert Decimal(gbp["months"][0]["net"]) == Decimal("3600.00")
    assert Decimal(gbp["months"][1]["net"]) == Decimal("0")
    clp = next(t for t in grid["totals_by_currency"] if t["currency"] == "CLP")
    assert Decimal(clp["months"][0]["expense"]) == Decimal("650.00")
    assert Decimal(clp["months"][1]["expense"]) == Decimal("600.00")

    # Country filter trims the response.
    grid_cl = client.get(
        "/api/v1/cashflow/grid",
        headers=headers,
        params={"start_year": 2026, "start_month": 4, "months": 2, "country": "CL"},
    ).json()
    assert {ln["name"] for ln in grid_cl["lines"]} == {"CMR Falabella"}
    assert [t["currency"] for t in grid_cl["totals_by_currency"]] == ["CLP"]


def test_delete_line_cascades_entries(
    auth_client: tuple[TestClient, dict[str, str], int],
) -> None:
    client, headers, _ = auth_client
    line = client.post(
        "/api/v1/cashflow/lines",
        headers=headers,
        json={"name": "House", "kind": "expense", "country": "GB", "currency": "GBP"},
    ).json()
    client.put(
        "/api/v1/cashflow/entries",
        headers=headers,
        json={"line_id": line["id"], "year": 2026, "month": 4, "amount": "1600"},
    )
    assert client.delete(f"/api/v1/cashflow/lines/{line['id']}", headers=headers).status_code == 204
    grid = client.get(
        "/api/v1/cashflow/grid",
        headers=headers,
        params={"start_year": 2026, "start_month": 4, "months": 1},
    ).json()
    assert grid["lines"] == []
    assert grid["totals_by_currency"] == []


def test_cross_user_isolation(
    auth_client: tuple[TestClient, dict[str, str], int],
) -> None:
    client, headers_a, _ = auth_client
    a_line = client.post(
        "/api/v1/cashflow/lines",
        headers=headers_a,
        json={"name": "A line", "kind": "income", "country": "GB", "currency": "GBP"},
    ).json()

    headers_b = _new_user(client, "isol-b@coffer.dev")
    # B's listing is empty.
    assert client.get("/api/v1/cashflow/lines", headers=headers_b).json() == []
    # B cannot mutate A's line.
    client.cookies.clear()
    r = client.patch(
        f"/api/v1/cashflow/lines/{a_line['id']}",
        headers=headers_b,
        json={"name": "stolen"},
    )
    assert r.status_code == 404
    # B cannot upsert into A's line.
    client.cookies.clear()
    r = client.put(
        "/api/v1/cashflow/entries",
        headers=headers_b,
        json={"line_id": a_line["id"], "year": 2026, "month": 4, "amount": "1"},
    )
    assert r.status_code == 404
