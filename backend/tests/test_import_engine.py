"""Unit tests for the import engine: format parsers, profile parsing, the UK
catalog, code adapters, and the resolver's fallback chain. No DB required —
the resolver only reads plain attributes off Account."""

from datetime import date
from decimal import Decimal

from app.models.account import Account, AccountType
from app.services.import_engine.adapters import get_adapter
from app.services.import_engine.catalog import UK_BANKS, find_preset, get_bank
from app.services.import_engine.formats import parse_ofx, parse_qif
from app.services.import_engine.profile import (
    ImportProfileConfig,
    ProfileParseError,
    parse_with_profile,
)
from app.services.import_engine.resolver import resolve_and_parse

# ---- Sample statements --------------------------------------------------------

OFX_SGML = b"""OFXHEADER:100
DATA:OFXSGML
VERSION:102

<OFX>
<BANKMSGSRSV1><STMTTRNRS><STMTRS>
<BANKTRANLIST>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20260301120000[+0:GMT]
<TRNAMT>-4.50
<FITID>2026030101
<NAME>PRET A MANGER
<MEMO>LONDON SE1
</STMTTRN>
<STMTTRN>
<TRNTYPE>CREDIT
<DTPOSTED>20260302
<TRNAMT>2500.00
<FITID>2026030201
<NAME>ACME LTD SALARY
</STMTTRN>
</BANKTRANLIST>
</STMTRS></STMTTRNRS></BANKMSGSRSV1>
</OFX>
"""

QIF = b"""!Type:Bank
D01/03/2026
T-4.50
PPRET A MANGER
MLONDON SE1
^
D02/03/2026
T2,500.00
PACME LTD SALARY
^
"""

LLOYDS_CSV = (
    b"Transaction Date,Transaction Type,Sort Code,Account Number,"
    b"Transaction Description,Debit Amount,Credit Amount,Balance\n"
    b"28/02/2026,DEB,'11-22-33,12345678,TESCO STORES 3297,23.50,,1200.00\n"
    b"27/02/2026,FPI,'11-22-33,12345678,ACME LTD SALARY,,2500.00,1223.50\n"
)

MONZO_CSV = (
    b"Transaction ID,Date,Time,Type,Name,Emoji,Category,Amount,Currency,"
    b"Local amount,Local currency,Notes and #tags,Address,Receipt,Description,"
    b"Category split,Money Out,Money In\n"
    b"tx_0001,01/03/2026,10:23:45,Card payment,Pret A Manger,,Eating out,-4.50,GBP,"
    b"-4.50,GBP,,London,,PRET A MANGER LONDON,,-4.50,\n"
    b"tx_0002,02/03/2026,09:00:00,Faster payment,ACME LTD,,Income,2500.00,GBP,"
    b"2500.00,GBP,Salary,,,ACME LTD SALARY,,,2500.00\n"
)

REVOLUT_CSV = (
    b"Type,Product,Started Date,Completed Date,Description,Amount,Fee,Currency,State,Balance\n"
    b"CARD_PAYMENT,Current,2026-03-01 10:23:45,2026-03-02 08:00:00,Pret A Manger,-4.50,0.00,GBP,COMPLETED,100.00\n"
    b"TRANSFER,Current,2026-03-03 10:00:00,,Pending transfer,-10.00,0.00,GBP,PENDING,90.00\n"
    b"EXCHANGE,Current,2026-03-04 12:00:00,2026-03-04 12:00:01,Exchanged to USD,-50.00,0.50,GBP,COMPLETED,40.00\n"
)


def _account(bank_id: str | None = None, type_: AccountType = AccountType.CHECKING) -> Account:
    return Account(user_id=1, name="Test", type=type_, currency="GBP", bank_id=bank_id)


# ---- Format parsers -----------------------------------------------------------


def test_parse_ofx_sgml() -> None:
    rows, skipped = parse_ofx(OFX_SGML)
    assert skipped == 0
    assert [r.amount for r in rows] == [Decimal("-4.50"), Decimal("2500.00")]
    assert rows[0].posted_on == date(2026, 3, 1)
    assert rows[0].description == "PRET A MANGER — LONDON SE1"
    # FITID is the bank's own id — used verbatim for exact dedup.
    assert rows[0].external_id == "2026030101"
    assert rows[1].posted_on == date(2026, 3, 2)


def test_parse_ofx_xml_flavor() -> None:
    xml = OFX_SGML.replace(b"<TRNAMT>-4.50", b"<TRNAMT>-4.50</TRNAMT>").replace(
        b"<NAME>PRET A MANGER", b"<NAME>PRET A MANGER</NAME>"
    )
    rows, _ = parse_ofx(xml)
    assert rows[0].amount == Decimal("-4.50")
    assert rows[0].description.startswith("PRET A MANGER")


def test_parse_qif() -> None:
    rows, skipped = parse_qif(QIF)
    assert skipped == 0
    assert [r.amount for r in rows] == [Decimal("-4.50"), Decimal("2500.00")]
    assert rows[0].posted_on == date(2026, 3, 1)  # day-first
    assert rows[0].description == "PRET A MANGER — LONDON SE1"


# ---- Profile parsing ----------------------------------------------------------


def test_profile_debit_credit_columns() -> None:
    config = ImportProfileConfig(
        date_column="Transaction Date",
        description_columns=["Transaction Description"],
        debit_column="Debit Amount",
        credit_column="Credit Amount",
    )
    rows, skipped = parse_with_profile(LLOYDS_CSV, config)
    assert skipped == 0
    assert rows[0].amount == Decimal("-23.50")
    assert rows[0].posted_on == date(2026, 2, 28)
    assert rows[1].amount == Decimal("2500.00")


def test_profile_invert_amount_for_credit_cards() -> None:
    csv = b"Date,Description,Amount\n01/03/2026,SAINSBURYS,12.40\n05/03/2026,PAYMENT RECEIVED,-100.00\n"
    config = ImportProfileConfig(
        date_column="Date",
        description_columns=["Description"],
        amount_column="Amount",
        invert_amount=True,
    )
    rows, _ = parse_with_profile(csv, config)
    assert rows[0].amount == Decimal("-12.40")  # charge becomes outflow
    assert rows[1].amount == Decimal("100.00")  # repayment becomes inflow


def test_profile_headerless_by_index() -> None:
    csv = b"01/03/2026,PRET A MANGER,-4.50\n02/03/2026,ACME LTD SALARY,2500.00\n"
    config = ImportProfileConfig(has_header=False, date_column=0, description_columns=[1], amount_column=2)
    rows, _ = parse_with_profile(csv, config)
    assert len(rows) == 2
    assert rows[0].description == "PRET A MANGER"


def test_profile_external_id_column() -> None:
    config = ImportProfileConfig(
        date_column="Date",
        description_columns=["Name"],
        amount_column="Amount",
        external_id_column="Transaction ID",
    )
    rows, _ = parse_with_profile(MONZO_CSV, config)
    assert rows[0].external_id == "tx_0001"


def test_profile_mismatch_raises() -> None:
    config = ImportProfileConfig(date_column="No Such Column", description_columns=["Nope"], amount_column="Missing")
    try:
        parse_with_profile(LLOYDS_CSV, config)
    except ProfileParseError:
        pass
    else:
        raise AssertionError("expected ProfileParseError")


# ---- Catalog ------------------------------------------------------------------


def test_catalog_is_internally_consistent() -> None:
    seen_ids: set[str] = set()
    for bank in UK_BANKS:
        assert bank.id not in seen_ids
        seen_ids.add(bank.id)
        assert bank.account_types, bank.id
        for preset in bank.presets:
            # Every preset must be parseable: a validated config or a registered adapter.
            assert (preset.config is not None) != (preset.adapter is not None), bank.id
            if preset.adapter is not None:
                assert get_adapter(preset.adapter) is not None, bank.id
            if preset.account_types is not None:
                assert set(preset.account_types) <= set(bank.account_types), bank.id


def test_find_preset_prefers_account_type_specific() -> None:
    card = find_preset("lloyds", AccountType.CREDIT_CARD)
    current = find_preset("lloyds", AccountType.CHECKING)
    assert card is not None and card.config is not None and card.config.invert_amount
    assert current is not None and current.config is not None and not current.config.invert_amount
    assert find_preset("unknown-bank", AccountType.CHECKING) is None
    assert get_bank("monzo") is not None


# ---- Adapters -----------------------------------------------------------------


def test_revolut_adapter_filters_and_folds_fee() -> None:
    adapter = get_adapter("revolut_csv")
    assert adapter is not None
    rows, skipped = adapter(REVOLUT_CSV)
    assert skipped == 1  # pending row dropped
    assert [r.amount for r in rows] == [Decimal("-4.50"), Decimal("-50.50")]
    assert rows[0].posted_on == date(2026, 3, 2)  # completed date wins


# ---- Resolver -----------------------------------------------------------------


def test_resolver_uses_bank_preset() -> None:
    outcome = resolve_and_parse(LLOYDS_CSV, ".csv", _account("lloyds"), None)
    assert outcome.source == "preset:lloyds"
    assert len(outcome.rows) == 2
    assert not outcome.warnings


def test_resolver_prefers_saved_profile_over_preset() -> None:
    profile = ImportProfileConfig(
        date_column="Transaction Date",
        description_columns=["Transaction Type"],  # deliberately different mapping
        debit_column="Debit Amount",
        credit_column="Credit Amount",
    )
    outcome = resolve_and_parse(LLOYDS_CSV, ".csv", _account("lloyds"), profile)
    assert outcome.source == "profile"
    assert outcome.rows[0].description == "DEB"


def test_resolver_falls_back_to_heuristic_with_warning_on_preset_mismatch() -> None:
    # A Monzo file uploaded against a Lloyds-linked account: preset won't match.
    outcome = resolve_and_parse(MONZO_CSV, ".csv", _account("lloyds"), None)
    assert outcome.source == "heuristic"
    assert outcome.warnings
    assert len(outcome.rows) == 2


def test_resolver_adapter_via_catalog() -> None:
    outcome = resolve_and_parse(REVOLUT_CSV, ".csv", _account("revolut"), None)
    assert outcome.source == "adapter:revolut_csv"
    assert len(outcome.rows) == 2


def test_resolver_infers_saveable_profile_from_heuristic() -> None:
    csv = b"Date,Description,Amount\n2026-03-01,Coffee,-4.50\n"
    outcome = resolve_and_parse(csv, ".csv", _account(None), None)
    assert outcome.source == "heuristic"
    assert outcome.inferred_config is not None
    # Round-trip: the inferred profile must parse the same file identically.
    rows, _ = parse_with_profile(csv, outcome.inferred_config)
    assert [(r.posted_on, r.amount) for r in rows] == [(r.posted_on, r.amount) for r in outcome.rows]


def test_resolver_ofx_and_qif_by_extension() -> None:
    assert resolve_and_parse(OFX_SGML, ".ofx", _account(None), None).source == "ofx"
    assert resolve_and_parse(OFX_SGML, ".qfx", _account(None), None).source == "ofx"
    assert resolve_and_parse(QIF, ".qif", _account(None), None).source == "qif"
