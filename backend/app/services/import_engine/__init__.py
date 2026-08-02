"""Statement import engine.

Layered strategy for turning a downloaded bank statement into ParsedRows:

1. Self-describing formats (OFX/QFX, QIF) parse with zero configuration.
2. A saved per-account ImportProfile parses CSVs deterministically.
3. The UK bank catalog supplies preset profiles / code adapters keyed by
   (bank_id, account_type) for accounts linked to a known bank.
4. The heuristic sniffer (services/csv_parser.py, services/pdf_parser.py) is
   the fallback — and its detected layout is offered back to the user to save
   as the account's profile.

`resolve_and_parse` in resolver.py is the single entry point.
"""

from app.services.import_engine.profiles import load_profile_config
from app.services.import_engine.resolver import ParseOutcome, resolve_and_parse

__all__ = ["ParseOutcome", "load_profile_config", "resolve_and_parse"]
