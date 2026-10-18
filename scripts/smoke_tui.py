"""Drive the TUI in a pseudo terminal and check that it starts and searches.

This is a smoke test, not a replacement for the unit suite. It proves the
installed launcher can initialise curses, index a directory, filter results
and exit cleanly. Windows has no POSIX pseudo terminals, so the script exits
with status 2 there and the platform-specific harness skips it.

Usage:
    python scripts/smoke_tui.py --binary ~/.local/bin/fuzzy-surgical
"""

from __future__ import annotations

import argparse
import errno
import os
import re
import select
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional, Sequence

_CSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_OSC = re.compile(r"\x1b\][^\x07]*\x07")
_CHARSET = re.compile(r"\x1b[()][A-Z0-9]")
EXIT_CODE_SKIP = 2


def clean_output(data: bytes) -> str:
    """Remove terminal control sequences so markers can be matched."""
    text = data.decode("utf-8", "replace")
    text = _CSI.sub("", text)
    text = _OSC.sub("", text)
    text = _CHARSET.sub("", text)
    return text.replace("\r", "")


class Session:
    """A child process attached to a pseudo terminal."""

    def __init__(self, process: "subprocess.Popen[bytes]", master: int) -> None:
        self.process = process
        self.master = master
        self.buffer = bytearray()

    def _read_once(self) -> bool:
        try:
            chunk = os.read(self.master, 65536)
        except OSError as exc:
            if exc.errno in (errno.EIO, errno.EBADF):
                return False
            raise
        if not chunk:
            return False
        self.buffer.extend(chunk)
        return True

    def drain(self) -> None:
        """Consume whatever the terminal has already produced."""
        while True:
            readable, _, _ = select.select([self.master], [], [], 0)
            if not readable:
                return
            if not self._read_once():
                return

    def wait_for(self, marker: str, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if marker in clean_output(bytes(self.buffer)):
                return True
            readable, _, _ = select.select([self.master], [], [], 0.1)
            if readable and not self._read_once():
                break
            if self.process.poll() is not None and not readable:
                break
        return marker in clean_output(bytes(self.buffer))

    def send(self, data: bytes) -> None:
        os.write(self.master, data)

    def wait(self, timeout: float) -> Optional[int]:
        """Wait for exit while draining the terminal.

        ncurses restores the terminal with TCSADRAIN. If nobody reads the
        pseudo terminal, that ioctl blocks once the output buffer fills, so the
        process looks hung after a successful quit.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            code = self.process.poll()
            if code is not None:
                self.drain()
                return code
            readable, _, _ = select.select([self.master], [], [], 0.05)
            if readable:
                self._read_once()
        return None

    def text(self) -> str:
        return clean_output(bytes(self.buffer))

    def close(self) -> None:
        try:
            os.close(self.master)
        except OSError:
            pass
        if self.process.poll() is None:
            try:
                os.killpg(os.getpgid(self.process.pid), 15)
            except (OSError, ProcessLookupError):
                self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(self.process.pid), 9)
                except (OSError, ProcessLookupError):
                    self.process.kill()
                self.process.wait(timeout=5)


def _resolve_binary(binary: str) -> Optional[str]:
    if os.path.sep in binary or (os.altsep and os.altsep in binary):
        return binary if os.path.exists(binary) else None
    return shutil.which(binary)


def _write_fixture(root: Path) -> None:
    (root / "match.py").write_text(
        "def target():\n    return 'zebracorn'\n",
        encoding="utf-8",
    )
    (root / "unrelated.py").write_text("unrelated = True\n", encoding="utf-8")


def run(binary: str, timeout: float) -> int:
    if os.name != "posix":
        print("tui smoke skipped: POSIX pseudo terminals are not available")
        return EXIT_CODE_SKIP

    try:
        import fcntl
        import pty
        import struct
        import termios
    except ImportError:
        print("tui smoke skipped: pty, fcntl or termios is unavailable")
        return EXIT_CODE_SKIP

    resolved = _resolve_binary(binary)
    if resolved is None:
        print("tui smoke failed: could not find " + binary)
        return 1

    with tempfile.TemporaryDirectory(prefix="fuzzy-surgical-tui-") as temp:
        root = Path(temp)
        _write_fixture(root)
        master, slave = pty.openpty()
        fcntl.ioctl(
            slave,
            termios.TIOCSWINSZ,
            struct.pack("HHHH", 30, 100, 0, 0),
        )
        environment = dict(os.environ)
        environment.update(
            {
                "TERM": "xterm-256color",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
            }
        )
        command = [
            resolved,
            str(root),
            "--no-picker",
            "--no-lsp",
            "--no-linters",
            "--no-history",
        ]
        process = subprocess.Popen(
            command,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=environment,
            start_new_session=True,
            close_fds=True,
        )
        os.close(slave)
        session = Session(process, master)
        try:
            if not session.wait_for("fuzzy-surgical", timeout):
                raise RuntimeError("the TUI did not draw its header")
            if not session.wait_for("unrelated.py", timeout):
                raise RuntimeError("the initial result list did not appear")

            session.drain()
            session.buffer.clear()
            session.send(b"zebracorn")
            if not session.wait_for("zebracorn", timeout):
                raise RuntimeError("the typed query did not appear")
            filtered = session.text()
            if "unrelated.py" in filtered:
                raise RuntimeError("filtering left an unrelated file on screen")

            session.send(b"\x18")
            code = session.wait(10)
            if code is None:
                raise RuntimeError("Ctrl+X did not quit the TUI")
            if code != 0:
                raise RuntimeError(f"the TUI exited with status {code}")
            if "Traceback" in session.text():
                raise RuntimeError("the TUI printed a traceback")
        except RuntimeError as exc:
            captured = session.text()
            if captured.strip():
                print(captured[-4000:])
            print("tui smoke failed: " + str(exc))
            return 1
        finally:
            session.close()

    print("tui smoke passed with " + resolved)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--binary",
        default="fuzzy-surgical",
        help="Installed launcher or main.py command to start",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=25.0,
        help="Seconds to wait for each screen marker",
    )
    arguments = parser.parse_args(argv)
    return run(arguments.binary, arguments.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
