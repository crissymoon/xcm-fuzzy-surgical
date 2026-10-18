"""Detection and wiring tests for language servers and linters."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from fuzzy_surgical.devbox import DevBoxSession
from fuzzy_surgical.lsp.client import LspClient
from fuzzy_surgical.lsp.commands import python_executable, resolve
from fuzzy_surgical.lsp.doctor import format_report, inspect_tools
from fuzzy_surgical.lsp.linters import available_linters
from fuzzy_surgical.lsp.registry import (
    ServerSpec,
    describe_server,
    language_id_for,
    missing_server_hint,
    server_for_path,
)

FAKE_CONFIG_SERVER = r'''
import json
import sys

ready = [False]


def read_message():
    length = None
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        name, _, value = line.decode("ascii", "replace").partition(":")
        if name.lower() == "content-length":
            length = int(value.strip())
    if length is None:
        return None
    return json.loads(sys.stdin.buffer.read(length))


def write_message(payload):
    body = json.dumps(payload).encode("utf-8")
    sys.stdout.buffer.write(
        b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
    )
    sys.stdout.buffer.flush()


while True:
    message = read_message()
    if message is None:
        break
    method = message.get("method")
    if method == "initialize":
        write_message(
            {"jsonrpc": "2.0", "id": message["id"], "result": {"capabilities": {}}}
        )
        write_message(
            {
                "jsonrpc": "2.0",
                "id": 900,
                "method": "workspace/configuration",
                "params": {"items": [{"section": "a"}, {"section": "b"}]},
            }
        )
    elif method == "textDocument/completion":
        label = "configured" if ready[0] else "unanswered"
        write_message(
            {"jsonrpc": "2.0", "id": message["id"], "result": [{"label": label}]}
        )
    elif method == "shutdown":
        write_message({"jsonrpc": "2.0", "id": message["id"], "result": None})
    elif method == "exit":
        break
    elif message.get("id") == 900:
        value = message.get("result")
        ready[0] = isinstance(value, list) and len(value) == 2
'''


def test_python_module_command_uses_running_interpreter() -> None:
    resolved = resolve(("{python}", "-m", "json"))

    assert resolved == [python_executable(), "-m", "json"]


def test_missing_python_module_is_not_reported_as_installed() -> None:
    assert resolve(("{python}", "-m", "fuzzy_surgical_missing_module")) is None


def test_glob_command_finds_bundled_server(tmp_path: Path) -> None:
    server = tmp_path / "redhat.java-1.0" / "jdtls" / "bin" / "jdtls"
    server.parent.mkdir(parents=True)
    server.write_text("#!/bin/sh\n", encoding="utf-8")
    server.chmod(0o755)

    resolved = resolve((str(tmp_path / "redhat.java-*" / "jdtls" / "bin" / "jdtls"),))

    assert resolved == [str(server)]


def test_java_files_use_jdtls(tmp_path: Path) -> None:
    spec = server_for_path(str(tmp_path / "TaxCalc.java"))

    assert spec is not None
    assert spec.name == "jdtls"
    assert spec.language_id == "java"
    assert language_id_for("TaxCalc.java") == "java"
    assert spec.initialize_timeout >= 30


def test_missing_server_hint_names_the_install_command() -> None:
    spec = ServerSpec(
        "ghost",
        "java",
        (".java",),
        (("definitely-not-a-real-server-xyz",),),
        hint="brew install jdtls",
    )

    assert spec.available_command() is None
    assert "brew install jdtls" in missing_server_hint(spec)
    assert "brew install jdtls" in describe_server(spec)


def test_devbox_advice_explains_missing_support(tmp_path: Path) -> None:
    session = DevBoxSession(tmp_path)
    disabled = DevBoxSession(tmp_path, enable_lsp=False)
    try:
        assert "disabled" in disabled.advice_for(str(tmp_path / "a.py"))
        assert session.advice_for(str(tmp_path / "notes.txt"))
    finally:
        session.close()
        disabled.close()


def test_cache_token_expands_to_the_user_cache() -> None:
    from fuzzy_surgical.lsp.commands import CACHE_TOKEN, cache_root, describe

    text = describe((f"{CACHE_TOKEN}/lsp/jdtls/bin/jdtls",))

    assert text == str(Path(cache_root()) / "lsp" / "jdtls" / "bin" / "jdtls")
    assert cache_root().endswith("fuzzy-surgical")


def test_doctor_lists_java_and_every_linter() -> None:
    reports = inspect_tools()
    names = {report.name for report in reports}

    assert "jdtls" in names
    assert "flake8" in names
    assert "pylsp" in names
    assert len(reports) == len(inspect_tools())
    assert len(available_linters()) >= 8

    text = format_report(reports)
    assert "jdtls" in text
    assert "tools ready." in text


def test_client_answers_configuration_requests(tmp_path: Path) -> None:
    script = tmp_path / "configured_lsp.py"
    script.write_text(FAKE_CONFIG_SERVER, encoding="utf-8")
    target = tmp_path / "app.py"
    target.write_text("one\n", encoding="utf-8")

    client = LspClient([sys.executable, str(script)], tmp_path, "python", name="fake")
    try:
        assert client.start() is True
        assert client.open_document(str(target), "one\n") is True
        client.error = "stale failure"

        items = client.completion(0, 0)

        assert client.error == ""
        assert [item.label for item in items] in (["unanswered"], ["configured"])
    finally:
        client.stop()


def test_configuration_answer_arrives_before_completion(tmp_path: Path) -> None:
    script = tmp_path / "configured_lsp.py"
    script.write_text(FAKE_CONFIG_SERVER, encoding="utf-8")
    target = tmp_path / "app.py"
    target.write_text("one\n", encoding="utf-8")

    client = LspClient([sys.executable, str(script)], tmp_path, "python", name="fake")
    try:
        assert client.start() is True
        assert client.open_document(str(target), "one\n") is True
        labels = []
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            items = client.completion(0, 0)
            labels = [item.label for item in items]
            if labels == ["configured"]:
                break
            time.sleep(0.05)

        assert labels == ["configured"]
    finally:
        client.stop()
