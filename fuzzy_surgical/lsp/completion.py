"""Completion item model, prefix detection and LSP result normalisation."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

MAX_COMPLETIONS = 200
IDENTIFIER_TAIL = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*$")
SNIPPET_PLACEHOLDER = re.compile(r"\$\{\d+:([^}]*)\}|\$\{\d+\}|\$\d+")
SNIPPET_CHOICE = re.compile(r"\$\{\d+\|([^}]*)\|\}")

KIND_NAMES: Dict[int, str] = {
    1: "text",
    2: "method",
    3: "function",
    4: "constructor",
    5: "field",
    6: "variable",
    7: "class",
    8: "interface",
    9: "module",
    10: "property",
    11: "unit",
    12: "value",
    13: "enum",
    14: "keyword",
    15: "snippet",
    16: "color",
    17: "file",
    18: "reference",
    19: "folder",
    20: "enum member",
    21: "constant",
    22: "struct",
    23: "event",
    24: "operator",
    25: "type parameter",
}


@dataclass(frozen=True)
class CompletionItem:
    """One completion entry ready for the popup."""

    label: str
    insert_text: str
    kind: str = "text"
    detail: str = ""
    documentation: str = ""
    sort_text: str = ""

    def matches(self, prefix: str) -> bool:
        if not prefix:
            return True
        return self.label.lstrip().lower().startswith(prefix.lstrip().lower())


def completion_prefix(line: str, index: int) -> Tuple[int, str]:
    """Return the start and text of the identifier before the cursor."""
    index = max(0, min(index, len(line)))
    match = IDENTIFIER_TAIL.search(line[:index])
    if match is None:
        return index, ""
    return match.start(), match.group(0)


def _strip_snippet(text: str) -> str:
    text = SNIPPET_CHOICE.sub(lambda match: match.group(1).split("|")[0], text)
    text = SNIPPET_PLACEHOLDER.sub(lambda match: match.group(1) or "", text)
    return text.replace("\\$", "$")


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        text = value.get("value")
        return text if isinstance(text, str) else ""
    return ""


def _one_item(raw: Dict[str, Any]) -> Optional[CompletionItem]:
    label = raw.get("label")
    if not isinstance(label, str):
        return None
    # Some servers (clangd) pad labels, which breaks prefix filtering.
    label = label.strip()
    if not label:
        return None
    insert = raw.get("insertText")
    if not isinstance(insert, str) or not insert:
        edit = raw.get("textEdit")
        if isinstance(edit, dict):
            insert = edit.get("newText") if isinstance(edit.get("newText"), str) else ""
        else:
            insert = label
    if int(raw.get("insertTextFormat") or 1) == 2:
        insert = _strip_snippet(insert)
    kind_value = raw.get("kind")
    try:
        kind_number = int(kind_value)
    except (TypeError, ValueError):
        kind_number = 0
    return CompletionItem(
        label=label,
        insert_text=insert or label,
        kind=KIND_NAMES.get(kind_number, "text"),
        detail=_as_text(raw.get("detail")),
        documentation=_as_text(raw.get("documentation")),
        sort_text=_as_text(raw.get("sortText")),
    )


def normalize_completions(payload: Any, limit: int = MAX_COMPLETIONS) -> List[CompletionItem]:
    """Turn a completion response into a bounded list of items."""
    if isinstance(payload, dict):
        raw_items = payload.get("items")
    else:
        raw_items = payload
    if not isinstance(raw_items, Iterable) or isinstance(raw_items, (str, bytes)):
        return []

    items: List[CompletionItem] = []
    seen = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        item = _one_item(raw)
        if item is None or item.label in seen:
            continue
        seen.add(item.label)
        items.append(item)
        if len(items) >= max(1, limit):
            break
    return items


def filter_completions(
    items: Sequence[CompletionItem], prefix: str, limit: int = MAX_COMPLETIONS
) -> List[CompletionItem]:
    """Filter by the typed prefix, keeping prefix matches first."""
    prefix = prefix or ""
    matches = [item for item in items if item.matches(prefix)]
    if prefix:
        lowered = prefix.lower()
        matches.sort(
            key=lambda item: (
                not item.label.lower().startswith(lowered),
                item.label.lower(),
            )
        )
    return matches[: max(1, limit)]
