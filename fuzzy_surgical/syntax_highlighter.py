"""Syntax highlighting for terminal rendering.

Pygments is used when available. A small regex based fallback keeps the
application usable (and colour coded) when Pygments is not installed.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import List, Tuple

try:  # pragma: no cover - exercised by the import environment
    from pygments.lexers import TextLexer, get_lexer_for_filename
    from pygments.token import Token
    from pygments.util import ClassNotFound

    PYGMENTS_AVAILABLE = True
except ImportError:  # pragma: no cover - fallback path
    TextLexer = None
    get_lexer_for_filename = None
    Token = None
    ClassNotFound = Exception
    PYGMENTS_AVAILABLE = False

Span = Tuple[str, str]
MAX_HIGHLIGHT_LENGTH = 8000

_KEYWORDS = {
    "as",
    "async",
    "await",
    "break",
    "case",
    "catch",
    "class",
    "const",
    "continue",
    "def",
    "default",
    "delete",
    "do",
    "elif",
    "else",
    "enum",
    "except",
    "export",
    "extends",
    "false",
    "finally",
    "for",
    "from",
    "function",
    "global",
    "if",
    "implements",
    "import",
    "in",
    "instanceof",
    "interface",
    "is",
    "lambda",
    "let",
    "match",
    "new",
    "none",
    "nonlocal",
    "not",
    "null",
    "of",
    "or",
    "package",
    "pass",
    "private",
    "protected",
    "public",
    "raise",
    "return",
    "static",
    "struct",
    "super",
    "switch",
    "this",
    "throw",
    "true",
    "try",
    "type",
    "typeof",
    "val",
    "var",
    "void",
    "while",
    "with",
    "yield",
    "None",
    "True",
    "False",
}

_FALLBACK_RE = re.compile(
    r"(?P<comment>#.*|//.*|/\*.*?\*/)"
    r"|(?P<string>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')"
    r"|(?P<number>\b(?:0[xX][0-9a-fA-F]+|\d+(?:\.\d+)?)\b)"
    r"|(?P<word>[A-Za-z_]\w*)"
)


def _fallback_line(line: str) -> List[Span]:
    """Classify a line without Pygments, keeping every character."""
    spans: List[Span] = []
    cursor = 0

    def add(text: str, category: str) -> None:
        if not text:
            return
        if spans and spans[-1][1] == category:
            spans[-1] = (spans[-1][0] + text, category)
        else:
            spans.append((text, category))

    for match in _FALLBACK_RE.finditer(line):
        if match.start() > cursor:
            add(line[cursor : match.start()], "default")

        kind = match.lastgroup
        value = match.group(0)
        if kind == "comment":
            add(value, "comment")
        elif kind == "string":
            add(value, "string")
        elif kind == "number":
            add(value, "number")
        else:
            if value in _KEYWORDS:
                add(value, "keyword")
            elif value[:1].isupper():
                add(value, "class")
            else:
                tail = line[match.end() :]
                if tail.lstrip().startswith("("):
                    add(value, "function")
                else:
                    add(value, "default")
        cursor = match.end()

    if cursor < len(line):
        add(line[cursor:], "default")
    return spans


def _category(ttype) -> str:
    """Map a Pygments token type onto a render category."""
    if ttype in Token.Comment:
        return "comment"
    if ttype in Token.Literal.String:
        return "string"
    if ttype in Token.Literal.Number:
        return "number"
    if ttype in Token.Keyword:
        return "keyword"
    if ttype in Token.Name.Builtin:
        return "builtin"
    if ttype in Token.Name.Function or ttype in Token.Name.Attribute:
        return "function"
    if ttype in Token.Name.Class:
        return "class"
    if ttype in Token.Name.Decorator:
        return "decorator"
    if ttype in Token.Name.Tag:
        return "keyword"
    if ttype in Token.Name.Constant or ttype in Token.Literal:
        return "constant"
    if ttype in Token.Operator:
        return "operator"
    if ttype in Token.Generic.Heading or ttype in Token.Generic.Subheading:
        return "keyword"
    if ttype in Token.Generic.Strong:
        return "class"
    if ttype in Token.Generic.Emph:
        return "function"
    if ttype in Token.Generic.Inserted:
        return "string"
    if ttype in Token.Generic.Deleted:
        return "constant"
    return "default"


@lru_cache(maxsize=256)
def _lexer_for(filename: str):
    """Return a cached lexer for a filename, or None when unavailable."""
    if not PYGMENTS_AVAILABLE:
        return None
    try:
        return get_lexer_for_filename(filename, stripnl=False)
    except (ClassNotFound, Exception):
        return TextLexer(stripnl=False)


@lru_cache(maxsize=8192)
def _highlight_cached(filename: str, line: str) -> Tuple[Span, ...]:
    if not PYGMENTS_AVAILABLE:
        return tuple(_fallback_line(line))

    lexer = _lexer_for(filename)
    if lexer is None:
        return tuple(_fallback_line(line))

    spans: List[Span] = []
    try:
        # A trailing newline lets line oriented lexers (for example Markdown)
        # recognise headings; the newline itself is stripped below.
        for _, ttype, value in lexer.get_tokens_unprocessed(line + "\n"):
            value = value.replace("\r", "").replace("\n", "")
            if not value:
                continue
            category = _category(ttype)
            if spans and spans[-1][1] == category:
                spans[-1] = (spans[-1][0] + value, category)
            else:
                spans.append((value, category))
    except Exception:
        return tuple(_fallback_line(line))

    if not spans:
        return ((line, "default"),) if line else ()
    return tuple(spans)


def highlight_line(filename: str, line: str) -> List[Span]:
    """Return (text, category) spans covering the supplied line."""
    line = line.replace("\n", "").replace("\r", "")
    if not line:
        return []
    if len(line) > MAX_HIGHLIGHT_LENGTH:
        head = line[:MAX_HIGHLIGHT_LENGTH]
        tail = line[MAX_HIGHLIGHT_LENGTH:]
        return list(_highlight_cached(filename, head)) + [(tail, "default")]
    return list(_highlight_cached(filename, line))


def clear_cache() -> None:
    """Drop cached lexers and token streams (used after dependency changes)."""
    _lexer_for.cache_clear()
    _highlight_cached.cache_clear()
