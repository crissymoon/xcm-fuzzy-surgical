"""
Tests for the fuzzy matcher module.
"""

from fuzzy_surgical.fuzzy_matcher import FuzzyMatcher


def test_fuzzy_match_basic():
    """Test basic fuzzy matching."""
    matcher = FuzzyMatcher()

    # Exact match
    match, score, positions = matcher.fuzzy_match("hello", "hello world")
    assert match is True
    assert score > 0
    assert len(positions) == 5

    # Partial match
    match, score, positions = matcher.fuzzy_match("hwd", "hello world")
    assert match is True

    # No match
    match, score, positions = matcher.fuzzy_match("xyz", "hello world")
    assert match is False
    assert score == 0

    # Empty query matches everything
    match, score, positions = matcher.fuzzy_match("", "anything")
    assert match is True


def test_fuzzy_match_word_boundary():
    """Test word boundary scoring."""
    matcher = FuzzyMatcher()

    match1, score1, _ = matcher.fuzzy_match("hw", "hello world")
    match2, score2, _ = matcher.fuzzy_match("hw", "helloworld")

    assert match1 is True
    assert match2 is True
    # 'h' at word boundary should score higher
    assert score1 > score2


def test_search_lines():
    """Test searching through indexed lines."""
    matcher = FuzzyMatcher()

    file_lines = {
        "file1.txt": [(1, "hello world"), (2, "goodbye world")],
        "file2.txt": [(1, "hello there"), (2, "hello again")],
    }

    results = matcher.search_lines(file_lines, "hello")

    assert len(results) == 3
    # Should be sorted by score
    assert all("hello" in result[2].lower() for result in results)

    # Test limit
    results = matcher.search_lines(file_lines, "hello", limit=2)
    assert len(results) == 2


def test_exact_substring_beats_scattered_subsequence():
    """A literal hit must outrank a line that only has the letters scattered."""
    matcher = FuzzyMatcher()
    file_lines = {
        "a.py": [
            (1, "n e e d l e notes"),
            (2, "value = needle + 1"),
        ]
    }

    results = matcher.search_lines(file_lines, "needle")

    assert results[0][1] == 2
    match, _, positions = matcher.fuzzy_match("needle", "value = needle + 1")
    assert match is True
    assert positions == list(range(8, 14))
