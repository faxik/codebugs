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
import dataclasses
import errno
import os
import re
import stat
import sys
import tempfile
from collections.abc import Iterator
from typing import IO

__all__ = ["atomic_write", "diagnostic_stream", "WriteResult"]

# A path that resolves INTO a descriptor directory is an fd alias, not a file.
# `realpath("/dev/stdout")` returns the redirect target when stdout is a FILE,
# but `/proc/<pid>/fd/pipe:[N]` when it is a PIPE — a name that does not exist,
# so `os.stat` raises FileNotFoundError and a stat-based classifier reads it as
# "a new file to create" and then tries to mkstemp inside /proc. Measured.
_FD_DIR = re.compile(r"\A(/proc/(?:self|thread-self|\d+)(?:/task/\d+)?/fd|/dev/fd)\Z")

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


def _open_fd_identities() -> dict[int, tuple[int, int]]:
    """fd -> (st_dev, st_ino) for every descriptor this process holds open.

    Same enumeration `_held_open_inodes` has always used, kept BY FD NUMBER
    instead of collapsed into a set (CB-194): deciding whether the write must
    go IN PLACE only needs to know whether *some* descriptor aliases the
    destination, but steering a post-write diagnostic (`diagnostic_stream`)
    needs to know WHICH one — fd 1, fd 2, or both — so it can pick a channel
    that is not the destination.

    Linux exposes the full set through /proc/self/fd. Elsewhere we fall back to
    the three standard descriptors, which still covers /dev/stdout, /dev/stderr
    and /dev/fd/{0,1,2} — the forms anyone actually types.
    """
    try:
        fds = [int(name) for name in os.listdir("/proc/self/fd") if name.isdigit()]
    except OSError:
        fds = [0, 1, 2]
    identities: dict[int, tuple[int, int]] = {}
    for fd in fds:
        try:
            st = os.fstat(fd)
        except OSError:
            continue
        identities[fd] = (st.st_dev, st.st_ino)
    return identities


def _held_open_inodes() -> set[tuple[int, int]]:
    """(st_dev, st_ino) for every descriptor this process holds open.

    This is what makes ``export-csv /dev/stdout > out.csv`` keep working, and a
    node-kind check alone CANNOT substitute for it: realpath('/dev/stdout')
    resolves to the redirect target, which is an ordinary REGULAR file
    (measured), so the destination looks perfectly replaceable. Replacing it
    would swap the inode out from under the shell's still-open descriptor and
    send every later write to an unlinked file.
    """
    return set(_open_fd_identities().values())


def _matched_fds_for(
    dest_id: tuple[int, int] | None, open_fds: dict[int, tuple[int, int]]
) -> frozenset[int]:
    """Which of THIS PROCESS's open descriptors ARE the destination (CB-194).

    `dest_id is None` means the destination's own identity could not be
    determined even on the in-place path where a collision is possible (see
    `atomic_write`'s pipe fallback). The honest answer there is "do not
    know", and CB-194 §4 resolves unknown in favour of SILENCE: widening to
    every channel `diagnostic_stream` ever chooses between (1 and 2 — it
    never asks about any other descriptor) means a caller who cannot
    determine the truth prints nothing rather than risks printing into the
    file it just wrote.
    """
    if dest_id is None:
        return frozenset({1, 2})
    return frozenset(fd for fd, ident in open_fds.items() if ident == dest_id)


@dataclasses.dataclass(frozen=True)
class WriteResult:
    """What `atomic_write` learned about the destination (CB-143 + CB-194).

    `in_place`: CB-143's original classification, unchanged in meaning — True
    exactly when the write went through the destination rather than through a
    temp-and-replace (see `atomic_write`'s docstring for the four cases).

    `matched_fds`: CB-194's addition — which of this process's OWN
    descriptors (candidates: 1 = stdout, 2 = stderr; nothing else is ever
    asked about, see `_matched_fds_for`) are the same open file as the
    destination just written. Empty on every temp-and-replace write, by
    construction: that path is taken only when nothing already holds the
    destination's inode open, so no descriptor can be an alias of it.

    INVARIANT, ENFORCED rather than merely documented: a nonempty
    `matched_fds` implies `in_place`. An alias can only be discovered on the
    in-place path (see above), so honest code can never construct the
    opposite; this exists to catch a future edit that tries, the same reason
    `merge.merge`/`capacity.pull_next` raise instead of asserting for their
    own invariants elsewhere in this package (`assert` is stripped under
    `-O`).
    """

    in_place: bool
    matched_fds: frozenset[int]

    def __post_init__(self) -> None:
        if self.matched_fds and not self.in_place:
            raise AssertionError(
                "WriteResult: matched_fds is nonempty but in_place is False — "
                "a descriptor collision can only be detected on the in-place "
                "write path (CB-194 invariant)"
            )


def diagnostic_stream(result: WriteResult) -> IO[str] | None:
    """Pick the stream a post-write confirmation should go to (CB-194).

    ONE RULE, implemented ONCE so `export-csv` and `reqs-export` cannot drift
    the way they did before (CB-194's own regression was one of them fixed
    and the other left broken by the same touch): take the channel today's
    code already picks — `stderr` if the write was in place, `stdout`
    otherwise (CB-143) — and if that channel is the same open file as the
    destination, take the other one. If THAT one is the destination too,
    print nothing: there is no third channel, and any byte printed would
    land inside the very file the caller just exported.
    """
    if result.in_place:
        primary, primary_fd, fallback, fallback_fd = sys.stderr, 2, sys.stdout, 1
    else:
        primary, primary_fd, fallback, fallback_fd = sys.stdout, 1, sys.stderr, 2
    if primary_fd not in result.matched_fds:
        return primary
    if fallback_fd not in result.matched_fds:
        return fallback
    return None


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
def atomic_write(
    path: str, *, newline: str | None = None
) -> Iterator[tuple[IO[str], WriteResult]]:
    """Yield (handle, result): a writable text handle whose content replaces
    `path` only on success, plus a `WriteResult` describing THIS call's write.

    `open(path, "w")` truncates the destination BEFORE the first byte is
    written, so any write failure destroys the previous file (CB-76). Here the
    payload goes to a temp beside the destination and is moved into place only
    after it has been written AND closed.

    `newline` is forwarded to the text wrapper. `encoding` is deliberately NOT
    accepted or set: `open()` and `os.fdopen()` both take the locale default,
    and pinning one here would silently desynchronise export from import on a
    non-UTF-8 host.

    `result.in_place` is the CLASSIFICATION THIS FUNCTION ALREADY COMPUTES to
    decide how to write (CB-143). A caller that needs to know whether its
    destination is an alias of its own stdout — so it can steer a post-write
    diagnostic away from the data channel — must use THIS value rather than
    re-deriving the answer from the path string a second time: a second
    classifier is a second copy of `_FD_DIR`/`_held_open_inodes()` that can
    disagree with this one under a race (the fd is reopened between the two
    calls) or under a future change to either. `in_place` is True exactly in
    the "Not always atomic" cases below — a FIFO, a character device, a
    file-descriptor alias, or a regular file whose inode this process already
    holds open — and False whenever the temp-and-replace path is taken,
    including for a brand-new file.

    `result.matched_fds` (CB-194) names WHICH of this process's own
    descriptors (1 = stdout, 2 = stderr) are the destination itself, so a
    caller choosing a diagnostic channel can avoid one that IS the file it
    just wrote — see `diagnostic_stream`, the one sanctioned way to act on
    this value. Do not branch on `matched_fds` directly in a second place;
    that reintroduces the very drift this field exists to prevent (CB-194
    §2: "второго классификатора не заводить" extends to this decision too).

    TWO LIMITS ON THE NAME, stated here because the caller cannot see them:

    * **Not always atomic.** A destination that is a FIFO, a character device,
      a file-descriptor alias, or a regular file whose inode this process
      already holds open is written IN PLACE — replacement would destroy a live
      node or orphan an open descriptor, so writing through it is the only
      correct behaviour, and it carries `open(w)`'s truncation window. There is
      nothing to protect in the first three cases; the fourth is a deliberate
      trade of atomicity for not breaking `export-csv out.csv > out.csv`.
    * **Single-threaded callers only.** Creating a NEW file reads the umask,
      which requires setting it, and that is process-global for two syscalls.
      Both callers today are CLI handlers. A threaded caller could observe the
      cleared umask.
    """
    # FIRST, and load-bearing — but NOT for the reason an earlier draft of this
    # comment gave. `mkstemp(dir="")` does not fail; it creates in the cwd
    # (measured), which for a bare filename is the right directory anyway. The
    # real reason is the SYMLINK case: the temp must sit beside the *resolved*
    # target so the replace is same-filesystem (no EXDEV) and lands in the
    # directory that actually holds the file.
    resolved = os.path.realpath(path)

    in_place = False
    keep_mode: int | None = None

    # Checked BEFORE the stat, because for a pipe the alias resolves to a name
    # that does not exist: `/proc/<pid>/fd/pipe:[N]`. A stat-based classifier
    # reads that as "a new file to create" and then tries to mkstemp inside
    # /proc — so `codebugs export-csv /dev/stdout | cat`, which streams CSV on
    # main, came back as `[Errno 2] ... '/dev/stdout'`. Reproduced against both
    # trees; an fd alias is a live descriptor, never a destination to replace.
    if _FD_DIR.match(os.path.dirname(resolved)):
        st: os.stat_result | None = None
        in_place = True
    else:
        try:
            st = os.stat(resolved)
        except FileNotFoundError:
            st = None  # a genuinely absent destination: we create it
        except OSError as exc:
            # ELOOP from a symlink cycle, EACCES on a path component, ENOTDIR...
            # Only FileNotFoundError means "missing"; classifying these as
            # missing would replace the very symlink we failed to resolve.
            raise _typed(exc, path) from exc

    if in_place or st is None:
        pass  # write through the alias, or create a new file
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
        # CB-194: which of THIS process's own descriptors are the destination
        # itself. `st` already carries the destination's identity in every
        # in_place branch except the fd-alias/pipe one (`st is None`, set
        # above): there `resolved` is a FABRICATED name (`pipe:[N]`) that does
        # not exist on disk, but the ORIGINAL `path` still resolves through
        # the /proc magic symlink via a real stat() SYSCALL rather than a
        # textual path lookup — measured (§3 of the brief): `os.stat` on the
        # symlink itself reaches the pipe's own identity even when `realpath`
        # cannot spell it. Any failure of that fallback is genuinely
        # undetermined, not "no match" — `_matched_fds_for` resolves that in
        # favour of silence rather than a guess (CB-194 §4).
        if st is not None:
            dest_id: tuple[int, int] | None = (st.st_dev, st.st_ino)
        else:
            try:
                dest_stat = os.stat(path)
                dest_id = (dest_stat.st_dev, dest_stat.st_ino)
            except OSError:
                dest_id = None
        matched_fds = _matched_fds_for(dest_id, _open_fd_identities())

        # Today's exact semantics, including its truncation window. Open `path`
        # rather than `resolved` so the symlink is followed by open() itself.
        try:
            handle = open(path, "w", newline=newline)  # noqa: SIM115
        except OSError as exc:
            raise _typed(exc, path) from exc
        with handle:
            yield handle, WriteResult(in_place=True, matched_fds=matched_fds)
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

    # One `finally`, not a `_discard` at each failure point: "the temp never
    # survives" is then structural rather than a convention every future edit
    # must re-establish — the same argument this repo applies to SQL-computed
    # deadlines and guarded rollbacks. After a successful replace the temp is
    # already gone and `_discard` no-ops on it.
    try:
        with handle:
            # matched_fds is empty here BY CONSTRUCTION (CB-194): this branch
            # is taken only when nothing already holds the destination's
            # inode open (that is what `in_place` would otherwise have
            # become), so no descriptor can alias a file this call is about
            # to create or replace — see WriteResult's own invariant.
            yield handle, WriteResult(in_place=False, matched_fds=frozenset())
        # Closed by the `with`, so a flush/close ENOSPC — where quota failures
        # usually surface — has already raised and we never reach the replace.

        try:
            os.chmod(tmp, keep_mode if keep_mode is not None else _default_file_mode())
            os.replace(tmp, resolved)
        except OSError as exc:
            raise _typed(exc, path) from exc
        # The caller's own write/close errors are deliberately NOT re-typed:
        # rebuilding them would keep only errno/strerror/filename and discard
        # anything richer they carried (a BlockingIOError's characters_written).
    finally:
        _discard(tmp)
