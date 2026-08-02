"""Statements as ground truth: month-gap detection, balance-chain continuity,
and read-only replay of stored originals against the ledger."""

from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from app.core.config import settings
from app.services.ground_truth import chain_breaks, month_gaps

# ---------------------------------------------------------------- pure checks


def test_month_gaps() -> None:
    assert month_gaps(set()) == []
    assert month_gaps({(2026, 3)}) == []
    assert month_gaps({(2026, 3), (2026, 4), (2026, 5)}) == []
    assert month_gaps({(2026, 3), (2026, 6)}) == ["2026-04", "2026-05"]
    # Cross-year hole.
    assert month_gaps({(2025, 11), (2026, 2)}) == ["2025-12", "2026-01"]


def test_chain_breaks_intact_and_broken() -> None:
    snaps = [(date(2026, 1, 31), Decimal("1000")), (date(2026, 2, 28), Decimal("1500"))]
    txns = [(date(2026, 2, 10), Decimal("500"))]
    assert chain_breaks(snaps, txns) == []

    # The bank attests 1500 but the ledger only explains 1200 — £300 of
    # activity is missing between the two statements.
    breaks = chain_breaks(snaps, [(date(2026, 2, 10), Decimal("200"))])
    assert len(breaks) == 1
    b = breaks[0]
    assert (b.prev_as_of, b.as_of) == (date(2026, 1, 31), date(2026, 2, 28))
    assert b.expected == Decimal("1200.00")
    assert b.delta == Decimal("300.00")


def test_chain_breaks_sorts_input_and_ignores_outside_txns() -> None:
    snaps = [(date(2026, 2, 28), Decimal("900")), (date(2026, 1, 31), Decimal("1000"))]
    txns = [
        (date(2026, 1, 15), Decimal("999")),  # before the first attestation
        (date(2026, 2, 5), Decimal("-100")),
        (date(2026, 3, 5), Decimal("999")),  # after the last
    ]
    assert chain_breaks(snaps, txns) == []


# ------------------------------------------------------------- API scenarios


def _account(client, headers, *, name="Current", bank_id="lloyds") -> int:
    r = client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": name, "type": "checking", "currency": "GBP", "opening_balance": "0", "bank_id": bank_id},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _lloyds_csv(rows: list[tuple[date, str, Decimal, Decimal]]) -> bytes:
    """rows: (posted_on, description, amount, running_balance)."""
    header = (
        "Transaction Date,Transaction Type,Sort Code,Account Number,"
        "Transaction Description,Debit Amount,Credit Amount,Balance\n"
    )
    lines = []
    for on, desc, amount, balance in rows:
        debit = f"{-amount:.2f}" if amount < 0 else ""
        credit = f"{amount:.2f}" if amount > 0 else ""
        lines.append(f"{on.strftime('%d/%m/%Y')},FPI,'11-22-33,123,{desc},{debit},{credit},{balance:.2f}\n")
    return (header + "".join(lines)).encode()


def _upload(client, headers, account_id: int, content: bytes, name: str = "statement.csv") -> dict:
    r = client.post(
        "/api/v1/imports/upload",
        headers=headers,
        files={"file": (name, BytesIO(content), "text/csv")},
        data={"account_id": str(account_id)},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _months_back(n: int) -> date:
    first = date.today().replace(day=1)
    for _ in range(n):
        first = (first - timedelta(days=1)).replace(day=1)
    return first


def test_integrity_clean_after_import(auth_client) -> None:
    client, headers, _ = auth_client
    account_id = _account(client, headers)
    m2, m1 = _months_back(2), _months_back(1)
    _upload(
        client, headers, account_id,
        _lloyds_csv([
            (m2.replace(day=5), "SALARY", Decimal("2000"), Decimal("2000")),
            (m1.replace(day=5), "SALARY", Decimal("2000"), Decimal("3800")),
            (m1.replace(day=20), "RENT", Decimal("-200"), Decimal("3600")),
        ]),
    )

    report = client.get("/api/v1/integrity", headers=headers).json()
    acct = next(a for a in report["accounts"] if a["account_id"] == account_id)
    assert acct["statement_count"] == 1
    assert acct["files_missing"] == 0
    assert acct["missing_months"] == []
    assert acct["chain_breaks"] == []
    assert acct["first_documented"] == m2.isoformat()

    replay = client.post("/api/v1/integrity/replay", headers=headers).json()
    assert replay["files_ok"] == 1
    assert replay["files"][0]["parsed_rows"] == 3
    assert replay["files"][0]["matched"] == 3
    assert replay["files"][0]["status"] == "ok"


def test_integrity_reports_coverage_gap(auth_client) -> None:
    client, headers, _ = auth_client
    account_id = _account(client, headers)
    m4, m1 = _months_back(4), _months_back(1)
    _upload(client, headers, account_id, _lloyds_csv([(m4.replace(day=5), "OLD", Decimal("10"), Decimal("10"))]))
    _upload(
        client, headers, account_id,
        _lloyds_csv([(m1.replace(day=5), "NEW", Decimal("10"), Decimal("20"))]),
        name="later.csv",
    )

    report = client.get("/api/v1/integrity", headers=headers).json()
    acct = next(a for a in report["accounts"] if a["account_id"] == account_id)
    assert len(acct["missing_months"]) == 2  # the two whole months in between


def test_replay_flags_deleted_and_edited_rows(auth_client) -> None:
    client, headers, _ = auth_client
    account_id = _account(client, headers)
    m1 = _months_back(1)
    _upload(
        client, headers, account_id,
        _lloyds_csv([
            (m1.replace(day=5), "SALARY", Decimal("2000"), Decimal("2000")),
            (m1.replace(day=10), "GROCERIES", Decimal("-50"), Decimal("1950")),
            (m1.replace(day=20), "RENT", Decimal("-800"), Decimal("1150")),
        ]),
    )
    txns = client.get(f"/api/v1/transactions?account_id={account_id}", headers=headers).json()
    by_desc = {t["description"]: t for t in txns}

    assert client.delete(f"/api/v1/transactions/{by_desc['GROCERIES']['id']}", headers=headers).status_code == 204
    # Editing the amount leaves external_id in place — replay sees the drift.
    r = client.patch(
        f"/api/v1/transactions/{by_desc['RENT']['id']}", headers=headers, json={"amount": "-750"}
    )
    assert r.status_code == 200, r.text

    replay = client.post("/api/v1/integrity/replay", headers=headers).json()
    f = replay["files"][0]
    assert f["status"] == "drift"
    assert f["missing_count"] == 1
    assert f["missing_from_ledger"][0]["description"] == "GROCERIES"
    assert f["altered_count"] == 1
    assert f["altered"][0]["external_id"] == by_desc["RENT"]["external_id"]
    assert Decimal(f["altered"][0]["ledger_amount"]) == Decimal("-750")
    assert f["matched"] == 1


def test_replay_reports_missing_file(auth_client) -> None:
    client, headers, user_id = auth_client
    account_id = _account(client, headers)
    m1 = _months_back(1)
    _upload(client, headers, account_id, _lloyds_csv([(m1.replace(day=5), "ROW", Decimal("10"), Decimal("10"))]))

    stored = list((Path(settings.upload_dir) / str(user_id)).iterdir())
    assert len(stored) == 1
    stored[0].unlink()

    report = client.get("/api/v1/integrity", headers=headers).json()
    acct = next(a for a in report["accounts"] if a["account_id"] == account_id)
    assert acct["files_missing"] == 1

    replay = client.post("/api/v1/integrity/replay", headers=headers).json()
    assert replay["files_missing"] == 1
    assert replay["files"][0]["status"] == "file_missing"


def test_integrity_detects_chain_break(auth_client) -> None:
    client, headers, _ = auth_client
    account_id = _account(client, headers)
    m2, m1 = _months_back(2), _months_back(1)
    _upload(
        client, headers, account_id,
        _lloyds_csv([(m2.replace(day=28), "SALARY", Decimal("1000"), Decimal("1000"))]),
    )
    # The next statement attests 5000, but the ledger only explains 1000+100:
    # £3,900 of activity is missing between the two attestation dates.
    _upload(
        client, headers, account_id,
        _lloyds_csv([(m1.replace(day=28), "SALARY", Decimal("100"), Decimal("5000"))]),
        name="later.csv",
    )

    report = client.get("/api/v1/integrity", headers=headers).json()
    acct = next(a for a in report["accounts"] if a["account_id"] == account_id)
    assert len(acct["chain_breaks"]) == 1
    b = acct["chain_breaks"][0]
    assert Decimal(b["delta"]) == Decimal("3900.00")
    assert b["prev_as_of"] == m2.replace(day=28).isoformat()


def test_replay_scoped_to_account_and_user(auth_client) -> None:
    client, headers, _ = auth_client
    a1 = _account(client, headers, name="A")
    a2 = _account(client, headers, name="B")
    m1 = _months_back(1)
    _upload(client, headers, a1, _lloyds_csv([(m1.replace(day=5), "ONE", Decimal("10"), Decimal("10"))]))
    _upload(client, headers, a2, _lloyds_csv([(m1.replace(day=6), "TWO", Decimal("20"), Decimal("20"))]))

    replay = client.post(f"/api/v1/integrity/replay?account_id={a1}", headers=headers).json()
    assert len(replay["files"]) == 1
    assert replay["files"][0]["account_id"] == a1

    assert client.post("/api/v1/integrity/replay?account_id=999999", headers=headers).status_code == 404

    r = client.post("/api/v1/auth/signup", json={"email": "other-gt@coffer.dev", "password": "other-pw-1234"})
    other = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get("/api/v1/integrity", headers=other).json()["accounts"] == []
    assert client.post("/api/v1/integrity/replay", headers=other).json()["files"] == []
