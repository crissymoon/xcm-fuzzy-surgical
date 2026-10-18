#!/usr/bin/env bash
# Builds and runs the fuzzy-surgical container harness.
#
#   docker/run-tests.sh linux          full Linux suite, installer and PTY TUI
#   docker/run-tests.sh linux-shell    interactive shell in the Linux image
#   docker/run-tests.sh windows        Windows Server Core harness
#   docker/run-tests.sh windows-shell  interactive PowerShell in that image
#
# The Windows image requires a Windows host running Docker in Windows container
# mode. Windows containers cannot run on macOS or Linux.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
TARGET="${1:-linux}"
IMAGE="${FUZZY_SURGICAL_TEST_IMAGE:-fuzzy-surgical-test}"

require_windows_host() {
    local os_type
    os_type="$(docker info --format '{{.OSType}}' 2>/dev/null || true)"
    if [ "$os_type" != "windows" ]; then
        cat >&2 <<'MESSAGE'
The Windows Server Core image needs a Windows host with Docker in Windows
container mode. Windows containers cannot run on macOS or Linux.

On Windows PowerShell run:
  docker\run-tests.ps1

Or from Git Bash on Windows:
  docker/run-tests.sh windows
MESSAGE
        exit 2
    fi
}

case "$TARGET" in
    linux)
        docker build -f docker/Dockerfile.linux -t "$IMAGE:linux" .
        exec docker run --rm "$IMAGE:linux"
        ;;
    linux-shell)
        docker build -f docker/Dockerfile.linux -t "$IMAGE:linux" .
        exec docker run --rm -it "$IMAGE:linux" bash
        ;;
    windows)
        require_windows_host
        docker build -f docker/Dockerfile.windows -t "$IMAGE:windows" .
        exec docker run --rm "$IMAGE:windows"
        ;;
    windows-shell)
        require_windows_host
        docker build -f docker/Dockerfile.windows -t "$IMAGE:windows" .
        exec docker run --rm -it "$IMAGE:windows" powershell -NoLogo -NoProfile
        ;;
    *)
        printf 'Unknown target: %s\n' "$TARGET" >&2
        printf 'Use linux, linux-shell, windows or windows-shell.\n' >&2
        exit 2
        ;;
esac
