"""Diagnostic value object shared by language servers and linters."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, List

SEVERITY_BY_LSP = {1: "error", 2: "warning", 3: "info", 4: "hint"}
SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2, "hint": 3}


@dataclass(frozen=True)
class Diagnostic:
    """One problem reported for a file."""

    path: str
    line: int
    column: int
    message: str
    severity: str = "error"
    source: str = "linter"
    code: str = ""

    def sort_key(self) -> tuple:
        return (
            os.path.abspath(self.path),
            max(1, self.line),
            max(0, self.column),
            SEVERITY_RANK.get(self.severity, 9),
            self.message,
        )


def sorted_diagnostics(items: Iterable[Diagnostic]) -> List[Diagnostic]:
    return sorted(items, key=lambda item: item.sort_key())


def count_by_severity(items: Iterable[Diagnostic]) -> dict:
    counts = {"error": 0, "warning": 0, "info": 0, "hint": 0}
    for item in items:
        counts[item.severity] = counts.get(item.severity, 0) + 1
    return counts
