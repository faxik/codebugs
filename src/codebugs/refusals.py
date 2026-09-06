"""ONE source for "an expected refusal" versus "a crash" (CB-311).

WHAT THE RULE IS, IN WORDS. Some failures mean *I understood you and I am
refusing you* — an unknown vocabulary value, a missing card, no tracker in this
directory — and their text is written FOR A PERSON, so both surfaces show it.
(Not necessarily as ONE LINE: three refusals in `findings.py` carry a newline in
their own message, and over MCP the text travels as a `ToolError` message where
"line" means nothing at all.) Everything else means *I broke*, and the two
surfaces then diverge in a way worth stating: over MCP the text is withheld and
stays in the server's log, because an unexpected exception's message can carry
another caller's data, while on the COMMAND LINE the traceback goes to the
process that ran the command — which already sees the output and the exit code,
so the traceback discloses nothing it could not obtain anyway, and is the more
useful answer. (That caller is often a script or an agent rather than a person,
which is why the reason is put this way rather than as "no one else to protect".)

WHY IT IS A MODULE RATHER THAN THREE `except` CLAUSES. Before this file the one
rule was spelled out by ENUMERATION in three places that nothing compared:
`cli.domain_errors`' inline `(ValueError, KeyError)`, `cli.main`'s outer arm
over three `db` classes, and `server._EXPECTED_REFUSALS`. They agreed only by
COINCIDENCE — `db.TrackerExistsError` sat in the CLI's list and not in the MCP
one, harmless only because `init` happens to have no MCP tool — and CB-310's own
comment named the gap exactly: *"what is missing is the enforcement, not the
member"*. A module that both surfaces DERIVE from replaces "three edits that
must agree" with one edit, and `tests/test_refusal_classification.py` then
checks the derivation against what the two REAL boundaries actually do.

HOW A CLASS IS CLASSIFIED: BY ITS OWN NAME IN `CLASSIFICATION`, NEVER BY
INHERITANCE. A class the table does not name is a CRASH, whatever it inherits
from — fail-closed, because a new exception nobody classified must not be able
to acquire a person-facing text by descending from `ValueError`. Silent
inheritance is the defect this unit exists to close, so the table names every
package class that would otherwise inherit a classification, and the guard
REFUSES a package exception class that is a subclass of a named refusal and is
not itself named. `db.WorktreeTrackerError` is exactly such a row.

**THE EXACT WIDTH OF THAT GUARANTEE, BECAUSE THE SENTENCE ABOVE IS TRUE OF THE
TABLE AND NOT OF THE BOUNDARIES.** `kind_of` reads the table by exact class. The
boundaries apply it as `except <tuple>`, and Python's `except` matches by
INHERITANCE — so the promise holds over the classes this PACKAGE declares (the
guard walks them and refuses an unnamed subclass), and it does NOT hold over
foreign subclasses of a named refusal. `UnicodeDecodeError` and `binascii.Error`
are subclasses of `ValueError` in the standard library; if either reached a
boundary today it would be shown to a person as an understandable refusal
without any row deciding that. That residual is NAMED rather than closed: making
it false would mean catching broadly and dispatching on `kind_of(type(exc))`,
which changes what both boundaries catch — a ratified boundary, and a different
negotiation from CB-311's. `CRASHES_INSIDE_REFUSALS` is the one compensation
that exists, and it only covers classes the table names.

THE THREE KINDS, AND WHY REFUSALS ARE SPLIT IN TWO. `crash` is the fail-closed
default. The two refusal kinds are not two mechanisms — both end as one line on
stderr and exit 1 at the CLI, and as a `ToolError` carrying the text over MCP —
they record WHERE THE REFUSAL COMES FROM, which is what decides which of the
CLI's two arms sees it:

- ``input``   — raised by the domain call itself, about the ARGUMENTS.
                Caught by `cli.domain_errors`, around one domain call.
- ``tracker`` — raised while OPENING or CREATING the tracker, before or around
                the domain call, by `db._open` / `db.init_project`. Nothing
                inside `domain_errors`' region can catch it, so `cli.main`'s
                outer arm does.

The MCP surface has one boundary and therefore takes both kinds together.

`CRASHES_INSIDE_REFUSALS` IS DERIVED, NOT LISTED, AND IT IS WHY ARC ORDER IS
PRESERVED RATHER THAN RE-DECIDED. `json.JSONDecodeError` **is** a `ValueError`
subclass, so a single `except` over the refusals would swallow it — and it means
the write ALREADY LANDED and only the response's serialization then failed
(CB-16/CB-86), which reported as "bad input" is the lie those cards are about.
Any class the table calls a `crash` while it descends from a refusal must
therefore be re-raised FIRST. Both surfaces spell that as one arm built from
this tuple, so the ordering is a property of the data rather than of two
`except` clauses remembering to agree.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from codebugs import db, surfacegen

INPUT = "input"
TRACKER = "tracker"
CRASH = "crash"


@dataclass(frozen=True)
class Classified:
    """One row: what this class is, and WHY — the reason is read by the guard.

    `reason` is not a comment. `tests/test_refusal_classification.py` reads it
    and refuses an empty one, on the CB-179 discipline: a table whose rows can
    lose their justification becomes the place classes get parked silently, and
    the parking is invisible precisely because the table looks deliberate.
    """

    kind: str
    reason: str


CLASSIFICATION: dict[type[BaseException], Classified] = {
    ValueError: Classified(
        INPUT,
        "The domain layer's declared way to say 'this argument is not valid' — an unknown "
        "vocabulary value, a malformed id, a refused combination. Its text names the value "
        "and is addressed to whoever typed it.",
    ),
    KeyError: Classified(
        INPUT,
        "The domain layer's declared way to say 'no such entity'. The text carries the id "
        "the caller asked for, which is the whole content of the answer.",
    ),
    json.JSONDecodeError: Classified(
        CRASH,
        "A ValueError SUBCLASS that must NOT be read as bad input. The case that decided it "
        "is corrupted stored meta/tags met while serializing the response of a write that "
        "ALREADY COMMITTED (CB-16/CB-86) — reporting a committed mutation as a tidy input "
        "error is the lie those cards name. It is NOT universally post-commit: "
        "`findings._bump_row` raises it on malformed stored meta BEFORE any write, landing "
        "nothing. The classification is the same either way, because a corrupted row is a "
        "broken tracker rather than a caller's bad argument, and a traceback is the useful "
        "answer to it — but the reason is stated at the width it actually holds.",
    ),
    surfacegen.DeclarationError: Classified(
        INPUT,
        "A ValueError subclass raised while a tool or CLI declaration is BUILT — at "
        "registration time, never from inside a call — so NO boundary observes it today and "
        "its kind decides nothing about running behaviour. It is named rather than left to "
        "inherit, because this table forbids silent inheritance, and it takes the kind its "
        "base class already gives it, which is the state before CB-311 and after. Read that "
        "as 'not re-decided' rather than as an endorsement: whether a declaration error "
        "SHOULD be a crash is a real question, its own subject, and answering it here would "
        "move a ratified boundary CB-311 is explicitly not moving.",
    ),
    db.DatabaseNotFoundError: Classified(
        TRACKER,
        "There is no tracker at or above this directory. The text names the path and the "
        "`codebugs init` remedy; a client that receives it instead of a bare 'Error "
        "executing tool get' is the case CB-310 was filed for.",
    ),
    db.TrackerUnwritableError: Classified(
        TRACKER,
        "The tracker was found but could not be opened for writing — classified inside "
        "`db._open` as a TYPE rather than at a boundary (CB-86), so the type itself carries "
        "the provenance 'this failed while opening a connection'.",
    ),
    db.TrackerExistsError: Classified(
        TRACKER,
        "`init` refusing to create a tracker where one already exists or would be shadowed. "
        "It is UNREACHABLE from MCP today, since only `db.init_project` raises it and `init` "
        "has no tool — and that is precisely why it belongs here: before CB-311 the MCP list "
        "omitted it and stayed correct by coincidence, with nothing to notice if `init` were "
        "ever exposed.",
    ),
    db.WorktreeTrackerError: Classified(
        TRACKER,
        "A subclass of TrackerExistsError for the git-worktree case. Named on its own row "
        "because classification is read by EXACT class and never inherited: a subclass that "
        "quietly borrows its parent's kind is the silent-inheritance hole this table closes.",
    ),
}


def kind_of(exc_type: type[BaseException]) -> str:
    """The classification of `exc_type` — `CRASH` unless the table names it exactly."""
    row = CLASSIFICATION.get(exc_type)
    return row.kind if row is not None else CRASH


def _of_kind(kind: str) -> tuple[type[BaseException], ...]:
    return tuple(cls for cls, row in CLASSIFICATION.items() if row.kind == kind)


#: Refusals raised by the domain call about its arguments — `cli.domain_errors`' half.
INPUT_REFUSALS: tuple[type[BaseException], ...] = _of_kind(INPUT)

#: Refusals raised while opening or creating the tracker — `cli.main`'s outer arm's half.
TRACKER_REFUSALS: tuple[type[BaseException], ...] = _of_kind(TRACKER)

#: Everything a refusal can be. The MCP boundary is single, so it takes the union.
EXPECTED_REFUSALS: tuple[type[BaseException], ...] = INPUT_REFUSALS + TRACKER_REFUSALS

#: Crashes that DESCEND from a refusal and must therefore be re-raised in an earlier arm.
CRASHES_INSIDE_REFUSALS: tuple[type[BaseException], ...] = tuple(
    cls
    for cls, row in CLASSIFICATION.items()
    if row.kind == CRASH and issubclass(cls, EXPECTED_REFUSALS)
)
