"""Known language servers and detection by file extension.

Nothing here starts a process. Detection only checks whether the configured
executable is on PATH, so a missing server never breaks the editor.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

Command = Tuple[str, ...]


@dataclass(frozen=True)
class ServerSpec:
    """One language server and the files it can serve."""

    name: str
    language_id: str
    extensions: Tuple[str, ...]
    commands: Tuple[Command, ...]

    def available_command(self) -> Optional[Command]:
        for command in self.commands:
            if command and shutil.which(command[0]):
                return command
        return None


DEFAULT_SERVERS: Tuple[ServerSpec, ...] = (
    ServerSpec(
        "pylsp",
        "python",
        (".py", ".pyi"),
        (("pylsp",), ("pyright-langserver", "--stdio"), ("basedpyright-langserver", "--stdio")),
    ),
    ServerSpec(
        "typescript",
        "typescript",
        (".ts", ".tsx", ".mts", ".cts"),
        (("typescript-language-server", "--stdio"),),
    ),
    ServerSpec(
        "javascript",
        "javascript",
        (".js", ".jsx", ".mjs", ".cjs"),
        (("typescript-language-server", "--stdio"),),
    ),
    ServerSpec("gopls", "go", (".go",), (("gopls", "serve"),)),
    ServerSpec("rust-analyzer", "rust", (".rs",), (("rust-analyzer",),)),
    ServerSpec(
        "clangd",
        "cpp",
        (".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"),
        (("clangd",),),
    ),
    ServerSpec("clangd", "c", (".c", ".h"), (("clangd",),)),
    ServerSpec(
        "bash-language-server",
        "shellscript",
        (".sh", ".bash", ".zsh"),
        (("bash-language-server", "start"),),
    ),
    ServerSpec(
        "lua-language-server",
        "lua",
        (".lua",),
        (("lua-language-server",),),
    ),
    ServerSpec("solargraph", "ruby", (".rb",), (("solargraph", "stdio"),)),
    ServerSpec(
        "intelephense",
        "php",
        (".php",),
        (("intelephense", "--stdio"),),
    ),
)

LANGUAGE_BY_EXTENSION: Dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
    ".sh": "shellscript",
    ".bash": "shellscript",
    ".zsh": "shellscript",
    ".lua": "lua",
    ".rb": "ruby",
    ".php": "php",
}


def language_id_for(path: str) -> str:
    extension = Path(path).suffix.lower()
    return LANGUAGE_BY_EXTENSION.get(extension, "plaintext")


def _merge_commands(
    default: ServerSpec, overrides: Dict[str, Sequence[Sequence[str]]]
) -> ServerSpec:
    keys = [Path(default.extensions[0]).suffix.lower()] if default.extensions else []
    keys.append(default.language_id)
    keys.extend(extension.lower() for extension in default.extensions)
    keys.append("*")
    for key in keys:
        if key in overrides and overrides[key]:
            commands = tuple(tuple(str(part) for part in command) for command in overrides[key])
            return ServerSpec(default.name, default.language_id, default.extensions, commands)
    return default


def server_for_path(
    path: str,
    overrides: Optional[Dict[str, Sequence[Sequence[str]]]] = None,
) -> Optional[ServerSpec]:
    """Return the best server spec for a file, honouring devbox overrides."""
    extension = Path(path).suffix.lower()
    language = LANGUAGE_BY_EXTENSION.get(extension, "plaintext")
    overrides = overrides or {}

    candidates = [spec for spec in DEFAULT_SERVERS if extension in spec.extensions]
    if candidates:
        spec = candidates[0]
        return _merge_commands(spec, overrides)

    custom_key = extension or language
    commands = overrides.get(custom_key) or overrides.get(language) or overrides.get("*")
    if commands:
        return ServerSpec(
            "custom",
            language,
            (extension,),
            tuple(tuple(str(part) for part in command) for command in commands),
        )
    return None


def describe_server(spec: Optional[ServerSpec]) -> str:
    """Short status text for the editor header."""
    if spec is None:
        return "no LSP for this file"
    command = spec.available_command()
    if command is None:
        return f"{spec.name} not installed"
    return f"LSP {spec.name}"


def env_without_color() -> Dict[str, str]:
    """Environment for child tools so diagnostics are plain text."""
    env = dict(os.environ)
    env["NO_COLOR"] = "1"
    env["FORCE_COLOR"] = "0"
    env["TERM"] = "dumb"
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env
