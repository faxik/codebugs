"""No module of ``codebugs`` imports a network capability (CB-174).

WHY THIS FILE EXISTS. The embedding tools now tell their caller, in the tool
description a client actually reads, that the requirement text goes nowhere and
that the vector it hands us is stored locally and never sent. A SAFETY CLAIM
WITH NO GATE BEHIND IT IS A "GATE THAT CANNOT FIRE" WRITTEN AS PROSE — the
literal subject of CB-159/CB-160 — so the claim is held by a mechanical check
rather than by a promise that ages.

WHAT IS CHECKED, STATED AT THE WIDTH IT IS TRUE. This asserts a property of
THIS PACKAGE'S OWN SOURCE: no module under ``src/codebugs/`` imports a name
through which a socket can be opened, and none of them reaches for a module by
computed name. It deliberately does NOT claim "codebugs cannot reach the
network". Three limits, each named rather than discovered later:

* The ``mcp`` dependency carries a network transport of its own (``server.py``
  says so — an HTTP mode exists; this project runs over stdio). A dependency's
  capabilities are not this package's imports.
* ``subprocess`` is used legitimately here, for git, and a subprocess can of
  course run ``curl``. Refusing ``subprocess`` would refuse the package's
  working code, and keying on argv would be a guess.
* Only ``src/codebugs/`` is read. ``tests/`` and ``tools/`` are not part of
  what ships to a caller.

KEY ON THE CAPABILITY, NOT ON THE MODULE NAME — and that is not a refinement,
it is the difference between a working gate and one that refuses the package's
own healthy state. ``src/codebugs/db.py`` carries
``from urllib.request import pathname2url``. A check keyed on the module name
would fail TODAY, on a tree with no defect in it. ``pathname2url`` is a pure
string function that opens nothing; ``import urllib.request`` binds the whole
module and hands you ``urlopen``. So a FROM-import of a network module is
judged NAME BY NAME, and a plain import of one is refused outright, because
what it binds is the module itself.

DECLARED EXCEPTIONS are keyed by ``(module, dotted name)`` and each carries a
reason. The table is SELF-DELETING: a row naming an import that is no longer
there fails this file's own tests, so it can only shrink. Without that, the
table becomes the place real network imports are quietly parked — the same hole
the gate exists to close, one level up. The pattern is
``tests/test_strict_bool_gates.py::DECLARED_EXCEPTIONS``.

AND THE DAY THIS TABLE NEEDS A NEW ROW IS THE DAY THE RULE IN ``CLAUDE.md``
STARTS APPLYING: an embedding provider inside the package must be configurable
from its first day, default to a local option, and expose a visible binding.
The rule and this gate are two halves of one thing; see the "Embeddings" entry
in the root ``CLAUDE.md``.
"""

from __future__ import annotations

import ast
import pathlib

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


def _capability_imports(rel: str, source: str) -> list[tuple[str, str]]:
    """Every (module, dotted name) in this file that grants network capability."""
    hits: list[tuple[str, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                # A plain import binds the MODULE. There is no narrower name to
                # judge, so the capability is the whole module's.
                if _network_prefix(alias.name):
                    hits.append((rel, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # a relative import cannot leave the package
                continue
            module = node.module or ""
            for alias in node.names:
                dotted = f"{module}.{alias.name}" if module else alias.name
                # ``from urllib import request`` binds a module too, so it is
                # judged as the submodule rather than as a name inside one.
                if _network_prefix(dotted):
                    hits.append((rel, dotted))
    return hits


def _dynamic_import_calls(rel: str, source: str) -> list[str]:
    """``__import__`` / ``importlib.import_module`` -- imports a name gate cannot read.

    Measured on the tree this landed on: zero occurrences in ``src/codebugs/``.
    Without this, one computed import would walk straight past everything above
    while the file kept claiming the package imports no network capability.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "__import__":
            found.append(f"{rel}: __import__(...)")
        elif isinstance(func, ast.Attribute) and func.attr == "import_module":
            found.append(f"{rel}: importlib.import_module(...)")
    return found


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

    def test_no_package_module_imports_by_computed_name(self):
        found = []
        for rel, path in _package_modules():
            found.extend(_dynamic_import_calls(rel, path.read_text(encoding="utf-8")))
        assert not found, (
            f"dynamic import(s) in src/codebugs/: {found}. A computed import is "
            "invisible to the check above, so it would let the package acquire a "
            "network capability while this gate still reported clean."
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

    def test_the_gate_actually_reads_files(self):
        modules = _package_modules()
        names = {rel for rel, _ in modules}
        assert "db.py" in names and "embeddings.py" in names, (
            f"the module sweep did not find the package's own files: {sorted(names)[:5]}"
        )

    def test_dynamic_import_detection_is_not_vacuous(self):
        assert _dynamic_import_calls("victim.py", "importlib.import_module('socket')")
        assert _dynamic_import_calls("victim.py", "__import__('socket')")
        assert not _dynamic_import_calls("victim.py", "from codebugs import db")


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
