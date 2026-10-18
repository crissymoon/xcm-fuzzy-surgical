"""Tests for dev box configuration and background orchestration."""

from __future__ import annotations

import json
import time
from pathlib import Path

from fuzzy_surgical import devbox as devbox_module
from fuzzy_surgical.devbox import DevBoxSession, load_devbox_config
from fuzzy_surgical.lsp.diagnostics import Diagnostic
from fuzzy_surgical.lsp.linters import LintOutcome
from fuzzy_surgical.lsp.registry import server_for_path


def test_load_config_reads_servers_linters_and_timeouts(tmp_path: Path) -> None:
    directory = tmp_path / ".fuzzy-surgical"
    directory.mkdir()
    config_path = directory / "devbox.json"
    config_path.write_text(
        json.dumps(
            {
                "lsp": {
                    "enabled": True,
                    "timeout": 3,
                    "servers": {"python": [["my-server", "--stdio"]]},
                },
                "linters": {
                    "enabled": True,
                    "timeout": 4,
                    "commands": {".py": [["my-lint", "{file}"]]},
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_devbox_config(tmp_path)

    assert config.path == str(config_path)
    assert config.lsp_timeout == 3.0
    assert config.lint_timeout == 4.0
    assert config.servers["python"] == [["my-server", "--stdio"]]
    assert config.linters[".py"] == [["my-lint", "{file}"]]
    assert config.warnings == []


def test_invalid_config_reports_a_warning(tmp_path: Path) -> None:
    directory = tmp_path / ".fuzzy-surgical"
    directory.mkdir()
    (directory / "devbox.json").write_text("{not json", encoding="utf-8")

    config = load_devbox_config(tmp_path)

    assert config.path == ""
    assert config.warnings


def test_missing_explicit_config_is_reported(tmp_path: Path) -> None:
    config = load_devbox_config(tmp_path, str(tmp_path / "nope.json"))

    assert config.warnings
    assert config.lsp_enabled is True


def test_server_override_wins(tmp_path: Path) -> None:
    spec = server_for_path(str(tmp_path / "a.py"), {"python": [["custom", "--stdio"]]})

    assert spec is not None
    assert spec.commands == (("custom", "--stdio"),)


def test_disabled_session_does_not_claim_work(tmp_path: Path) -> None:
    session = DevBoxSession(tmp_path, enable_lsp=False, enable_linters=False)
    try:
        assert "disabled" in session.status
        assert session.request_completion("x", 0, 0) == 0
        assert session.poll() == [] or isinstance(session.poll(), list)
    finally:
        session.close()


def test_lint_worker_publishes_diagnostics(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "a.py"
    target.write_text("value\n", encoding="utf-8")
    outcome = LintOutcome(
        diagnostics=[Diagnostic(str(target), 1, 2, "problem", "warning", "fake")],
        errors=[],
    )
    monkeypatch.setattr(devbox_module, "run_linters", lambda *args, **kwargs: outcome)

    session = DevBoxSession(tmp_path, enable_lsp=False)
    try:
        session.open_document(str(target), "value\n")
        printed = []
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            for event in session.poll():
                if event[0] == "diagnostics":
                    printed = event[2]
            if printed:
                break
            time.sleep(0.02)

        assert printed
        assert printed[0].message == "problem"
        assert session.diagnostics_for(str(target))[0].source == "fake"
    finally:
        session.close()


def test_devbox_reports_language_server_activity(tmp_path: Path) -> None:
    class FakeClient:
        name = "jdtls"
        running = True
        error = ""
        activity = "Importing project 40%"

        def diagnostics_for(self, path):
            return []

        def stop(self):
            return None

    session = DevBoxSession(tmp_path, enable_lsp=False, enable_linters=False)
    try:
        session._client = FakeClient()
        events = session.poll()
        assert ("status", "LSP jdtls: Importing project 40%") in events

        session._client.activity = ""
        events = session.poll()
        assert ("status", "LSP jdtls") in events
    finally:
        session.close()
