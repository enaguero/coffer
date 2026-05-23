"""Rule-based category assignment.

Rules are user-scoped, ordered by (priority asc, id asc). The first rule whose
lower-cased pattern is a substring of the lower-cased description wins.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CompiledRule:
    pattern_lower: str
    category_id: int
    priority: int
    rule_id: int


def compile_rules(rules: Iterable) -> list[CompiledRule]:
    """Sort + lowercase rules once so per-row matching is cheap."""
    compiled = [
        CompiledRule(
            pattern_lower=r.pattern.lower(),
            category_id=r.category_id,
            priority=r.priority,
            rule_id=r.id,
        )
        for r in rules
    ]
    compiled.sort(key=lambda r: (r.priority, r.rule_id))
    return compiled


def match_category(description: str, rules: list[CompiledRule]) -> int | None:
    if not rules:
        return None
    lowered = description.lower()
    for rule in rules:
        if rule.pattern_lower in lowered:
            return rule.category_id
    return None
