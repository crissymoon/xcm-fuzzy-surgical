"""End to end test of the stdio language server client with a fake server."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from fuzzy_surgical.lsp.client import LspClient

FAKE_SERVER = r'''
import json
import sys


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
    body = sys.stdin.buffer.read(length)
    return json.loads(body.decode("utf-8"))


def write_message(payload):
    body = json.dumps(payload).encode("utf-8")
    sys.stdout.buffer.write(b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
    sys.stdout.buffer.flush()


while True:
    message = read_message()
    if message is None:
        break
    method = message.get("method")
    if method == "initialize":
        write_message({"jsonrpc": "2.0", "id": message["id"], "result": {"capabilities": {}}})
    elif method == "edited":
        write_message({"jsonrpc": "2.0", "id": message["id"], "result": None})
    elif method == "textDocument/didOpen":
        uri = message["params"]["textDocument"]["uri"]
        write_message(
            {
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {
                    "uri": uri,
                    "diagnostics": [
                        {
                            "range": {"start": {"line": 1, "character": 2}},
                            "severity": 1,
                            "message": "synthetic problem",
                            "source": "fake",
                        }
                    ],
                },
            }
        )
    elif method == "textDocument/completion":
        write_message(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": [
                    {"label": "alpha", "kind": 3, "detail": "function"},
                    {"label": "beta", "insertText": "b_value"},
                ],
            }
        )
    elif method == "shutdown":
        write_message({"jsonrpc": "2.0", "id": message["id"], "result": None})
    elif method == "exit":
        break
'''


def test_client_initializes_completes_and_reports_diagnostics(tmp_path: Path) -> None:
    script = tmp_path / "fake_lsp.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    target = tmp_path / "app.py"
    target.write_text("one\ntwo\n", encoding="utf-8")

    client = LspClient([sys.executable, str(script)], tmp_path, "python", name="fake")
    try:
        assert client.start() is True
        assert client.open_document(str(target), "one\ntwo\n") is True

        items = client.completion(1, 0)
        assert [item.label for item in items] == ["alpha", "beta"]
        assert items[1].insert_text == "b_value"

        deadline = time.monotonic() + 3.0
        diagnostics = []
        while time.monotonic() < deadline:
            diagnostics = client.diagnostics_for(str(target))
            if diagnostics:
                break
            time.sleep(0.02)

        assert diagnostics
        assert diagnostics[0].message == "synthetic problem"
        assert diagnostics[0].severity == "error"
        assert diagnostics[0].line == 2
    finally:
        client.stop()

    assert client.running is False
