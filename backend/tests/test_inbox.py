"""Statement inbox: drop, list, preview-from-inbox, discard, and safety."""

from io import BytesIO
from pathlib import Path

import pytest

from app.core.config import settings

CSV = b"Date,Description,Amount\n01/07/2026,COFFEE,-3.50\n02/07/2026,SALARY,2500\n"


@pytest.fixture(autouse=True)
def _isolated_inbox(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "inbox_dir", str(tmp_path / "inbox"))
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))


def _account(client, headers) -> int:
    r = client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": "Current", "type": "checking", "currency": "GBP"},
    )
    return r.json()["id"]


def _drop(client, headers, name="stmt.csv", content=CSV):
    return client.post(
        "/api/v1/imports/inbox",
        headers=headers,
        files={"file": (name, BytesIO(content), "text/csv")},
    )


def test_drop_list_preview_flow(auth_client) -> None:
    client, headers, user_id = auth_client
    account_id = _account(client, headers)

    r = _drop(client, headers)
    assert r.status_code == 201, r.text
    assert r.json()["filename"] == "stmt.csv"

    files = client.get("/api/v1/imports/inbox", headers=headers).json()
    assert [f["filename"] for f in files] == ["stmt.csv"]

    r = client.post(
        "/api/v1/imports/inbox/stmt.csv/preview",
        headers=headers,
        json={"account_id": account_id},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["rows"]) == 2

    # File moved out of pending into processed/ — inbox is empty again.
    assert client.get("/api/v1/imports/inbox", headers=headers).json() == []
    processed = Path(settings.inbox_dir) / str(user_id) / "processed" / "stmt.csv"
    assert processed.exists()

    # The preview is a normal one — confirm works.
    confirm = client.post(
        f"/api/v1/imports/{body['import_id']}/confirm",
        headers=headers,
        json={"rows": [{"id": r["id"], "skip": False, "category_id": None} for r in body["rows"]]},
    )
    assert confirm.status_code == 200
    assert confirm.json()["rows_imported"] == 2


def test_duplicate_names_get_suffixed(auth_client) -> None:
    client, headers, _ = auth_client
    assert _drop(client, headers).json()["filename"] == "stmt.csv"
    assert _drop(client, headers).json()["filename"] == "stmt-1.csv"
    assert _drop(client, headers).json()["filename"] == "stmt-2.csv"


def test_watch_folder_files_appear_without_api(auth_client) -> None:
    # A file dropped straight into the directory (Syncthing/NAS) is listed.
    client, headers, user_id = auth_client
    pending = Path(settings.inbox_dir) / str(user_id) / "pending"
    pending.mkdir(parents=True)
    (pending / "from-nas.csv").write_bytes(CSV)
    (pending / "ignored.txt").write_text("not a statement")

    files = client.get("/api/v1/imports/inbox", headers=headers).json()
    assert [f["filename"] for f in files] == ["from-nas.csv"]  # unsupported types hidden


def test_discard_and_traversal_rejected(auth_client) -> None:
    client, headers, _ = auth_client
    _drop(client, headers)
    assert client.delete("/api/v1/imports/inbox/stmt.csv", headers=headers).status_code == 204
    assert client.get("/api/v1/imports/inbox", headers=headers).json() == []

    r = client.delete("/api/v1/imports/inbox/..%2F..%2Fetc", headers=headers)
    assert r.status_code in (400, 404)
    account_id = _account(client, headers)
    r = client.post(
        "/api/v1/imports/inbox/%2e%2e%2fsecret/preview",
        headers=headers,
        json={"account_id": account_id},
    )
    assert r.status_code in (400, 404)


def test_inbox_is_per_user(auth_client) -> None:
    client, headers, _ = auth_client
    _drop(client, headers)
    r = client.post("/api/v1/auth/signup", json={"email": "inbox2@coffer.dev", "password": "inbox-password-2"})
    other_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    client.cookies.clear()
    assert client.get("/api/v1/imports/inbox", headers=other_headers).json() == []


def test_oversize_and_bad_type_rejected(auth_client) -> None:
    client, headers, _ = auth_client
    r = _drop(client, headers, name="big.csv", content=b"x" * (10 * 1024 * 1024 + 1))
    assert r.status_code == 413
    r = _drop(client, headers, name="evil.exe", content=b"MZ")
    assert r.status_code == 400


def test_processed_archive_never_overwritten(auth_client) -> None:
    client, headers, user_id = auth_client
    account_id = _account(client, headers)

    _drop(client, headers)  # stmt.csv
    r = client.post("/api/v1/imports/inbox/stmt.csv/preview", headers=headers, json={"account_id": account_id})
    assert r.status_code == 200
    _drop(client, headers, content=CSV.replace(b"COFFEE", b"TEA-42"))  # different stmt.csv
    r = client.post("/api/v1/imports/inbox/stmt.csv/preview", headers=headers, json={"account_id": account_id})
    assert r.status_code == 200

    processed = Path(settings.inbox_dir) / str(user_id) / "processed"
    names = sorted(p.name for p in processed.iterdir())
    assert names == ["stmt-1.csv", "stmt.csv"]  # both archives kept


def test_symlinks_hidden_from_listing(auth_client) -> None:
    client, headers, user_id = auth_client
    pending = Path(settings.inbox_dir) / str(user_id) / "pending"
    pending.mkdir(parents=True)
    outside = Path(settings.inbox_dir) / "outside.csv"
    outside.write_bytes(CSV)
    (pending / "sneaky.csv").symlink_to(outside)

    assert client.get("/api/v1/imports/inbox", headers=headers).json() == []
