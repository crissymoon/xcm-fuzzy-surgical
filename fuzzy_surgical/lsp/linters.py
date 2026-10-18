"""Bounded linter runner used by the dev box.

Every linter is an explicit argv list, never a shell string. A missing tool or
a hung process produces a status message instead of an exception, so a save is
never blocked by diagnostics.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from fuzzy_surgical.lsp.diagnostics import Diagnostic
from fuzzy_surgical.lsp.registry import env_without_color

MAX_LINT_OUTPUT = 200_000
DEFAULT_LINT_TIMEOUT = 8.0
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
GCC_LINE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+):(?:(?P<column>\d+):)?\s*(?P<message>.+)$"
)
PHP_LINE = re.compile(
    r"(?:PHP )?(?:Parse|Fatal) error:\s*(?P<message>.*?) in (?P<path>.+?) "
    r"on line (?P<line>\d+)"
)
XML_CODE = re.compile(r"\[([A-Za-z0-9_./-]+)\]\s*$")
SHORT_CODE = re.compile(r"\b([EFW]\d{2,4})\b")

Command = Tuple[str, ...]


@dataclass(frozen=True)
class LinterSpec:
    """One linter and the file types it understands."""

    name: str
    extensions: Tuple[str, ...]
    commands: Tuple[Command, ...]

    def available_command(self) -> Optional[Command]:
        for command in self.commands:
            if command and shutil.which(command[0]):
                return command
        return None


DEFAULT_LINTERS: Tuple[LinterSpec, ...] = (
    LinterSpec(
        "ruff",
        (".py", ".pyi"),
        (("ruff", "check", "--no-cache", "--output-format=concise", "{file}"),),
    ),
    LinterSpec("flake8", (".py", ".pyi"), (("flake8", "{file}"),)),
    LinterSpec("pyflakes", (".py", ".pyi"), (("pyflakes", "{file}"),)),
    LinterSpec(
        "eslint",
        (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"),
        (("eslint", "--no-color", "--format", "unix", "{file}"),),
    ),
    LinterSpec(
        "shellcheck",
        (".sh", ".bash", ".zsh"),
        (("shellcheck", "--format=gcc", "{file}"),),
    ),
    LinterSpec("ruby", (".rb",), (("ruby", "-c", "{file}"),)),
    LinterSpec("php", (".php",), (("php", "-l", "{file}"),)),
    LinterSpec("gofmt", (".go",), (("gofmt", "-e", "{file}"),)),
)


@dataclass
class LintOutcome:
    """Diagnostics plus non-fatal problems with running the tools."""

    diagnostics: List[Diagnostic] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


def _candidate_keys(path: str) -> List[str]:
    extension = Path(path).suffix.lower()
    keys = [extension]
    if extension:
        keys.append(extension.lstrip("."))
    return keys


def linters_for(path: str) -> Tuple[LinterSpec, ...]:
    extension = Path(path).suffix.lower()
    return tuple(spec for spec in DEFAULT_LINTERS if extension in spec.extensions)


def _severity_for(message: str, code: str = "") -> str:
    lowered = message.lower()
    if lowered.startswith("warning") or ": warning" in lowered:
        return "warning"
    if lowered.startswith("note") or lowered.startswith("info") or ": note" in lowered:
        return "info"
    if code.startswith("W"):
        return "warning"
    if code.startswith(("E", "F")):
        return "error"
    return "error"


def _code_for(message: str) -> str:
    match = XML_CODE.search(message)
    if match:
        return match.group(1).split("/")[-1]
    match = SHORT_CODE.search(message)
    return match.group(1) if match else ""


def _clean_message(message: str) -> str:
    message = ANSI_ESCAPE.sub("", message).strip()
    for prefix in ("error:", "warning:", "note:", "info:"):
        if message.lower().startswith(prefix):
            message = message[len(prefix) :].strip()
            break
    return message


def _same_file(reported: str, target: str) -> bool:
    reported = reported.strip()
    if reported in ("-", "<stdin>", ""):
        return True
    reported_path = os.path.abspath(reported)
    target_path = os.path.abspath(target)
    if reported_path == target_path:
        return True
    return os.path.basename(reported_path) == os.path.basename(target_path)


def parse_lint_output(text: str, target: str, source: str) -> List[Diagnostic]:
    """Parse common compiler and linter line formats."""
    diagnostics: List[Diagnostic] = []
    seen = set()
    for raw_line in text.splitlines()[:5000]:
        line = ANSI_ESCAPE.sub("", raw_line).strip()
        if not line or line.startswith(("Traceback", "File \"", "  ")):
            continue
        php = PHP_LINE.search(line)
        if php:
            raw_message = php.group("message")
            message = _clean_message(raw_message)
            code = _code_for(message)
            key = (message, int(php.group("line")))
            if key not in seen:
                seen.add(key)
                diagnostics.append(
                    Diagnostic(
                        path=target,
                        line=max(1, int(php.group("line"))),
                        column=0,
                        message=message,
                        severity=_severity_for(raw_message, code),
                        source=source,
                        code=code,
                    )
                )
            continue

        match = GCC_LINE.match(line)
        if match is None or not _same_file(match.group("path"), target):
            continue
        raw_message = match.group("message")
        message = _clean_message(raw_message)
        if not message:
            continue
        try:
            line_number = max(1, int(match.group("line")))
            column = max(0, int(match.group("column") or 0))
        except ValueError:
            continue
        key = (line_number, column, message)
        if key in seen:
            continue
        seen.add(key)
        code = _code_for(message)
        diagnostics.append(
            Diagnostic(
                path=target,
                line=line_number,
                column=column,
                message=message,
                severity=_severity_for(raw_message, code),
                source=source,
                code=code,
            )
        )
        if len(diagnostics) >= 500:
            break
    return diagnostics


def _command_for(target: str, template: Sequence[str]) -> List[str]:
    command = [part.replace("{file}", target) for part in template]
    if not any("{file}" in part for part in template):
        command.append(target)
    return command


def _run(command: Sequence[str], cwd: str, timeout: float) -> Tuple[Optional[int], str, str]:
    """Run one tool with output and time limits, never raising."""
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env_without_color(),
            creationflags=creationflags,
        )
    except (OSError, ValueError) as exc:
        return None, "", str(exc)
    try:
        output, _ = process.communicate(timeout=max(0.5, timeout))
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            output, _ = process.communicate(timeout=2.0)
        except (OSError, subprocess.TimeoutExpired):
            output = b""
        return None, output.decode("utf-8", "replace")[:MAX_LINT_OUTPUT], "timed out"
    return (
        process.returncode,
        output.decode("utf-8", "replace")[:MAX_LINT_OUTPUT],
        "",
    )


def _custom_commands(path: str, custom: Dict[str, Sequence[Sequence[str]]]) -> List[Sequence[str]]:
    for key in _candidate_keys(path) + ["*"]:
        if key in custom:
            return [tuple(str(part) for part in command) for command in custom[key]]
    return []


def run_linters(
    path: str,
    custom: Optional[Dict[str, Sequence[Sequence[str]]]] = None,
    timeout: float = DEFAULT_LINT_TIMEOUT,
) -> LintOutcome:
    """Run the configured linters for one file and return diagnostics."""
    outcome = LintOutcome()
    if not os.path.isfile(path):
        outcome.errors.append("file is not readable")
        return outcome

    templates: List[Tuple[str, Sequence[str]]] = []
    overrides = custom or {}
    configured = _custom_commands(path, overrides)
    if configured:
        templates = [("config", command) for command in configured]
    else:
        for spec in linters_for(path):
            command = spec.available_command()
            if command is not None:
                templates = [(spec.name, command)]
                break

    if not templates:
        return outcome

    cwd = os.path.dirname(os.path.abspath(path)) or "."
    for name, template in templates:
        command = _command_for(path, template)
        if not command or shutil.which(command[0]) is None:
            outcome.errors.append(f"{command[0] if command else 'linter'} not installed")
            continue
        returncode, output, problem = _run(command, cwd, timeout)
        if problem == "timed out":
            outcome.errors.append(f"{name} timed out after {timeout:.0f}s")
            continue
        if problem:
            outcome.errors.append(f"{name}: {problem}")
            continue
        parsed = parse_lint_output(output, path, name)
        outcome.diagnostics.extend(parsed)
        if returncode not in (0, None) and not parsed and output.strip():
            last = output.strip().splitlines()[-1][:300]
            outcome.errors.append(f"{name} exited {returncode}: {_clean_message(last)}")
    return outcome
