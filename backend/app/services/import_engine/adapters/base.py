"""Code-adapter registry.

Most banks are covered declaratively by an ImportProfileConfig preset in
catalog.py — reach for a code adapter only when parsing needs real logic
(row filtering, computed amounts, PDF layouts). Adapters are plain functions
`bytes -> (rows, skipped)` registered under a string key that catalog presets
reference.
"""

from __future__ import annotations

from collections.abc import Callable

from app.services.csv_parser import ParsedRow

AdapterFn = Callable[[bytes], tuple[list[ParsedRow], int]]

_ADAPTERS: dict[str, AdapterFn] = {}


def register_adapter(key: str) -> Callable[[AdapterFn], AdapterFn]:
    def deco(fn: AdapterFn) -> AdapterFn:
        if key in _ADAPTERS:
            raise ValueError(f"Duplicate import adapter key: {key}")
        _ADAPTERS[key] = fn
        return fn

    return deco


def get_adapter(key: str | None) -> AdapterFn | None:
    if not key:
        return None
    return _ADAPTERS.get(key)
