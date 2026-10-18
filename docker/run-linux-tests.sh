#!/usr/bin/env bash
# Executed inside docker/Dockerfile.linux as the unprivileged tester user.
# Proves the project works from a clean machine: suite, installer, launcher,
# headless flows and the interactive TUI through a pseudo terminal.
set -euo pipefail

cd /src

TEST_VENV=/tmp/fuzzy-surgical-test-venv
LAUNCHER="$HOME/.local/bin/fuzzy-surgical"
export TERM=xterm-256color
export SHELL=/bin/bash

step() {
    printf '\n== %s ==\n' "$1"
}

step "Python and PowerShell"
python3 --version
pwsh -NoLogo -NoProfile -Command '$PSVersionTable.PSVersion.ToString()'

step "Test environment"
python3 -m venv "$TEST_VENV"
"$TEST_VENV/bin/python" -m pip install --quiet --upgrade pip
"$TEST_VENV/bin/python" -m pip install --quiet -r requirements-dev.txt

step "Unit tests"
"$TEST_VENV/bin/python" -m pytest -q tests

step "Lint and generated documentation"
"$TEST_VENV/bin/python" -m flake8 main.py fuzzy_surgical tests scripts
"$TEST_VENV/bin/python" scripts/build_readme.py --check
bash -n install.sh

step "PowerShell scripts parse and target Windows PowerShell 5.1"
pwsh -NoLogo -NoProfile -File docker/check-install-ps1.ps1

step "POSIX installer"
./install.sh
test -x "$LAUNCHER"
found="$(bash -ic 'command -v fuzzy-surgical' 2>/dev/null || true)"
if [ "$found" != "$LAUNCHER" ]; then
    printf 'launcher not on PATH in an interactive shell: %s\n' "$found" >&2
    exit 1
fi
"$LAUNCHER" --version

step "Headless end-to-end"
"$TEST_VENV/bin/python" scripts/headless_smoke.py

step "Interactive TUI in a pseudo terminal"
"$TEST_VENV/bin/python" scripts/smoke_tui.py --binary "$LAUNCHER"

step "Uninstall"
./install.sh --uninstall
if [ -e "$LAUNCHER" ]; then
    printf 'launcher still exists after uninstall\n' >&2
    exit 1
fi
if grep -Fq '# >>> fuzzy-surgical >>>' "$HOME/.bashrc"; then
    printf 'PATH block still exists after uninstall\n' >&2
    exit 1
fi

step "Reinstall for an interactive session"
./install.sh
"$LAUNCHER" --version

printf '\nlinux container harness passed\n'
