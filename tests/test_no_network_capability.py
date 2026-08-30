"""What ``src/codebugs/`` may import: a capability set and a ratchet (CB-174, CB-190).

WHY THIS FILE EXISTS. The embedding tools tell their caller, in the tool
description a client actually reads, that the requirement text goes nowhere and
that the vector it hands us is stored locally and never sent. A SAFETY CLAIM
WITH NO GATE BEHIND IT IS A "GATE THAT CANNOT FIRE" WRITTEN AS PROSE — the
literal subject of CB-159/CB-160 — so the claim is held by a mechanical check
rather than by a promise that ages.

TWO MECHANISMS, AND NEITHER SUBSUMES THE OTHER. Read them separately, because
they are true at different widths and CB-190 was filed because six texts in
three files described the first one as if it were the second.

1. THE CAPABILITY SET (``NETWORK_MODULES``, CB-174) — an ENUMERATION of module
   names through which a socket can be opened, refused wherever they appear.
   Being an enumeration is its defining limit, not a detail: a socket-opening
   name nobody listed walks straight past it. Measured on this very function:
   ``from logging.handlers import SocketHandler``, the same module's
   ``HTTPHandler``, and ``from multiprocessing.connection import Client`` all
   return an empty list today. So this set supports a claim about THE NAMES IT
   LISTS, and about nothing else.

2. THE THIRD-PARTY RATCHET (``DECLARED_THIRD_PARTY``, CB-190) — every import
   whose dotted name leaves BOTH this package and the standard library must be
   declared here, by exact dotted name, with a reason. This one is not an
   enumeration of what to refuse; it refuses by default and enumerates what is
   ALLOWED, so a client nobody thought of is caught for being foreign rather
   than for being recognised. Measured: ``import cohere``, ``import ollama``
   and ``import httplib2`` are green against (1) and red against (2).

Why both, rather than the better one: (1) is the only thing that reaches INTO
the standard library, where the ratchet by construction cannot help, since
those names are stdlib and that is exactly what it waves through; (2) is the
only thing that covers everything outside it, where a list is only as good as
whoever wrote it. ``import socket`` is caught by (1) alone and ``import
cohere`` by (2) alone — pinned, so neither is deleted in favour of the other.

A third check stands beside them: no module loads code from a string at run
time (``__import__``, ``importlib.import_module``, ``exec``, ``eval``),
because both mechanisms above read import STATEMENTS and none of those four
is one.

WHAT IS CLAIMED, AND WHAT IS NOT. Together these assert a property of THIS
PACKAGE'S OWN SOURCE TEXT: it names no socket-opening module from the set
below, and it names nothing outside the package and the standard library that
is not declared below with a reason. That is a statement about what the source
NAMES. It is deliberately NOT "codebugs cannot reach the network", and it is
not a statement about what the process ends up holding. Four limits, each
named rather than discovered later:

* The ``mcp`` dependency carries a network transport of its own (``server.py``
  says so — an HTTP mode exists; this project runs over stdio). A dependency's
  capabilities are not this package's imports. Measured, so this is concrete
  rather than cautious: importing the single declared
  ``mcp.server.mcpserver.MCPServer`` already puts ``mcp.server.sse``,
  ``mcp.client.sse`` and 113 other ``mcp.*`` modules into ``sys.modules``. The
  ratchet keeps the SOURCE from naming a second one without a row somebody
  reads; it does not and cannot empty the process.
* ``subprocess`` is used legitimately here, for git, and a subprocess can of
  course run ``curl``. Refusing ``subprocess`` would refuse the package's
  working code, and keying on argv would be a guess.
* ``sys.stdlib_module_names`` is a set of NAMES, not an oracle of origin: it
  is flat and identical on every platform, so five of its names (``winsound``,
  ``msvcrt``, ``winreg``, ``nt``, ``idlelib``) are unclaimed on Linux and a
  planted ``winsound.py`` opening a socket would classify as stdlib. Shadowing
  through the search path is the second door, and this repository uses that
  mechanism itself (``pythonpath = ["src"]``). Not a regression — the gate
  before CB-190 passed the same thing — and closing it needs an origin oracle,
  which ``_leaves_the_package`` explains why it is not.
* Only ``src/codebugs/`` is read. ``tests/`` and ``tools/`` are not part of
  what ships to a caller.

ONE PROPERTY IS NEW AND IS DECLARED RATHER THAN COUNTED AS COVERED. Before
CB-190 the verdict was a pure function of the source text; the ratchet makes
it a function of the source text AND the interpreter version, because
``sys.stdlib_module_names`` changes between releases. On this tree there is no
divergence — the four top-level names it imports (``codebugs``, ``mcp``,
``mcp_types``, ``pydantic``) are foreign on every version ``requires-python``
admits — but CI runs this file on the pinned interpreter only, so nothing
would notice if that stopped being true.

KEY ON THE CAPABILITY, NOT ON THE MODULE NAME — and that is not a refinement,
it is the difference between a working gate and one that refuses the package's
own healthy state. ``src/codebugs/db.py`` carries
``from urllib.request import pathname2url``. A check keyed on the module name
would fail TODAY, on a tree with no defect in it. ``pathname2url`` is a pure
string function that opens nothing; ``import urllib.request`` binds the whole
module and hands you ``urlopen``. So a FROM-import of a network module is
judged NAME BY NAME, and a plain import of one is refused outright, because
what it binds is the module itself.

BOTH TABLES CARRY A REASON PER ROW AND BOTH ARE SELF-DELETING: a row naming an
import that is no longer in the tree fails this file's own tests, so each can
only shrink. Without that, a table becomes the place real imports are quietly
parked — the same hole these mechanisms exist to close, one level up. The
pattern is ``tests/test_strict_bool_gates.py::DECLARED_EXCEPTIONS``. They are
keyed differently on purpose, and each key is argued where it is defined:
``DECLARED_EXCEPTIONS`` by ``(module, dotted name)``, because it answers
whether a NAME grants a socket in a given file; ``DECLARED_THIRD_PARTY`` by
the dotted name alone, because it answers whether a DEPENDENCY is declared.

AND THE DAY EITHER TABLE NEEDS A NEW ROW IS THE DAY THE RULE IN ``CLAUDE.md``
STARTS APPLYING: an embedding provider inside the package must be configurable
from its first day, default to a local option, and expose a visible binding.
CB-190's practical complaint was that the trigger did not fire — a provider
built on a client the capability set never listed would have needed no row at
all — and the ratchet is what repairs it, because a provider arrives as a
DEPENDENCY whatever network shape it has. The rule and these mechanisms are
two halves of one thing; see the "Embeddings" entry in the root ``CLAUDE.md``.
"""

from __future__ import annotations

import ast
import pathlib
import sys

import pytest

import codebugs

# ---------------------------------------------------------------------------
# What counts as a network capability. Prefix-matched on the dotted module
# path, so ``urllib.request`` catches ``urllib.request.anything``.
# ---------------------------------------------------------------------------
NETWORK_MODULES: frozenset[str] = frozenset(
    {
        # stdlib: opens or serves a socket
        "socket",
        "socketserver",
        "ssl",
        "http",
        "urllib.request",
        "urllib.error",
        "ftplib",
        "smtplib",
        "poplib",
        "imaplib",
        # Removed from the standard library in 3.12 (asyncore, asynchat,
        # smtpd) and in 3.13 (nntplib, telnetlib) -- and KEPT here for a
        # measured reason, not out of tidiness. ``requires-python`` admits
        # 3.11, where all five still exist and still open sockets. They are
        # also the one place the two mechanisms in this file disagree by
        # interpreter version: the ratchet below classifies a name by
        # ``sys.stdlib_module_names``, so on 3.13/3.14 these five are FOREIGN
        # and the ratchet refuses them on its own, while on 3.11 they are
        # stdlib and only this set catches them. Five strings cost nothing and
        # make the verdict on these names the same on every admitted version.
        "asyncore",
        "asynchat",
        "smtpd",
        "nntplib",
        "telnetlib",
        "xmlrpc",
        "wsgiref",
        "webbrowser",
        # ``asyncio.open_connection`` needs no other import, so a plain
        # ``import asyncio`` really does grant the capability. Kept knowing the
        # cost: a future async surface here would need a declared row with a
        # reason. That is the table doing its job -- a genuine capability import
        # becoming a moment somebody reads -- rather than a false refusal.
        "asyncio",
        # third-party clients this package could plausibly grow. Deliberately
        # NOT a list of ML libraries: ``torch``/``transformers`` are model code,
        # and enumerating what a provider MIGHT be built on is the enumeration
        # failure this repository keeps relearning. A provider will import one
        # of the clients below to reach an API, and that is what is keyed on.
        "requests",
        "httpx",
        "aiohttp",
        "urllib3",
        "websockets",
        "grpc",
        "boto3",
        "botocore",
        "openai",
        "anthropic",
    }
)

# A FROM-import of one of the modules above is judged name by name. A row here
# says: this particular name grants no network capability, and here is why.
DECLARED_EXCEPTIONS: dict[tuple[str, str], str] = {
    ("db.py", "urllib.request.pathname2url"): (
        "CB-174: a pure string function -- it percent-encodes a filesystem path "
        "for use in a URI and opens nothing. db.py uses it to build the "
        "file: URI it hands to sqlite3.connect(uri=True). Importing the "
        "MODULE would grant urlopen; importing this NAME grants string "
        "formatting."
    ),
}


def _package_modules() -> list[tuple[str, pathlib.Path]]:
    """The package's source files, re-globbed on every call, DELIBERATELY.

    NOT MEMOIZED, and the reason is a property of this repository rather than
    an oversight. Measured with ``--durations``: the five traversals in this
    file re-read and re-parse the same ~33 sources, ~0.5s of the file's ~0.6s,
    and an ``lru_cache`` here would remove nearly all of it. It is refused
    because these are SAFETY gates and the tree moves under a running suite:
    ``tests/conftest.py`` carries an alarm (CB-215) for exactly that — an
    acceptor re-runs this suite in the main checkout while other directions
    land branches into it, and main's median gap between commits is shorter
    than one suite run. A cached read would let a gate report clean about a
    snapshot rather than about the tree, which is the "guard reporting clean
    because it could not look" shape these files exist to refuse. Re-reading
    costs half a second out of a three-minute suite; being right about which
    bytes were judged is what the gate is for.

    Known and accepted: each future table added under this convention brings
    one more O(package) traversal. If that ever matters, the answer is to read
    the tree ONCE PER TEST at a fixed moment and share it within that test —
    not to cache across tests.
    """
    root = pathlib.Path(codebugs.__file__).parent
    found = sorted(root.rglob("*.py"))
    assert found, f"no package sources found under {root} -- this gate cannot look"
    return [(str(path.relative_to(root)), path) for path in found]


def _network_prefix(dotted: str) -> str | None:
    """The network module ``dotted`` is, or lives under. ``None`` if neither."""
    parts = dotted.split(".")
    for i in range(len(parts), 0, -1):
        candidate = ".".join(parts[:i])
        if candidate in NETWORK_MODULES:
            return candidate
    return None


def _dotted_imports(rel: str, source: str) -> list[tuple[str, str]]:
    """Every (module, dotted name) this file imports, judged by neither rule.

    ONE WALK, TWO PREDICATES. Both mechanisms in this file ask a different
    question about the SAME list of names, so the traversal is written once and
    each mechanism filters it. Two copies of this loop would be two rules one
    edit apart — and the thing they would disagree about is the dotted name
    itself, which is the input to both.

    What the name is: ``import a.b`` yields ``a.b``, because a plain import
    binds the MODULE and there is no narrower name to judge; ``from a.b import
    c`` yields ``a.b.c``. ``from urllib import request`` therefore yields
    ``urllib.request``, a module rather than a name inside one, which is what
    lets the capability set judge it as the submodule it is.
    """
    names: list[tuple[str, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append((rel, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # a relative import cannot leave the package
                continue
            module = node.module or ""
            for alias in node.names:
                names.append((rel, f"{module}.{alias.name}" if module else alias.name))
    return names


def _capability_imports(rel: str, source: str) -> list[tuple[str, str]]:
    """Every (module, dotted name) in this file that grants network capability."""
    return [(r, d) for r, d in _dotted_imports(rel, source) if _network_prefix(d)]


#: Ways to reach code by NAME at run time. Everything above reads import
#: statements, and an import statement is the one thing none of these is.
#:
#: EVERY NAME IS CHECKED IN BOTH SYNTACTIC FORMS, and it took an adversarial
#: pass to see why. The first version guarded ``__import__`` as a bare NAME and
#: ``import_module`` as an ATTRIBUTE, which is how each is usually written --
#: and each was then reachable through the other's spelling. Both bypasses were
#: reproduced with a working ``socket.create_connection`` inside the package
#: (``from importlib import import_module`` in ``milestones/``, and
#: ``builtins.__import__``), with this file still reporting 23 passed. One rule
#: written as two half-rules, which is this repository's most repeated defect.
_CODE_BY_NAME = frozenset({"__import__", "import_module", "exec", "eval"})


def _reaches_code_by_name(rel: str, source: str) -> list[str]:
    """``__import__`` / ``importlib.import_module`` / ``exec`` / ``eval``.

    Both checks above read import STATEMENTS. Each of these acquires a module
    from a string instead, so one of them would walk straight past both while
    this file kept reporting that the package's source names neither a listed
    socket-opening module nor an undeclared foreign one — a guard reporting
    clean because it could not look.

    Measured on the tree this landed on: **zero** occurrences of any of the
    four in ``src/codebugs/``, which is what makes refusing them free rather
    than a rule somebody has to work around on day one.

    KNOWN AND NOT CLOSED, named here rather than left to be rediscovered: this
    matches a CALL SITE by the name being called, so an indirection that hides
    the name — ``getattr(importlib, "import_module")(...)``, or the
    ``find_spec`` / ``module_from_spec`` / ``exec_module`` sequence — is not
    seen. Both were reproduced by adversarial review. Closing them means
    tracking values rather than names, which is a different and much larger
    check; what this file buys is that the ordinary spellings cannot be used by
    accident, and the prose elsewhere is written to that width and no wider.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Both spellings for every name: ``__import__(...)`` and
        # ``builtins.__import__(...)`` are the same capability, as are
        # ``import_module(...)`` and ``importlib.import_module(...)``.
        called = None
        if isinstance(func, ast.Name):
            called = func.id
        elif isinstance(func, ast.Attribute):
            called = func.attr
        if called in _CODE_BY_NAME:
            found.append(f"{rel}: {called}(...)")
    return found


#: The package's own top-level name, DERIVED rather than written down. A row
#: in the table below naming this package would lie about what that table
#: declares -- it is a list of imports that leave the package -- and it would
#: make the self-deletion test vacuous for the 165 in-package imports that
#: keep it "live" forever. ``test_the_package_names_itself_by_derivation``
#: refuses such a row.
_OWN_TOP_LEVEL = codebugs.__name__

#: Every import in ``src/codebugs/`` that leaves both this package and the
#: standard library, keyed by its EXACT DOTTED NAME with a reason per row.
#: An import not named here is refused.
#:
#: KEYED BY THE DOTTED NAME ALONE, and the neighbouring table is keyed by
#: ``(module, dotted name)`` -- the difference is deliberate. There, a row
#: answers "does this NAME grant a socket, in this file"; here it answers "is
#: this DEPENDENCY declared", which is a property of the dependency and not of
#: the file that reached for it. Measured: ``pydantic.Field`` is imported in
#: nine files, so the pair key would put nine rows carrying one identical
#: reason into a five-row table, and a table that is mostly duplication is one
#: people stop reading.
DECLARED_THIRD_PARTY: dict[str, str] = {
    "mcp.server.mcpserver.MCPServer": (
        "CB-190: the MCP server class this package's own server is built on. "
        "The `mcp` 2.x SDK is the package's single declared runtime "
        "dependency (pyproject.toml), and server.py exists to run against it."
    ),
    "mcp.shared.exceptions.MCPError": (
        "CB-190: the SDK's protocol-error type, raised by server.py's strict "
        "argument middleware so a bad `tools/call` fails as a protocol error "
        "rather than as a tool exception."
    ),
    "mcp_types.CallToolResult": (
        "CB-190: the SDK's result type for a tool call, read by server.py's "
        "middleware to tell a tool's own failure from a protocol failure. "
        "UNDECLARED DISTRIBUTION -- `mcp_types` is shipped by `mcp-types`, "
        "which reaches this tree only as a transitive dependency of `mcp`; "
        "pyproject.toml names `mcp` and nothing else."
    ),
    "mcp_types.INVALID_PARAMS": (
        "CB-190: the SDK's JSON-RPC error code for a malformed argument set, "
        "which server.py's strict-argument middleware returns. Same "
        "undeclared distribution as the row above."
    ),
    "pydantic.Field": (
        "CB-190: parameter metadata for MCP tool signatures. The SDK builds "
        "each tool's argument model with pydantic, so `Field` is how a tool "
        "declares a description or a default for one of its arguments. "
        "UNDECLARED DISTRIBUTION -- `pydantic` is a transitive dependency of "
        "`mcp`, imported directly in nine of this package's modules while "
        "pyproject.toml constrains only `mcp`."
    ),
}


def _leaves_the_package(dotted: str) -> bool:
    """Does this dotted import name reach outside this package and the stdlib?

    Three-part split, and the first two parts are answered WITHOUT a table:
    the package's own name is derived, and the standard library is
    ``sys.stdlib_module_names``. Everything else is foreign and needs a row.

    ``sys.stdlib_module_names`` is a set of NAMES, not an origin oracle, and
    the residuals that follow from that are named in this module's docstring.
    An origin-based test (``importlib.util.find_spec`` plus a
    ``sysconfig`` path comparison) was considered and rejected: it answers
    ``None`` for a package that is merely not installed -- which is exactly
    the shape of the mutant this gate must catch -- it executes a parent
    package's ``__init__`` to locate a submodule, and it would make the
    verdict depend on what happens to be installed in the environment rather
    than on the source text alone.
    """
    top = dotted.split(".", 1)[0]
    if top == _OWN_TOP_LEVEL:
        return False
    return top not in sys.stdlib_module_names


def _foreign_imports(rel: str, source: str) -> list[tuple[str, str]]:
    """Every (module, dotted name) in this file that leaves the package.

    The grain of the rule is the EXACT dotted name ``_dotted_imports`` built:
    a second name under an already-declared distribution is a second row, so
    ``import mcp.server.sse`` beside the declared ``mcp.server.mcpserver`` is
    refused rather than inherited.
    """
    return [(r, d) for r, d in _dotted_imports(rel, source) if _leaves_the_package(d)]


class TestNoNetworkCapability:
    def test_no_package_module_imports_a_network_capability(self):
        undeclared = []
        for rel, path in _package_modules():
            source = path.read_text(encoding="utf-8")
            for key in _capability_imports(rel, source):
                if key not in DECLARED_EXCEPTIONS:
                    undeclared.append(key)
        assert not undeclared, (
            "network-capable import(s) in src/codebugs/: "
            f"{undeclared}. The embedding tool descriptions and CLAUDE.md tell "
            "callers this package sends nothing anywhere. Either remove the "
            "import, or -- if the name genuinely grants no network access -- add "
            "it to DECLARED_EXCEPTIONS in this file with a reason. If it DOES "
            "grant network access, the CLAUDE.md rule on a provider inside the "
            "package now applies: configurable from day one, local default, "
            "visible binding."
        )

    def test_no_package_module_reaches_for_code_by_name(self):
        found = []
        for rel, path in _package_modules():
            found.extend(_reaches_code_by_name(rel, path.read_text(encoding="utf-8")))
        assert not found, (
            f"run-time code loading in src/codebugs/: {found}. The check above "
            "reads import STATEMENTS, and none of these is one, so any of them "
            "would let the package acquire a network capability while this gate "
            "still reported clean."
        )


class TestThirdPartyImportRatchet:
    def test_every_import_that_leaves_the_package_is_declared(self):
        undeclared = []
        for rel, path in _package_modules():
            source = path.read_text(encoding="utf-8")
            for rel_name, dotted in _foreign_imports(rel, source):
                if dotted not in DECLARED_THIRD_PARTY:
                    undeclared.append((rel_name, dotted))
        assert not undeclared, (
            "undeclared import(s) leaving src/codebugs/ and the standard "
            f"library: {undeclared}. Add each to DECLARED_THIRD_PARTY in this "
            "file with a reason, or remove the import. If the new dependency "
            "is an embedding provider, the CLAUDE.md rule now applies: "
            "configurable from day one, local default, visible binding."
        )


class TestTheRatchetItself:
    """The ratchet is worth its lines only if it fails, and only fails rightly."""

    @pytest.mark.parametrize(
        "mutant",
        [
            "import cohere",
            "import ollama",
            "import httplib2",
            "from cohere import Client",
            "import langchain.llms",
        ],
    )
    def test_the_measured_gap_is_caught(self, mutant):
        """CB-190's whole subject: these are green against the capability set.

        The set upstairs is an enumeration of names somebody thought of, so a
        client it does not name walks straight past it. Measured before this
        ratchet existed: all five of these produced an empty list from
        ``_capability_imports``. They are caught here because they are
        foreign, not because anyone predicted them.
        """
        assert not _capability_imports("victim.py", mutant), (
            f"{mutant!r} was expected to be invisible to the capability set -- "
            "if it is now named there, this test no longer measures the gap"
        )
        found = _foreign_imports("victim.py", mutant)
        assert found, f"the ratchet does not see {mutant!r}"
        assert all(dotted not in DECLARED_THIRD_PARTY for _, dotted in found)

    @pytest.mark.parametrize(
        "mutant",
        [
            "import mcp.server.sse",
            "from mcp.client.sse import sse_client",
            "import mcp",
            "from mcp import types",
            "import pydantic",
            "from pydantic import BaseModel",
            "import mcp_types",
        ],
    )
    def test_a_second_name_under_a_declared_distribution_needs_its_own_row(self, mutant):
        """No wholesale grant of a namespace -- AT THE WIDTH THAT IS TRUE.

        A row keyed by the top-level name would have licensed every one of
        these, including the two SSE transports. Keyed by the exact dotted
        name, each needs a row of its own and has none, so the SOURCE cannot
        name them without a moment somebody reads.

        That is a statement about what this package's source names, and NOT
        about what its process holds. Measured: importing the single declared
        ``mcp.server.mcpserver.MCPServer`` already pulls ``mcp.server.sse``,
        ``mcp.client.sse`` and 115 other ``mcp.*`` modules into
        ``sys.modules``. A dependency's own imports are not this package's,
        which is the same bound the capability gate above declares.
        """
        found = _foreign_imports("victim.py", mutant)
        assert found, f"the ratchet does not see {mutant!r}"
        undeclared = [dotted for _, dotted in found if dotted not in DECLARED_THIRD_PARTY]
        assert undeclared == [dotted for _, dotted in found], (
            f"{mutant!r} was waved through by a row written for another name"
        )

    @pytest.mark.parametrize(
        "innocent",
        [
            "from codebugs import db",
            "import codebugs",
            "import codebugs.milestones.capacity",
            "from codebugs.types import utc_now",
            "from . import types",
            "from .. import db",
            "from __future__ import annotations",
            "import sqlite3, json, struct",
            "from urllib.request import pathname2url",
            "from urllib.parse import quote",
        ],
    )
    def test_the_package_and_the_stdlib_are_not_foreign(self, innocent):
        """The false refusal this ratchet must never produce.

        A first draft of the design licensed the shape with "the package makes
        exactly three third-party imports", a count that had silently dropped
        ``codebugs`` itself -- while the rule it was licensing ("a top-level
        name absent from ``sys.stdlib_module_names``") plainly contains it. A
        table of three rows would have refused all 165 in-package imports and
        turned the whole suite red on a tree with no defect in it.
        """
        assert not _foreign_imports("victim.py", innocent)

    def test_the_package_names_itself_by_derivation(self):
        """The own-package name is derived and may not be parked in the table."""
        assert _OWN_TOP_LEVEL == codebugs.__name__
        parked = [
            dotted
            for dotted in DECLARED_THIRD_PARTY
            if dotted.split(".", 1)[0] == _OWN_TOP_LEVEL
        ]
        assert not parked, (
            f"DECLARED_THIRD_PARTY names this package: {parked}. The table "
            "declares imports that LEAVE the package, so such a row lies "
            "about what it declares -- and, being live forever, it can never "
            "go stale, which is the one thing that keeps the table shrinking."
        )

    def test_the_two_mechanisms_answer_different_questions(self):
        """Neither subsumes the other, so neither may be deleted for the other.

        ``import socket`` is stdlib, so the ratchet waves it through and only
        the capability set catches it. ``import cohere`` is foreign and
        unnamed upstairs, so only the ratchet catches it.
        """
        assert _capability_imports("victim.py", "import socket")
        assert not _foreign_imports("victim.py", "import socket")
        assert not _capability_imports("victim.py", "import cohere")
        assert _foreign_imports("victim.py", "import cohere")


class TestDeclaredThirdPartyCannotRot:
    """The same discipline as the table above it, for the same reason."""

    def test_every_row_carries_a_reason(self):
        empty = [key for key, reason in DECLARED_THIRD_PARTY.items() if not reason.strip()]
        assert not empty, (
            f"DECLARED_THIRD_PARTY row(s) with no reason: {empty} -- a table "
            "that can grow silently is the hole this ratchet exists to close"
        )

    def test_no_row_is_stale(self):
        live = set()
        for rel, path in _package_modules():
            live.update(
                dotted for _, dotted in _foreign_imports(rel, path.read_text(encoding="utf-8"))
            )
        stale = [key for key in DECLARED_THIRD_PARTY if key not in live]
        assert not stale, (
            f"stale DECLARED_THIRD_PARTY row(s): {stale} -- the import is gone, "
            "so delete the row rather than leaving a standing permission behind"
        )


class TestTheGateItself:
    """A gate is only worth its line count if it can fail, and only fail rightly."""

    @pytest.mark.parametrize(
        "mutant",
        [
            "import socket",
            "import ssl\n",
            "from urllib.request import urlopen",
            "from urllib import request",
            "import http.client",
            "import requests as r",
            "from httpx import AsyncClient",
        ],
    )
    def test_a_real_network_import_is_caught(self, mutant):
        assert _capability_imports("victim.py", mutant), (
            f"the gate does not see {mutant!r}"
        )

    @pytest.mark.parametrize(
        "innocent",
        [
            "import sqlite3",
            "import json, struct, math",
            "from urllib.parse import quote",  # pure string work, not in the set
            "from codebugs import db",
            "from . import types",
        ],
    )
    def test_ordinary_imports_are_not_flagged(self, innocent):
        assert not _capability_imports("victim.py", innocent)

    def test_the_todays_tree_exception_is_the_from_import_and_not_the_module(self):
        """The measurement that shaped the whole design.

        ``from urllib.request import pathname2url`` is declared; the module
        import that would grant ``urlopen`` is not, and must never be waved
        through by the same row.
        """
        declared = _capability_imports("db.py", "from urllib.request import pathname2url")
        assert declared == [("db.py", "urllib.request.pathname2url")]
        assert declared[0] in DECLARED_EXCEPTIONS

        module_import = _capability_imports("db.py", "import urllib.request")
        assert module_import == [("db.py", "urllib.request")]
        assert module_import[0] not in DECLARED_EXCEPTIONS

    @pytest.mark.parametrize(
        "removed", ["asyncore", "asynchat", "smtpd", "nntplib", "telnetlib"]
    )
    def test_a_module_removed_from_the_stdlib_is_still_caught(self, removed):
        """These five must be caught on EVERY version ``requires-python`` admits.

        WHICH mechanism catches them depends on the interpreter, and that is
        the whole reason for keeping them in the set. Measured: on 3.11 all
        five are in ``sys.stdlib_module_names`` (3.12 keeps
        ``nntplib``/``telnetlib`` only), so the ratchet reads them as stdlib
        and this set is the sole catcher; on 3.13 and 3.14 none of them is, so
        the ratchet refuses them as foreign on its own.

        BOTH HALVES ARE ASSERTED, and the second half is why this test is not
        a tautology over five strings added two screens above. An earlier
        version stated the divergence in prose and then called only
        ``_capability_imports``, so deleting the entire ratchet left it green
        — measured by a reviewer who did exactly that. The second assertion
        ties the ratchet's verdict to stdlib membership on whatever
        interpreter is running, which is a statement that holds on all four
        versions while still being false if either mechanism moves.
        """
        assert _capability_imports("victim.py", f"import {removed}"), (
            f"{removed!r} must stay in NETWORK_MODULES: on 3.11 it is a live "
            "socket-opening stdlib module and this set is its only catcher"
        )
        seen_by_ratchet = bool(_foreign_imports("victim.py", f"import {removed}"))
        assert seen_by_ratchet is (removed not in sys.stdlib_module_names), (
            f"the ratchet's verdict on {removed!r} disagrees with this "
            "interpreter's own stdlib listing, so the two mechanisms no "
            "longer partition the name the way the comment beside "
            "NETWORK_MODULES claims"
        )

    def test_the_gate_actually_reads_files(self):
        """One premise test for both mechanisms -- they share the sweep.

        ``server.py`` is named alongside the original two because it is where
        every row of ``DECLARED_THIRD_PARTY`` but one lives: if the sweep
        stopped reaching it, those rows would go stale rather than the gate
        going quiet, which is a confusing way to learn the sweep is broken.
        """
        names = {rel for rel, _ in _package_modules()}
        assert {"db.py", "embeddings.py", "server.py"} <= names, (
            f"the module sweep did not find the package's own files: {sorted(names)[:5]}"
        )

    @pytest.mark.parametrize(
        "mutant",
        [
            "importlib.import_module('socket')",
            "from importlib import import_module\nimport_module('socket')",
            "__import__('socket')",
            "import builtins\nbuiltins.__import__('socket')",
            "exec('import socket')",
            "eval(\'__import__(\"socket\")\')",
        ],
    )
    def test_run_time_code_loading_is_caught(self, mutant):
        assert _reaches_code_by_name("victim.py", mutant), mutant

    def test_that_detection_is_not_indiscriminate(self):
        assert not _reaches_code_by_name("victim.py", "from codebugs import db")
        assert not _reaches_code_by_name("victim.py", "conn.execute('SELECT 1')")


class TestDeclaredExceptionsCannotRot:
    """The table may only shrink. A row with no reason, or naming an import that
    is no longer in the tree, fails --- otherwise the exceptions list becomes the
    place real network imports are parked."""

    def test_every_row_carries_a_reason(self):
        empty = [key for key, reason in DECLARED_EXCEPTIONS.items() if not reason.strip()]
        assert not empty, (
            f"DECLARED_EXCEPTIONS row(s) with no reason: {empty} -- a table that "
            "can grow silently is the hole this gate exists to close"
        )

    def test_no_row_is_stale(self):
        live = set()
        for rel, path in _package_modules():
            live.update(_capability_imports(rel, path.read_text(encoding="utf-8")))
        stale = [key for key in DECLARED_EXCEPTIONS if key not in live]
        assert not stale, (
            f"stale DECLARED_EXCEPTIONS row(s): {stale} -- the import is gone, so "
            "delete the row rather than leaving a standing permission behind"
        )
