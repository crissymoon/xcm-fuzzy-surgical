"""Per-project dev box: language server sessions plus linter runs.

The session keeps every child process and every blocking request off the UI
thread. The editor polls a queue for completions, diagnostics and status text.

Configuration is optional. Without a file the built-in server and linter
registries are used, and a missing tool is reported instead of failing.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fuzzy_surgical.lsp.client import LspClient
from fuzzy_surgical.lsp.diagnostics import Diagnostic, sorted_diagnostics
from fuzzy_surgical.lsp.linters import DEFAULT_LINT_TIMEOUT, run_linters
from fuzzy_surgical.lsp.registry import (
    ServerSpec,
    describe_server,
    missing_server_hint,
    server_for_path,
)

CONFIG_DIRECTORY = ".fuzzy-surgical"
CONFIG_NAME = "devbox.json"
CONFIG_ALTERNATE = "fuzzy-surgical.json"
MAX_TASKS = 32
MAX_COMPLETIONS_PER_REQUEST = 200
SERVER_RETRY_SECONDS = 10.0

Event = Tuple[Any, ...]


@dataclass
class DevBoxConfig:
    """Parsed dev box settings, with invalid entries skipped."""

    root: Path
    path: str = ""
    lsp_enabled: bool = True
    linters_enabled: bool = True
    lsp_timeout: float = 5.0
    lint_timeout: float = DEFAULT_LINT_TIMEOUT
    initialize_timeout: float = 12.0
    servers: Dict[str, Sequence[Sequence[str]]] = field(default_factory=dict)
    linters: Dict[str, Sequence[Sequence[str]]] = field(default_factory=dict)
    options: dict = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


def _command_list(value: Any) -> List[List[str]]:
    if not isinstance(value, list):
        return []
    commands: List[List[str]] = []
    for entry in value[:10]:
        if isinstance(entry, str) and entry.strip():
            commands.append([entry.strip()])
        elif isinstance(entry, list) and entry:
            parts = [str(part) for part in entry if isinstance(part, (str, int))]
            if parts and parts[0].strip():
                commands.append(parts)
    return commands


def _command_map(value: Any, warnings: List[str], label: str) -> Dict[str, List[List[str]]]:
    result: Dict[str, List[List[str]]] = {}
    if not isinstance(value, dict):
        if value is not None:
            warnings.append(f"{label} must be an object")
        return result
    for key, entry in list(value.items())[:100]:
        commands = _command_list(entry)
        if commands:
            result[str(key).lower()] = commands
        else:
            warnings.append(f"{label}.{key} must be a command or list of commands")
    return result


def _number(value: Any, default: float, low: float, high: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, parsed))


def _candidate_paths(root: Path, explicit: Optional[str]) -> List[Path]:
    if explicit:
        target = Path(explicit).expanduser()
        if target.is_dir():
            return [target / CONFIG_DIRECTORY / CONFIG_NAME, target / CONFIG_NAME]
        return [target]
    return [
        root / CONFIG_DIRECTORY / CONFIG_NAME,
        root / CONFIG_ALTERNATE,
    ]


def load_devbox_config(root: Path, explicit: Optional[str] = None) -> DevBoxConfig:
    """Load the first readable dev box JSON file, never raising."""
    config = DevBoxConfig(root=Path(root))
    for candidate in _candidate_paths(config.root, explicit):
        try:
            if not candidate.is_file():
                continue
            with open(candidate, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError) as exc:
            config.warnings.append(f"could not read {candidate.name}: {exc}")
            if explicit:
                break
            continue
        if not isinstance(raw, dict):
            config.warnings.append(f"{candidate.name} must contain a JSON object")
            continue
        config.path = str(candidate)
        _apply_config(config, raw)
        break
    if explicit and not config.path:
        config.warnings.append(f"no dev box config found at {explicit}")
    return config


def _apply_config(config: DevBoxConfig, raw: dict) -> None:
    lsp = raw.get("lsp")
    if isinstance(lsp, dict):
        if "enabled" in lsp:
            config.lsp_enabled = bool(lsp.get("enabled"))
        config.lsp_timeout = _number(lsp.get("timeout"), config.lsp_timeout, 0.5, 30.0)
        config.initialize_timeout = _number(
            lsp.get("initialize_timeout"), config.initialize_timeout, 1.0, 60.0
        )
        if isinstance(lsp.get("options"), dict):
            config.options = lsp["options"]
        config.servers = _command_map(lsp.get("servers"), config.warnings, "lsp.servers")
    elif lsp is not None:
        config.warnings.append("lsp must be an object")

    linters = raw.get("linters")
    if isinstance(linters, dict):
        if "enabled" in linters:
            config.linters_enabled = bool(linters.get("enabled"))
        config.lint_timeout = _number(linters.get("timeout"), config.lint_timeout, 0.5, 60.0)
        commands = linters.get("commands", linters.get("servers"))
        config.linters = _command_map(commands, config.warnings, "linters.commands")
    elif isinstance(linters, list):
        config.linters = _command_map({"*": linters}, config.warnings, "linters")
    elif linters is not None:
        config.warnings.append("linters must be an object")

    enabled = raw.get("enabled")
    if enabled is False:
        config.lsp_enabled = False
        config.linters_enabled = False


class DevBoxSession:
    """Owns the language server process and background lint runs."""

    def __init__(
        self,
        root: Path,
        config: Optional[DevBoxConfig] = None,
        enable_lsp: bool = True,
        enable_linters: bool = True,
    ) -> None:
        self.root = Path(root)
        self.config = config if config is not None else load_devbox_config(self.root)
        self.lsp_enabled = enable_lsp and self.config.lsp_enabled
        self.linters_enabled = enable_linters and self.config.linters_enabled

        self._tasks: "queue.Queue[Event]" = queue.Queue(maxsize=MAX_TASKS)
        self._lint_tasks: "queue.Queue[Event]" = queue.Queue(maxsize=MAX_TASKS)
        self._events: "queue.Queue[Event]" = queue.Queue()
        self._worker = threading.Thread(target=self._work, name="devbox", daemon=True)
        self._lint_worker = threading.Thread(
            target=self._lint_work, name="devbox-lint", daemon=True
        )
        self._state_lock = threading.Lock()
        self._completion_seq = 0
        self._diagnostic_generation = 0
        self._last_diagnostic_signature: tuple = ()
        self._lint_diagnostics: Dict[str, List[Diagnostic]] = {}
        self._document_path = ""
        self._document_text = ""
        self._sent_text = ""
        self._client: Optional[LspClient] = None
        self._server: Optional[ServerSpec] = None
        self._failed_command: Tuple[str, ...] = ()
        self._failed_at = 0.0
        self._status = "dev box idle"
        self._last_status_emitted = ""
        self._closed = False
        self._worker.start()
        self._lint_worker.start()
        if self.config.warnings:
            self._push(("status", self.config.warnings[0]))
        self._set_status(self._initial_status())
        self._push_diagnostics(force=True)

    def _initial_status(self) -> str:
        parts = []
        if self.lsp_enabled:
            parts.append("LSP on")
        if self.linters_enabled:
            parts.append("linters on")
        return "dev box: " + (", ".join(parts) if parts else "disabled")

    @property
    def status(self) -> str:
        with self._state_lock:
            return self._status

    @property
    def config_path(self) -> str:
        return self.config.path

    def advice_for(self, path: str) -> str:
        """Why completion cannot work for a file, or an empty string."""
        if not self.lsp_enabled:
            return "language server support is disabled"
        spec = server_for_path(path, self.config.servers)
        return missing_server_hint(spec)

    def open_document(self, path: str, text: str) -> None:
        """Load a file into the language server and lint it in the background."""
        absolute = os.path.abspath(path)
        with self._state_lock:
            self._document_path = absolute
            self._document_text = text
        self._submit(("open", absolute, text))
        self.request_lint(absolute)

    def request_completion(self, text: str, line: int, character: int) -> int:
        """Queue a completion request and return its sequence number."""
        if not self.lsp_enabled:
            return 0
        with self._state_lock:
            self._completion_seq += 1
            sequence = self._completion_seq
            path = self._document_path
        if path:
            self._submit(("complete", sequence, path, text, line, character))
        return sequence

    def document_saved(self, path: str, text: str) -> None:
        absolute = os.path.abspath(path)
        self._submit(("save", absolute, text))
        self.request_lint(absolute)

    def request_lint(self, path: str) -> None:
        if not self.linters_enabled or self._closed:
            return
        try:
            self._lint_tasks.put_nowait(("lint", os.path.abspath(path)))
        except queue.Full:
            self._set_status("lint queue is busy, request skipped")

    def close_document(self) -> None:
        self._submit(("close",))

    def poll(self) -> List[Event]:
        """Drain queued events and refresh language server diagnostics."""
        client = self._client
        if client is not None and not client.running and client.error:
            self._set_status(client.status)
        self._refresh_lsp_diagnostics()
        events: List[Event] = []
        while len(events) < 100:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                break
        return events

    def diagnostics_for(self, path: str) -> List[Diagnostic]:
        absolute = os.path.abspath(path)
        with self._state_lock:
            lint = list(self._lint_diagnostics.get(absolute, ()))
            client = self._client
            current = self._document_path
        lsp: List[Diagnostic] = []
        if client is not None and current == absolute:
            lsp = client.diagnostics_for(absolute)
        return sorted_diagnostics(lsp + lint)

    def close(self) -> None:
        """Stop the worker and the language server without raising."""
        if self._closed:
            return
        self._closed = True
        for pending in (self._tasks, self._lint_tasks):
            while True:
                try:
                    pending.get_nowait()
                except queue.Empty:
                    break
        try:
            self._tasks.put_nowait(("stop",))
        except queue.Full:
            pass
        try:
            self._lint_tasks.put_nowait(("stop",))
        except queue.Full:
            pass
        self._worker.join(timeout=3.0)
        self._lint_worker.join(timeout=1.0)
        if self._worker.is_alive() or self._lint_worker.is_alive():
            client = self._client
            if client is not None:
                client.stop()

    def _submit(self, task: Event) -> None:
        if self._closed:
            return
        try:
            self._tasks.put_nowait(task)
        except queue.Full:
            self._set_status("dev box is busy, request skipped")

    def _set_status(self, text: str) -> None:
        with self._state_lock:
            self._status = text
        if text != self._last_status_emitted:
            self._last_status_emitted = text
            self._push(("status", text))

    def _push(self, event: Event) -> None:
        try:
            self._events.put_nowait(event)
        except queue.Full:
            pass

    def _work(self) -> None:
        while True:
            task = self._tasks.get()
            kind = task[0]
            if kind == "stop":
                break
            try:
                if kind == "open":
                    self._handle_open(task[1], task[2])
                elif kind == "complete":
                    self._handle_completion(
                        int(task[1]), task[2], task[3], int(task[4]), int(task[5])
                    )
                elif kind == "save":
                    self._handle_save(task[1], task[2])
                elif kind == "close":
                    self._handle_close()
            except Exception as exc:  # background work must never kill the app
                self._set_status(f"dev box error: {exc}")
        self._stop_client()

    def _lint_work(self) -> None:
        while True:
            task = self._lint_tasks.get()
            if task[0] == "stop":
                break
            try:
                self._handle_lint(task[1])
            except Exception as exc:
                self._set_status(f"linter error: {exc}")

    def _ensure_client(self, path: str) -> Optional[LspClient]:
        if not self.lsp_enabled:
            return None
        spec = server_for_path(path, self.config.servers)
        if spec is None:
            self._set_status(describe_server(None))
            return None
        command = spec.available_command()
        if command is None:
            self._set_status(missing_server_hint(spec))
            return None
        current = self._client
        if current is not None and current.running and current.command == command:
            return current
        if current is not None:
            current.stop()
            self._client = None
        now = time.monotonic()
        if command == self._failed_command and now - self._failed_at < SERVER_RETRY_SECONDS:
            return None
        client = LspClient(
            command,
            self.root,
            spec.language_id,
            name=spec.name,
            timeout=self.config.lsp_timeout,
            initialize_timeout=max(self.config.initialize_timeout, spec.initialize_timeout),
            options=self.config.options,
        )
        self._set_status(f"starting {spec.name}")
        if not client.start():
            self._failed_command = command
            self._failed_at = time.monotonic()
            self._set_status(client.status)
            return None
        self._failed_command = ()
        self._failed_at = 0.0
        self._client = client
        self._server = spec
        self._sent_text = ""
        self._set_status(f"LSP {spec.name}")
        return client

    def _handle_open(self, path: str, text: str) -> None:
        with self._state_lock:
            self._document_path = path
            self._document_text = text
        if not self.lsp_enabled:
            return
        client = self._ensure_client(path)
        if client is None:
            return
        if client.open_document(path, text):
            self._sent_text = text
        else:
            self._set_status(client.status)

    def _handle_completion(
        self, sequence: int, path: str, text: str, line: int, character: int
    ) -> None:
        if not self.lsp_enabled:
            return
        client = self._ensure_client(path)
        if client is None:
            self._push(("completions", sequence, []))
            return
        with self._state_lock:
            current = self._document_path
        if current != path:
            if not client.open_document(path, text):
                self._push(("completions", sequence, []))
                return
            with self._state_lock:
                self._document_path = path
                self._document_text = text
            self._sent_text = text
        elif text != self._sent_text:
            client.change_document(text)
            with self._state_lock:
                self._document_text = text
            self._sent_text = text
        items = client.completion(line, character)[:MAX_COMPLETIONS_PER_REQUEST]
        self._push(("completions", sequence, items))
        self._refresh_lsp_diagnostics()

    def _handle_save(self, path: str, text: str) -> None:
        with self._state_lock:
            previous = self._document_path
            self._document_path = path
            self._document_text = text
        client = self._client
        if client is not None and client.running:
            if previous and previous != path:
                client.open_document(path, text)
            else:
                client.save_document(text)
            self._sent_text = text
        elif self.lsp_enabled:
            client = self._ensure_client(path)
            if client is not None and client.open_document(path, text):
                self._sent_text = text

    def _handle_lint(self, path: str) -> None:
        outcome = run_linters(path, self.config.linters, self.config.lint_timeout)
        with self._state_lock:
            if outcome.diagnostics:
                self._lint_diagnostics[path] = outcome.diagnostics
            else:
                self._lint_diagnostics.pop(path, None)
            while len(self._lint_diagnostics) > 200:
                self._lint_diagnostics.pop(next(iter(self._lint_diagnostics)))
        if outcome.errors:
            self._set_status(outcome.errors[0])
        self._push_diagnostics(force=True)

    def _handle_close(self) -> None:
        client = self._client
        if client is not None:
            client.close_document()
        with self._state_lock:
            self._document_path = ""

    def _refresh_lsp_diagnostics(self) -> None:
        client = self._client
        if client is None:
            return
        with self._state_lock:
            path = self._document_path
        if not path:
            return
        self._push_diagnostics()

    def _push_diagnostics(
        self, force: bool = False, extra: Optional[Sequence[Diagnostic]] = None
    ) -> None:
        with self._state_lock:
            path = self._document_path
            lint = list(self._lint_diagnostics.get(path, ()))
            client = self._client
        lsp = list(extra) if extra is not None else []
        if not lsp and client is not None and path:
            lsp = client.diagnostics_for(path)
        merged = sorted_diagnostics(lint + lsp)
        signature = tuple(
            (item.path, item.line, item.column, item.message, item.severity, item.source)
            for item in merged
        )
        with self._state_lock:
            if not force and signature == self._last_diagnostic_signature:
                return
            self._last_diagnostic_signature = signature
            self._diagnostic_generation += 1
            generation = self._diagnostic_generation
        self._push(("diagnostics", generation, merged))

    def _stop_client(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            client.stop()
        self._set_status("dev box stopped")
