"""Tests for the command name reported by the CLI."""

from __future__ import annotations

import sys

from fuzzy_surgical import cli


def test_short_name_from_the_launcher_environment(monkeypatch) -> None:
    monkeypatch.setenv(cli.NAME_ENV, "fuzzy")

    assert cli.program_name() == "fuzzy"


def test_long_name_stays_available(monkeypatch) -> None:
    monkeypatch.setenv(cli.NAME_ENV, "fuzzy-surgical")

    assert cli.program_name() == "fuzzy-surgical"


def test_unknown_override_falls_back_to_the_executable(monkeypatch) -> None:
    monkeypatch.setenv(cli.NAME_ENV, "not-a-command")
    monkeypatch.setattr(sys, "argv", ["/usr/local/bin/fuzzy"])

    assert cli.program_name() == "fuzzy"


def test_parser_uses_the_reported_name() -> None:
    parser = cli.build_parser("fuzzy")

    assert parser.prog == "fuzzy"


def test_source_checkout_reports_the_long_name(monkeypatch) -> None:
    monkeypatch.delenv(cli.NAME_ENV, raising=False)
    monkeypatch.setattr(sys, "argv", ["main.py"])

    assert cli.program_name() == "fuzzy-surgical"
