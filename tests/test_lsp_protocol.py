"""Tests for LSP framing, positions and file URIs."""

from __future__ import annotations

import io

import pytest

from fuzzy_surgical.lsp.protocol import (
    LspClosed,
    LspProtocolError,
    encode_message,
    path_to_uri,
    read_message,
    uri_to_path,
    utf16_column,
    utf16_index,
)


def test_encode_and_read_roundtrip() -> None:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"x": "é"}}
    stream = io.BytesIO(encode_message(payload) + encode_message({"jsonrpc": "2.0"}))

    assert read_message(stream) == payload
    assert read_message(stream) == {"jsonrpc": "2.0"}
    assert read_message(stream) is None


def test_partial_body_raises_closed() -> None:
    data = encode_message({"jsonrpc": "2.0", "id": 1})
    with pytest.raises(LspClosed):
        read_message(io.BytesIO(data[:-3]))


def test_missing_header_raises() -> None:
    with pytest.raises(LspProtocolError):
        read_message(io.BytesIO(b"\r\n{}"))


def test_bad_json_raises() -> None:
    body = b"not json"
    with pytest.raises(LspProtocolError):
        read_message(io.BytesIO(b"Content-Length: 8\r\n\r\n" + body))


def test_utf16_columns_handle_astral_characters() -> None:
    line = "a\U0001f600b"

    assert utf16_column(line, 0) == 0
    assert utf16_column(line, 1) == 1
    assert utf16_column(line, 2) == 3
    assert utf16_column(line, 3) == 4
    assert utf16_index(line, 1) == 1
    assert utf16_index(line, 3) == 2
    assert utf16_index(line, 4) == 3
    assert utf16_index(line, 99) == 3


def test_file_uri_roundtrip(tmp_path) -> None:
    target = tmp_path / "a file.py"
    uri = path_to_uri(str(target))

    assert uri.startswith("file://")
    assert "a%20file.py" in uri
    assert uri_to_path(uri).endswith("a file.py")
