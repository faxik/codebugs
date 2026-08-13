"""The one shared fixture in this suite: neutralize an ambient tracker declaration.

THIS FILE IS A DELIBERATE EXCEPTION to the project's "no shared conftest.py"
convention, because the thing it guards cannot be guarded per-file.

`CODEBUGS_ROOT` redirects every `db.connect()` in this process *and* in any
subprocess that inherits the environment. Three test modules shell out to the
CLI and run mutating verbs — `update` in `test_findings.py`, `claim` in
`test_claims.py`, `resolve-trailers` in `test_provenance.py` — relying on the
subprocess binding to its own `cwd`. With the variable exported, they bind to
whatever it names instead.

Verified before this file existed, not theorized: with `CODEBUGS_ROOT` pointing
at a scratch tracker, running the findings CLI tests rewrote that tracker's CB-1
from `low`/`open` to `high`/`fixed`. Pointed at a developer's real tracker,
`pytest` silently corrupts real findings.

A per-file fixture would have to be remembered by every test module added later,
and the cost of forgetting is silent destruction of the developer's own data —
exactly the kind of rule that must not be an enumeration. Tests that exercise
the override set the variable themselves *after* this fixture has run.
"""

import pytest

from codebugs import db


@pytest.fixture(autouse=True)
def _no_ambient_tracker_root(monkeypatch):
    monkeypatch.delenv(db.ENV_ROOT, raising=False)
    monkeypatch.setattr(db, "_tracker_root_override", None)
