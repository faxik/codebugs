"""Filesystem output utilities for CLI handlers (CB-76).

Deliberately NOT part of ``fmt.py``: that module formats text for a stream, and
writing a file is not formatting. This one owns tempfile lifecycle, destination
classification, permission checks and atomic replacement. It imports nothing
from the package, so any module may use it.

THE CONTRACT, in one sentence: *the destination is replaced only by a file that
was written and closed successfully, and this helper never grants access the
plain ``open(path, "w")`` it replaces would have refused.*

What replacement CANNOT carry, stated here because an export is user-visible and
a docstring is the only contract a reader gets: ``os.replace`` installs a NEW
inode, so ownership, ACLs, xattrs and hard-link aliases of the previous file are
not preserved, and other hard links keep pointing at the old content. Atomic
*visibility* is also not crash *durability* — nothing here calls ``fsync``, so a
power cut may leave either version, though never a truncated one.
"""

from __future__ import annotations

import contextlib
import errno
import os
import stat
import tempfile
from collections.abc import Iterator
from typing import IO

__all__ = ["atomic_write"]

# Attributable and hidden, so a temp leaked by an uncatchable signal (SIGKILL)
# can be recognised and swept. Nothing here can clean that case up; see below.
_TEMP_PREFIX = ".codebugs-export-"

# `open()` authorizes with EFFECTIVE credentials; `os.access` defaults to REAL
# ones. Using the default would falsely refuse a write the old code accepted
# under setuid/capabilities — and a false refusal is worse than the bug being
# fixed here.
_ACCESS_KW: dict[str, bool] = (
    {"effective_ids": True} if os.access in os.supports_effective_ids else {}
)


def _typed(exc: OSError, path: str) -> OSError:
    """Re-bind an OSError to the path the CALLER typed.

    Without this, a failure reports either a `.codebugs-export-XXXX` temp the
    user never named or a realpath-canonicalised absolute path — a diagnostic
    whose coordinate system does not travel with it (CB-49). Constructing
    OSError with (errno, strerror, filename) returns the right subclass, so
    FileNotFoundError stays FileNotFoundError.
    """
    if exc.errno is None:
        return exc
    return OSError(exc.errno, exc.strerror, path)


def _discard(path: str) -> None:
    """Remove a temp without ever masking the exception being handled.

    Same rule as db.txn's guarded ROLLBACK: cleanup that raises would replace
    the real error with a less useful one.
    """
    with contextlib.suppress(OSError):
        os.unlink(path)


def _held_open_inodes() -> set[tuple[int, int]]:
    """(st_dev, st_ino) for every descriptor this process holds open.

    This is what makes ``export-csv /dev/stdout > out.csv`` keep working, and a
    node-kind check alone CANNOT substitute for it: realpath('/dev/stdout')
    resolves to the redirect target, which is an ordinary REGULAR file
    (measured), so the destination looks perfectly replaceable. Replacing it
    would swap the inode out from under the shell's still-open descriptor and
    send every later write to an unlinked file.

    Linux exposes the full set through /proc/self/fd. Elsewhere we fall back to
    the three standard descriptors, which still covers /dev/stdout, /dev/stderr
    and /dev/fd/{0,1,2} — the forms anyone actually types.
    """
    try:
        fds = [int(name) for name in os.listdir("/proc/self/fd") if name.isdigit()]
    except OSError:
        fds = [0, 1, 2]
    held: set[tuple[int, int]] = set()
    for fd in fds:
        try:
            st = os.fstat(fd)
        except OSError:
            continue
        held.add((st.st_dev, st.st_ino))
    return held


def _default_file_mode() -> int:
    """0o666 masked by the process umask — what `open(path, "w")` would create.

    `mkstemp` creates 0600, so without this a brand-new export would be private
    where it used to be group/world readable. Reading the umask requires setting
    it, which is process-GLOBAL: safe here only because CLI handlers are
    single-threaded and this window spans two syscalls. A threaded caller could
    observe the cleared umask.
    """
    old = os.umask(0)
    os.umask(old)
    return 0o666 & ~old


@contextlib.contextmanager
def atomic_write(path: str, *, newline: str | None = None) -> Iterator[IO[str]]:
    """Yield a writable text handle whose content replaces `path` only on success.

    `open(path, "w")` truncates the destination BEFORE the first byte is
    written, so any write failure destroys the previous file (CB-76). Here the
    payload goes to a temp beside the destination and is moved into place only
    after it has been written AND closed.

    `newline` is forwarded to the text wrapper. `encoding` is deliberately NOT
    accepted or set: `open()` and `os.fdopen()` both take the locale default,
    and pinning one here would silently desynchronise export from import on a
    non-UTF-8 host.
    """
    # FIRST, so `os.path.dirname` is never "" for a bare filename like the
    # default `codebugs_export.csv`; mkstemp(dir="") fails. Ordering is
    # load-bearing.
    resolved = os.path.realpath(path)

    try:
        st: os.stat_result | None = os.stat(resolved)
    except FileNotFoundError:
        st = None  # a genuinely absent destination: we create it
    except OSError as exc:
        # ELOOP from a symlink cycle, EACCES on a path component, ENOTDIR...
        # Only FileNotFoundError means "missing"; classifying these as missing
        # would replace the very symlink we failed to resolve.
        raise _typed(exc, path) from exc

    in_place = False
    keep_mode: int | None = None

    if st is None:
        pass  # create
    elif stat.S_ISDIR(st.st_mode):
        raise IsADirectoryError(errno.EISDIR, os.strerror(errno.EISDIR), path)
    elif stat.S_ISFIFO(st.st_mode) or stat.S_ISCHR(st.st_mode):
        # Stream-like. Writing THROUGH the node is the entire point and there is
        # no previous content to protect, whereas os.replace would delete the
        # node and leave a regular file in its place.
        in_place = True
    elif stat.S_ISREG(st.st_mode):
        if (st.st_dev, st.st_ino) in _held_open_inodes():
            in_place = True  # an fd alias — see _held_open_inodes
        else:
            keep_mode = stat.S_IMODE(st.st_mode)
    else:
        # Block devices and sockets. A partial direct write to a block device
        # corrupts persistent bytes, and `open(sock, "w")` already fails today;
        # refusing both is a deliberate narrowing of what an export accepts.
        raise OSError(errno.EINVAL, "unsupported destination type", path)

    if in_place:
        # Today's exact semantics, including its truncation window. Open `path`
        # rather than `resolved` so the symlink is followed by open() itself.
        try:
            handle = open(path, "w", newline=newline)  # noqa: SIM115
        except OSError as exc:
            raise _typed(exc, path) from exc
        with handle:
            yield handle
        return

    # `open(path, "w")` needs write permission on the FILE; os.replace needs it
    # on the DIRECTORY. Without this check a read-only file inside a writable
    # directory — refused today — would be silently overwritten.
    if keep_mode is not None and not os.access(resolved, os.W_OK, **_ACCESS_KW):
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), path)

    try:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(resolved), prefix=_TEMP_PREFIX)
    except OSError as exc:
        # Includes the writable-file-inside-an-unwritable-directory case, which
        # exported before CB-76 and now fails cleanly: atomic replacement is
        # impossible there, and an errno-keyed fallback to a direct write cannot
        # distinguish that from ENOSPC/EDQUOT — the very conditions where the
        # following write fails and the old file would be lost.
        raise _typed(exc, path) from exc

    try:
        handle = os.fdopen(fd, "w", newline=newline)
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(fd)
        _discard(tmp)
        raise

    try:
        with handle:
            yield handle
        # Closed by the `with`, so a flush/close ENOSPC — where quota failures
        # usually surface — has already raised and we never reach the replace.
    except BaseException:
        _discard(tmp)
        raise

    try:
        os.chmod(tmp, keep_mode if keep_mode is not None else _default_file_mode())
        os.replace(tmp, resolved)
    except OSError as exc:
        _discard(tmp)
        raise _typed(exc, path) from exc
