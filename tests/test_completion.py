"""Tests for completion items, prefix detection and the popup model."""

from __future__ import annotations

from fuzzy_surgical.completion_ui import CompletionPopup
from fuzzy_surgical.lsp.completion import (
    CompletionItem,
    completion_prefix,
    filter_completions,
    normalize_completions,
)
from fuzzy_surgical.text_buffer import TextBuffer


def test_completion_prefix_finds_the_identifier() -> None:
    assert completion_prefix("value = obj.me", 14) == (12, "me")
    assert completion_prefix("    ", 4) == (4, "")
    assert completion_prefix("abc", 0) == (0, "")


def test_normalize_completions_handles_lists_and_snippets() -> None:
    items = normalize_completions(
        [
            {"label": "method", "kind": 2, "detail": "def"},
            {"label": "print", "insertTextFormat": 2, "insertText": "print(${1:value})"},
            {"label": "print", "insertText": "duplicate"},
            {"nope": True},
        ]
    )

    assert [item.label for item in items] == ["method", "print"]
    assert items[1].insert_text == "print(value)"
    assert items[0].kind == "method"


def test_normalize_completions_reads_completion_lists() -> None:
    items = normalize_completions(
        {"isIncomplete": True, "items": [{"label": "alpha", "textEdit": {"newText": "a1"}}]}
    )

    assert items[0].insert_text == "a1"


def test_normalize_completions_strips_padded_labels() -> None:
    items = normalize_completions([{"label": " void", "kind": 14}])

    assert items[0].label == "void"
    assert items[0].insert_text == "void"
    assert [item.label for item in filter_completions(items, "vo")] == ["void"]


def test_filter_prefers_prefix_matches() -> None:
    items = [
        CompletionItem("get_value", "get_value"),
        CompletionItem("target", "target"),
        CompletionItem("getter", "getter"),
    ]

    filtered = filter_completions(items, "get")

    assert [item.label for item in filtered] == ["get_value", "getter"]


def test_popup_ignores_stale_results_and_accepts_selection() -> None:
    popup = CompletionPopup()
    popup.open(2, "pr", 7)

    assert popup.set_result(6, [CompletionItem("stale", "stale")]) is False
    assert popup.set_result(
        7, [CompletionItem("print", "print"), CompletionItem("prompt", "prompt")]
    )
    assert popup.visible is True
    assert [item.label for item in popup.filtered] == ["print", "prompt"]

    popup.move(1)
    assert popup.selected_item().label == "prompt"

    buffer = TextBuffer("x = pr")
    buffer.cursor_col = len(buffer.lines[0])
    popup.prefix_start = 4
    item = popup.selected_item()
    buffer.replace_span(0, 4, buffer.cursor_col, item.insert_text)

    assert buffer.to_text() == "x = prompt"
    assert buffer.cursor_col == len("x = prompt")


def test_popup_refine_hides_when_nothing_matches() -> None:
    popup = CompletionPopup()
    popup.open(0, "z", 1)
    popup.set_result(1, [CompletionItem("alpha", "alpha")])

    assert popup.visible is False

    popup.refine("a")
    assert popup.visible is True
