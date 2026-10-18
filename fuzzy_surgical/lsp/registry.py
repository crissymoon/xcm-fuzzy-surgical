"""Known language servers and detection by file extension.

Nothing here starts a process. Detection only resolves a command template, so a
missing server never breaks the editor. Commands may use ``{python}`` for the
interpreter running this program, or contain a ``~`` path or a glob.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from fuzzy_surgical.lsp.commands import resolve

Command = Tuple[str, ...]


@dataclass(frozen=True)
class ServerSpec:
    """One language server and the files it can serve."""

    name: str
    language_id: str
    extensions: Tuple[str, ...]
    commands: Tuple[Command, ...]
    hint: str = ""
    initialize_timeout: float = 0.0
    request_timeout: float = 0.0

    def available_command(self) -> Optional[Tuple[str, ...]]:
        for command in self.commands:
            resolved = resolve(command)
            if resolved:
                return tuple(resolved)
        return None


DEFAULT_SERVERS: Tuple[ServerSpec, ...] = (
    ServerSpec(
        "pylsp",
        "python",
        (".py", ".pyi"),
        (
            ("{python}", "-m", "pylsp"),
            ("pylsp",),
            ("{python}", "-m", "pyright.langserver"),
            ("pyright-langserver", "--stdio"),
            ("basedpyright-langserver", "--stdio"),
        ),
        hint="pip install python-lsp-server",
    ),
    ServerSpec(
        "typescript",
        "typescript",
        (".ts", ".tsx", ".mts", ".cts"),
        (("typescript-language-server", "--stdio"),),
        hint="npm install -g typescript-language-server typescript",
    ),
    ServerSpec(
        "javascript",
        "javascript",
        (".js", ".jsx", ".mjs", ".cjs"),
        (("typescript-language-server", "--stdio"),),
        hint="npm install -g typescript-language-server typescript",
    ),
    ServerSpec(
        "gopls",
        "go",
        (".go",),
        (("gopls", "serve"),),
        hint="go install golang.org/x/tools/gopls@latest",
    ),
    ServerSpec(
        "rust-analyzer",
        "rust",
        (".rs",),
        (("rust-analyzer",),),
        hint="rustup component add rust-analyzer",
    ),
    ServerSpec(
        "jdtls",
        "java",
        (".java",),
        (
            ("jdtls",),
            ("{cache}/lsp/jdtls/bin/jdtls",),
            ("/opt/homebrew/bin/jdtls",),
            ("/usr/local/bin/jdtls",),
            ("~/.local/share/nvim/mason/bin/jdtls",),
            ("~/.vscode/extensions/redhat.java-*/jdtls/bin/jdtls",),
            ("~/.vscode-server/extensions/redhat.java-*/jdtls/bin/jdtls",),
        ),
        hint="brew install jdtls",
        initialize_timeout=45.0,
        request_timeout=20.0,
    ),
    ServerSpec(
        "clangd",
        "cpp",
        (".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"),
        (("clangd",),),
        hint="install clangd (xcode-select --install or your package manager)",
    ),
    ServerSpec(
        "clangd",
        "c",
        (".c", ".h"),
        (("clangd",),),
        hint="install clangd (xcode-select --install or your package manager)",
    ),
    ServerSpec(
        "bash-language-server",
        "shellscript",
        (".sh", ".bash", ".zsh"),
        (("bash-language-server", "start"),),
        hint="npm install -g bash-language-server",
    ),
    ServerSpec(
        "lua-language-server",
        "lua",
        (".lua",),
        (("lua-language-server",),),
        hint="install lua-language-server",
    ),
    ServerSpec(
        "solargraph",
        "ruby",
        (".rb",),
        (("solargraph", "stdio"),),
        hint="gem install solargraph",
    ),
    ServerSpec(
        "intelephense",
        "php",
        (".php",),
        (("intelephense", "--stdio"),),
        hint="npm install -g intelephense",
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
    ".java": "java",
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
            return ServerSpec(
                default.name,
                default.language_id,
                default.extensions,
                commands,
                hint=default.hint,
                initialize_timeout=default.initialize_timeout,
            )
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
        if spec.hint:
            return f"{spec.name} not installed ({spec.hint})"
        return f"{spec.name} not installed"
    return f"LSP {spec.name}"


def missing_server_hint(spec: Optional[ServerSpec]) -> str:
    """Install advice for a spec that cannot run, or an empty string."""
    if spec is None:
        return "No language server is configured for this file type."
    if spec.available_command() is not None:
        return ""
    if spec.hint:
        return f"{spec.name} is not installed. Install it with: {spec.hint}"
    return f"{spec.name} is not installed."


def available_specs() -> List[ServerSpec]:
    """Every built-in spec, one per language, for diagnostics."""
    seen = set()
    specs: List[ServerSpec] = []
    for spec in DEFAULT_SERVERS:
        if spec.language_id in seen:
            continue
        seen.add(spec.language_id)
        specs.append(spec)
    return specs


def env_without_color() -> Dict[str, str]:
    """Environment for child tools so diagnostics are plain text."""
    env = dict(os.environ)
    env["NO_COLOR"] = "1"
    env["FORCE_COLOR"] = "0"
    env["TERM"] = "dumb"
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env
