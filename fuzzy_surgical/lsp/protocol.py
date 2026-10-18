"""JSON-RPC framing and position helpers for the Language Server Protocol."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import BinaryIO, Optional
from urllib.parse import quote, unquote, urlparse

MAX_HEADER_BYTES = 64 * 1024
MAX_MESSAGE_BYTES = 64 * 1024 * 1024


class LspProtocolError(Exception):
    """Raised when a language server sends an unusable message."""


class LspClosed(LspProtocolError):
    """Raised when the language server closes its output stream."""


def encode_message(payload: dict) -> bytes:
    """Encode one JSON-RPC message with a Content-Length header."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


def _read_exact(stream: BinaryIO, length: int) -> bytes:
    """Read exactly length bytes; pipes may return short reads."""
    chunks = []
    remaining = length
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            raise LspClosed("language server closed before the message was complete")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_message(stream: BinaryIO) -> Optional[dict]:
    """Read one framed message. Returns None at a clean end of stream."""
    length = None
    header_bytes = 0
    while True:
        line = stream.readline()
        if not line:
            if length is None and header_bytes == 0:
                return None
            raise LspClosed("language server closed in the middle of a header")
        header_bytes += len(line)
        if header_bytes > MAX_HEADER_BYTES:
            raise LspProtocolError("language server header is too large")
        if line in (b"\r\n", b"\n"):
            break
        try:
            text = line.decode("ascii", "replace").strip()
        except Exception as exc:  # pragma: no cover - decode with replace is safe
            raise LspProtocolError("unreadable language server header") from exc
        if not text:
            continue
        name, _, value = text.partition(":")
        if name.lower() == "content-length":
            try:
                length = int(value.strip())
            except ValueError as exc:
                raise LspProtocolError("invalid Content-Length header") from exc
    if length is None:
        raise LspProtocolError("language server message has no Content-Length")
    if length < 0 or length > MAX_MESSAGE_BYTES:
        raise LspProtocolError("language server message is too large")
    body = _read_exact(stream, length)
    try:
        message = json.loads(body.decode("utf-8", "replace"))
    except ValueError as exc:
        raise LspProtocolError("language server sent invalid JSON") from exc
    if not isinstance(message, dict):
        raise LspProtocolError("language server sent a non-object message")
    return message


def utf16_column(line: str, index: int) -> int:
    """Convert a Python string index to an LSP UTF-16 column."""
    index = max(0, min(index, len(line)))
    return len(line[:index].encode("utf-16-le")) // 2


def utf16_index(line: str, column: int) -> int:
    """Convert an LSP UTF-16 column back to a Python string index."""
    if column <= 0:
        return 0
    units = 0
    for index, char in enumerate(line):
        units += 2 if ord(char) > 0xFFFF else 1
        if units > column:
            return index
    return len(line)


def path_to_uri(path: str) -> str:
    """Build a file URI that works on POSIX and Windows."""
    absolute = os.path.abspath(path)
    if os.name == "nt":
        drive, rest = os.path.splitdrive(absolute)
        normalised = rest.replace("\\", "/")
        return "file:///" + drive.rstrip(":") + ":" + quote(normalised, safe="/:")
    return Path(absolute).as_uri()


def uri_to_path(uri: str) -> str:
    """Turn a file URI into a local path, tolerating unusual encodings."""
    try:
        parsed = urlparse(uri)
    except ValueError:
        return uri
    if parsed.scheme and parsed.scheme != "file":
        return uri
    raw = unquote(parsed.path or "")
    if os.name == "nt" and raw.startswith("/") and len(raw) > 2 and raw[2] == ":":
        raw = raw[1:]
    return raw or uri
