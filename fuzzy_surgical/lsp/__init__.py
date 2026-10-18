"""Language server and dev box integration for FuzzySurgical."""

from fuzzy_surgical.lsp.completion import CompletionItem, completion_prefix, filter_completions
from fuzzy_surgical.lsp.diagnostics import Diagnostic, count_by_severity, sorted_diagnostics
from fuzzy_surgical.lsp.protocol import utf16_column, utf16_index

__all__ = [
    "CompletionItem",
    "Diagnostic",
    "completion_prefix",
    "count_by_severity",
    "filter_completions",
    "sorted_diagnostics",
    "utf16_column",
    "utf16_index",
]
