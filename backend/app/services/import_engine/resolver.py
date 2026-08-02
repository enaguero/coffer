"""Chooses how to parse an uploaded statement and runs the parse.

Priority for CSVs: saved account profile → catalog preset/adapter for the
account's (bank, account type) → heuristic sniffer. A profile or preset that
doesn't fit the file degrades to the next layer and records a warning instead
of failing the upload. OFX/QIF are self-describing and short-circuit on
extension. When the heuristic wins, its detected layout is returned as
`inferred_config` so the UI can offer to save it as the account's profile.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.models.account import Account
from app.models.statement import StatementFormat
from app.services.csv_parser import ParsedRow, parse_csv_detailed
from app.services.import_engine.adapters import get_adapter
from app.services.import_engine.catalog import find_preset, get_bank
from app.services.import_engine.formats import parse_ofx, parse_qif
from app.services.import_engine.formats.ofx import extract_ofx_ledger_balance
from app.services.import_engine.profile import (
    ImportProfileConfig,
    ProfileParseError,
    parse_with_profile,
)
from app.services.pdf_parser import parse_pdf

log = logging.getLogger(__name__)


@dataclass
class ParseOutcome:
    rows: list[ParsedRow]
    skipped: int
    format: StatementFormat
    # "ofx" | "qif" | "profile" | "preset:<bank_id>" | "adapter:<key>" | "heuristic"
    source: str
    inferred_config: ImportProfileConfig | None = None
    warnings: list[str] = field(default_factory=list)
    # The statement's attested closing balance, when the file carries one
    # (running-balance CSV column or OFX <LEDGERBAL>). Feeds BalanceSnapshot.
    closing_balance: Decimal | None = None
    closing_balance_date: date | None = None


def _attach_closing_balance(outcome: ParseOutcome) -> ParseOutcome:
    """Latest-dated row that carries a running balance wins. Among same-day
    rows, file order decides which one is chronologically last: newest-first
    exports (the common UK ordering) list the closing day's latest transaction
    FIRST, so taking the last row in file order there would record a balance
    that excludes the day's later activity."""
    dated = [r for r in outcome.rows if r.balance is not None]
    if not dated:
        return outcome
    newest_first = len(outcome.rows) > 1 and outcome.rows[0].posted_on > outcome.rows[-1].posted_on
    closing_day = max(r.posted_on for r in dated)
    same_day = [r for r in dated if r.posted_on == closing_day]
    best = same_day[0] if newest_first else same_day[-1]
    outcome.closing_balance = best.balance
    outcome.closing_balance_date = best.posted_on
    return outcome


def resolve_and_parse(
    content: bytes,
    suffix: str,
    account: Account,
    profile_config: ImportProfileConfig | None,
) -> ParseOutcome:
    if suffix in {".ofx", ".qfx"}:
        rows, skipped = parse_ofx(content)
        outcome = ParseOutcome(rows=rows, skipped=skipped, format=StatementFormat.OFX, source="ofx")
        ledger = extract_ofx_ledger_balance(content)
        if ledger is not None:
            outcome.closing_balance_date, outcome.closing_balance = ledger[0], ledger[1]
        return outcome

    if suffix == ".qif":
        rows, skipped = parse_qif(content)
        return ParseOutcome(rows=rows, skipped=skipped, format=StatementFormat.QIF, source="qif")

    if suffix == ".pdf":
        rows, skipped = parse_pdf(content)
        return ParseOutcome(rows=rows, skipped=skipped, format=StatementFormat.PDF, source="heuristic")

    return _attach_closing_balance(_parse_csv(content, account, profile_config))


def _parse_csv(
    content: bytes,
    account: Account,
    profile_config: ImportProfileConfig | None,
) -> ParseOutcome:
    warnings: list[str] = []

    if profile_config is not None:
        try:
            rows, skipped = parse_with_profile(content, profile_config)
            return ParseOutcome(rows=rows, skipped=skipped, format=StatementFormat.CSV, source="profile")
        except ProfileParseError as exc:
            log.info("account %s: saved profile did not match upload: %s", account.id, exc)
            warnings.append(
                "This account's saved import profile didn't match the file; "
                "trying the bank preset / automatic detection instead."
            )

    preset = find_preset(account.bank_id, account.type)
    if preset is not None:
        bank = get_bank(account.bank_id)
        bank_name = bank.name if bank else account.bank_id
        adapter = get_adapter(preset.adapter)
        if adapter is not None:
            try:
                rows, skipped = adapter(content)
                if rows:
                    return ParseOutcome(
                        rows=rows,
                        skipped=skipped,
                        format=StatementFormat.CSV,
                        source=f"adapter:{preset.adapter}",
                        warnings=warnings,
                    )
            except Exception:  # noqa: BLE001 — a broken adapter must not fail the upload
                log.exception("adapter %s failed for account %s", preset.adapter, account.id)
            warnings.append(f"The {bank_name} importer couldn't read this file; falling back to automatic detection.")
        elif preset.config is not None:
            try:
                rows, skipped = parse_with_profile(content, preset.config)
                return ParseOutcome(
                    rows=rows,
                    skipped=skipped,
                    format=StatementFormat.CSV,
                    source=f"preset:{account.bank_id}",
                    warnings=warnings,
                )
            except ProfileParseError as exc:
                log.info("account %s: preset %s did not match: %s", account.id, account.bank_id, exc)
                warnings.append(
                    f"The file doesn't look like a standard {bank_name} export; falling back to automatic detection."
                )

    rows, skipped, layout = parse_csv_detailed(content)
    inferred = ImportProfileConfig.from_detected_layout(layout) if layout and rows else None
    return ParseOutcome(
        rows=rows,
        skipped=skipped,
        format=StatementFormat.CSV,
        source="heuristic",
        inferred_config=inferred,
        warnings=warnings,
    )
