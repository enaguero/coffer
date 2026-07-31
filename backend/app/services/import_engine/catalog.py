"""UK bank catalog: which banks the account picker offers, and how each one's
statement downloads parse.

A preset is either a declarative ImportProfileConfig (the normal case) or a
code-adapter key (when parsing needs logic — see adapters/). Presets encode the
export layouts these banks are known to produce; they are best-effort. A preset
that doesn't match a given file raises ProfileParseError inside the resolver,
which falls back to the heuristic sniffer and tells the user — so a stale
preset degrades gracefully instead of failing the import. Users can always
override any preset by saving an account-level ImportProfile, which takes
precedence.

Adding a bank = adding data here. No new classes, no migration.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.account import AccountType
from app.services.import_engine.profile import ImportProfileConfig

_ALL = (
    AccountType.CHECKING,
    AccountType.SAVINGS,
    AccountType.CREDIT_CARD,
)


@dataclass(frozen=True)
class ImportPreset:
    # Which account types this preset covers; None = any of the bank's types.
    account_types: tuple[AccountType, ...] | None
    config: ImportProfileConfig | None = None
    adapter: str | None = None


@dataclass(frozen=True)
class UkBank:
    id: str
    name: str
    account_types: tuple[AccountType, ...] = _ALL
    # Statement formats the bank's site can export, best first (UI hint only —
    # the actual parser is chosen by file extension).
    formats: tuple[str, ...] = ("csv",)
    presets: tuple[ImportPreset, ...] = field(default=())
    notes: str = ""


def _csv(**kwargs) -> ImportProfileConfig:
    return ImportProfileConfig(**kwargs)


# Lloyds Banking Group brands (Lloyds, Halifax, Bank of Scotland) and TSB share
# the same current-account CSV layout.
_LLOYDS_STYLE = _csv(
    date_column="Transaction Date",
    description_columns=["Transaction Description"],
    debit_column="Debit Amount",
    credit_column="Credit Amount",
    balance_column="Balance",
)
# ... and the same credit-card export, which lists charges as positive.
_LLOYDS_STYLE_CARD = _csv(
    date_column="Date",
    description_columns=["Description"],
    amount_column="Amount",
    invert_amount=True,
)
# HSBC group downloads are headerless: date, description, amount.
_HSBC_STYLE = _csv(
    has_header=False,
    date_column=0,
    description_columns=[1],
    amount_column=2,
)
_NATWEST_STYLE = _csv(
    date_column="Date",
    description_columns=["Description"],
    amount_column="Value",
    balance_column="Balance",
)

UK_BANKS: tuple[UkBank, ...] = (
    UkBank(
        id="barclays",
        name="Barclays",
        account_types=(AccountType.CHECKING, AccountType.SAVINGS),
        formats=("csv", "ofx", "qif"),
        presets=(
            ImportPreset(
                account_types=None,
                config=_csv(
                    date_column="Date",
                    description_columns=["Memo"],
                    amount_column="Amount",
                ),
            ),
        ),
        notes="Download from Statements & documents → Export. OFX gives exact dedup ids.",
    ),
    UkBank(
        id="barclaycard",
        name="Barclaycard",
        account_types=(AccountType.CREDIT_CARD,),
        presets=(
            ImportPreset(
                account_types=None,
                config=_csv(
                    date_column="Transaction Date",
                    description_columns=["Merchant Name"],
                    amount_column="Amount",
                    invert_amount=True,
                ),
            ),
        ),
        notes="Statements page → Download CSV. Charges are flipped to negative on import.",
    ),
    UkBank(
        id="hsbc",
        name="HSBC UK",
        formats=("csv", "ofx", "qif"),
        presets=(ImportPreset(account_types=None, config=_HSBC_STYLE),),
        notes="CSV downloads have no header row — date, description, amount.",
    ),
    UkBank(
        id="first-direct",
        name="first direct",
        formats=("csv", "ofx", "qif"),
        presets=(ImportPreset(account_types=None, config=_HSBC_STYLE),),
    ),
    UkBank(
        id="lloyds",
        name="Lloyds Bank",
        formats=("csv", "ofx", "qif"),
        presets=(
            ImportPreset(account_types=(AccountType.CREDIT_CARD,), config=_LLOYDS_STYLE_CARD),
            ImportPreset(account_types=None, config=_LLOYDS_STYLE),
        ),
    ),
    UkBank(
        id="halifax",
        name="Halifax",
        formats=("csv", "ofx", "qif"),
        presets=(
            ImportPreset(account_types=(AccountType.CREDIT_CARD,), config=_LLOYDS_STYLE_CARD),
            ImportPreset(account_types=None, config=_LLOYDS_STYLE),
        ),
    ),
    UkBank(
        id="bank-of-scotland",
        name="Bank of Scotland",
        formats=("csv", "ofx", "qif"),
        presets=(
            ImportPreset(account_types=(AccountType.CREDIT_CARD,), config=_LLOYDS_STYLE_CARD),
            ImportPreset(account_types=None, config=_LLOYDS_STYLE),
        ),
    ),
    UkBank(
        id="tsb",
        name="TSB",
        presets=(ImportPreset(account_types=None, config=_LLOYDS_STYLE),),
    ),
    UkBank(
        id="natwest",
        name="NatWest",
        presets=(ImportPreset(account_types=None, config=_NATWEST_STYLE),),
        notes="Use 'Download transactions' → CSV.",
    ),
    UkBank(
        id="rbs",
        name="Royal Bank of Scotland",
        presets=(ImportPreset(account_types=None, config=_NATWEST_STYLE),),
    ),
    UkBank(
        id="santander",
        name="Santander UK",
        presets=(
            ImportPreset(
                account_types=None,
                config=_csv(
                    date_column="Date",
                    description_columns=["Description"],
                    amount_column="Amount",
                    balance_column="Balance",
                ),
            ),
        ),
        notes="Export as CSV (not the default .txt) from the transactions page.",
    ),
    UkBank(
        id="nationwide",
        name="Nationwide",
        account_types=(AccountType.CHECKING, AccountType.SAVINGS),
        presets=(
            ImportPreset(
                account_types=None,
                config=_csv(
                    date_column="Date",
                    description_columns=["Description"],
                    debit_column="Paid out",
                    credit_column="Paid in",
                    balance_column="Balance",
                ),
            ),
        ),
        notes="Amounts arrive with £ signs; they're stripped on import.",
    ),
    UkBank(
        id="monzo",
        name="Monzo",
        account_types=(AccountType.CHECKING, AccountType.SAVINGS),
        presets=(
            ImportPreset(
                account_types=None,
                config=_csv(
                    date_column="Date",
                    description_columns=["Name"],
                    amount_column="Amount",
                    external_id_column="Transaction ID",
                ),
            ),
        ),
        notes="Export via app: Settings → Export bank statement. Transaction IDs give exact dedup.",
    ),
    UkBank(
        id="starling",
        name="Starling Bank",
        account_types=(AccountType.CHECKING, AccountType.SAVINGS),
        presets=(
            ImportPreset(
                account_types=None,
                config=_csv(
                    date_column="Date",
                    description_columns=["Counter Party", "Reference"],
                    amount_column="Amount (GBP)",
                    balance_column="Balance (GBP)",
                ),
            ),
        ),
    ),
    UkBank(
        id="revolut",
        name="Revolut",
        account_types=(AccountType.CHECKING, AccountType.SAVINGS),
        presets=(ImportPreset(account_types=None, adapter="revolut_csv"),),
        notes="Pending and reverted rows are skipped; fees are folded into amounts.",
    ),
    UkBank(
        id="metro-bank",
        name="Metro Bank",
        presets=(
            ImportPreset(
                account_types=None,
                config=_csv(
                    date_column="Date",
                    description_columns=["Transaction"],
                    debit_column="Money Out",
                    credit_column="Money In",
                    balance_column="Balance",
                ),
            ),
        ),
    ),
    UkBank(
        id="chase",
        name="Chase UK",
        account_types=(AccountType.CHECKING, AccountType.SAVINGS),
        presets=(
            ImportPreset(
                account_types=None,
                config=_csv(
                    date_column="Date",
                    description_columns=["Description"],
                    amount_column="Amount",
                ),
            ),
        ),
    ),
    UkBank(
        id="amex",
        name="American Express UK",
        account_types=(AccountType.CREDIT_CARD,),
        presets=(
            ImportPreset(
                account_types=None,
                config=_csv(
                    date_column="Date",
                    description_columns=["Description"],
                    amount_column="Amount",
                    invert_amount=True,
                ),
            ),
        ),
        notes="Statement CSVs list charges as positive; they're flipped on import.",
    ),
)

_BY_ID: dict[str, UkBank] = {b.id: b for b in UK_BANKS}


def get_bank(bank_id: str | None) -> UkBank | None:
    if not bank_id:
        return None
    return _BY_ID.get(bank_id)


def find_preset(bank_id: str | None, account_type: AccountType) -> ImportPreset | None:
    bank = get_bank(bank_id)
    if bank is None:
        return None
    for preset in bank.presets:
        if preset.account_types is None or account_type in preset.account_types:
            return preset
    return None
