"""Tests for the renderer helpers that do not require a live terminal."""

from fuzzy_surgical.renderer import sanitize, shorten


def test_sanitize_strips_control_characters_and_expands_tabs():
    assert sanitize("a\tb\x00c\r\nd") == "a    bcd"


def test_shorten_keeps_the_tail():
    assert shorten("abcdef", 6) == "abcdef"
    assert shorten("abcdef", 5) == "...ef"
    assert shorten("abcdef", 2) == "ef"
    assert shorten("abcdef", 0) == ""


def test_match_offset_brings_late_matches_into_view():
    from fuzzy_surgical.renderer import match_offset

    assert match_offset([], 40) == 0
    assert match_offset([3], 40) == 0
    assert match_offset([36], 40, margin=6) == 30
    assert match_offset([60], 40, margin=6) == 54
    assert match_offset([5], 0) == 0


def test_middle_shorten_keeps_the_label_and_the_tail():
    from fuzzy_surgical.renderer import middle_shorten

    text = "Saved /very/long/path/that/keeps/going/TaxCalc.java:63 (120 bytes, verified)"
    shortened = middle_shorten(text, 50)

    assert shortened.startswith("Saved /very")
    assert shortened.endswith("(120 bytes, verified)")
    assert "..." in shortened
    assert len(shortened) <= 50
