"""Write text files without hiding the update from other programs.

Editors and pagers such as Sublime, less and ``tail -f`` keep the original
inode open. Replacing the file with ``os.replace`` leaves those programs
looking at stale data, and replacing a symlink turns the link into a regular
file while the real target keeps the old contents. This module writes through
the resolved target and keeps the inode whenever permissions allow it, then
falls back to an atomic replace that preserves the original mode.

Every write is read back before it is reported as successful, and the
modification time is nudged forward when the filesystem stores it with a
coarse resolution. That keeps timestamp based reload checks working.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
import tempfile
import time
from dataclasses import dataclass
from typing import Optional


class FileWriteError(Exception):
    """Raised when a file cannot be written safely."""


@dataclass(frozen=True)
class WriteResult:
    """Facts about a successful write, used for the on screen notice."""

    path: str
    bytes_written: int
    inode_replaced: bool
    mtime_ns: int
    permission_adjusted: bool = False
    requested_path: str = ""
    digest: str = ""


def file_digest(file_path: str) -> str:
    """Return the sha256 of a file, or an empty string when it is unreadable."""
    try:
        with open(file_path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return ""


def resolve_target(file_path: str) -> str:
    """Return the real path, following symlinks without raising."""
    try:
        return os.path.realpath(file_path)
    except OSError:
        return file_path


def write_text(file_path: str, text: str) -> WriteResult:
    """Write text to file_path and return details about the write.

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

    previous_mtime_ns = info.st_mtime_ns
    inode_replaced = False
    permission_adjusted = False

    try:
        _write_in_place_with_retry(target, data)
    except PermissionError:
        if _write_with_mode_override(target, data, info.st_mode):
            permission_adjusted = True
        else:
            # Read-only files still change when the directory is writable.
            try:
                _replace_atomically(target, data, stat.S_IMODE(info.st_mode))
                inode_replaced = True
            except OSError as fallback_exc:
                _restore(target, original)
                raise FileWriteError(_message(fallback_exc)) from fallback_exc
    except OSError as exc:
        _restore(target, original)
        raise FileWriteError(_message(exc)) from exc

    if not _content_matches(target, data):
        raise FileWriteError(
            "The write could not be verified. Another program may be holding the file."
        )

    requested = os.path.abspath(file_path)
    if requested != target and not _content_matches(file_path, data):
        raise FileWriteError(
            "The opened path and the real file now differ. "
            "Save again after closing the other program."
        )

    mtime_ns = _stat_mtime(target)
    if mtime_ns <= previous_mtime_ns:
        mtime_ns = _bump_mtime(target, mtime_ns, previous_mtime_ns)
    _fsync_directory(os.path.dirname(target) or ".")

    return WriteResult(
        path=target,
        bytes_written=len(data),
        inode_replaced=inode_replaced,
        mtime_ns=mtime_ns,
        permission_adjusted=permission_adjusted,
        requested_path=requested,
        digest=hashlib.sha256(data).hexdigest(),
    )


def _read_bytes(target: str) -> bytes:
    with open(target, "rb") as handle:
        return handle.read()


def _content_matches(target: str, data: bytes) -> bool:
    try:
        return _read_bytes(target) == data
    except OSError:
        return False


def _write_in_place(target: str, data: bytes) -> None:
    """Rewrite the existing inode so open file handles see the new text."""
    with open(target, "r+b") as handle:
        handle.seek(0)
        handle.write(data)
        handle.truncate()
        handle.flush()
        _fsync(handle.fileno())


def _write_in_place_with_retry(target: str, data: bytes, attempts: int = 4) -> None:
    """Retry brief sharing violations, mostly seen on Windows."""
    for attempt in range(attempts):
        try:
            _write_in_place(target, data)
            return
        except PermissionError:
            if attempt == attempts - 1 or os.name != "nt":
                raise
            time.sleep(0.05)
        except OSError as exc:
            if exc.errno not in (errno.EBUSY, errno.ETXTBSY) or attempt == attempts - 1:
                raise
            time.sleep(0.05)


def _write_with_mode_override(target: str, data: bytes, mode: int) -> bool:
    """Temporarily add owner write permission, keeping the inode.

    This matters for read-only files, which would otherwise need an atomic
    replace and lose the inode that open editors are watching.
    """
    original_mode = stat.S_IMODE(mode)
    try:
        os.chmod(target, original_mode | stat.S_IWUSR)
    except OSError:
        return False
    try:
        _write_in_place_with_retry(target, data)
        return True
    except OSError:
        return False
    finally:
        try:
            os.chmod(target, original_mode)
        except OSError:
            pass


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


def _stat_mtime(target: str) -> int:
    try:
        return os.stat(target).st_mtime_ns
    except OSError:
        return 0


def _bump_mtime(target: str, current_ns: int, previous_ns: int) -> int:
    """Make the modification time strictly newer than before the write.

    Some filesystems store timestamps with one second resolution. Without a
    nudge a quick edit can keep the same timestamp, and editors that poll it
    never reload the file.
    """
    try:
        info = os.stat(target)
        atime_ns = info.st_atime_ns
    except OSError:
        return current_ns
    desired_ns = max(time.time_ns(), previous_ns + 1_000_000_000)
    try:
        os.utime(target, ns=(atime_ns, desired_ns))
        return desired_ns
    except OSError:
        return current_ns


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
