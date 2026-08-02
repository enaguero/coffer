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
    assert chain_breaks(snaps, [(date(2026, 2, 10), Decimal("500"))]) == []

    # The bank attests 1500 but the ledger only explains 1200 — £300 of
    # activity is missing between the two statements.
    breaks = chain_breaks(snaps, [(date(2026, 2, 10), Decimal("200"))])
    assert len(breaks) == 1
    b = breaks[0]
    assert (b.prev_as_of, b.as_of) == (date(2026, 1, 31), date(2026, 2, 28))
    assert b.expected == Decimal("1200.00")
    assert b.delta == Decimal("300.00")


def test_chain_breaks_tolerates_boundary_day_ambiguity() -> None:
    """Statements can cut mid-day: a transaction dated on an attestation day
    may fall on either side of the balance. Any boundary assignment that
    explains the next balance means the chain holds."""
    snaps = [(date(2026, 1, 31), Decimal("1000")), (date(2026, 2, 28), Decimal("1500"))]
    # The Jan 31 txn was NOT in the Jan 31 attestation (mid-day cut) — strict
    # end-of-day arithmetic would report a phantom break.
    txns = [(date(2026, 1, 31), Decimal("400")), (date(2026, 2, 10), Decimal("100"))]
    assert chain_breaks(snaps, txns) == []


def test_chain_breaks_handles_card_sign_convention() -> None:
    """Card statements attest positive-owed balances while the ledger stores
    charges negative — a sign convention must never read as missing money."""
    snaps = [(date(2026, 1, 31), Decimal("500")), (date(2026, 2, 28), Decimal("800"))]
    txns = [(date(2026, 2, 10), Decimal("-300"))]  # a £300 charge
    assert chain_breaks(snaps, txns) == []


def test_chain_breaks_sorts_input_and_ignores_outside_txns() -> None:
    snaps = [(date(2026, 2, 28), Decimal("900")), (date(2026, 1, 31), Decimal("1000"))]
    txns = [
        (date(2026, 1, 15), Decimal("999")),  # before the first attestation
        (date(2026, 2, 5), Decimal("-100")),
        (date(2026, 3, 5), Decimal("999")),  # after the last
    ]
    assert chain_breaks(snaps, txns) == []


# ------------------------------------------------------------- API scenarios


def _account(client, headers, *, name="Current", type_="checking", bank_id="lloyds") -> int:
    r = client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": name, "type": type_, "currency": "GBP", "opening_balance": "0", "bank_id": bank_id},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _lloyds_csv(rows: list[tuple[date, str, Decimal, Decimal]], *, newest_first: bool = False) -> bytes:
    """rows: (posted_on, description, amount, running_balance), oldest first."""
    header = (
        "Transaction Date,Transaction Type,Sort Code,Account Number,"
        "Transaction Description,Debit Amount,Credit Amount,Balance\n"
    )
    lines = []
    for on, desc, amount, balance in rows:
        debit = f"{-amount:.2f}" if amount < 0 else ""
        credit = f"{amount:.2f}" if amount > 0 else ""
        lines.append(f"{on.strftime('%d/%m/%Y')},FPI,'11-22-33,123,{desc},{debit},{credit},{balance:.2f}\n")
    if newest_first:
        lines.reverse()
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


def _replay(client, headers) -> dict:
    r = client.post("/api/v1/integrity/replay", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


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
    assert acct["missing_month_count"] == 0
    assert acct["chain_breaks"] == []
    assert acct["first_documented"] == m2.replace(day=1).isoformat()

    replay = _replay(client, headers)
    assert replay["files_ok"] == 1
    f = replay["files"][0]
    assert (f["parsed_rows"], f["matched"], f["status"]) == (3, 3, "ok")


def test_integrity_reports_coverage_gap_exact_months(auth_client) -> None:
    client, headers, _ = auth_client
    account_id = _account(client, headers)
    m4, m3, m2, m1 = (_months_back(n) for n in (4, 3, 2, 1))
    _upload(client, headers, account_id, _lloyds_csv([(m4.replace(day=5), "OLD", Decimal("10"), Decimal("10"))]))
    _upload(
        client, headers, account_id,
        _lloyds_csv([(m1.replace(day=5), "NEW", Decimal("10"), Decimal("20"))]),
        name="later.csv",
    )

    report = client.get("/api/v1/integrity", headers=headers).json()
    acct = next(a for a in report["accounts"] if a["account_id"] == account_id)
    assert acct["missing_months"] == [m3.strftime("%Y-%m"), m2.strftime("%Y-%m")]
    assert acct["missing_month_count"] == 2


def test_coverage_comes_from_statement_period_not_surviving_rows(auth_client) -> None:
    """Deleting a statement's transactions (or importing an all-duplicate
    file) must not un-document the months the statement covers."""
    client, headers, _ = auth_client
    account_id = _account(client, headers)
    m4, m3, m2 = (_months_back(n) for n in (4, 3, 2))
    _upload(client, headers, account_id, _lloyds_csv([(m4.replace(day=5), "A", Decimal("10"), Decimal("10"))]))
    _upload(
        client, headers, account_id,
        _lloyds_csv([(m3.replace(day=5), "B", Decimal("10"), Decimal("20"))]),
        name="mid.csv",
    )
    _upload(
        client, headers, account_id,
        _lloyds_csv([(m2.replace(day=5), "C", Decimal("10"), Decimal("30"))]),
        name="new.csv",
    )
    txns = client.get(f"/api/v1/transactions?account_id={account_id}", headers=headers).json()
    mid = next(t for t in txns if t["description"] == "B")
    assert client.delete(f"/api/v1/transactions/{mid['id']}", headers=headers).status_code == 204

    report = client.get("/api/v1/integrity", headers=headers).json()
    acct = next(a for a in report["accounts"] if a["account_id"] == account_id)
    assert acct["missing_months"] == []  # the mid statement still documents its month


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

    f = _replay(client, headers)["files"][0]
    assert f["status"] == "drift"
    assert f["missing_count"] == 1
    assert f["missing_from_ledger"][0]["description"] == "GROCERIES"
    assert f["altered_count"] == 1
    assert f["altered"][0]["external_id"] == by_desc["RENT"]["external_id"]
    assert Decimal(f["altered"][0]["ledger_amount"]) == Decimal("-750")
    assert f["matched"] == 1


def test_replay_matches_rekeyed_rows_by_date_and_amount(auth_client) -> None:
    """A row whose external_id changed (parser-layer evolution, manual
    re-creation with a bank-native id) is matched by (date, amount) — parser
    drift must never read as data loss."""
    client, headers, _ = auth_client
    account_id = _account(client, headers)
    m1 = _months_back(1)
    _upload(
        client, headers, account_id,
        _lloyds_csv([(m1.replace(day=5), "SALARY", Decimal("2000"), Decimal("2000"))]),
    )
    txns = client.get(f"/api/v1/transactions?account_id={account_id}", headers=headers).json()
    assert client.delete(f"/api/v1/transactions/{txns[0]['id']}", headers=headers).status_code == 204
    r = client.post(
        "/api/v1/transactions", headers=headers,
        json={
            "account_id": account_id, "posted_on": m1.replace(day=5).isoformat(),
            "description": "Salary (recreated)", "amount": "2000", "external_id": "BANK-NATIVE-123",
        },
    )
    assert r.status_code == 201, r.text

    f = _replay(client, headers)["files"][0]
    assert (f["status"], f["matched"], f["missing_count"]) == ("ok", 1, 0)


def test_replay_counts_deduped_identical_rows_as_missing(auth_client) -> None:
    """Two identical rows in one file dedup to a single ledger transaction at
    import — a real under-import that set-matching would mask."""
    client, headers, _ = auth_client
    account_id = _account(client, headers)
    m1 = _months_back(1)
    _upload(
        client, headers, account_id,
        _lloyds_csv([
            (m1.replace(day=5), "COFFEE", Decimal("-3.20"), Decimal("-3.20")),
            (m1.replace(day=5), "COFFEE", Decimal("-3.20"), Decimal("-6.40")),
        ]),
    )
    txns = client.get(f"/api/v1/transactions?account_id={account_id}", headers=headers).json()
    assert len(txns) == 1  # the import deduped the second row

    f = _replay(client, headers)["files"][0]
    assert (f["parsed_rows"], f["matched"], f["missing_count"], f["status"]) == (2, 1, 1, "drift")


def test_replay_honors_rows_skipped_at_confirm(auth_client) -> None:
    client, headers, _ = auth_client
    account_id = _account(client, headers)
    m1 = _months_back(1)
    content = _lloyds_csv([
        (m1.replace(day=5), "SALARY", Decimal("2000"), Decimal("2000")),
        (m1.replace(day=10), "TRANSFER", Decimal("-500"), Decimal("1500")),
    ])
    r = client.post(
        "/api/v1/imports/preview",
        headers=headers,
        files={"file": ("statement.csv", BytesIO(content), "text/csv")},
        data={"account_id": str(account_id)},
    )
    assert r.status_code == 201, r.text
    preview = r.json()
    transfer_row = next(row for row in preview["rows"] if row["description"] == "TRANSFER")
    r = client.post(
        f"/api/v1/imports/{preview['import_id']}/confirm",
        headers=headers,
        json={"rows": [{"id": transfer_row["id"], "skip": True}]},
    )
    assert r.status_code == 200, r.text

    f = _replay(client, headers)["files"][0]
    assert (f["status"], f["matched"], f["skipped"], f["missing_count"]) == ("ok", 1, 1, 0)


def test_replay_reports_corrupt_file_as_parse_failed(auth_client) -> None:
    client, headers, user_id = auth_client
    account_id = _account(client, headers)
    m1 = _months_back(1)
    _upload(client, headers, account_id, _lloyds_csv([(m1.replace(day=5), "ROW", Decimal("10"), Decimal("10"))]))

    stored = list((Path(settings.upload_dir) / str(user_id)).iterdir())
    assert len(stored) == 1
    stored[0].write_bytes(b"\x00\x01 not a statement \xff")

    replay = _replay(client, headers)
    assert replay["files_failed"] == 1
    f = replay["files"][0]
    assert f["status"] == "parse_failed"
    assert "0 rows" in f["error"]


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

    replay = _replay(client, headers)
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
    assert acct["chain_break_count"] == 1
    b = acct["chain_breaks"][0]
    assert b["prev_as_of"] == m2.replace(day=28).isoformat()
    assert b["as_of"] == m1.replace(day=28).isoformat()
    assert Decimal(b["expected"]) == Decimal("1100.00")
    assert Decimal(b["delta"]) == Decimal("3900.00")


def test_newest_first_statement_records_true_closing_balance(auth_client) -> None:
    """Newest-first exports list the closing day's latest transaction FIRST;
    the recorded snapshot must be that row's balance, not the day's earliest."""
    client, headers, _ = auth_client
    account_id = _account(client, headers)
    m1 = _months_back(1)
    day = m1.replace(day=20)
    _upload(
        client, headers, account_id,
        _lloyds_csv(
            [
                (m1.replace(day=5), "SALARY", Decimal("2000"), Decimal("2000")),
                (day, "LUNCH", Decimal("-10"), Decimal("1990")),
                (day, "DINNER", Decimal("-40"), Decimal("1950")),
            ],
            newest_first=True,
        ),
    )
    snaps = client.get(f"/api/v1/accounts/{account_id}/snapshots", headers=headers).json()
    assert len(snaps) == 1
    assert snaps[0]["as_of"] == day.isoformat()
    assert Decimal(snaps[0]["balance"]) == Decimal("1950")  # after DINNER, the true close


def test_manual_snapshot_cannot_overwrite_statement_attestation(auth_client) -> None:
    client, headers, _ = auth_client
    account_id = _account(client, headers)
    m1 = _months_back(1)
    day = m1.replace(day=5)
    _upload(client, headers, account_id, _lloyds_csv([(day, "ROW", Decimal("10"), Decimal("10"))]))

    r = client.post(
        f"/api/v1/accounts/{account_id}/snapshots", headers=headers,
        json={"as_of": day.isoformat(), "balance": "999"},
    )
    assert r.status_code == 409
    r = client.post(
        f"/api/v1/accounts/{account_id}/snapshots", headers=headers,
        json={"as_of": (day + timedelta(days=1)).isoformat(), "balance": "999"},
    )
    assert r.status_code == 201, r.text


def test_replay_scoped_to_account_and_user(auth_client) -> None:
    client, headers, _ = auth_client
    a1 = _account(client, headers, name="A")
    a2 = _account(client, headers, name="B")
    m1 = _months_back(1)
    _upload(client, headers, a1, _lloyds_csv([(m1.replace(day=5), "ONE", Decimal("10"), Decimal("10"))]))
    _upload(client, headers, a2, _lloyds_csv([(m1.replace(day=6), "TWO", Decimal("20"), Decimal("20"))]))

    r = client.post(f"/api/v1/integrity/replay?account_id={a1}", headers=headers).json()
    assert len(r["files"]) == 1
    assert r["files"][0]["account_id"] == a1

    assert client.post("/api/v1/integrity/replay?account_id=999999", headers=headers).status_code == 404

    r = client.post("/api/v1/auth/signup", json={"email": "other-gt@coffer.dev", "password": "other-pw-1234"})
    other = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get("/api/v1/integrity", headers=other).json()["accounts"] == []
    assert client.post("/api/v1/integrity/replay", headers=other).json()["files"] == []
