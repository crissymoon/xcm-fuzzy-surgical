"""Editor preferences that outlive a single file editor.

The search view and the file browser open a new full editor for every file.
One shared object keeps choices such as the line number style stable while the
session runs, so toggling it once applies to the next file too.
"""

from __future__ import annotations

LINE_NUMBER_MODES = ("absolute", "hybrid")
DEFAULT_LINE_NUMBERS = "absolute"


def normalize_line_numbers(value: object) -> str:
    """Return a supported line number mode, falling back to the default."""
    text = str(value or "").strip().lower()
    if text in LINE_NUMBER_MODES:
        return text
    return DEFAULT_LINE_NUMBERS


class EditorPreferences:
    """Small shared state for choices an editor keeps between files."""

    def __init__(self, line_numbers: str = DEFAULT_LINE_NUMBERS) -> None:
        self.line_numbers = normalize_line_numbers(line_numbers)

    @property
    def hybrid_line_numbers(self) -> bool:
        return self.line_numbers == "hybrid"

    def toggle_line_numbers(self) -> str:
        """Flip between absolute and hybrid numbers and return the new mode."""
        self.line_numbers = (
            "absolute" if self.hybrid_line_numbers else "hybrid"
        )
        return self.line_numbers

    def describe_line_numbers(self) -> str:
        if self.hybrid_line_numbers:
            return (
                "Line numbers: hybrid. The current line is absolute, "
                "the rest count from it."
            )
        return "Line numbers: absolute."
