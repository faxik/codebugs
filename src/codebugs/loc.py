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

import re
import subprocess
import threading
import time
from collections.abc import Sequence
from typing import Any, NamedTuple

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
