"""Tests for syntax highlighting and the regex fallback."""

from fuzzy_surgical.syntax_highlighter import _fallback_line, highlight_line


def _reconstruct(spans):
    return "".join(text for text, _ in spans)


def test_highlight_reconstructs_source():
    line = "def hello(name):  # greet\n"
    spans = highlight_line("example.py", line)
    assert _reconstruct(spans) == line.rstrip("\n")


def test_python_keyword_and_comment_categories():
    spans = highlight_line("example.py", "def hello():  # greet")
    categories = {category for _, category in spans}
    assert "keyword" in categories
    assert "comment" in categories


def test_fallback_covers_every_character():
    line = "const value = call(42, 'text'); // note"
    spans = _fallback_line(line)
    assert _reconstruct(spans) == line
    categories = {category for _, category in spans}
    assert "keyword" in categories
    assert "string" in categories
    assert "number" in categories
    assert "comment" in categories


def test_unknown_extension_does_not_raise():
    spans = highlight_line("no-extension", "plain text")
    assert _reconstruct(spans) == "plain text"


def test_markdown_heading_is_classified():
    spans = highlight_line("readme.md", "# Title")
    assert ("# Title", "keyword") in spans
