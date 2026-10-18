"""Write text files without hiding the update from other programs.

Editors and pagers such as Sublime, less and ``tail -f`` keep the original
inode open. Replacing the file with ``os.replace`` leaves those programs
looking at stale data, and replacing a symlink turns the link into a regular
file while the real target keeps the old contents. This module writes through
the resolved target and keeps the inode whenever permissions allow it, then
falls back to an atomic replace that preserves the original mode.
"""

from __future__ import annotations

import errno
import os
import stat
import tempfile
from typing import Optional


class FileWriteError(Exception):
    """Raised when a file cannot be written safely."""


def resolve_target(file_path: str) -> str:
    """Return the real path, following symlinks without raising."""
    try:
        return os.path.realpath(file_path)
    except OSError:
        return file_path


def write_text(file_path: str, text: str) -> str:
    """Write text to file_path and return the real path that was written.

    Raises FileWriteError with a short, user facing message on failure.
    """
    target = resolve_target(file_path)
    data = text.encode("utf-8", "surrogateescape")

    try:
        info = os.stat(target)
    except OSError as exc:
        raise FileWriteError(_message(exc)) from exc

    if not stat.S_ISREG(info.st_mode):
        raise FileWriteError("Target is not a regular file.")

    try:
        original: Optional[bytes] = _read_bytes(target)
    except OSError:
        original = None

    try:
        _write_in_place(target, data)
        return target
    except PermissionError:
        # Read-only files still change when the directory is writable.
        try:
            _replace_atomically(target, data, stat.S_IMODE(info.st_mode))
            return target
        except OSError as fallback_exc:
            _restore(target, original)
            raise FileWriteError(_message(fallback_exc)) from fallback_exc
    except OSError as exc:
        _restore(target, original)
        raise FileWriteError(_message(exc)) from exc


def _read_bytes(target: str) -> bytes:
    with open(target, "rb") as handle:
        return handle.read()


def _write_in_place(target: str, data: bytes) -> None:
    """Rewrite the existing inode so open file handles see the new text."""
    with open(target, "r+b") as handle:
        handle.seek(0)
        handle.write(data)
        handle.truncate()
        handle.flush()
        _fsync(handle.fileno())


def _replace_atomically(target: str, data: bytes, mode: int) -> None:
    directory = os.path.dirname(target) or "."
    temp_path = None
    handle = None
    try:
        descriptor, temp_path = tempfile.mkstemp(
            prefix=".fuzzy-surgical-",
            dir=directory,
        )
        handle = os.fdopen(descriptor, "wb")
        handle.write(data)
        handle.flush()
        _fsync(handle.fileno())
        handle.close()
        handle = None
        os.chmod(temp_path, mode)
        os.replace(temp_path, target)
        temp_path = None
        _fsync_directory(directory)
    finally:
        if handle is not None:
            handle.close()
        if temp_path is not None:
            try:
                os.remove(temp_path)
            except OSError:
                pass


def _restore(target: str, original: Optional[bytes]) -> None:
    if original is None:
        return
    try:
        with open(target, "r+b") as handle:
            handle.seek(0)
            handle.write(original)
            handle.truncate()
            handle.flush()
            _fsync(handle.fileno())
    except OSError:
        pass


def _fsync(descriptor: int) -> None:
    try:
        os.fsync(descriptor)
    except OSError:
        # Some network and virtual filesystems reject fsync.
        pass


def _fsync_directory(directory: str) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        _fsync(descriptor)
    finally:
        os.close(descriptor)


def _message(exc: OSError) -> str:
    if isinstance(exc, FileNotFoundError):
        return "File no longer exists."
    if isinstance(exc, PermissionError) or exc.errno in (errno.EACCES, errno.EPERM):
        return "Permission denied. Close the file or check its permissions."
    if exc.errno in (errno.EBUSY, errno.ETXTBSY):
        return "File is open in another program. Close it and try again."
    text = exc.strerror or str(exc)
    return "Write failed: " + text
