"""Tests for linter output parsing and command construction."""

from __future__ import annotations

from fuzzy_surgical.lsp.linters import (
    _command_for,
    linters_for,
    parse_lint_output,
    run_linters,
)


def test_parse_ruff_concise_output() -> None:
    output = "app.py:4:9: E501 line too long (101 > 100)\nFound 1 error.\n"

    items = parse_lint_output(output, "/tmp/app.py", "ruff")

    assert len(items) == 1
    assert items[0].line == 4
    assert items[0].column == 9
    assert items[0].code == "E501"
    assert items[0].source == "ruff"
    assert "line too long" in items[0].message


def test_parse_eslint_unix_and_shellcheck() -> None:
    eslint = "/tmp/app.ts:2:3: 'x' is not defined [Error/no-undef]\n"
    shellcheck = "/tmp/run.sh:7:1: warning: Quote this to prevent word splitting [SC2046]\n"

    issues = parse_lint_output(eslint, "/tmp/app.ts", "eslint")
    shell = parse_lint_output(shellcheck, "/tmp/run.sh", "shellcheck")

    assert issues[0].severity == "error"
    assert issues[0].code == "no-undef"
    assert shell[0].severity == "warning"
    assert shell[0].code == "SC2046"


def test_warning_code_without_prefix_is_a_warning() -> None:
    items = parse_lint_output("app.py:1:1: W999 odd thing", "/tmp/app.py", "fake")

    assert items[0].severity == "warning"


def test_parse_php_error_format() -> None:
    output = "PHP Parse error:  syntax error, unexpected '}' in /tmp/a.php on line 9\n"

    items = parse_lint_output(output, "/tmp/a.php", "php")

    assert items[0].line == 9
    assert "syntax error" in items[0].message


def test_parse_ignores_other_files_and_noise() -> None:
    output = (
        "Traceback (most recent call last):\n"
        "File \"x\", line 1\n"
        "/other/thing.py:1:2: E1 wrong file\n"
        "app.py:3: message without column\n"
    )

    items = parse_lint_output(output, "/tmp/app.py", "flake8")

    assert len(items) == 1
    assert items[0].line == 3
    assert items[0].column == 0


def test_command_placeholder_and_append() -> None:
    assert _command_for("/tmp/a.py", ("ruff", "{file}")) == ["ruff", "/tmp/a.py"]
    assert _command_for("/tmp/a.py", ("ruff", "check")) == ["ruff", "check", "/tmp/a.py"]


def test_linters_for_extension() -> None:
    names = [spec.name for spec in linters_for("a.py")]

    assert names[0] == "ruff"
    assert "eslint" in [spec.name for spec in linters_for("a.ts")]


def test_run_linters_ignores_unknown_extensions(tmp_path) -> None:
    target = tmp_path / "a.zzz"
    target.write_text("nothing\n", encoding="utf-8")

    outcome = run_linters(str(target))

    assert outcome.diagnostics == []
    assert outcome.errors == []


def test_run_linters_reports_missing_file(tmp_path) -> None:
    outcome = run_linters(str(tmp_path / "missing.py"))

    assert outcome.errors
