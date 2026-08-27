"""Location anchor CAPTURE (BT-7 Т-a, CB-95): where in the code a finding is.

The package's SECOND zero-SQL extension (`similarity.py` is the precedent, and
the same rule makes this legal): every row this module touches arrives as the
observation the pre-add resolver seam hands it, and it issues no SQL of its own.
It registers with `meta_keys=("loc",)` and `updatable_keys=("loc",)`, so a
caller-supplied `meta.loc` is REFUSED on add (a coordinate the caller invented
is spoofing) and REPAIRABLE on update (an annotation that can never be fixed is
the CB-26 shape).

WHAT IT DOES, and the boundary that matters: it reads the anchored lines out of
the GIT OBJECT STORE at the revision the finding was reported against, never
from the working tree. Three measured reasons (BT-7 Р3): a dirty tree would
give text from a revision the anchor does not name; unversioned files are
unreachable BY CONSTRUCTION, so the whole read-boundary apparatus an earlier
design needed (containment, `realpath`, `S_ISREG`, FIFO refusal) collapses into
"ask git"; and the cost is 1-2 ms against the 8-16 ms the write lock already
holds for the similarity resolver.

WHAT IT DOES NOT DO. Resolving an anchor to a line on HEAD — `git blame
--reverse`, the ancestor gate, the `moved_file` status, channel B — is the
READ side and is a separate unit. Nothing here walks history. The one read-side
thing that does live here is `read_anchor`, because the object's invariants
belong to the module that defines the object.

REFUSAL IS PERSISTED, NEVER SILENT (Р7). A capture that cannot produce
coordinates stores `{"v": 2, "skipped": <token>, "sites_dropped": <n>}` rather
than omitting the key, because an absent key cannot be told from a finding
older than the anchor, and both the lost-rate and the `sites_dropped`
distribution — the ratified triggers for the deferred multi-anchor work — are
uncountable without it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from collections.abc import Sequence
from typing import Annotated, Any, NamedTuple

from pydantic import Field

from codebugs import db
from codebugs.provenance import resolve_in_worktree, worktree_root

#: The object version this module WRITES. `read_anchor` is the only reader of a
#: stored version, and it answers `unsupported_anchor_version` for anything else
#: rather than raising — an anchor written by a future codebugs must degrade, not
#: crash, in an older one.
ANCHOR_VERSION = 2

#: The normalizer version stamped into every anchor (`norm`). It names
#: `normalize_lines` below; channel B on the read side re-derives its comparison
#: through the same function, so the stored version is what tells a reader
#: whether its normalizer still agrees with the one that captured.
NORM_VERSION = "v1"

#: Raised from 3 in BT-7 v6 for a MEASURED reason that is not about coverage:
#: a controlled split is not tracked by `blame --reverse -C -C` on any span of
#: three lines or fewer, and is tracked at four to five. Resolution share barely
#: moves (73.0/60.3% at 3 against 73.0/60.6% at 5) — the extra lines buy the
#: ABILITY TO SEE A MOVE, not a percentage.
MAX_ANCHOR_LINES = 5

#: Lines of context on EACH side that channel B uses to tell two identical
#: candidates apart (Р6). It is not a field of the anchor and never becomes one:
#: context is free BY PLACE — both sides of the comparison are read out of the
#: object store at resolution time, the original from the anchor's own revision
#: and the candidates from HEAD — so storing it would be a second representation
#: of a fact the object already implies, which Р2 removed the hashes for.
CONTEXT_LINES = 8

#: Size bound on the stored text, measured as `len("\n".join(text).encode())`.
#: This is a bound on the RECORD, not on access: access is bounded by git
#: refusing everything it does not track (Р3(ii), §7.1).
MAX_TEXT_BYTES = 2048

#: RECALIBRATED BY THIS UNIT on first-party code, which was v6's one declared
#: calibration debt: the 24 it replaces came from a 52 260-file Python corpus
#: that was ~93% third-party, because it swept the filesystem instead of asking
#: git what is tracked. Reproduce with
#: `tests/manual/calibrate_min_anchor_chars.py`, which carries the predicate,
#: the population and the curve.
#:
#: READ IT FOR WHAT IT IS. This threshold is NOT a uniqueness mechanism and
#: cannot become one: at 60+ characters 12.7% of lines still repeat inside their
#: own file, and context does 6.8x more separating work than length ever does.
#: Its only job is to refuse anchor text that is NOISE, and the number is the
#: knee of the ambiguity curve — the length past which one more character stops
#: buying a meaningful drop.
#:
#: THE MEASUREMENT (2026-08-22; 3 570 git-tracked .py files across codebugs and
#: autosorter, 968 794 non-blank positions). §6.2's ten-wide buckets are what hid
#: the knee: at four-wide resolution the per-bucket drop in "not unique in its
#: own file" runs -20.4, -17.8, -12.1 pp through length 15 and then COLLAPSES to
#: -2.5, -2.2, -2.3, -0.4 pp from 16 onward. Sixteen is where the curve stops
#: paying.
#:
#: WHY 24 WAS TOO HIGH, said as the trade it makes: it refuses 31.5% of all
#: positions whose ambiguity is 56.5%, to keep 68.3% whose ambiguity is 21.3%.
#: Sixteen refuses 19.1% at 68.1% and keeps 80.9% at 23.9%. The extra 12.4 points
#: of corpus that 24 throws away are barely less ambiguous than what it keeps —
#: coverage paid for nothing, in a design whose headline number is that only
#: 21% / 6% of findings get an anchor at all. The error is also ASYMMETRIC: too
#: permissive costs an `ambiguous` answer from the SECONDARY channel, since the
#: core resolves by reverse blame and uses the text only to verify; too strict
#: costs the anchor outright.
#:
#: The contamination corrected the SHORT end, not the long one: first-party code
#: is markedly noisier below ten characters (83.6% against §6.2's 69.2%) because
#: it is full of repeated `)`, `else:` and `"""`, while at 60+ the two corpora
#: agree within two points. That is an argument for having a threshold, and for
#: putting it at the knee rather than above it.
MIN_ANCHOR_CHARS = 16

#: Wall-clock budget for ONE capture, across every git call it makes — not per
#: call, which is the distinction that makes it safe (§7.3). It must stay
#: WELL under `busy_timeout=5000`, because the lock this capture holds is the
#: lock a competing writer is waiting on, and `SQLITE_BUSY` (5) is not in
#: `db._is_environmental`'s `{8, 10, 13, 14}` — so a writer that times out does
#: not get a tidy message, it gets a raw traceback. Measured cost of the git
#: calls involved is 1-10 ms each, so this is ~200x headroom and still 2.5x
#: under the competing writer's tolerance.
#: `tests/test_loc.py::TestCaptureBudget` pins the relation against the PRAGMA
#: read off a real connection rather than against a copy of the literal 5000.
CAPTURE_BUDGET_S = 2.0

#: §4.3, verbatim and CLOSED. Tokens are shared with the read side, so this is
#: the whole vocabulary of "why is there no coordinate", not just capture's part
#: of it; `CAPTURE_REASONS` below names the subset this module can produce, so a
#: reader can tell a capture refusal from a resolution refusal by token alone.
REASONS = frozenset(
    {
        "no_commit",
        "commit_unreachable",
        "commit_not_ancestor",
        "repo_mismatch",
        "shallow_history",
        "no_repo",
        "path_absent_at_commit",
        "not_a_file",
        "no_grammar",
        "no_matching_site",
        "out_of_range",
        "too_short",
        "too_large",
        "binary",
        "timeout",
        "no_root",
        "verify_mismatch",
        "internal_error",
        "unsupported_anchor_version",
        "invalid_anchor",
        "retracted",
        # The `meta` COLUMN itself does not parse, which is a different fact
        # from any judgement about the stored anchor object: there is no object
        # to judge. Read-side only; capture never produces it.
        "unreadable_meta",
    }
)

#: The subset CAPTURE can produce. Everything else in `REASONS` belongs to
#: resolution (the ancestor gate, the repo cross-check, the verify step) or to
#: reading a stored object.
CAPTURE_REASONS = frozenset(
    {
        "no_root",
        "no_repo",
        "no_grammar",
        "no_matching_site",
        "no_commit",
        "commit_unreachable",
        "path_absent_at_commit",
        "not_a_file",
        "binary",
        "out_of_range",
        "too_short",
        "too_large",
        "timeout",
        "internal_error",
    }
)

_HEX_COMMIT = re.compile(r"[0-9a-fA-F]{7,40}")
_WS = re.compile(r"\s+")

# One site as the grammar (§4) parses it: an optional path the value NAMED, and
# a 1-based inclusive span. The path is only ever used to CHOOSE which site
# applies to this finding — §4's measured rule is that the FILE IS ALWAYS THE
# `file` COLUMN (609 of 709 autosorter values name no file at all; where a value
# names exactly one, it agrees with the column 41/47 and 22/23).
class _Site(NamedTuple):
    path: str | None
    line: int
    end: int


class _Refused(Exception):  # noqa: N818 — a refusal, not an error; see Р7
    """A capture that cannot produce coordinates. Carries its §4.3 token.

    Deliberately NOT an error: the resolver converts it into a stored refusal
    object, which is the whole of Р7. It never reaches `db.run_pre_add_resolvers`'
    swallow, so it never stamps `meta.resolver_errors`.
    """

    def __init__(self, token: str) -> None:
        # RAISE, never `assert` — this repository's standing rule, and it earns
        # its keep here specifically: `assert` is stripped under `-O`, so the
        # optimized build would store a token outside the closed §4.3 vocabulary
        # and `read_anchor` would later report it as `invalid_anchor`, blaming
        # the stored object for this module's bug. A ValueError is not in
        # `_EXCEPTION_TOKENS`, so it propagates and becomes `resolver_errors` —
        # which is exactly what Р8 says an unclassified failure is.
        if token not in CAPTURE_REASONS:
            raise ValueError(f"capture refusal token {token!r} is not in CAPTURE_REASONS")
        super().__init__(token)
        self.token = token


#: Р8, and this is the mechanism the promise rests on rather than the prose.
#: `db.run_pre_add_resolvers` catches EVERY exception, keeps the insert, and
#: stamps `meta.resolver_errors` — and it runs `_validate_resolver_outcome`
#: INSIDE that same catch. So "a capture refusal is never a resolver error" can
#: only be true if this module classifies its own failures BEFORE returning.
#: This table is that classification, and it is CLOSED: anything not listed here
#: propagates, because an unclassified exception really is a broken resolver and
#: `resolver_errors` really is its channel.
#:
#: ORDER IS LOAD-BEARING and pinned by a test: `TimeoutExpired` IS a
#: `SubprocessError`, so listing the general case first would silently reclassify
#: an exhausted budget as an internal error and lose the one signal that says
#: "the lock was held long enough to matter".
_EXCEPTION_TOKENS: tuple[tuple[type[BaseException], str], ...] = (
    (subprocess.TimeoutExpired, "timeout"),
    (UnicodeDecodeError, "binary"),
    (subprocess.SubprocessError, "internal_error"),
    (OSError, "internal_error"),
)

# Repository identity, cached PER PROCESS (Р2). `git rev-list --max-parents=0`
# costs 8-10 ms — ten times the capture itself — so paying it per add would
# invert the cost argument the whole design rests on. Only successes are cached:
# a negative answer is usually a transient (git busy, a repo mid-clone) and
# caching it would make one bad moment permanent for the process's life.
_repo_ids: dict[str, str] = {}
_repo_ids_lock = threading.Lock()


class _Budget:
    """One capture's wall-clock allowance, shared across every git call.

    A per-call timeout is the wrong shape here and the difference is the whole
    point: three calls at 2 s each is a 6 s lock hold, which is longer than the
    5 s a competing writer will wait, so the guard would authorize exactly the
    failure it exists to prevent. The deadline is sampled once and every call
    gets what is LEFT of it.
    """

    def __init__(self, seconds: float | None = None) -> None:
        # Read at CONSTRUCTION, not bound as a default: a default is evaluated
        # once at import, which would make the module constant unreachable to
        # anything that later changes it — including the test that has to prove
        # an exhausted budget classifies as `timeout` rather than as a bug.
        self.seconds = CAPTURE_BUDGET_S if seconds is None else seconds
        self.deadline = time.monotonic() + self.seconds

    def remaining(self) -> float:
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise subprocess.TimeoutExpired(cmd="git", timeout=self.seconds)
        return left


def normalize_lines(lines: Sequence[str]) -> list[tuple[int, str]]:
    """`norm: "v1"` (Р6): one `(indent depth, collapsed body)` pair per line.

    Leading indentation becomes a DEPTH TOKEN (`indent // 4`, tabs expanded to
    four) rather than surviving as characters, and internal whitespace runs
    collapse. Both are deliberate lossiness with a named cost, stated in the
    design and repeated here because a reader of the code is the one who needs
    it: `indent // 4` cannot tell 4 spaces from 7, and collapsing whitespace
    rewrites string literals — so this normalizer CAN call semantically changed
    code unchanged.

    Callers decode with `errors="replace"` before splitting; line endings are
    normalized here so a CRLF checkout and an LF one give the same answer.
    """
    out: list[tuple[int, str]] = []
    for raw in lines:
        expanded = raw.replace("\r\n", "\n").replace("\r", "").expandtabs(4)
        stripped = expanded.lstrip(" ")
        depth = (len(expanded) - len(stripped)) // 4
        out.append((depth, _WS.sub(" ", stripped).strip()))
    return out


def _anchor_chars(lines: Sequence[str]) -> int:
    """Normalized body characters across the span — what MIN_ANCHOR_CHARS bounds."""
    return sum(len(body) for _, body in normalize_lines(lines))


# --- Grammar (§4) --------------------------------------------------------------------

# Measured priority among keys of ONE row (§4). Singular keys are unambiguous by
# construction (`line`: 0% multi-site; `site`: 15/15 in the gate corpus) and
# plural ones are not (`sites`: 63% multi-file), so the singular spellings win.
# `function` is NEVER a source — 28/28 of its values are prose. The table is not
# here because conflicts are common (the whole corpus holds TWO genuinely
# competing rows) but so that capture does not depend on dict iteration order.
_KEY_PRIORITY: tuple[str, ...] = ("line", "site", "lines", "sites", "location")

# `a-b`, `a:b` and `a` — the span spellings B3/B4 use. `:` is accepted as a
# range separator only where a path has already been split off, so `foo.py:12`
# cannot be read as the range 12..(nothing).
_SPAN = re.compile(r"^\s*(\d+)\s*(?:[-–]\s*(\d+)\s*)?$")


def _parse_one(token: str) -> list[_Site]:
    """One textual token into sites. `path:N`, `path:N-M`, `N`, `N-M`, `N,M,K-L`."""
    sites: list[_Site] = []
    for part in token.split(","):
        part = part.strip()
        if not part:
            continue
        path: str | None = None
        span = part
        if ":" in part:
            head, _, tail = part.rpartition(":")
            if head.strip() and _SPAN.match(tail):
                path, span = head.strip(), tail
        m = _SPAN.match(span)
        if not m:
            continue
        line = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else line
        if line < 1 or end < line:
            continue
        sites.append(_Site(path, line, end))
    return sites


def _parse_value(value: Any, *, path: str | None = None) -> list[_Site]:
    """Every site a meta value names, in reading order (§4 branches B1-B6).

    B2 is the branch worth naming: a `list[int]` is N SEPARATE lines and a range
    is never inferred from it. That is libcheck's contract, held 206/206 in the
    corpus, and inferring `10-12` from `[10, 11, 12]` would silently widen an
    anchor whose producer meant three independent sites.
    """
    if isinstance(value, bool):  # bool is an int; a flag is not a line number
        return []
    if isinstance(value, int):
        return [_Site(path, value, value)] if value >= 1 else []
    if isinstance(value, str):
        return [_Site(path or s.path, s.line, s.end) for s in _parse_one(value)]
    if isinstance(value, (list, tuple)):
        out: list[_Site] = []
        for item in value:
            out += _parse_value(item, path=path)
        return out
    if isinstance(value, dict):
        out = []
        for key, item in value.items():
            out += _parse_value(item, path=key if isinstance(key, str) else path)
        return out
    return []  # B6: prose, None, anything else — the row does not say where


def parse_sites(meta: dict[str, Any] | None) -> list[_Site]:
    """The sites one observation's meta names, by the measured key priority.

    The FIRST key in priority order that yields any site wins outright; later
    keys are not merged in. Merging would make the answer depend on how many
    spellings a producer happened to use, and the singular keys are exactly the
    ones measured to be unambiguous.
    """
    if not isinstance(meta, dict):
        return []
    for key in _KEY_PRIORITY:
        if key in meta:
            sites = _parse_value(meta[key])
            if sites:
                return sites
    return []


def _paths_agree(named: str, column: str) -> bool:
    """Does a site's path name the same file as the `file` column?

    Compared on the tail rather than exactly, because a value writes
    `src/codebugs/db.py` where the column writes `db.py` and vice versa. A bare
    basename match is enough — §4 measured 41/47 and 22/23 agreement, so this
    filter exists to discard sites naming a DIFFERENT file, not to adjudicate
    two spellings of the same one.
    """
    a = named.replace("\\", "/").strip("/")
    b = column.replace("\\", "/").strip("/")
    if not a or not b:
        return False
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def select_site(sites: Sequence[_Site], file: str) -> _Site | None:
    """The one site this finding's anchor covers, or None if it names no such file.

    Sites carrying a path are filtered against the `file` column first, because
    a multi-file `sites` value (63% of them) may name this file among others. If
    NONE of the named paths is this file, that is a real refusal
    (`no_matching_site`) and not a reason to anchor into a file the row never
    mentioned: the coordinates would be another file's line numbers read against
    this one, which is the confidently-wrong answer the whole design refuses.
    """
    named = [s for s in sites if s.path]
    if named:
        matching = [s for s in named if _paths_agree(s.path or "", file)]
        unnamed = [s for s in sites if not s.path]
        pool = matching + unnamed
        return pool[0] if pool else None
    return sites[0] if sites else None


# --- git access ----------------------------------------------------------------------


def _git(root: str, args: Sequence[str], budget: _Budget) -> tuple[int, bytes, bytes]:
    """One git call inside the shared budget. Raises TimeoutExpired when spent."""
    proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["git", "-C", root, *args],
        capture_output=True,
        timeout=budget.remaining(),
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def repo_identity(root: str, budget: _Budget | None = None) -> str | None:
    """The SHA of `root`'s root commit — the tree's identity (Р2's `repo` field).

    This closes the one path to a CONFIDENTLY WRONG answer (П14): a finding can
    carry a commit from one tree and later be resolved in another, and the
    read-side verify cannot see that by construction, because it re-reads the
    same tree the blame walked. Between unrelated repositories resolution
    already fails closed; between repositories sharing history it does not, and
    `repo` is what makes the mismatch visible.

    RESIDUAL, named rather than discovered later: a repository with MORE THAN
    ONE root commit (a subtree merge brings a foreign root in) has no single
    identity, so the smallest reachable root SHA is used. It is deterministic
    for a given commit graph, but a branch that reaches a different set of roots
    computes a different value — which surfaces on the read side as
    `repo_mismatch`, a FALSE REFUSAL rather than a wrong answer. That is the
    direction this field exists to fail in.
    """
    with _repo_ids_lock:
        cached = _repo_ids.get(root)
    if cached is not None:
        return cached
    rc, out, _ = _git(root, ["rev-list", "--max-parents=0", "HEAD"], budget or _Budget())
    if rc != 0:
        return None
    roots = sorted(out.decode("ascii", "replace").split())
    if not roots:
        return None
    with _repo_ids_lock:
        _repo_ids.setdefault(root, roots[0])
    return roots[0]


def _resolve_commit(root: str, raw: str | None, budget: _Budget) -> str:
    """The row's revision, expanded to 40 hex in `root`. Raises `_Refused` otherwise.

    The hex SHAPE is checked BEFORE git is invoked, which is doing two jobs.
    It separates `no_commit` — the row does not carry a revision at all, or
    carries free text that is not one — from `commit_unreachable`, which means
    the actionable thing: a real SHA this tree does not have (wrong repository,
    shallow clone, rewritten history). And it keeps a caller-supplied string out
    of argv: `^{commit}` is appended to this value, and `rev-parse` would
    happily accept a branch name or an option-looking token.

    Abbreviated SHAs are accepted (7..40) because the corpus has 222 rows
    carrying them, all of which resolve; `^{commit}` is what refuses a value
    that resolves to a tag or a tree.
    """
    if not isinstance(raw, str) or not _HEX_COMMIT.fullmatch(raw.strip()):
        raise _Refused("no_commit")
    rc, out, _ = _git(root, ["rev-parse", "--verify", f"{raw.strip()}^{{commit}}"], budget)
    if rc != 0:
        raise _Refused("commit_unreachable")
    sha = out.decode("ascii", "replace").strip()
    if len(sha) != 40:
        raise _Refused("commit_unreachable")
    return sha


def read_blob(root: str, commit: str, rel: str, budget: _Budget) -> bytes:
    """The blob at `<commit>:<rel>`, straight out of the object store.

    `git cat-file blob` and NOT `git show`, and this is a DEVIATION FROM THE
    DESIGN'S LITERAL COMMAND made because the design's premise about it is false
    — measured on git 2.53, in a throwaway repository, before writing this:

        $ git show HEAD:sub        -> rc 0, prints "tree HEAD:sub" and a listing
        $ git cat-file blob HEAD:sub -> rc 128, "fatal: ... bad file"

    The unit's own instruction says the non-blob rejection is held by `git
    show`. It is not: `git show` succeeds on a TREE and prints its listing, so a
    finding whose `file` names a DIRECTORY — which CB-88 records as a real,
    occurring value, trailing slash and all — would have captured a directory
    listing as its anchor `text` and stored it as if it were code. That is a
    confidently wrong answer produced silently, which is the exact class BT-7
    v6 spent a round closing. `cat-file blob` refuses it, so the `not_a_file`
    token the instruction requires has a producer.

    `git show HEAD:.` does refuse (rc 128), so the directory case is only
    reachable for a SUBdirectory — which is precisely the ordinary spelling.
    """
    rc, out, err = _git(root, ["cat-file", "blob", f"{commit}:{rel}"], budget)
    if rc == 0:
        return out
    # `cat-file`'s two refusals are distinguishable by message and by nothing
    # else: an absent path says "does not exist in"/"exists on disk, but not
    # in", a non-blob says "bad file". Defaulting the UNRECOGNIZED message to
    # `path_absent_at_commit` is deliberate — it is the answer that claims less.
    if b"bad file" in err:
        raise _Refused("not_a_file")
    raise _Refused("path_absent_at_commit")


# --- capture -------------------------------------------------------------------------


def _refusal(token: str, sites_dropped: int = 0) -> dict[str, Any]:
    """The stored refusal object (Р7). A refusal is DATA, not a missing key."""
    return {"v": ANCHOR_VERSION, "skipped": token, "sites_dropped": sites_dropped}


def capture(observation: dict[str, Any]) -> dict[str, Any]:
    """One observation into an anchor object — coordinates, or a refusal object.

    NEVER RAISES for anything in `_EXCEPTION_TOKENS` or `_Refused`; see Р8 and
    the table's own comment for why that promise has to be mechanical.
    """
    sites_dropped = 0
    budget = _Budget()
    try:
        # (1) The root, and it must be TOLD to us. `worktree_root(project_dir=
        # None)` falls back to the process cwd, and BT-7 Р3 refuses ambient cwd
        # in capitals: a long-lived server's cwd has nothing to do with the
        # tracker a call is writing to, and silently anchoring into whatever
        # tree the process happens to stand in is the confidently-wrong answer
        # again. Only the CALLER can know, so an absent value fails closed.
        project_dir = observation.get("project_dir")
        if not isinstance(project_dir, str) or not project_dir:
            raise _Refused("no_root")
        root = worktree_root(project_dir=project_dir)
        if root is None:
            raise _Refused("no_repo")

        # (2) Does the row say WHERE to look? This is the ceiling of the whole
        # design and it is a property of the filing, not of the mechanism:
        # 63.6% of codebugs rows and 79.2% of autosorter rows name no span.
        sites = parse_sites(observation.get("meta"))
        if not sites:
            raise _Refused("no_grammar")
        sites_dropped = len(sites) - 1
        site = select_site(sites, observation.get("file") or "")
        if site is None:
            raise _Refused("no_matching_site")

        commit = _resolve_commit(root, observation.get("reported_at_commit"), budget)

        placed = resolve_in_worktree(root=root, file_path=observation.get("file") or "")
        if placed is None:
            # Outside this worktree. The closed vocabulary has no `out_of_repo`
            # token, and this IS the honest one: the path is not in the tree, so
            # it is not in that commit either.
            raise _Refused("path_absent_at_commit")

        data = read_blob(root, commit, placed.rel, budget)
        if b"\0" in data:
            raise _Refused("binary")
        content = data.decode("utf-8")  # UnicodeDecodeError -> "binary", per the table

        lines = content.split("\n")
        if lines and lines[-1] == "":
            lines.pop()  # a trailing newline is a terminator, not an empty last line
        if site.line > len(lines):
            raise _Refused("out_of_range")
        # Both clips are deliberate and neither is a refusal. A span running past
        # EOF still names a real first line, and a span longer than the cap still
        # names the right place — refusing either would throw away a usable
        # anchor to punish the caller's arithmetic. What must not happen is
        # storing an object that violates Р2's read-side invariants, and clipping
        # is what makes `end - line + 1 <= MAX_ANCHOR_LINES` true by construction.
        end = min(site.end, len(lines), site.line + MAX_ANCHOR_LINES - 1)
        text = lines[site.line - 1 : end]

        if len("\n".join(text).encode("utf-8")) > MAX_TEXT_BYTES:
            raise _Refused("too_large")
        if _anchor_chars(text) < MIN_ANCHOR_CHARS:
            raise _Refused("too_short")

        repo = repo_identity(root, budget)
        if repo is None:
            raise _Refused("no_repo")

        return {
            "v": ANCHOR_VERSION,
            "repo": repo,
            "commit": commit,
            "path": placed.rel,
            "line": site.line,
            "end": end,
            "text": text,
            "norm": NORM_VERSION,
            "sites_dropped": sites_dropped,
        }
    except _Refused as refused:
        return _refusal(refused.token, sites_dropped)
    except BaseException as exc:  # noqa: BLE001 — classified, then re-raised if unlisted
        for kind, token in _EXCEPTION_TOKENS:
            if isinstance(exc, kind):
                return _refusal(token, sites_dropped)
        raise


# --- read side: the object's own invariants -------------------------------------------


def read_anchor(value: Any) -> tuple[dict[str, Any] | None, str | None]:
    """`(anchor, reason)` for one stored `meta.loc`. NEVER RAISES.

    Р2 puts the invariants HERE, on the read, and not on the write, for a reason
    that is a property of the seam rather than a preference: `updatable_keys`
    validates NOTHING (П7), and `restore_findings` writes meta verbatim, so a
    stored `loc` can be anything a hand-written `update_finding(meta_update=)`
    put there. A validator on the write path would therefore be a gate that
    cannot fire, which this repository has now documented as a failure mode
    three separate times.

    Exactly one of the two is non-None. `reason` is a §4.3 token:
    `retracted` for the `loc: null` tombstone ("do not recapture"),
    `unsupported_anchor_version` for a version this build does not implement,
    `invalid_anchor` for a v2 object that breaks its own invariants, and the
    stored token itself for a persisted refusal.

    ONE DELIBERATE NARROWING against the design's letter, reported as such: the
    doc lists `v in {1, 2}` as valid, and this accepts only 2. There has never
    been a v1 producer — v1 was an earlier design's object, superseded before
    any code shipped — so a v1 branch would be validation logic for a shape that
    does not exist anywhere, and the honest answer for a version this code does
    not implement is exactly `unsupported_anchor_version`. The doc's intent is
    "never raise on an unknown version", and that is what this does.
    """
    if value is None:
        return None, "retracted"
    if not isinstance(value, dict):
        return None, "invalid_anchor"
    if value.get("v") != ANCHOR_VERSION or isinstance(value.get("v"), bool):
        return None, "unsupported_anchor_version"
    if "skipped" in value:
        token = value["skipped"]
        return None, token if token in REASONS else "invalid_anchor"

    commit = value.get("commit")
    path = value.get("path")
    line = value.get("line")
    end = value.get("end")
    text = value.get("text")
    if not isinstance(commit, str) or not _HEX_COMMIT.fullmatch(commit):
        return None, "invalid_anchor"
    if not isinstance(path, str) or not path or path.startswith("/"):
        return None, "invalid_anchor"
    # A `..` component escapes the worktree the anchor is anchored to; a bare
    # substring test would also reject a legitimate `..foo.py`.
    if ".." in path.replace("\\", "/").split("/"):
        return None, "invalid_anchor"
    # `isinstance(True, int)` is True, and a bool here would sort and compare
    # like a line number while meaning nothing.
    if isinstance(line, bool) or isinstance(end, bool):
        return None, "invalid_anchor"
    if not isinstance(line, int) or not isinstance(end, int) or line < 1 or end < line:
        return None, "invalid_anchor"
    if end - line + 1 > MAX_ANCHOR_LINES:
        return None, "invalid_anchor"
    if not isinstance(text, list) or not all(isinstance(t, str) for t in text):
        return None, "invalid_anchor"
    if len(text) != end - line + 1:
        return None, "invalid_anchor"
    if len("\n".join(text).encode("utf-8")) > MAX_TEXT_BYTES:
        return None, "invalid_anchor"
    return value, None


# --- read side: resolving an anchor to a line on HEAD (Р5) ----------------------------

#: The statuses a resolution can answer with (Р5), CLOSED exactly like `REASONS`.
#: The two are DIFFERENT AXES and the design says so: `status` answers "where is
#: this line now", `reason` answers "why is there no answer". A reader that
#: collapses them gets `unknown` for both a repository mismatch and a line that
#: was genuinely deleted, which are opposite facts.
STATUSES: tuple[str, ...] = ("current", "moved", "moved_file", "lost", "ambiguous", "unknown")

#: `<sha> <line-at-HEAD> <line-in-the-anchored-revision> [<count>]`. Порcelain
#: inverts its two line numbers under `--reverse` (П3), and getting them the
#: wrong way round is silent: on an unshifted file the two are EQUAL, so every
#: test whose fixture does not move the lines passes either way.
_BLAME_HEADER = re.compile(r"^([0-9a-f]{40}) (\d+) (\d+)(?: (\d+))?$")


def _record(
    status: str,
    *,
    path: str | None = None,
    line: int | None = None,
    end: int | None = None,
    channel: str | None = None,
    reason: str | None = None,
    survived: str | None = None,
    resolved_against: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One resolution record. EVERY key is present; `None` is a normal answer.

    The unconditional-keys rule is this package's, not this module's: it is
    `claims._response`'s fifteen common keys and BT-5's `attention` block, where
    `[]` means "evaluated, nothing fired" and never "there is no such channel".
    A key that appears only sometimes teaches a reader to test for its presence,
    and presence then silently encodes a second fact.

    `channel` names THE MECHANISM THAT PRODUCED THIS RECORD, which is a slightly
    wider promise than "the channel that found the line" and is deliberate:
    `lost` carries `"git"` because reverse blame is what proved the lines are
    gone, `ambiguous` carries `"content"` because channel B is the only one that
    can produce it, and a record refused by a gate before any trace ran carries
    `None`. So `channel is None` means "nothing was traced", which is exactly the
    distinction a reader needs and which "the channel that answered" would lose.

    `survived` is `"<n>/<m>"` — how many of the anchor's `m` lines reverse blame
    found alive. Р5 mandates the number on a partially surviving span; it is a
    key here rather than a conditional extra for the reason above.
    """
    if status not in STATUSES:  # RAISE, never assert: `-O` strips assert
        raise ValueError(f"resolution status {status!r} is not in STATUSES")
    if reason is not None and reason not in REASONS:
        raise ValueError(f"resolution reason {reason!r} is not in REASONS")
    return {
        "status": status,
        "path": path,
        "line": line,
        "end": end,
        "channel": channel,
        "reason": reason,
        "survived": survived,
        "resolved_against": resolved_against,
    }


def _resolved_against(root: str, head: str | None, path: str | None) -> dict[str, Any]:
    """§7.5's evidence record — EVIDENCE, never proof, and the doc says so.

    `root` and `head` are what the answer was computed against; the coordinate
    is only meaningful in that pair. `mtime_ns` and `size` describe the file ON
    DISK at the resolved path, and they are here for a reason that survived the
    move to the object store: resolution reads git, but the human who receives a
    line number opens the WORKING TREE, so the one thing a reader cannot see
    from `head` alone is whether the file in front of them still matches it.
    Both are `None` when the path does not exist on disk — a `moved_file` answer
    into a path the checkout does not have is not an error, and `os.stat` is not
    allowed to turn a good answer into an exception.
    """
    mtime_ns: int | None = None
    size: int | None = None
    if path:
        try:
            st = os.stat(os.path.join(root, path))
        except OSError:  # absent, unreadable parent, a path this checkout lacks
            pass
        else:
            mtime_ns, size = st.st_mtime_ns, st.st_size
    return {
        "root": root,
        "head": head,
        "path": path,
        "mtime_ns": mtime_ns,
        "size": size,
    }


def _blob_lines(root: str, rev: str, rel: str, budget: _Budget) -> list[str] | None:
    """`<rev>:<rel>` as a list of lines, or None when it is not a readable text blob.

    Shares `read_blob` with capture rather than re-deriving it, so the read side
    cannot drift from the write side about what "the file at a revision" means —
    including the `cat-file blob` / `git show` distinction that keeps a DIRECTORY
    from being read as source text.
    """
    try:
        data = read_blob(root, rev, rel, budget)
    except _Refused:
        return None
    if b"\0" in data:
        return None
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    lines = content.split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # a trailing newline terminates the last line, it is not one
    return lines


def _parse_reverse_blame(out: bytes) -> list[tuple[str, int, int, str | None]]:
    """Porcelain into `[(sha, line-at-HEAD, line-in-the-anchored-revision, path)]`.

    Two properties of the format that the parser has to know and that are easy
    to get silently wrong:

    * The two line numbers are INVERTED under `--reverse` (П3), so field two is
      the line in the LATEST revision. On a file whose lines never shifted the
      two are equal, which is why a fixture that only edits in place cannot tell
      a correct parser from a transposed one.
    * `--porcelain` suppresses commit metadata it has already emitted, so a
      `filename` record may be absent from an entry. It is memoized per sha —
      and `--line-porcelain` is NOT used instead, because the memo is the same
      three lines and the ratified command is `-p`.

    A `filename` seen inside an entry always wins over the memo, which is what
    keeps a split (two lines of one span ending in two different files under the
    same HEAD sha) from being flattened onto whichever path arrived first.
    """
    entries: list[tuple[str, int, int, str | None]] = []
    memo: dict[str, str] = {}
    header: tuple[str, int, int] | None = None
    filename: str | None = None
    for raw in out.decode("utf-8", "replace").split("\n"):
        if header is None:
            m = _BLAME_HEADER.match(raw)
            if m:
                header, filename = (m.group(1), int(m.group(2)), int(m.group(3))), None
            continue
        if raw.startswith("filename "):
            filename = raw[len("filename ") :]
        elif raw.startswith("\t"):
            sha, head_line, src_line = header
            if filename is not None:
                memo[sha] = filename
            entries.append((sha, head_line, src_line, filename or memo.get(sha)))
            header, filename = None, None
    return entries


def _channel_a(
    root: str,
    anchor: dict[str, Any],
    *,
    head: str,
    budget: _Budget,
) -> dict[str, Any] | None:
    """Reverse blame plus the MANDATORY verify. `None` means "A did not answer".

    Three details are load-bearing and each one has a measured reason behind it:

    * **A SINGLE `-C`, and this is a correction of the ratified letter made on a
      MEASUREMENT — see `TestBlameInvocation` and `TestQuotedCodeLimit`.** v6
      ratified `-C -C` on "live history: `-C` follows one move, `-C -C` follows
      two". Re-measured over autosorter's whole eligible population (134 rows,
      402 blame calls, ancestor gate applied): NEITHER half reproduces. `-C`
      yields 0 `moved_file` candidates and `-C -C` yields 1 — and that one is a
      FALSE POSITIVE, a line of `server.py` quoted verbatim as an example inside
      a markdown plan, which `-C -C` followed into the document while `-C` and
      `-M -C` traced it correctly. `-M -C` is still refused, because the design's
      controlled experiment showed it loses a split that `-C` catches.
    * **`-c core.quotePath=false`.** Porcelain C-quotes a non-ASCII path by
      default, and the path is what this function compares against the anchor's
      to decide `moved_file` — so the default would report a move for every
      non-ASCII filename that never moved. This repository has now paid for that
      same default three times elsewhere.
    * **The verify reads the RESOLVED path, never the recorded one.** This is
      the single detail whose inversion produced the design's phantom "git is
      wrong 0.21% of the time": checking a moved line against the path it moved
      FROM declares every successful move a failure. Corrected, git was right
      537 times out of 537, byte for byte.
    """
    line, end = anchor["line"], anchor["end"]
    span = end - line + 1
    rc, out, _ = _git(
        root,
        [
            "-c",
            "core.quotePath=false",
            "blame",
            "--reverse",
            "-p",
            "-C",
            "-L",
            f"{line},{end}",
            f"{anchor['commit']}..HEAD",
            "--",
            anchor["path"],
        ],
        budget,
    )
    if rc != 0:
        return None

    alive = [e for e in _parse_reverse_blame(out) if e[0] == head and e[3]]
    if not alive:
        return _record(
            "lost",
            path=anchor["path"],
            channel="git",
            survived=f"0/{span}",
            resolved_against=_resolved_against(root, head, anchor["path"]),
        )

    # The verify, per surviving line, against the file it actually landed in.
    # A split puts one span into two files, so the blobs are fetched per path.
    blobs: dict[str, list[str] | None] = {}
    matched: list[tuple[str, int]] = []
    for _sha, head_line, src_line, path in alive:
        assert path is not None  # filtered above; narrows the type for the reader
        if path not in blobs:
            blobs[path] = _blob_lines(root, "HEAD", path, budget)
        current = blobs[path]
        idx = src_line - line
        if current is None or not 0 <= idx < len(anchor["text"]):
            return _record(
                "unknown",
                path=path,
                channel="git",
                reason="verify_mismatch",
                survived=f"{len(alive)}/{span}",
                resolved_against=_resolved_against(root, head, path),
            )
        if not 1 <= head_line <= len(current) or current[head_line - 1] != anchor["text"][idx]:
            return _record(
                "unknown",
                path=path,
                channel="git",
                reason="verify_mismatch",
                survived=f"{len(alive)}/{span}",
                resolved_against=_resolved_against(root, head, path),
            )
        matched.append((path, head_line))

    # Every surviving line verified. The answer is reported in the file the
    # FIRST surviving line landed in; when a split scattered the span across two
    # files, `survived` is what tells the reader the span is no longer whole.
    where = matched[0][0]
    here = [n for p, n in matched if p == where]
    status = "moved_file" if where != anchor["path"] else ("moved" if min(here) != line else "current")
    return _record(
        status,
        path=where,
        line=min(here),
        end=max(here),
        channel="git",
        survived=f"{len(alive)}/{span}",
        resolved_against=_resolved_against(root, head, where),
    )


def _context(lines: Sequence[str], line: int, end: int) -> tuple[list[tuple[int, str]], ...]:
    """The normalized `CONTEXT_LINES` above and below a 1-based inclusive span."""
    before = lines[max(0, line - 1 - CONTEXT_LINES) : line - 1]
    after = lines[end : end + CONTEXT_LINES]
    return normalize_lines(before), normalize_lines(after)


def _channel_b(
    root: str,
    anchor: dict[str, Any],
    *,
    head: str,
    budget: _Budget,
) -> dict[str, Any] | None:
    """The content channel: find the normalized anchor text in `HEAD:<recorded path>`.

    Secondary by measurement, not by taste: it resolves nothing the core cannot
    on this tracker's corpus and 6.7 points more on the larger one, and — the
    reason it is not redundant — it is the ONLY thing that recovers a line
    deleted and later restored byte-for-byte, which the core reads as lost
    because the liveness test runs before the verify and can only reject.

    It searches the RECORDED path, so it can never answer `moved_file`; a move
    into another file is the core's to see. Short anchors are refused outright
    rather than searched: `MIN_ANCHOR_CHARS` is a noise floor, and below it a
    normalized span matches almost everywhere, which would turn a silent wrong
    answer into this channel's normal output.
    """
    if _anchor_chars(anchor["text"]) < MIN_ANCHOR_CHARS:
        return None
    lines = _blob_lines(root, "HEAD", anchor["path"], budget)
    if lines is None:
        return None
    want = normalize_lines(anchor["text"])
    n = len(want)
    if n == 0 or n > len(lines):
        return None
    normalized = normalize_lines(lines)
    hits = [i for i in range(len(normalized) - n + 1) if normalized[i : i + n] == want]
    if not hits:
        return None

    if len(hits) > 1:
        # Disambiguate by context. BOTH sides are read at resolution time — the
        # original out of the anchor's own revision, the candidates out of HEAD —
        # which is why the object stores no context and Р2 is not widened.
        origin = _blob_lines(root, anchor["commit"], anchor["path"], budget)
        scored: list[tuple[int, int]] = []
        if origin is not None:
            before, after = _context(origin, anchor["line"], anchor["end"])
            for i in hits:
                cand_before, cand_after = _context(lines, i + 1, i + n)
                score = sum(1 for x in cand_before if x in before)
                score += sum(1 for x in cand_after if x in after)
                scored.append((score, i))
        best = sorted(scored, reverse=True)
        if not best or (len(best) > 1 and best[0][0] == best[1][0]):
            return _record(
                "ambiguous",
                path=anchor["path"],
                channel="content",
                resolved_against=_resolved_against(root, head, anchor["path"]),
            )
        hits = [best[0][1]]

    at = hits[0] + 1
    return _record(
        "current" if at == anchor["line"] else "moved",
        path=anchor["path"],
        line=at,
        end=at + n - 1,
        channel="content",
        resolved_against=_resolved_against(root, head, anchor["path"]),
    )


def resolve_anchor(
    root: str,
    anchor: dict[str, Any],
    *,
    head: str,
    repo: str | None,
    budget: _Budget | None = None,
) -> dict[str, Any]:
    """One VALIDATED anchor into a coordinate on HEAD. NEVER raises for a git failure.

    The cascade is Р5 and its ORDER is the design, not an implementation detail:

    0. **The ancestor gate, before any trace.** `git blame --reverse` over a
       range whose left end is not an ancestor of HEAD exits 0, writes nothing to
       stderr, and attributes the line to the branch commit — so the liveness
       test reads a live line as deleted. In THIS repository every card is filed
       on an unmerged branch, so without the gate the ordinary case is a silent
       false "lost". The answer is `unknown(commit_not_ancestor)`, and it is a
       TEMPORARY state that the branch's merge clears.
    1. Channel A — reverse blame.
    2. The verify, which is NOT skippable and reads the resolved path.
    3. Channel B, only if A did not answer.
    4. Otherwise the refusal A produced.

    The repository gate runs before all of it, because it is the only guard
    against a CONFIDENTLY WRONG answer rather than a missing one: a card can
    carry a commit from one tree and be resolved in another, and the verify
    cannot see that by construction, since it re-reads the tree the blame
    walked. An anchor carrying no `repo` at all — reachable, because
    `updatable_keys` validates nothing — fails the gate rather than skipping it.

    **Named honestly, because the number is in the design and belongs at the
    call site too: the verify rejects a gross error, it does not prove the
    answer.** Half the correctly resolved lines in this tracker (50%, and 35% in
    the larger corpus) carry text that is not unique inside its own file at HEAD,
    so on those the verify would also pass at a wrong line number.
    """
    budget = budget or _Budget()
    stored_repo = anchor.get("repo")
    if not isinstance(stored_repo, str) or repo is None or stored_repo != repo:
        return _record("unknown", path=anchor["path"], reason="repo_mismatch")

    try:
        commit = _resolve_commit(root, anchor.get("commit"), budget)
    except _Refused as refused:
        token = refused.token
        if token == "commit_unreachable":
            rc, out, _ = _git(root, ["rev-parse", "--is-shallow-repository"], budget)
            if rc == 0 and out.strip() == b"true":
                token = "shallow_history"
        return _record("unknown", path=anchor["path"], reason=token)
    except subprocess.TimeoutExpired:
        return _record("unknown", path=anchor["path"], reason="timeout")

    anchor = {**anchor, "commit": commit}
    try:
        rc, _out, _err = _git(root, ["merge-base", "--is-ancestor", commit, "HEAD"], budget)
        if rc != 0:
            return _record("unknown", path=anchor["path"], reason="commit_not_ancestor")

        answer = _channel_a(root, anchor, head=head, budget=budget)
        if answer is not None and answer["status"] not in ("lost", "unknown"):
            return answer
        fallback = _channel_b(root, anchor, head=head, budget=budget)
        if fallback is not None:
            return fallback
        if answer is not None:
            return answer
        # Channel A did not RUN — blame itself exited non-zero. Reporting that as
        # `lost` would be a claim about the CODE made from a failure to look,
        # which is the "guard reporting clean because it could not look" shape
        # this repository has now recorded three times. Ask the object store what
        # is actually wrong and let `read_blob`'s own classifier answer; its
        # default for an unrecognized message is deliberately the token that
        # claims the least.
        try:
            read_blob(root, anchor["commit"], anchor["path"], budget)
        except _Refused as refused:
            return _record("unknown", path=anchor["path"], reason=refused.token)
        return _record("unknown", path=anchor["path"], reason="internal_error")
    except subprocess.TimeoutExpired:
        return _record("unknown", path=anchor["path"], reason="timeout")
    except (subprocess.SubprocessError, OSError):
        return _record("unknown", path=anchor["path"], reason="internal_error")


# --- read side: the batch surface -----------------------------------------------------


#: The three states a candidate row's anchor column can be in. TWO of them make
#: `_stored_loc` return `(False, None)` and all three used to be read through
#: `stored is None`, which is exactly the conflation the backfill population
#: cannot survive: "no anchor was ever captured" is the row this pass exists to
#: reach, "the meta column does not parse" is a row it must never write into,
#: and `loc: null` is a TOMBSTONE that says "do not recapture". The distinction
#: lives here, in the reader, because `read_anchor` receives a VALUE and cannot
#: know by construction whether the key was there at all.
ANCHOR_UNREADABLE = "unreadable"
ANCHOR_ABSENT = "absent"
ANCHOR_PRESENT = "present"


def _anchor_state(row: dict[str, Any]) -> tuple[str, Any]:
    """`(which of the three states, the stored value)` from a raw candidate row.

    Reads `meta_json` as the STORED STRING (CB-24 consequence 4) and tolerates a
    `meta` that does not parse at all: a batch over ten thousand rows must not be
    aborted by one row's malformed column. What it does NOT do any more is call
    that row "carries no anchor" — an unreadable column and an absent key are
    different facts about the row, and only the second one is repairable.

    The value is meaningful only in `ANCHOR_PRESENT`; the other two states carry
    `None` because there is nothing to carry, NOT because the anchor is null.
    """
    if "meta_json" in row:
        # The STORED STRING, which is what `findings.anchor_candidates` hands a
        # batch (CB-24 consequence 4) and the only shape in which "does not
        # parse" is still observable.
        try:
            meta = json.loads(row.get("meta_json") or "{}")
        except (TypeError, ValueError):
            return ANCHOR_UNREADABLE, None
    else:
        # An ordinary read path hands a row through `db.row_to_dict`, which has
        # already parsed the column, so a column that does not PARSE raises
        # there and never reaches this branch. `ANCHOR_UNREADABLE` is still
        # reachable from here and an earlier draft of this comment claimed
        # otherwise: `meta` holding a valid JSON scalar or array (`"a string"`,
        # `[1, 2]`, `123`) parses fine and simply is not an object, which is a
        # different fact from "does not parse" and is why the branch below tests
        # the TYPE rather than trusting the parse. Reading the parsed
        # value here rather than re-serialising it is the point: two spellings
        # of "what counts as an anchor" is one drift away from the read side and
        # the repair side disagreeing about the same row.
        # `row.get("meta", {})` and NOT `row.get("meta") or {}`: a stored JSON
        # `null` parses to Python `None`, and mapping that to `{}` would call it
        # "carries no anchor" while the `meta_json` branch above calls the same
        # bytes `unreadable`. A default reached only when the KEY is missing
        # keeps the two branches answering identically — the drift this
        # docstring warns about, found by review inside the paragraph warning
        # about it.
        meta = row.get("meta", {})
    if not isinstance(meta, dict):
        return ANCHOR_UNREADABLE, None
    if "loc" not in meta:
        return ANCHOR_ABSENT, None
    return ANCHOR_PRESENT, meta["loc"]


def _stored_loc(row: dict[str, Any]) -> tuple[bool, Any]:
    """`(the row carries an anchor key, its stored value)`.

    Derived from `_anchor_state` rather than parsing again: two copies of "what
    counts as an anchor" is one drift away from the read side and the repair
    side disagreeing about the same row.
    """
    state, value = _anchor_state(row)
    return state == ANCHOR_PRESENT, value


class _Context(NamedTuple):
    """Everything a resolution needs that is a property of the TREE, not the row.

    Built once per pass and never per row: `worktree_root` and the two git calls
    behind `head`/`repo` answer the same question for every row in a population,
    so a per-row rebuild would multiply the fixed cost by the page size — the
    exact defect the cost split between `get` and `query` exists to avoid.
    """

    project_dir: str | None
    root: str | None
    head: str | None
    repo: str | None
    reason: str | None = None


def _resolution_context(project_dir: str | None) -> _Context:
    """The per-tree half of a resolution. Costs up to three git calls, or none.

    An EMPTY `project_dir` short-circuits before any process is spawned, exactly
    as `None` does: BT-7 Р3 refuses ambient cwd, so there is nothing to look at
    and nothing to pay for, and both spellings mean "not supplied" on a write-ish
    argument (CB-82). `reason` carries which of the two it was, so `_resolve_one`
    reports `no_root` for both instead of calling an empty string a repository
    that could not be identified.

    **NEVER RAISES, and that had to be said in code rather than assumed.** `_git`
    raises `TimeoutExpired` when the shared budget is spent, and `subprocess.run`
    raises `OSError` of its own (EMFILE, ENOMEM, a git that is not executable —
    CB-79's family). `resolve_anchor` converts all three into a record; this
    function is a NEW subprocess site and review found it was the one place in
    the module that did not. The cost of the omission was not local: the context
    is built once for a whole PAGE, so one infrastructure hiccup escaped through
    `summarize_rows` into the enricher's guard and rewrote every row's summary as
    `unavailable` — including rows carrying no anchor at all, whose correct
    answer needs no git and cannot be wrong. Degrading here keeps the failure
    where it belongs: on the rows that actually asked for a resolution.
    """
    root = worktree_root(project_dir=project_dir) if project_dir else None
    if root is None:
        return _Context(
            project_dir, None, None, None, "no_root" if not project_dir else "no_repo"
        )
    budget = _Budget()
    try:
        rc, out, _ = _git(root, ["rev-parse", "--verify", "HEAD^{commit}"], budget)
        head = out.decode("ascii", "replace").strip() if rc == 0 else None
        return _Context(project_dir, root, head, repo_identity(root, budget))
    except subprocess.TimeoutExpired:
        return _Context(project_dir, root, None, None, "timeout")
    except (subprocess.SubprocessError, OSError):
        return _Context(project_dir, root, None, None, "internal_error")


def _resolve_one(anchor: dict[str, Any], *, ctx: _Context) -> dict[str, Any]:
    """One VALIDATED anchor against a prepared context. THE only resolution path.

    Both the explicit verb (`resolve_findings`) and the ordinary read paths call
    this, deliberately: an inline "cheap" resolver beside the real one is two
    spellings of one decision, and the two would answer differently the first
    time either learned something.
    """
    if ctx.root is None or ctx.head is None:
        return _record("unknown", path=anchor["path"], reason=ctx.reason or "no_repo")
    return resolve_anchor(ctx.root, anchor, head=ctx.head, repo=ctx.repo, budget=_Budget())


def resolve_findings(
    conn: Any,
    *,
    finding_id: str | None = None,
    status: str | None = "open",
    category: str | None = None,
    file: str | None = None,
    project_dir: str | None = None,
    limit: int | None = 10000,
) -> dict[str, Any]:
    """Resolve every stored anchor in a population to a coordinate on HEAD.

    THE DENOMINATOR, said before anything else, because this summary is a
    RATIFIED DEMAND COUNTER and not a convenience. The owner reads the frequency
    of `moved_file` out of it to decide whether a sanctioned identity re-key is
    worth building, so the number must be a share of something it is actually a
    share of:

    * `total` — rows the FILTERS matched.
    * `anchored` — of those, the rows that CARRY an anchor key (`meta.loc`
      present, a persisted refusal object and the `null` tombstone included).
      Anchors exist only on findings filed since BT-7 landed, so on a live
      tracker this is a small fraction of `total`.
    * `results` — one record per anchored row, and nothing else. A row with no
      anchor has nothing to resolve and gets no record.
    * `summary` — status counts over `results`. It sums to `anchored`, NEVER to
      `total`, so `summary["moved_file"] / anchored` is the ratified figure and
      `/ total` is a share of a population the number does not describe.
    * `without_anchor` — `total - anchored`, present so the arithmetic closes in
      the response instead of in the reader's head.

    Every status in the closed vocabulary appears in `summary` with a count of
    zero when nothing landed there, for the same reason each record's keys are
    unconditional: a missing bucket reads as "not evaluated".

    The default population is `open`, matching `provenance.check_findings`, and
    the sentinel `"all"` widens it, matching `similarity.group_report`. Both
    precedents are in this package; the sentinel is compared type-first, because
    a bare `== "all"` is satisfied by `unittest.mock.ANY` (CB-25).

    `project_dir` is REQUIRED in effect: BT-7 Р3 refuses ambient cwd in capitals,
    and a resolution against whatever tree the process happens to stand in is the
    confidently-wrong answer this design spends its whole budget avoiding. An
    unresolvable root is reported once, as `unknown(no_root)`/`unknown(no_repo)`
    on every record, rather than raised — the caller asked about findings, and
    the findings are still there.
    """
    from codebugs import findings

    widen = isinstance(status, str) and status == "all"
    rows = findings.anchor_candidates(
        conn,
        finding_id=finding_id,
        status=None if widen else status,
        category=category,
        file=file,
        limit=limit,
    )

    results: list[dict[str, Any]] = []
    summary = dict.fromkeys(STATUSES, 0)
    anchored = 0

    ctx = _resolution_context(project_dir)

    for row in rows:
        present, value = _stored_loc(row)
        if not present:
            continue
        anchored += 1
        anchor, reason = read_anchor(value)
        if anchor is None:
            record = _record("unknown", path=row.get("file"), reason=reason)
        else:
            record = _resolve_one(anchor, ctx=ctx)
        summary[record["status"]] += 1
        results.append({"finding_id": row["id"], "file": row.get("file"), "anchor": record})

    return {
        "results": results,
        "total": len(rows),
        "anchored": anchored,
        "without_anchor": len(rows) - anchored,
        "summary": summary,
    }


# --- read side: the ORDINARY read paths (Т-56) ----------------------------------------

#: The key an enriched finding row carries. Declared to `db.register_read_enricher`
#: so core never learns this module's vocabulary, exactly as `meta_keys` keeps it
#: ignorant of the capture stamp's name.
SUMMARY_KEY = "anchor"

#: What the STORED column says, before any git runs. CLOSED, and every member is
#: a fact a reader acts on differently — which is the whole point of the seam.
#: Collapsing any two of them is the conflation this design exists to end:
#:
#: * `absent`     — no `meta.loc` key. The 79–96% population. Costs one JSON read.
#: * `unreadable` — the `meta` column itself does not parse.
#: * `invalid`    — there is an object and it breaks its own invariants, or it
#:                  carries a version this build does not implement.
#: * `retracted`  — the `loc: null` TOMBSTONE. Someone said "do not anchor this",
#:                  which is the opposite of nobody having tried.
#: * `refused`    — a persisted refusal object (Р7): capture LOOKED and had
#:                  nothing to grab. On a live tracker this is the common
#:                  anchored state (136 of 158 rows here), so it must read as an
#:                  answer and not as a defect.
#: * `anchored`   — real coordinates. THE ONLY STATE THAT MAY REACH GIT.
#: * `unavailable` — the enricher itself did not produce an answer for this
#:                  row. NOT a fact about the card, which is why it is a state
#:                  and not a silence: the seam guarantees the key, and a reader
#:                  must be able to tell "we could not look" from "there is
#:                  nothing there". `db.run_read_enrichers` builds it through
#:                  `unavailable_summary`, so it carries the SAME ten keys as
#:                  every other summary — a two-key failure object would crash
#:                  every consumer written against the documented shape, on
#:                  exactly the path the seam exists to make survivable (review).
SUMMARY_STATES: tuple[str, ...] = (
    "absent",
    "unreadable",
    "invalid",
    "retracted",
    "refused",
    "anchored",
    "unavailable",
)

#: `read_anchor` reasons that mean "the object is broken" rather than "capture
#: refused". Derived from the refusal vocabulary rather than re-listed, so a new
#: capture token classifies itself.
_INVALID_REASONS = frozenset({"invalid_anchor", "unsupported_anchor_version"})


def _summary(
    state: str,
    *,
    reason: str | None = None,
    stored_path: str | None = None,
    resolution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One anchor summary. EVERY key is present; `None` is a normal answer.

    The unconditional-keys rule is this package's — `claims._response`'s fifteen
    common keys, BT-5's `attention` block, `_record` two hundred lines up. Here
    it is load-bearing twice over, because the reader's question is exactly
    "is there an answer", and a key that appears only sometimes teaches them to
    test for presence, at which point presence silently encodes a second fact.

    `loc_status`, `moved_file` and `path` are HOISTED out of `resolution` because
    they are the three the owner's acceptance names; `resolution` keeps the full
    record beside them (channel, survived, the evidence) for a reader who wants
    to know how the answer was reached. `moved_file` is `None` and never `False`
    when nothing was resolved: "did not move" and "was not checked" are the same
    two facts this vocabulary refuses to merge one level up.
    """
    if state not in SUMMARY_STATES:  # RAISE, never assert: `-O` strips assert
        raise ValueError(f"anchor summary state {state!r} is not in SUMMARY_STATES")
    if reason is not None and reason not in REASONS:
        raise ValueError(f"anchor summary reason {reason!r} is not in REASONS")
    return {
        "state": state,
        "reason": reason,
        "stored_path": stored_path,
        "resolved": resolution is not None,
        "loc_status": resolution["status"] if resolution else None,
        "moved_file": (resolution["status"] == "moved_file") if resolution else None,
        "path": resolution["path"] if resolution else None,
        "line": resolution["line"] if resolution else None,
        "end": resolution["end"] if resolution else None,
        "resolution": resolution,
    }


def unavailable_summary(error: str | None) -> dict[str, Any]:
    """The summary for a row this extension could not answer for.

    Registered with the seam so `db.py` can stamp it without learning a single
    one of this module's key names — the same reason `meta_keys` is declared by
    the pre-add resolver rather than known by the runner.
    """
    summary = _summary("unavailable")
    summary["error"] = error
    return summary


def classify_row(row: dict[str, Any]) -> tuple[str, dict[str, Any] | None, str | None]:
    """`(state, the validated anchor, the read reason)` for one row. NO GIT, EVER.

    This is the structural half of the "costs nothing on an unanchored card"
    guarantee: a row that is not `anchored` never acquires an anchor object, so
    the caller has nothing to hand a resolver and the resolver is not reached —
    as opposed to being reached and returning quickly.

    A persisted REFUSAL is `refused`, not `anchored`, and that split is the one
    worth stating: `_stored_loc` calls it "an anchor is present" because a key
    is there, which is the right answer to ITS question and the wrong one to
    this one. There is nothing to resolve in a refusal, so sending it to git
    would be a process spawned to learn nothing — and on this tracker that is
    the majority of the anchored population.
    """
    state, value = _anchor_state(row)
    if state == ANCHOR_UNREADABLE:
        return "unreadable", None, "unreadable_meta"
    if state == ANCHOR_ABSENT:
        return "absent", None, None
    anchor, reason = read_anchor(value)
    if anchor is not None:
        return "anchored", anchor, None
    if reason == "retracted":
        return "retracted", None, reason
    if reason in _INVALID_REASONS:
        return "invalid", None, reason
    return "refused", None, reason


def summarize_rows(
    rows: Sequence[dict[str, Any]],
    *,
    resolve: bool = False,
    project_dir: str | None = None,
) -> list[dict[str, Any]]:
    """One summary per row, resolving anchored rows against HEAD when asked.

    ONE PASS: the per-tree context is built at most once for the whole
    population, and LAZILY — the first anchored row pays for it and a page
    without one never spawns a process at all. So the git cost of a page is
    `fixed + per-anchored-row`, never `page-size × anything`.

    `resolve=False` still answers, and answers cheaply: state, the stored path,
    the refusal token. That is the `query` default, and it is not a degraded
    mode — "this card carries an anchor" is a fact worth a page of results and
    it costs one JSON read that the row had already paid for.
    """
    ctx: _Context | None = None
    out: list[dict[str, Any]] = []
    for row in rows:
        state, anchor, reason = classify_row(row)
        if state != "anchored":
            out.append(_summary(state, reason=reason))
            continue
        if not resolve:
            out.append(_summary(state, stored_path=anchor["path"]))
            continue
        if ctx is None:
            ctx = _resolution_context(project_dir)
        record = _resolve_one(anchor, ctx=ctx)
        out.append(
            _summary(
                state,
                reason=record["reason"],
                stored_path=anchor["path"],
                resolution=record,
            )
        )
    return out


def enrich_findings(
    conn: Any,
    rows: list[dict[str, Any]],
    *,
    resolve: bool,
    project_dir: str | None = None,
) -> None:
    """`db.register_read_enricher` seam: stamp `row["anchor"]` on finding rows.

    `conn` is unused, and that is this module's licence to exist: like
    `similarity.py`, `loc` issues ZERO SQL and never reaches into a domain
    module's tables. The rows arrive already read.
    """
    del conn  # zero-SQL extension; the seam's signature requires the parameter
    # `strict=True`: a length mismatch would truncate SILENTLY and the tail rows
    # would then be filled with `unavailable` by the runner — a logic bug in this
    # module reported to the reader as an infrastructure failure. Raising routes
    # it into the seam's visible-failure path instead, which is what that path is
    # for.
    summaries = summarize_rows(rows, resolve=resolve, project_dir=project_dir)
    for row, summary in zip(rows, summaries, strict=True):
        row[SUMMARY_KEY] = summary


# --- read side: the sanctioned repair path (Р4) ---------------------------------------

#: What `recapture_findings` did to one row. CLOSED, and every outcome is a
#: decision the four specified points make explicitly rather than a fall-through.
RECAPTURE_OUTCOMES: tuple[str, ...] = (
    "updated",
    "would_update",
    "unchanged",
    "kept",
    "tombstoned",
    "stale",
    # --- the backfill population (BT-7 Т-c), reachable only under
    # `include_unanchored=True`. DELIBERATELY NOT folded into `updated`: the
    # question this pass exists to answer is "how many rows acquired an anchor
    # for the FIRST time", and added to "how many anchors were refreshed" it
    # stops answering it. Both halves of the dry-run/apply pair, by the
    # `would_update`/`updated` precedent.
    "would_backfill",
    "backfilled",
    # A row whose `meta` column does not parse is NOT a backfill candidate:
    # writing an anchor into it would rewrite a column we could not read. It is
    # counted rather than silently skipped, because "nothing to report" and
    # "eleven rows I refused to touch" are different answers.
    "unreadable_meta",
)


def recapture_findings(
    conn: Any,
    *,
    finding_id: str | None = None,
    status: str | None = "open",
    category: str | None = None,
    file: str | None = None,
    project_dir: str | None = None,
    apply: bool = False,
    force_tombstone: bool = False,
    include_unanchored: bool = False,
    limit: int | None = 10000,
) -> dict[str, Any]:
    """Rebuild stored anchors from the git object store — the SANCTIONED repair (Р4).

    WHY THE VERB EXISTS AT ALL. `updatable_keys=("loc",)` opens `meta.loc` on the
    update path and validates NOTHING, so a hand-assembled object is accepted at
    the write and read back as `unknown(invalid_anchor)`. That is deliberate — an
    annotation nobody can repair is the CB-26 shape — but it means the repair has
    to be a verb that BUILDS the object, not a human typing JSON. This is that
    verb, and it shares `capture` with the resolver seam, so a repaired anchor is
    byte-identical to one the file-time path would have produced.

    DRY RUN BY DEFAULT, like `findings.normalize_categories`: without
    `apply=True` no transaction is opened at all.

    **The four points §9 requires to be SPECIFIED — behaviour chosen, written
    down, and pinned by a test — and the answer to each:**

    1. **Does it read inside a transaction? Split, and the split is the answer.**
       The population scan and the git capture run with NO transaction open, and
       only the version check and the write share one `db.txn`. Doing it the
       other way is not a style choice: capture spends up to `CAPTURE_BUDGET_S`
       per row in subprocesses, so holding the write lock across a batch would
       blow past the competing writer's `busy_timeout=5000` after three rows —
       and `SQLITE_BUSY` is not in `db._is_environmental`, so that writer gets a
       raw traceback. Keeping the git work outside the lock and the check-and-
       write inside it makes point 4 a real compare-and-swap instead of a
       check-then-act with a two-second window.
    2. **Does a FAILED capture replace a valid stored anchor? No.** This is the
       one point the design answers itself. When the new capture is a refusal
       object and the stored anchor still holds coordinates, nothing is written
       and the outcome is `kept`. A repair that can destroy what it was called to
       repair is worse than no repair: the refusal is usually about the
       ENVIRONMENT (a commit this clone lacks, a shallow history), and the
       anchor it would overwrite is still perfectly good in a clone that has the
       history.
    3. **Does it override the tombstone? Only on an explicit flag.** `loc: null`
       means "do not recapture" and is written by hand for exactly that purpose,
       so a bulk repair sweeping it away silently would make the tombstone
       unwritable in practice. Default outcome is `tombstoned`;
       `force_tombstone=True` is a typed statement of intent.
    4. **Does it check the row's version before overwriting? Yes** — inside the
       transaction from point 1, against the stored `meta.loc` this run scanned.
       A row whose anchor changed while the capture ran is left alone and
       reported `stale`, because the other writer is the one holding fresher
       information. The comparison is on the anchor VALUE, not on a row
       timestamp: an unrelated status write in the same window must not cost a
       repair.

    **THE BACKFILL POPULATION (BT-7 Т-c), behind `include_unanchored`.** Anchor
    capture lives only in the resolver seam of a genuine new finding, so every
    row filed before that seam landed carries no anchor at all — and this pass
    could not reach them, because it skipped any row without an anchor key
    before it ever captured. `include_unanchored=True` widens the POPULATION (it
    says which rows are taken, not what is done to them) to rows whose `meta`
    carries no `loc` key. Three things it deliberately is not:

    - **It is not a fingerprint backfill.** `fingerprint` is IDENTITY and
      re-keying is a separately negotiated contract with exactly one sanctioned
      operation (`findings.normalize_categories`, CB-61). Nothing here reads or
      writes that column. An anchor is a derived coordinate, is not part of
      identity, and is rebuilt from what the row already stores.
    - **It is not `force_tombstone`.** `loc: null` is a key that is present and
      null — an instruction not to recapture — and this flag never touches it.
      Merging the two would turn "take the rows that were never anchored" into a
      way to erase tombstones, which is a different question with a different
      answer.
    - **It does not weaken points 1-4.** Capture still runs outside any
      transaction, the compare-and-swap of point 4 still refuses a row that
      moved under the capture (and compares the anchor's STATE as well as its
      value, so a row that ACQUIRED an anchor mid-run is refused), and point 2
      is untouched because it has nothing to protect on a row with no anchor.

    Its outcomes are `would_backfill`/`backfilled`, never folded into
    `would_update`/`updated`: "acquired an anchor for the first time" is the
    number this exists to produce and adding it to "refreshed an anchor"
    destroys it.
    """
    from codebugs import findings

    # CB-82 on a write path: "not supplied" is `None`, never truthiness. These
    # three gate WRITES — applying at all, overwriting a tombstone, and widening
    # the population — so `apply="false"` must not open a transaction. Validated
    # as one rule over all three rather than at the one the card named: a
    # per-argument guard is an enumeration, and the next bool added to this
    # signature would have to re-acquire it.
    for _name, _value in (
        ("apply", apply),
        ("force_tombstone", force_tombstone),
        ("include_unanchored", include_unanchored),
    ):
        if not isinstance(_value, bool):
            raise ValueError(f"{_name} must be a bool, got {type(_value).__name__}")

    widen = isinstance(status, str) and status == "all"
    rows = findings.anchor_candidates(
        conn,
        finding_id=finding_id,
        status=None if widen else status,
        category=category,
        file=file,
        limit=limit,
    )

    results: list[dict[str, Any]] = []
    summary = dict.fromkeys(RECAPTURE_OUTCOMES, 0)

    for row in rows:
        state, stored = _anchor_state(row)
        if state != ANCHOR_PRESENT and not include_unanchored:
            # Today's behaviour, and the default: this pass repairs anchors that
            # EXIST. A row that never carried one is a different question, and
            # it is answered only when the caller names the population.
            continue

        if state == ANCHOR_UNREADABLE:
            # In the population (it does look unanchored from outside) but never
            # a candidate: an anchor written here would overwrite a `meta`
            # column this process could not read.
            outcome, fresh = "unreadable_meta", None
        elif state == ANCHOR_ABSENT:
            fresh = _fresh_capture(row, project_dir)
            # POINT 2 DOES NOT APPLY HERE, and reading it as if it did is the
            # trap this branch exists to avoid. Point 2 protects a VALID STORED
            # anchor from being replaced by a refusal; there is no stored anchor
            # on this branch, so `had_anchor` would be asking about something
            # that does not exist and `kept` would report a protection that
            # protected nothing. A refusal object is written as the honest
            # record instead — byte-identical to what the file-time resolver
            # stamps on a new finding (Р8 stores `{"v", "skipped",
            # "sites_dropped"}`), and its Р8 token reaches the caller in
            # `reason`, which is where every other outcome's token already is.
            if not apply:
                outcome = "would_backfill"
            else:
                outcome = _apply_recapture(
                    conn,
                    row["id"],
                    was_state=state,
                    stored=stored,
                    fresh=fresh,
                    written="backfilled",
                )
        elif stored is None and not force_tombstone:
            # Reachable ONLY from ANCHOR_PRESENT, which is the whole fix: the
            # tombstone is `loc: null`, a key that is THERE and null. Keyed on
            # `stored is None` alone, this arm swallowed every unanchored row
            # and reported the backfill population as "retracted".
            outcome, fresh = "tombstoned", None
        else:
            fresh = _fresh_capture(row, project_dir)
            # The tombstone counts as something worth protecting, not just a
            # valid anchor. `force_tombstone` says "recapture this one", not
            # "destroy the instruction if the recapture fails" — and a refusal
            # object written over `loc: null` is worse than leaving it, because
            # the tombstone means "never recapture" while a refusal object is
            # something the very next unforced run would happily overwrite.
            had_anchor = stored is None or read_anchor(stored)[0] is not None
            if "skipped" in fresh and had_anchor:
                outcome = "kept"  # point 2
            elif fresh == stored:
                outcome = "unchanged"
            elif not apply:
                outcome = "would_update"
            else:
                outcome = _apply_recapture(
                    conn,
                    row["id"],
                    was_state=state,
                    stored=stored,
                    fresh=fresh,
                    written="updated",
                )

        summary[outcome] += 1
        results.append(
            {
                "finding_id": row["id"],
                "outcome": outcome,
                "reason": (fresh or {}).get("skipped"),
            }
        )

    return {
        "results": results,
        "total": len(rows),
        "applied": apply,
        "summary": summary,
    }


def _fresh_capture(row: dict[str, Any], project_dir: str | None) -> dict[str, Any]:
    """Build the anchor this row would get today — OUTSIDE any transaction (point 1).

    Shared by the repair branch and the backfill branch so the two cannot drift
    into capturing from different observation shapes: a backfilled anchor must be
    byte-identical to one the file-time resolver seam would have produced, which
    is the entire argument for backfilling instead of re-filing.
    """
    return capture(
        {
            "file": row.get("file"),
            "meta": _parsed_meta(row),
            "reported_at_commit": row.get("reported_at_commit"),
            "project_dir": project_dir,
        }
    )


def _parsed_meta(row: dict[str, Any]) -> dict[str, Any]:
    """The row's stored meta as a dict, or `{}` when it does not parse.

    A row whose meta is unreadable has no grammar to capture from, so `capture`
    refuses it with `no_grammar` — which is the honest token and, crucially, one
    that leaves a valid stored anchor untouched by point 2.
    """
    try:
        meta = json.loads(row.get("meta_json") or "{}")
    except (TypeError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def _apply_recapture(
    conn: Any,
    finding_id: str,
    *,
    was_state: str,
    stored: Any,
    fresh: dict[str, Any],
    written: str,
) -> str:
    """Point 4: compare-and-swap the anchor inside ONE transaction.

    `db.txn` takes the write lock BEFORE the re-read, which is the whole
    difference between this and a check-then-act — the same argument CB-24 makes
    for `update_finding`'s own body. `update_finding`'s `db.txn` is reentrant and
    yields False under this one, so the check and the write commit together.

    This module still issues ZERO SQL, which is its licence to exist: it composes
    a `db` transaction primitive with two `findings`-owned operations. No
    statement is written here.
    """
    from codebugs import findings

    with db.txn(conn):
        current = findings.anchor_candidates(conn, finding_id=finding_id, limit=1)
        if not current:
            return "stale"  # the row went away under us
        state_now, now = _anchor_state(current[0])
        # The comparison is on the STATE as well as the value, and the value
        # alone is not enough — that is what the backfill population changes
        # here. A row scanned as ANCHOR_ABSENT that acquired a `loc: null`
        # tombstone under the capture has `now == stored` (both `None`), so a
        # value-only CAS would write straight through the instruction it was
        # told never to touch. Comparing the state refuses instead, which is
        # point 4 read for a population that did not exist when it was written.
        if state_now != was_state or now != stored:
            return "stale"
        # `authored=False` (CB-230): the anchor is this module's own output, not a
        # human's edit of the card, so it must not move `updated_at`. Without it the
        # mass capture of 2026-08-24 rewrote the last-change date of 136 of this
        # tracker's 233 cards inside one six-second window, and there is no source
        # from which those dates can be restored.
        findings.update_finding(
            conn, finding_id, meta_update={"loc": fresh}, authored=False
        )
    return written


# --- CLI ------------------------------------------------------------------------------


def register_cli(sub, commands) -> None:
    """Register the two anchor CLI verbs.

    The capture resolver registers at module import and is unaffected by
    `--mode`; this only gates which VERBS are exposed.
    """
    import json as _json

    from codebugs.fmt import format_table

    p_res = sub.add_parser(
        "anchor-resolve", help="Resolve stored location anchors to lines on HEAD"
    )
    p_rec = sub.add_parser(
        "anchor-recapture", help="Rebuild stored location anchors (dry run by default)"
    )
    for p in (p_res, p_rec):
        p.add_argument("--finding-id", default=None, dest="finding_id")
        p.add_argument(
            "--status",
            default="open",
            help='status filter; "all" widens to every status (default: open)',
        )
        p.add_argument("--category", default=None)
        p.add_argument("--file", default=None)
        p.add_argument(
            "--repo",
            default=None,
            help="repo dir the anchors resolve against (also locates .codebugs/). "
            "BT-7 refuses ambient cwd for the ANCHOR, so omitting it reports "
            "no_root rather than guessing a tree",
        )
        p.add_argument("--limit", type=int, default=10000)
        p.add_argument("--json", action="store_true", dest="as_json")
    p_rec.add_argument(
        "--apply", action="store_true", help="write the rebuilt anchors (default: dry run)"
    )
    p_rec.add_argument(
        "--force-tombstone",
        action="store_true",
        dest="force_tombstone",
        help='override a `loc: null` tombstone ("do not recapture"), which is '
        "otherwise left alone",
    )
    # A SEPARATE flag from --force-tombstone, deliberately. This one widens the
    # POPULATION (rows that never carried an anchor); that one overrides an
    # INSTRUCTION (`loc: null`). One flag for both would make "take the rows
    # nobody anchored" the way to erase tombstones.
    p_rec.add_argument(
        "--include-unanchored",
        action="store_true",
        dest="include_unanchored",
        help="also take rows whose meta carries no `loc` key at all — the "
        "backfill population, skipped by default. Does NOT touch a `loc: null` "
        "tombstone (that is --force-tombstone) and is still a dry run without "
        "--apply",
    )

    def _cmd_anchor_resolve(args) -> None:
        from codebugs.cli import domain_errors

        # `anchor_candidates` hands back `meta_json` as the stored string and
        # `_stored_loc` tolerates a column that does not parse, so this path
        # cannot actually raise json.JSONDecodeError today. Routed through the
        # shared wrapper anyway (cli.py's domain_errors) rather than reasoned
        # about per-handler, which is what similarity.py's history shows goes
        # wrong: an arm hand-written here and then hand-removed as
        # "unreachable" is exactly the thrash CB-55 exists to end.
        conn = db.connect()
        try:
            with domain_errors(prefix="Error: "):
                result = resolve_findings(
                    conn,
                    finding_id=args.finding_id,
                    status=args.status,
                    category=args.category,
                    file=args.file,
                    project_dir=args.repo,
                    limit=args.limit,
                )
        finally:
            conn.close()

        if args.as_json:
            print(_json.dumps(result, indent=2))
            return
        cols = ["ID", "STATUS", "CHANNEL", "PATH", "LINE", "REASON", "SURVIVED"]
        rows = [
            {
                "ID": r["finding_id"],
                "STATUS": r["anchor"]["status"],
                "CHANNEL": r["anchor"]["channel"] or "-",
                "PATH": r["anchor"]["path"] or "-",
                "LINE": r["anchor"]["line"] if r["anchor"]["line"] is not None else "-",
                "REASON": r["anchor"]["reason"] or "-",
                "SURVIVED": r["anchor"]["survived"] or "-",
            }
            for r in result["results"]
        ]
        print(format_table(rows, cols))
        counts = ", ".join(f"{k}={v}" for k, v in result["summary"].items())
        print(f"\n{counts}")
        # The denominator is printed with the number, never left to the reader:
        # the moved_file share is of ANCHORED rows, and `total` is a different
        # population entirely (resolve_findings' docstring carries the rule).
        print(
            f"{result['anchored']} of {result['total']} findings carry an anchor "
            f"({result['without_anchor']} do not); the counts above are over the "
            f"{result['anchored']} anchored."
        )

    def _cmd_anchor_recapture(args) -> None:
        from codebugs.cli import domain_errors

        # JSONDecodeError re-raises rather than printing as bad input, and here
        # the hazard is real: on `--apply` this path reaches `update_finding`,
        # which converts the mutated row AFTER its transaction commits, so a
        # row with malformed stored `meta`/`tags` raises from a write that HAS
        # ALREADY LANDED. Reporting that through the input-validation arm is
        # the CB-15/CB-16 lie — exactly what `domain_errors` (cli.py) encodes.
        conn = db.connect()
        try:
            with domain_errors(prefix="Error: "):
                result = recapture_findings(
                    conn,
                    finding_id=args.finding_id,
                    status=args.status,
                    category=args.category,
                    file=args.file,
                    project_dir=args.repo,
                    apply=args.apply,
                    force_tombstone=args.force_tombstone,
                    include_unanchored=args.include_unanchored,
                    limit=args.limit,
                )
        finally:
            conn.close()

        if args.as_json:
            print(_json.dumps(result, indent=2))
            return
        rows = [
            {"ID": r["finding_id"], "OUTCOME": r["outcome"], "REASON": r["reason"] or "-"}
            for r in result["results"]
        ]
        print(format_table(rows, ["ID", "OUTCOME", "REASON"]))
        counts = ", ".join(f"{k}={v}" for k, v in result["summary"].items())
        print(f"\n{counts}")
        if not args.apply:
            print("Dry run — nothing was written. Pass --apply to write.")

    commands.update(
        {
            "anchor-resolve": _cmd_anchor_resolve,
            "anchor-recapture": _cmd_anchor_recapture,
        }
    )


# --- MCP ------------------------------------------------------------------------------


def register_tools(mcp, conn_factory) -> None:
    """Register the two anchor MCP tools.

    NAMING, because it is an exception and this repository records those rather
    than letting them look like drift: the module is `loc` and the tools are
    `anchor_*`. The prefix names the OBJECT the tools operate on, which is what
    BT-7 §9 specifies them as and what the design and every review round called
    them. A `loc_` prefix would match the file name and match nothing a reader
    of the design has ever seen.
    """

    @mcp.tool()
    def anchor_resolve(
        finding_id: str | None = None,
        status: str | None = "open",
        category: str | None = None,
        file: str | None = None,
        project_dir: str | None = None,
        limit: int = 10000,
    ) -> dict[str, Any]:
        """Resolve stored location anchors to their current lines on HEAD.

        Each anchored finding gets a record: `status` (current / moved /
        moved_file / lost / ambiguous / unknown), the coordinate, the `channel`
        that produced it ("git" for reverse blame, "content" for the secondary
        text channel), a `reason` token when there is no answer, and
        `survived` as "<n>/<m>" when part of a span outlived the rest.

        `moved_file` is a status of its own, not `moved` with a different path:
        the code left the file the finding names, and a consumer must see that
        rather than receive a line number in a file it never asked about.

        THE SUMMARY'S DENOMINATOR IS `anchored`, NOT `total`. `anchored` counts
        the rows that CARRY an anchor (a persisted refusal and the tombstone
        included); rows filed before anchors existed carry none and are counted
        in `without_anchor` instead. So a `moved_file` share is
        `summary["moved_file"] / anchored`; computing it against `total` is a
        share of a population the number does not describe.

        Args:
            finding_id: Resolve one finding instead of a population
            status: Status filter; "all" widens to every status (default: open)
            category: Restrict to one category
            file: Restrict to one file (the finding's `file` column)
            project_dir: The repository the anchors resolve against. Omitting it
                reports `no_root` rather than reading whatever tree the server
                process happens to stand in — a long-lived server's cwd has
                nothing to do with the tracker a call is about
            limit: Maximum findings examined (default 10000)
        """
        with conn_factory() as conn:
            return resolve_findings(
                conn,
                finding_id=finding_id,
                status=status,
                category=category,
                file=file,
                project_dir=project_dir,
                limit=limit,
            )

    @mcp.tool()
    def anchor_recapture(
        finding_id: str | None = None,
        status: str | None = "open",
        category: str | None = None,
        file: str | None = None,
        project_dir: str | None = None,
        apply: Annotated[bool, Field(strict=True)] = False,
        force_tombstone: Annotated[bool, Field(strict=True)] = False,
        include_unanchored: Annotated[bool, Field(strict=True)] = False,
        limit: int = 10000,
    ) -> dict[str, Any]:
        """Rebuild stored location anchors from the git object store. DRY RUN by default.

        The sanctioned repair path: `meta.loc` is writable through
        `update_finding(meta_update=)` and NOTHING validates it there, so a
        hand-assembled object is accepted at the write and read back as
        `unknown(invalid_anchor)`. This verb builds the object itself, from the
        same capture the file-time resolver uses.

        Four behaviours are specified rather than incidental. A FAILED capture
        never replaces a valid stored anchor (outcome `kept`) — the refusal is
        usually about the environment, and the anchor it would destroy is still
        good in a clone that has the history. The `loc: null` tombstone ("do not
        recapture") is left alone unless `force_tombstone` says otherwise. The
        git work runs outside any transaction, and only the version check and
        the write share one — so a row whose anchor changed while the capture
        ran is reported `stale` and left to the other writer.

        `include_unanchored` widens the POPULATION to rows that never carried an
        anchor at all — every finding filed before the capture seam landed, since
        capture runs only when a genuine new finding is filed. They report
        `would_backfill`/`backfilled`, never folded into `would_update`/`updated`:
        "acquired an anchor for the first time" is the number this exists to
        produce. It is NOT `force_tombstone` (a `loc: null` tombstone is a key
        that is present and null, and this flag never touches it) and it is NOT a
        fingerprint backfill — nothing here reads or writes that column.

        Args:
            finding_id: Repair one finding instead of a population
            status: Status filter; "all" widens to every status (default: open)
            category: Restrict to one category
            file: Restrict to one file (the finding's `file` column)
            project_dir: The repository to capture from. Omitting it makes every
                capture refuse with `no_root`, which by the rule above leaves
                every valid anchor untouched
            apply: Write the rebuilt anchors (default: report only)
            force_tombstone: Overwrite a `loc: null` tombstone
            include_unanchored: Also take rows carrying no `loc` key at all (the
                backfill population). Leaves tombstones alone; still a dry run
                unless `apply` is set
            limit: Maximum findings examined (default 10000)
        """
        with conn_factory() as conn:
            return recapture_findings(
                conn,
                finding_id=finding_id,
                status=status,
                category=category,
                file=file,
                project_dir=project_dir,
                apply=apply,
                force_tombstone=force_tombstone,
                include_unanchored=include_unanchored,
                limit=limit,
            )


# --- registration ---------------------------------------------------------------------


def _capture_resolver(conn: Any, observation: dict[str, Any]) -> dict[str, Any] | None:
    """Pre-add resolver: stamp `meta.loc` on a genuine new finding.

    `conn` is unused and that is the module's licence to exist: like
    `similarity.py`, this extension issues ZERO SQL, so it never reaches into a
    domain module's tables.

    Returns a patch ALWAYS, never None — the refusal object is the point (Р7).
    """
    del conn  # zero-SQL extension; the seam's signature requires the parameter
    return {"loc": capture(observation)}


# `loc` is declared UPDATABLE for the same reason `similar_to` is: the add-side
# reservation is what stops a caller inventing coordinates, but an anchor that
# can never be repaired or retracted is the CB-26 shape — and this object has a
# tombstone (`loc: null`) whose whole purpose is to be written by hand.
db.register_pre_add_resolver(
    "loc.capture",
    _capture_resolver,
    meta_keys=("loc",),
    updatable_keys=("loc",),
)

db.register_read_enricher(
    "loc.anchor", enrich_findings, key=SUMMARY_KEY, fallback=unavailable_summary
)

db.register_tool_provider("loc", register_tools)
db.register_cli_provider("loc", register_cli)
