"""Report which language servers and linters this machine can actually run.

The report is read only: nothing is installed and no child process is started.
It exists because "completion does nothing" is nearly always a missing server,
and the answer should be one command away.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from fuzzy_surgical.lsp.commands import describe
from fuzzy_surgical.lsp.linters import available_linters
from fuzzy_surgical.lsp.registry import available_specs


@dataclass(frozen=True)
class ToolReport:
    """One row of the report."""

    kind: str
    name: str
    accepts: str
    command: Optional[Tuple[str, ...]]
    hint: str

    @property
    def ready(self) -> bool:
        return self.command is not None

    def command_text(self) -> str:
        return describe(self.command) if self.command else "-"

    def line(self, name_width: int = 22, accepts_width: int = 22) -> str:
        state = "ready" if self.ready else "missing"
        text = (
            f"{self.kind:<8} {self.name:<{name_width}} "
            f"{self.accepts:<{accepts_width}} {state}"
        )
        if not self.ready and self.hint:
            text += f"  ({self.hint})"
        return text


def inspect_tools() -> List[ToolReport]:
    """Describe every built-in language server and linter."""
    reports: List[ToolReport] = []
    for spec in available_specs():
        reports.append(
            ToolReport(
                kind="lsp",
                name=spec.name,
                accepts=", ".join(spec.extensions) or spec.language_id,
                command=spec.available_command(),
                hint=spec.hint,
            )
        )
    for linter in available_linters():
        reports.append(
            ToolReport(
                kind="linter",
                name=linter.name,
                accepts=", ".join(linter.extensions),
                command=linter.available_command(),
                hint=linter.hint,
            )
        )
    return reports


def format_report(reports: Sequence[ToolReport]) -> str:
    """Human readable report, one tool per line."""
    if not reports:
        return "No tools are registered."
    name_width = max(8, min(26, max(len(report.name) for report in reports)))
    accepts_width = max(12, min(30, max(len(report.accepts) for report in reports)))
    header = (
        f"{'kind':<8} {'tool':<{name_width}} {'handles':<{accepts_width}} state"
    )
    lines = [header, "-" * 76]
    for report in reports:
        lines.append(report.line(name_width, accepts_width))
    ready = sum(1 for report in reports if report.ready)
    lines.append("")
    lines.append(f"{ready} of {len(reports)} tools ready.")
    missing = [report for report in reports if not report.ready and report.hint]
    if missing:
        lines.append("")
        lines.append("To install the missing ones:")
        seen = set()
        for report in missing:
            if report.hint in seen:
                continue
            seen.add(report.hint)
            lines.append(f"  {report.hint}")
    return "\n".join(lines)
