"""Minimal, defensive Language Server Protocol client over stdio.

The client owns one child process, one reader thread and one stderr drain
thread. Requests have timeouts, malformed messages are contained, and a server
crash never raises into the curses loop.
"""

from __future__ import annotations

import hashlib
import os
import queue
import subprocess
import threading
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Sequence

from fuzzy_surgical.lsp.commands import cache_root

from fuzzy_surgical.lsp.completion import CompletionItem, normalize_completions
from fuzzy_surgical.lsp.diagnostics import SEVERITY_BY_LSP, Diagnostic
from fuzzy_surgical.lsp.protocol import (
    LspProtocolError,
    encode_message,
    path_to_uri,
    read_message,
    uri_to_path,
)
from fuzzy_surgical.lsp.registry import env_without_color

DEFAULT_TIMEOUT = 5.0
DEFAULT_INITIALIZE_TIMEOUT = 12.0
MAX_MESSAGE_LOG = 20


def workspace_data_dir(root: Path) -> Path:
    """Stable per-project state directory for servers that need one."""
    digest = hashlib.sha1(str(Path(root).resolve()).encode("utf-8")).hexdigest()[:12]
    return Path(cache_root()) / "lsp" / digest


class LspClient:
    """One language server process for one project root."""

    def __init__(
        self,
        command: Sequence[str],
        root: Path,
        language_id: str,
        name: str = "",
        timeout: float = DEFAULT_TIMEOUT,
        initialize_timeout: float = DEFAULT_INITIALIZE_TIMEOUT,
        options: Optional[dict] = None,
    ) -> None:
        self.command = tuple(command)
        self.root = Path(root)
        self.language_id = language_id
        self.name = name or (self.command[0] if self.command else "lsp")
        self.timeout = timeout
        self.initialize_timeout = initialize_timeout
        self.options = options or {}

        self.error = ""
        self.last_message = ""
        self._process: Optional[subprocess.Popen] = None
        self._reader: Optional[threading.Thread] = None
        self._stderr_reader: Optional[threading.Thread] = None
        self._write_lock = threading.Lock()
        self._response_lock = threading.Lock()
        self._pending: Dict[int, "queue.Queue[Optional[dict]]"] = {}
        self._next_id = 0
        self._version = 0
        self._current_path = ""
        self._open = False
        self._closing = False
        self._closed = threading.Event()
        self._diagnostics: Dict[str, List[Diagnostic]] = {}
        self._stderr_tail: Deque[str] = deque(maxlen=MAX_MESSAGE_LOG)

    @property
    def running(self) -> bool:
        return (
            self._process is not None
            and self._process.poll() is None
            and not self._closed.is_set()
        )

    @property
    def status(self) -> str:
        if self.error:
            return self.error
        if self._closed.is_set():
            return f"{self.name} stopped"
        if self.running:
            return f"LSP {self.name}"
        return f"{self.name} not running"

    def _argv(self) -> List[str]:
        """Command line for this server, adding per-project state if needed."""
        argv = [str(part) for part in self.command]
        if self.name == "jdtls" and "-data" not in argv:
            data_dir = workspace_data_dir(self.root)
            try:
                data_dir.mkdir(parents=True, exist_ok=True)
                argv.extend(["-data", str(data_dir)])
            except OSError:
                pass
        return argv

    def start(self) -> bool:
        """Spawn the server and complete the initialize handshake."""
        if self.running:
            return True
        if not self.command:
            self.error = "no language server command"
            return False
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._process = subprocess.Popen(
                self._argv(),
                cwd=str(self.root),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                env=env_without_color(),
                creationflags=creationflags,
            )
        except (OSError, ValueError) as exc:
            self.error = f"could not start {self.name}: {exc}"
            return False

        self._reader = threading.Thread(target=self._read_loop, name="lsp-reader", daemon=True)
        self._reader.start()
        if self._process.stderr is not None:
            self._stderr_reader = threading.Thread(
                target=self._drain_stderr, name="lsp-stderr", daemon=True
            )
            self._stderr_reader.start()

        result = self.request("initialize", self._initialize_params(), self.initialize_timeout)
        if result is None:
            self.error = self.error or f"{self.name} did not answer initialize"
            self.stop()
            return False
        self.notify("initialized", {})
        return True

    def _initialize_params(self) -> dict:
        root_path = str(self.root)
        return {
            "processId": os.getpid(),
            "clientInfo": {"name": "fuzzy-surgical"},
            "rootPath": root_path,
            "rootUri": path_to_uri(root_path),
            "workspaceFolders": [
                {"uri": path_to_uri(root_path), "name": self.root.name or "workspace"}
            ],
            "capabilities": {
                "workspace": {"workspaceFolders": True, "configuration": False},
                "textDocument": {
                    "synchronization": {"dynamicRegistration": False, "didSave": True},
                    "completion": {
                        "dynamicRegistration": False,
                        "completionItem": {
                            "snippetSupport": False,
                            "documentationFormat": ["plaintext"],
                        },
                    },
                    "hover": {"contentFormat": ["plaintext"]},
                    "publishDiagnostics": {"relatedInformation": False},
                },
                "general": {"positionEncodings": ["utf-16"]},
            },
            "initializationOptions": self.options,
        }

    def open_document(self, path: str, text: str) -> bool:
        """Open one document, closing a previously opened different file."""
        if not self.start():
            return False
        if self._current_path and self._current_path != path:
            self._notify(
                "textDocument/didClose",
                {"textDocument": {"uri": path_to_uri(self._current_path)}},
            )
            self._open = False
        if self._current_path == path and self._open:
            self.change_document(text)
            return True
        self._current_path = path
        self._version = 1
        self._notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": path_to_uri(path),
                    "languageId": self.language_id,
                    "version": self._version,
                    "text": text,
                }
            },
        )
        self._open = True
        return True

    def change_document(self, text: str) -> None:
        if not self._current_path or not self._open:
            return
        self._version += 1
        self._notify(
            "textDocument/didChange",
            {
                "textDocument": {
                    "uri": path_to_uri(self._current_path),
                    "version": self._version,
                },
                "contentChanges": [{"text": text}],
            },
        )

    def save_document(self, text: str) -> None:
        if not self._current_path:
            return
        self.change_document(text)
        self._notify(
            "textDocument/didSave",
            {"textDocument": {"uri": path_to_uri(self._current_path)}},
        )

    def close_document(self) -> None:
        if self._current_path and self._open:
            self._notify(
                "textDocument/didClose",
                {"textDocument": {"uri": path_to_uri(self._current_path)}},
            )
        self._open = False
        self._current_path = ""

    def completion(self, line: int, character: int) -> List[CompletionItem]:
        """Request completions at a zero based line and UTF-16 column."""
        if not self._current_path:
            return []
        result = self.request(
            "textDocument/completion",
            {
                "textDocument": {"uri": path_to_uri(self._current_path)},
                "position": {"line": max(0, line), "character": max(0, character)},
                "context": {"triggerKind": 1},
            },
        )
        if result is None:
            return []
        return normalize_completions(result)

    def hover(self, line: int, character: int) -> str:
        """Return plain text hover information, or an empty string."""
        if not self._current_path:
            return ""
        result = self.request(
            "textDocument/hover",
            {
                "textDocument": {"uri": path_to_uri(self._current_path)},
                "position": {"line": max(0, line), "character": max(0, character)},
            },
            timeout=min(self.timeout, 3.0),
        )
        if not isinstance(result, dict):
            return ""
        contents = result.get("contents")
        if isinstance(contents, str):
            return contents
        if isinstance(contents, dict):
            value = contents.get("value")
            return value if isinstance(value, str) else ""
        if isinstance(contents, list):
            parts = []
            for entry in contents:
                if isinstance(entry, str):
                    parts.append(entry)
                elif isinstance(entry, dict) and isinstance(entry.get("value"), str):
                    parts.append(entry["value"])
            return "\n".join(parts)
        return ""

    def diagnostics_for(self, path: str) -> List[Diagnostic]:
        with self._response_lock:
            return list(self._diagnostics.get(os.path.abspath(path), ()))

    def request(self, method: str, params: Any, timeout: Optional[float] = None) -> Any:
        """Send a request and wait for its result, returning None on failure."""
        if not self.running:
            return None
        with self._response_lock:
            self._next_id += 1
            request_id = self._next_id
            waiter: "queue.Queue[Optional[dict]]" = queue.Queue(maxsize=1)
            self._pending[request_id] = waiter

        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        if not self._write(payload):
            with self._response_lock:
                self._pending.pop(request_id, None)
            return None
        try:
            response = waiter.get(timeout=max(0.2, timeout or self.timeout))
        except queue.Empty:
            with self._response_lock:
                self._pending.pop(request_id, None)
            self.error = f"{self.name} timed out on {method}"
            return None
        if response is None:
            self.error = self.error or f"{self.name} stopped during {method}"
            return None
        if "error" in response:
            error = response.get("error")
            if isinstance(error, dict):
                self.error = str(error.get("message") or error)
            else:
                self.error = str(error)
            return None
        # A successful answer clears a previous timeout or server error.
        self.error = ""
        return response.get("result")

    def notify(self, method: str, params: Any) -> None:
        self._notify(method, params)

    def _notify(self, method: str, params: Any) -> None:
        if not self.running:
            return
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _write(self, payload: dict) -> bool:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            return False
        try:
            data = encode_message(payload)
        except (TypeError, ValueError):
            return False
        try:
            with self._write_lock:
                process.stdin.write(data)
                process.stdin.flush()
            return True
        except (OSError, ValueError):
            self._closed.set()
            return False

    def _read_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            while not self._closing:
                try:
                    message = read_message(process.stdout)
                except (LspProtocolError, OSError, ValueError) as exc:
                    if not self._closing:
                        self.error = self._explain(str(exc))
                    break
                if message is None:
                    if not self._closing:
                        self.error = self._explain("server closed its output")
                    break
                self._dispatch(message)
        finally:
            self._closed.set()
            with self._response_lock:
                waiters = list(self._pending.values())
                self._pending.clear()
            for waiter in waiters:
                try:
                    waiter.put_nowait(None)
                except queue.Full:
                    pass

    def _dispatch(self, message: dict) -> None:
        if "id" in message and ("result" in message or "error" in message):
            with self._response_lock:
                waiter = self._pending.pop(message.get("id"), None)
            if waiter is not None:
                try:
                    waiter.put_nowait(message)
                except queue.Full:
                    pass
            return
        if "id" in message and "method" in message:
            self._answer_request(message)
            return
        method = message.get("method")
        params = message.get("params") or {}
        if method == "textDocument/publishDiagnostics" and isinstance(params, dict):
            self._store_diagnostics(params)
        elif method in ("window/showMessage", "window/logMessage"):
            if isinstance(params, dict) and isinstance(params.get("message"), str):
                self.last_message = params["message"][:500]

    def _answer_request(self, message: dict) -> None:
        """Answer a server to client request with a usable result.

        A null answer is enough for registration and progress requests, but
        configuration and workspace folder requests expect a list of the right
        shape. Answering those correctly is what lets jdtls and some pylsp
        versions finish initializing instead of waiting for a reply.
        """
        method = message.get("method")
        result: Any = None
        if method == "workspace/configuration":
            params = message.get("params") or {}
            items = params.get("items") if isinstance(params, dict) else None
            settings = self.options.get("settings") if isinstance(self.options, dict) else None
            value = settings if isinstance(settings, dict) else {}
            count = len(items) if isinstance(items, list) else 1
            result = [value for _ in range(max(0, min(count, 100)))]
        elif method == "workspace/workspaceFolders":
            result = [
                {
                    "uri": path_to_uri(str(self.root)),
                    "name": self.root.name or "workspace",
                }
            ]
        elif method == "workspace/applyEdit":
            result = {"applied": False}
        self._write({"jsonrpc": "2.0", "id": message.get("id"), "result": result})

    def _store_diagnostics(self, params: dict) -> None:
        uri = params.get("uri")
        raw_items = params.get("diagnostics")
        if not isinstance(uri, str) or not isinstance(raw_items, list):
            return
        path = os.path.abspath(uri_to_path(uri))
        items: List[Diagnostic] = []
        for raw in raw_items[:500]:
            if not isinstance(raw, dict):
                continue
            message = raw.get("message")
            if not isinstance(message, str) or not message:
                continue
            try:
                start = raw.get("range", {}).get("start", {})
                line = int(start.get("line", 0)) + 1
                character = int(start.get("character", 0))
            except (AttributeError, TypeError, ValueError):
                line, character = 1, 0
            severity_value = raw.get("severity")
            try:
                severity = SEVERITY_BY_LSP.get(int(severity_value), "info")
            except (TypeError, ValueError):
                severity = "info"
            code = raw.get("code")
            items.append(
                Diagnostic(
                    path=path,
                    line=max(1, line),
                    column=max(0, character),
                    message=message[:1000],
                    severity=severity,
                    source=str(raw.get("source") or self.name),
                    code=str(code) if code is not None else "",
                )
            )
        with self._response_lock:
            if items:
                self._diagnostics[path] = items
            else:
                self._diagnostics.pop(path, None)

    def _explain(self, message: str) -> str:
        """Add the most relevant stderr line to an exit message.

        Servers report the real reason on stderr: a missing JDK, an unresolved
        module or a bad interpreter. Without this the user only sees that the
        pipe closed.
        """
        lines = [line.strip() for line in self._stderr_tail if line.strip()]
        interesting = ""
        for line in reversed(lines):
            lowered = line.lower()
            if lowered.startswith(("warning:", "picked up", "slf4j")):
                continue
            if any(word in lowered for word in ("error", "requires", "requirement", "exception")):
                interesting = line
                break
        if not interesting and lines:
            interesting = lines[-1]
        if not interesting:
            return f"{self.name}: {message}"
        return f"{self.name}: {message} ({interesting[:200]})"

    def _drain_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        stream = process.stderr
        try:
            while not self._closing:
                line = stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").strip()
                if text:
                    self._stderr_tail.append(text[:500])
        except (OSError, ValueError):
            pass

    def stderr_tail(self) -> List[str]:
        return list(self._stderr_tail)

    def stop(self) -> None:
        """Shut the server down without raising, even if it is already gone."""
        process = self._process
        if process is None:
            return
        try:
            if process.poll() is None:
                self.request("shutdown", None, timeout=1.5)
                self._notify("exit", None)
        except Exception:
            pass
        self._closing = True
        try:
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            try:
                process.terminate()
                process.wait(timeout=1.0)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass
        self._closed.set()
        self._process = None
