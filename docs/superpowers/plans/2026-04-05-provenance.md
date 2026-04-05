# Finding Provenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add commit SHA and version tracking to findings, with a staleness detection tool that identifies obsolete bugs via git history.

**Architecture:** Two new nullable columns on `findings` (`reported_at_commit`, `reported_at_ref`), auto-populated commit SHA at add time via subprocess in server.py, and a new `staleness_check` MCP tool that batches git operations by file to detect stale/deleted/renamed findings.

**Tech Stack:** Python, SQLite, subprocess (git), FastMCP

**Spec:** `docs/superpowers/specs/2026-04-05-provenance-design.md`

---

### Task 1: Schema and Migration

**Files:**
- Modify: `src/codebugs/db.py:15-35` (SCHEMA constant)
- Modify: `src/codebugs/db.py:109-146` (_migrate_statuses hardcoded DDL)
- Modify: `src/codebugs/db.py:82-106` (connect — call new migration)
- Test: `tests/test_db.py`

- [ ] **Step 1: Write failing test for new columns on fresh DB**

```python
class TestProvenance:
    def test_fresh_db_has_provenance_columns(self, conn):
        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(findings)").fetchall()
        }
        assert "reported_at_commit" in cols
        assert "reported_at_ref" in cols

    def test_provenance_columns_nullable(self, conn):
        result = db.add_finding(
            conn, severity="high", category="test", file="a.py",
            description="no provenance",
        )
        assert result.get("reported_at_commit") is None
        assert result.get("reported_at_ref") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_db.py::TestProvenance -v`
Expected: FAIL — columns don't exist yet

- [ ] **Step 3: Update SCHEMA constant**

In `db.py`, update the `SCHEMA` constant (lines 15-35) to add the two columns before `created_at`:

```python
SCHEMA = """\
CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY,
    severity TEXT NOT NULL CHECK(severity IN ('critical', 'high', 'medium', 'low')),
    category TEXT NOT NULL,
    file TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK(status IN ('open', 'in_progress', 'fixed', 'not_a_bug', 'wont_fix', 'stale')),
    description TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'human',
    tags TEXT NOT NULL DEFAULT '[]',
    meta TEXT NOT NULL DEFAULT '{}',
    reported_at_commit TEXT,
    reported_at_ref TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_findings_status ON findings(status);
CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(severity);
CREATE INDEX IF NOT EXISTS idx_findings_file ON findings(file);
CREATE INDEX IF NOT EXISTS idx_findings_category ON findings(category);
CREATE INDEX IF NOT EXISTS idx_findings_reported_at_ref ON findings(reported_at_ref);
"""
```

- [ ] **Step 4: Update `_migrate_statuses()` hardcoded DDL**

In `db.py`, update the CREATE TABLE inside `_migrate_statuses()` (lines 121-135) to include the new columns. Also update the INSERT to handle the column count difference — old tables won't have provenance columns:

```python
def _migrate_statuses(conn: sqlite3.Connection) -> None:
    """Add 'in_progress' to the status CHECK constraint on existing databases."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='findings'"
    ).fetchone()
    if row is None:
        return
    ddl = row[0] or ""
    if "in_progress" in ddl:
        return  # already up-to-date

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        """CREATE TABLE findings_new (
            id TEXT PRIMARY KEY,
            severity TEXT NOT NULL CHECK(severity IN ('critical', 'high', 'medium', 'low')),
            category TEXT NOT NULL,
            file TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open'
                CHECK(status IN ('open', 'in_progress', 'fixed', 'not_a_bug', 'wont_fix', 'stale')),
            description TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'human',
            tags TEXT NOT NULL DEFAULT '[]',
            meta TEXT NOT NULL DEFAULT '{}',
            reported_at_commit TEXT,
            reported_at_ref TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """INSERT INTO findings_new
           (id, severity, category, file, status, description, source, tags, meta, created_at, updated_at)
           SELECT id, severity, category, file, status, description, source, tags, meta, created_at, updated_at
           FROM findings"""
    )
    conn.execute("DROP TABLE findings")
    conn.execute("ALTER TABLE findings_new RENAME TO findings")
    # Re-create indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_status ON findings(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(severity)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_file ON findings(file)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_category ON findings(category)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_reported_at_ref ON findings(reported_at_ref)")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
```

- [ ] **Step 5: Add `_migrate_provenance()` and call it from `connect()`**

Add after `_migrate_statuses`:

```python
def _migrate_provenance(conn: sqlite3.Connection) -> None:
    """Add provenance columns to existing databases that already passed status migration."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(findings)").fetchall()}
    if "reported_at_commit" not in cols:
        conn.execute("ALTER TABLE findings ADD COLUMN reported_at_commit TEXT")
    if "reported_at_ref" not in cols:
        conn.execute("ALTER TABLE findings ADD COLUMN reported_at_ref TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_reported_at_ref ON findings(reported_at_ref)")
    conn.commit()
```

In `connect()`, add after the `_migrate_statuses(conn)` call (line 94):

```python
    _migrate_provenance(conn)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_db.py::TestProvenance -v`
Expected: PASS

- [ ] **Step 7: Write migration test for existing DB without provenance columns**

```python
    def test_migrate_adds_provenance_to_existing_db(self, tmp_project):
        """Simulate a DB created before provenance columns existed."""
        path = os.path.join(tmp_project, db.DB_DIR, db.DB_FILE)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        old_conn = sqlite3.connect(path)
        old_conn.execute("""CREATE TABLE findings (
            id TEXT PRIMARY KEY,
            severity TEXT NOT NULL,
            category TEXT NOT NULL,
            file TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open'
                CHECK(status IN ('open', 'in_progress', 'fixed', 'not_a_bug', 'wont_fix', 'stale')),
            description TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'human',
            tags TEXT NOT NULL DEFAULT '[]',
            meta TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        old_conn.execute(
            "INSERT INTO findings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("CB-1", "high", "bug", "x.py", "open", "old bug", "human", "[]", "{}", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        old_conn.commit()
        old_conn.close()

        # Re-open via connect() which triggers migration
        conn = db.connect(tmp_project)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(findings)").fetchall()}
        assert "reported_at_commit" in cols
        assert "reported_at_ref" in cols

        # Old data survives
        row = conn.execute("SELECT * FROM findings WHERE id = 'CB-1'").fetchone()
        assert row is not None
        assert row["reported_at_commit"] is None
        assert row["reported_at_ref"] is None
        conn.close()
```

- [ ] **Step 8: Run the migration test**

Run: `python -m pytest tests/test_db.py::TestProvenance::test_migrate_adds_provenance_to_existing_db -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add src/codebugs/db.py tests/test_db.py
git commit -m "feat(provenance): add schema columns and migration for reported_at_commit/ref"
```

---

### Task 2: Update `add_finding` and `batch_add_findings` in db.py

**Files:**
- Modify: `src/codebugs/db.py:162-233` (add_finding, batch_add_findings)
- Test: `tests/test_db.py`

- [ ] **Step 1: Write failing tests**

```python
    def test_add_with_explicit_provenance(self, conn):
        result = db.add_finding(
            conn, severity="high", category="bug", file="a.py",
            description="test",
            reported_at_commit="a" * 40,
            reported_at_ref="v2.1.0",
        )
        assert result["reported_at_commit"] == "a" * 40
        assert result["reported_at_ref"] == "v2.1.0"

    def test_add_without_provenance_defaults_none(self, conn):
        result = db.add_finding(
            conn, severity="high", category="bug", file="a.py",
            description="test",
        )
        assert result["reported_at_commit"] is None
        assert result["reported_at_ref"] is None

    def test_batch_add_with_provenance(self, conn):
        results = db.batch_add_findings(conn, [
            {
                "severity": "high", "category": "bug", "file": "a.py",
                "description": "d1",
                "reported_at_commit": "b" * 40,
                "reported_at_ref": "v1.0",
            },
            {
                "severity": "low", "category": "style", "file": "b.py",
                "description": "d2",
            },
        ])
        assert results[0]["reported_at_commit"] == "b" * 40
        assert results[0]["reported_at_ref"] == "v1.0"
        assert results[1]["reported_at_commit"] is None
        assert results[1]["reported_at_ref"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_db.py::TestProvenance::test_add_with_explicit_provenance -v`
Expected: FAIL — `add_finding` doesn't accept `reported_at_commit`

- [ ] **Step 3: Update `add_finding()`**

Add parameters and update the INSERT:

```python
def add_finding(
    conn: sqlite3.Connection,
    *,
    severity: str,
    category: str,
    file: str,
    description: str,
    source: str = "human",
    tags: list[str] | None = None,
    meta: dict[str, Any] | None = None,
    finding_id: str | None = None,
    reported_at_commit: str | None = None,
    reported_at_ref: str | None = None,
) -> dict[str, Any]:
    """Add a single finding. Returns the created finding as a dict."""
    if severity not in VALID_SEVERITIES:
        raise ValueError(f"Invalid severity: {severity}. Must be one of {VALID_SEVERITIES}")

    fid = finding_id or _next_id(conn)
    now = _now()
    tags_json = json.dumps(tags or [])
    meta_json = json.dumps(meta or {})

    conn.execute(
        """INSERT INTO findings (id, severity, category, file, status, description,
           source, tags, meta, reported_at_commit, reported_at_ref, created_at, updated_at)
           VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?)""",
        (fid, severity, category, file, description, source, tags_json, meta_json,
         reported_at_commit, reported_at_ref, now, now),
    )
    conn.commit()
    return _row_to_dict(conn.execute("SELECT * FROM findings WHERE id = ?", (fid,)).fetchone())
```

- [ ] **Step 4: Update `batch_add_findings()`**

```python
def batch_add_findings(
    conn: sqlite3.Connection,
    findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add multiple findings at once. Returns list of created findings."""
    now = _now()
    results = []
    for f in findings:
        severity = f.get("severity", "medium")
        if severity not in VALID_SEVERITIES:
            raise ValueError(f"Invalid severity: {severity}")

        fid = f.get("id") or _next_id(conn)
        tags_json = json.dumps(f.get("tags", []))
        meta_json = json.dumps(f.get("meta", {}))

        conn.execute(
            """INSERT INTO findings (id, severity, category, file, status, description,
               source, tags, meta, reported_at_commit, reported_at_ref, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                fid,
                severity,
                f["category"],
                f["file"],
                f["description"],
                f.get("source", "human"),
                tags_json,
                meta_json,
                f.get("reported_at_commit"),
                f.get("reported_at_ref"),
                now,
                now,
            ),
        )
        results.append(fid)

    conn.commit()
    rows = conn.execute(
        f"SELECT * FROM findings WHERE id IN ({','.join('?' for _ in results)})",
        results,
    ).fetchall()
    return [_row_to_dict(r) for r in rows]
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_db.py::TestProvenance -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/codebugs/db.py tests/test_db.py
git commit -m "feat(provenance): accept reported_at_commit/ref in add and batch_add"
```

---

### Task 3: Update `query_findings` and `update_finding` in db.py

**Files:**
- Modify: `src/codebugs/db.py:236-283` (update_finding)
- Modify: `src/codebugs/db.py:286-355` (query_findings)
- Test: `tests/test_db.py`

- [ ] **Step 1: Write failing tests for query filters**

```python
    def test_query_by_commit_prefix(self, conn):
        sha = "a1b2c3d4e5" + "0" * 30
        db.add_finding(
            conn, severity="high", category="bug", file="a.py",
            description="d", reported_at_commit=sha,
        )
        db.add_finding(
            conn, severity="low", category="style", file="b.py",
            description="d2",
        )
        result = db.query_findings(conn, commit="a1b2c3d4e5")
        assert result["total"] == 1
        assert result["findings"][0]["reported_at_commit"] == sha

    def test_query_by_commit_rejects_non_hex(self, conn):
        with pytest.raises(ValueError, match="hex"):
            db.query_findings(conn, commit="not-hex!")

    def test_query_by_ref(self, conn):
        db.add_finding(
            conn, severity="high", category="bug", file="a.py",
            description="d", reported_at_ref="v2.1.0",
        )
        db.add_finding(
            conn, severity="low", category="style", file="b.py",
            description="d2", reported_at_ref="v3.0.0",
        )
        result = db.query_findings(conn, ref="v2.1.0")
        assert result["total"] == 1
        assert result["findings"][0]["reported_at_ref"] == "v2.1.0"
```

- [ ] **Step 2: Write failing tests for update**

```python
    def test_update_reported_at_ref(self, conn):
        f = db.add_finding(
            conn, severity="high", category="bug", file="a.py", description="d",
        )
        updated = db.update_finding(conn, f["id"], reported_at_ref="v2.0")
        assert updated["reported_at_ref"] == "v2.0"

    def test_update_does_not_accept_reported_at_commit(self, conn):
        """reported_at_commit is immutable — not a parameter of update_finding."""
        f = db.add_finding(
            conn, severity="high", category="bug", file="a.py", description="d",
            reported_at_commit="a" * 40,
        )
        # update_finding has no reported_at_commit param — this should TypeError
        with pytest.raises(TypeError):
            db.update_finding(conn, f["id"], reported_at_commit="b" * 40)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_db.py::TestProvenance::test_query_by_commit_prefix tests/test_db.py::TestProvenance::test_update_reported_at_ref -v`
Expected: FAIL

- [ ] **Step 4: Update `query_findings()` to add commit and ref filters**

Add `commit` and `ref` parameters. Validate `commit` as hex:

```python
def query_findings(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    severity: str | None = None,
    category: str | None = None,
    file: str | None = None,
    source: str | None = None,
    tag: str | None = None,
    meta_key: str | None = None,
    meta_value: str | None = None,
    commit: str | None = None,
    ref: str | None = None,
    group_by: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Query findings with filters. Returns results or grouped counts."""
    conditions: list[str] = []
    params: list[Any] = []

    if status:
        conditions.append("status = ?")
        params.append(resolve_status(status))
    if severity:
        conditions.append("severity = ?")
        params.append(severity)
    if category:
        conditions.append("category = ?")
        params.append(category)
    if file:
        conditions.append("file LIKE ?")
        params.append(f"%{file}%")
    if source:
        conditions.append("source = ?")
        params.append(source)
    if tag:
        conditions.append("EXISTS (SELECT 1 FROM json_each(tags) WHERE json_each.value = ?)")
        params.append(tag)
    if meta_key and meta_value:
        conditions.append("json_extract(meta, ?) = ?")
        params.append(f"$.{meta_key}")
        params.append(meta_value)
    elif meta_key:
        conditions.append("json_extract(meta, ?) IS NOT NULL")
        params.append(f"$.{meta_key}")
    if commit:
        if not re.fullmatch(r"[0-9a-fA-F]+", commit):
            raise ValueError(f"commit filter must be hex, got: {commit!r}")
        conditions.append("reported_at_commit LIKE ? || '%'")
        params.append(commit.lower())
    if ref:
        conditions.append("reported_at_ref = ?")
        params.append(ref)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    if group_by:
        valid_groups = ("file", "category", "severity", "status", "source")
        if group_by not in valid_groups:
            raise ValueError(f"Invalid group_by: {group_by}. Must be one of {valid_groups}")
        rows = conn.execute(
            f"SELECT {group_by} as group_key, COUNT(*) as count FROM findings {where} GROUP BY {group_by} ORDER BY count DESC",
            params,
        ).fetchall()
        return {"grouped": True, "group_by": group_by, "groups": [dict(r) for r in rows]}

    count = conn.execute(f"SELECT COUNT(*) as c FROM findings {where}", params).fetchone()["c"]
    params.extend([limit, offset])
    rows = conn.execute(
        f"SELECT * FROM findings {where} ORDER BY severity, created_at DESC LIMIT ? OFFSET ?",
        params,
    ).fetchall()
    return {
        "grouped": False,
        "total": count,
        "limit": limit,
        "offset": offset,
        "findings": [_row_to_dict(r) for r in rows],
    }
```

- [ ] **Step 5: Update `update_finding()` to accept `reported_at_ref`**

Add the `reported_at_ref` parameter. Add a comment documenting the immutability of `reported_at_commit`:

```python
def update_finding(
    conn: sqlite3.Connection,
    finding_id: str,
    *,
    status: str | None = None,
    notes: str | None = None,
    tags: list[str] | None = None,
    meta_update: dict[str, Any] | None = None,
    reported_at_ref: str | None = None,
) -> dict[str, Any]:
    """Update a finding. Returns updated finding.

    Note: reported_at_commit is intentionally excluded — it is immutable after insert.
    """
    row = conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone()
    if not row:
        raise KeyError(f"Finding not found: {finding_id}")

    updates = []
    params: list[Any] = []

    if status is not None:
        status = resolve_status(status)
        updates.append("status = ?")
        params.append(status)

    if notes is not None:
        existing_meta = json.loads(row["meta"])
        existing_meta["notes"] = notes
        updates.append("meta = ?")
        params.append(json.dumps(existing_meta))

    if tags is not None:
        updates.append("tags = ?")
        params.append(json.dumps(tags))

    if meta_update is not None:
        existing_meta = json.loads(row["meta"])
        existing_meta.update(meta_update)
        updates.append("meta = ?")
        params.append(json.dumps(existing_meta))

    if reported_at_ref is not None:
        updates.append("reported_at_ref = ?")
        params.append(reported_at_ref)

    if not updates:
        return _row_to_dict(row)

    updates.append("updated_at = ?")
    params.append(_now())
    params.append(finding_id)

    conn.execute(f"UPDATE findings SET {', '.join(updates)} WHERE id = ?", params)
    conn.commit()
    return _row_to_dict(conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone())
```

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/test_db.py::TestProvenance -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/codebugs/db.py tests/test_db.py
git commit -m "feat(provenance): add commit/ref filters to query, ref update to update_finding"
```

---

### Task 4: Update MCP tools in server.py (`add`, `batch_add`, `query`, `update`)

**Files:**
- Modify: `src/codebugs/server.py:23-29` (add `_get_head_sha` helper)
- Modify: `src/codebugs/server.py:35-67` (add tool)
- Modify: `src/codebugs/server.py:69-79` (batch_add tool)
- Modify: `src/codebugs/server.py:81-114` (update tool)
- Modify: `src/codebugs/server.py:117-162` (query tool)
- Test: `tests/test_server.py` (if exists, otherwise `tests/test_db.py`)

- [ ] **Step 1: Add `_get_head_sha()` helper in server.py**

Add after `_get_main_head()`:

```python
def _get_head_sha() -> str | None:
    """Get current HEAD SHA for provenance auto-population. Returns None if git unavailable."""
    import subprocess
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True, timeout=10, stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
```

- [ ] **Step 2: Update `add()` tool**

```python
    @mcp.tool()
    def add(
        severity: str,
        category: str,
        file: str,
        description: str,
        source: str = "claude",
        tags: list[str] | None = None,
        meta: dict[str, Any] | None = None,
        reported_at_commit: str | None = None,
        reported_at_ref: str | None = None,
    ) -> dict[str, Any]:
        """Add a code finding.

        Args:
            severity: critical, high, medium, or low
            category: Finding category (e.g. tz_naive_datetime, n_plus_one, missing_validation).
                      Call `categories` first to reuse existing category names.
            file: File path relative to project root
            description: What's wrong
            source: Who created this finding (default: claude)
            tags: Optional tags for grouping
            meta: Optional JSON metadata (lines, module, rule_code, etc.)
            reported_at_commit: Git SHA when finding was created (auto-detected from HEAD if omitted)
            reported_at_ref: Version/tag label (e.g. "v2.1.0"), always caller-supplied
        """
        if reported_at_commit is None:
            reported_at_commit = _get_head_sha()
        with _conn() as conn:
            return db.add_finding(
                conn,
                severity=severity,
                category=category,
                file=file,
                description=description,
                source=source,
                tags=tags,
                meta=meta,
                reported_at_commit=reported_at_commit,
                reported_at_ref=reported_at_ref,
            )
```

- [ ] **Step 3: Update `batch_add()` tool**

```python
    @mcp.tool()
    def batch_add(
        findings: list[dict[str, Any]],
        reported_at_commit: str | None = None,
        reported_at_ref: str | None = None,
    ) -> list[dict[str, Any]]:
        """Add multiple findings at once.

        Args:
            findings: List of finding objects, each with keys:
                severity, category, file, description, and optionally:
                source, tags, meta, reported_at_commit, reported_at_ref
            reported_at_commit: Default commit SHA for all findings (auto-detected if omitted).
                                Per-finding values override this.
            reported_at_ref: Default version label for all findings.
                             Per-finding values override this.
        """
        default_commit = reported_at_commit if reported_at_commit is not None else _get_head_sha()
        for f in findings:
            if "reported_at_commit" not in f:
                f["reported_at_commit"] = default_commit
            if "reported_at_ref" not in f and reported_at_ref is not None:
                f["reported_at_ref"] = reported_at_ref
        with _conn() as conn:
            return db.batch_add_findings(conn, findings)
```

- [ ] **Step 4: Update `update()` tool**

Add `reported_at_ref` parameter:

```python
    @mcp.tool()
    def update(
        finding_id: str,
        status: str | None = None,
        notes: str | None = None,
        tags: list[str] | None = None,
        meta_update: dict[str, Any] | None = None,
        reported_at_ref: str | None = None,
    ) -> dict[str, Any]:
        """Update a finding's status, notes, tags, or metadata.

        Args:
            finding_id: The finding ID (e.g. CB-1)
            status: New status: open, in_progress, fixed, not_a_bug, wont_fix, stale.
                    Aliases accepted: done/resolved/implemented/closed → fixed,
                    wontfix → wont_fix, invalid → not_a_bug,
                    active/working/in-progress → in_progress
            notes: Add/update notes (stored in meta.notes)
            tags: Replace tags list
            meta_update: Merge additional metadata keys
            reported_at_ref: Update version/tag label (e.g. "v2.1.0")
        """
        with _conn() as conn:
            result = db.update_finding(
                conn,
                finding_id,
                status=status,
                notes=notes,
                tags=tags,
                meta_update=meta_update,
                reported_at_ref=reported_at_ref,
            )
            if status and result.get("status") in blockers.TERMINAL_STATUSES.get(blockers.ENTITY_FINDING, set()):
                unblocked = blockers.get_unblocked_by(conn, finding_id, blockers.ENTITY_FINDING)
                if unblocked:
                    result["unblocked_items"] = unblocked
            return result
```

- [ ] **Step 5: Update `query()` tool**

Add `commit` and `ref` parameters:

```python
    @mcp.tool()
    def query(
        status: str | None = None,
        severity: str | None = None,
        category: str | None = None,
        file: str | None = None,
        source: str | None = None,
        tag: str | None = None,
        meta_key: str | None = None,
        meta_value: str | None = None,
        commit: str | None = None,
        ref: str | None = None,
        group_by: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Search and filter findings. Returns structured results.

        Args:
            status: Filter by status (open, in_progress, fixed, not_a_bug, wont_fix, stale, deferred). Aliases accepted.
                    Use 'deferred' to find items with active blockers.
            severity: Filter by severity (critical, high, medium, low)
            category: Filter by exact category
            file: Filter by file path (substring match)
            source: Filter by source (claude, ruff, human, etc.)
            tag: Filter by tag (finds findings containing this tag)
            meta_key: Filter by metadata key existence
            meta_value: Filter by metadata value (requires meta_key)
            commit: Filter by reported_at_commit (prefix match, hex validated)
            ref: Filter by reported_at_ref (exact match)
            group_by: Group results by: file, category, severity, status, source
            limit: Max results (default 100)
            offset: Pagination offset
        """
        with _conn() as conn:
            if status == "deferred":
                return blockers.query_deferred_entities(conn, blockers.ENTITY_FINDING, limit=limit, offset=offset)
            return db.query_findings(
                conn,
                status=status,
                severity=severity,
                category=category,
                file=file,
                source=source,
                tag=tag,
                meta_key=meta_key,
                meta_value=meta_value,
                commit=commit,
                ref=ref,
                group_by=group_by,
                limit=limit,
                offset=offset,
            )
```

- [ ] **Step 6: Run all existing tests to check nothing breaks**

Run: `python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add src/codebugs/server.py
git commit -m "feat(provenance): update MCP tools with commit/ref params and auto-population"
```

---

### Task 5: Staleness Check Tool

**Files:**
- Modify: `src/codebugs/server.py` (add `staleness_check` tool inside `register_findings_tools`)
- Test: `tests/test_staleness.py` (new file)

- [ ] **Step 1: Write tests for staleness check**

Create `tests/test_staleness.py`:

```python
"""Tests for staleness detection."""

import os
import subprocess

import pytest

from codebugs import db


@pytest.fixture
def git_project(tmp_path):
    """Create a temporary git repo with a tracked file and some commits."""
    project = str(tmp_path)
    subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=project, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=project, check=True, capture_output=True)

    # Create and commit a file
    test_file = os.path.join(project, "src", "auth.py")
    os.makedirs(os.path.dirname(test_file), exist_ok=True)
    with open(test_file, "w") as f:
        f.write("# auth module\n")
    subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=project, check=True, capture_output=True)

    # Record the initial commit
    initial_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=project, text=True
    ).strip()

    return project, initial_sha


@pytest.fixture
def conn(git_project):
    project, _ = git_project
    c = db.connect(project)
    yield c
    c.close()


def _head(project):
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=project, text=True
    ).strip()


class TestCheckFileStaleness:
    """Test the _check_file_staleness helper directly."""

    def test_current_file(self, git_project):
        project, initial_sha = git_project
        # Import here to avoid circular issues
        from codebugs.server import _check_file_staleness
        result = _check_file_staleness("src/auth.py", initial_sha, project)
        assert result["file_status"] == "current"

    def test_modified_file(self, git_project):
        project, initial_sha = git_project
        # Modify the file and commit
        with open(os.path.join(project, "src", "auth.py"), "a") as f:
            f.write("def login(): pass\n")
        subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add login"], cwd=project, check=True, capture_output=True)

        from codebugs.server import _check_file_staleness
        result = _check_file_staleness("src/auth.py", initial_sha, project)
        assert result["file_status"] == "modified"
        assert "1 commit" in result["reason"]

    def test_deleted_file(self, git_project):
        project, initial_sha = git_project
        os.remove(os.path.join(project, "src", "auth.py"))
        subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "remove auth"], cwd=project, check=True, capture_output=True)

        from codebugs.server import _check_file_staleness
        result = _check_file_staleness("src/auth.py", initial_sha, project)
        assert result["file_status"] == "deleted"

    def test_renamed_file(self, git_project):
        project, initial_sha = git_project
        os.rename(
            os.path.join(project, "src", "auth.py"),
            os.path.join(project, "src", "authentication.py"),
        )
        subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "rename auth"], cwd=project, check=True, capture_output=True)

        from codebugs.server import _check_file_staleness
        result = _check_file_staleness("src/auth.py", initial_sha, project)
        assert result["file_status"] == "renamed"
        assert "authentication.py" in result["reason"]

    def test_unknown_no_commit(self, git_project):
        project, _ = git_project
        from codebugs.server import _check_file_staleness
        result = _check_file_staleness("src/auth.py", None, project)
        assert result["file_status"] == "unknown"
        assert result["reason"] == "no_provenance"

    def test_unknown_bad_commit(self, git_project):
        project, _ = git_project
        from codebugs.server import _check_file_staleness
        result = _check_file_staleness("src/auth.py", "deadbeef" * 5, project)
        assert result["file_status"] == "unknown"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_staleness.py -v`
Expected: FAIL — `_check_file_staleness` doesn't exist

- [ ] **Step 3: Implement `_check_file_staleness()` helper in server.py**

Add after `_get_head_sha()`:

```python
def _check_file_staleness(
    file_path: str,
    reported_at_commit: str | None,
    project_dir: str | None = None,
) -> dict[str, Any]:
    """Check staleness of a single file against a commit. Returns file_status dict."""
    import subprocess

    cwd = project_dir or os.getcwd()

    if not reported_at_commit:
        return {"file_status": "unknown", "reason": "no_provenance"}

    # Check if the commit is reachable
    try:
        subprocess.check_output(
            ["git", "cat-file", "-t", reported_at_commit],
            cwd=cwd, text=True, timeout=10, stderr=subprocess.DEVNULL,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return {"file_status": "unknown", "reason": "unreachable_commit"}

    # Check if file was modified since the commit
    try:
        log_output = subprocess.check_output(
            ["git", "log", "--oneline", f"{reported_at_commit}..HEAD", "--", file_path],
            cwd=cwd, text=True, timeout=10, stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return {"file_status": "unknown", "reason": "git_error"}

    if not log_output:
        return {"file_status": "current", "reason": f"{file_path} unchanged since {reported_at_commit[:12]}"}

    commit_count = len(log_output.splitlines())

    # File was changed — check if it still exists at HEAD
    file_exists = os.path.isfile(os.path.join(cwd, file_path))

    if file_exists:
        s = "commit" if commit_count == 1 else "commits"
        return {
            "file_status": "modified",
            "reason": f"{file_path} modified in {commit_count} {s} since {reported_at_commit[:12]}",
        }

    # File doesn't exist — check for rename
    try:
        rename_output = subprocess.check_output(
            ["git", "log", "--diff-filter=R", "--find-renames", "--format=", "--name-status",
             f"{reported_at_commit}..HEAD", "--", file_path],
            cwd=cwd, text=True, timeout=10, stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        rename_output = ""

    if rename_output:
        # Parse rename: "R100\told_path\tnew_path"
        for line in rename_output.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                new_path = parts[2]
                return {
                    "file_status": "renamed",
                    "reason": f"{file_path} renamed to {new_path}",
                }

    return {
        "file_status": "deleted",
        "reason": f"{file_path} deleted since {reported_at_commit[:12]}",
    }
```

Add `import os` to the top of server.py if not present.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_staleness.py::TestCheckFileStaleness -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codebugs/server.py tests/test_staleness.py
git commit -m "feat(provenance): add _check_file_staleness helper with rename detection"
```

---

### Task 6: `staleness_check` MCP Tool

**Files:**
- Modify: `src/codebugs/server.py` (register tool inside `register_findings_tools`)
- Test: `tests/test_staleness.py`

- [ ] **Step 1: Write integration test for the MCP tool**

Add to `tests/test_staleness.py`:

```python
class TestStalenessCheckTool:
    """Test the staleness_check MCP tool end-to-end."""

    def test_staleness_check_single_finding(self, git_project, conn):
        project, initial_sha = git_project
        # Add a finding with provenance
        db.add_finding(
            conn, severity="high", category="bug", file="src/auth.py",
            description="auth bug", reported_at_commit=initial_sha,
        )

        from codebugs.server import _staleness_check_impl
        result = _staleness_check_impl(conn, project, finding_id="CB-1")
        assert len(result["findings"]) == 1
        assert result["findings"][0]["file_status"] == "current"

    def test_staleness_check_filters_by_status(self, git_project, conn):
        project, initial_sha = git_project
        db.add_finding(
            conn, severity="high", category="bug", file="src/auth.py",
            description="open bug", reported_at_commit=initial_sha,
        )
        db.update_finding(conn, "CB-1", status="fixed")
        db.add_finding(
            conn, severity="low", category="style", file="src/auth.py",
            description="open style", reported_at_commit=initial_sha,
        )

        from codebugs.server import _staleness_check_impl
        result = _staleness_check_impl(conn, project, status="open")
        assert len(result["findings"]) == 1
        assert result["findings"][0]["finding_id"] == "CB-2"

    def test_staleness_check_batches_by_file(self, git_project, conn):
        """Multiple findings on the same file should not cause redundant git calls."""
        project, initial_sha = git_project
        for i in range(3):
            db.add_finding(
                conn, severity="high", category="bug", file="src/auth.py",
                description=f"bug {i}", reported_at_commit=initial_sha,
            )

        from codebugs.server import _staleness_check_impl
        result = _staleness_check_impl(conn, project)
        assert len(result["findings"]) == 3
        # All should have the same file_status since they reference the same file+commit
        statuses = {f["file_status"] for f in result["findings"]}
        assert statuses == {"current"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_staleness.py::TestStalenessCheckTool -v`
Expected: FAIL — `_staleness_check_impl` doesn't exist

- [ ] **Step 3: Implement `_staleness_check_impl()` and register MCP tool**

Add the implementation function and tool registration inside `register_findings_tools`:

```python
def _staleness_check_impl(
    conn: sqlite3.Connection,
    project_dir: str | None,
    *,
    finding_id: str | None = None,
    status: str | None = None,
    category: str | None = None,
    file: str | None = None,
) -> dict[str, Any]:
    """Core staleness check logic. Separated for testability."""
    import subprocess

    cwd = project_dir or os.getcwd()

    # Build query to get relevant findings
    query_kwargs: dict[str, Any] = {"limit": 10000}
    if finding_id:
        query_kwargs["limit"] = 1
    if status:
        query_kwargs["status"] = status
    elif not finding_id:
        query_kwargs["status"] = "open"
    if category:
        query_kwargs["category"] = category
    if file:
        query_kwargs["file"] = file

    if finding_id:
        row = conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone()
        if not row:
            raise KeyError(f"Finding not found: {finding_id}")
        findings_list = [db._row_to_dict(row)]
    else:
        result = db.query_findings(conn, **query_kwargs)
        findings_list = result["findings"]

    # Get current HEAD
    try:
        current_head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=cwd, text=True, timeout=10,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        current_head = None

    # Batch by (file, reported_at_commit) to avoid redundant git calls
    staleness_cache: dict[tuple[str, str | None], dict[str, Any]] = {}
    results = []

    for f in findings_list:
        cache_key = (f["file"], f.get("reported_at_commit"))
        if cache_key not in staleness_cache:
            staleness_cache[cache_key] = _check_file_staleness(
                f["file"], f.get("reported_at_commit"), cwd,
            )
        staleness = staleness_cache[cache_key]
        results.append({
            "finding_id": f["id"],
            "file": f["file"],
            "file_status": staleness["file_status"],
            "reason": staleness["reason"],
            "reported_at_commit": f.get("reported_at_commit"),
            "current_head": current_head,
        })

    return {"findings": results, "total": len(results)}
```

Then register the MCP tool inside `register_findings_tools()`, after the `categories` tool:

```python
    @mcp.tool()
    def staleness_check(
        finding_id: str | None = None,
        status: str | None = None,
        category: str | None = None,
        file: str | None = None,
    ) -> dict[str, Any]:
        """Check if findings are stale by comparing against git history.

        Returns file_status for each finding:
        - current: file unchanged since finding was reported
        - modified: file changed but still exists
        - renamed: file was renamed/moved
        - deleted: file no longer exists
        - unknown: can't determine (no provenance data, unreachable commit)

        Args:
            finding_id: Check a single finding (e.g. CB-1)
            status: Filter by finding status (default: open)
            category: Filter by category
            file: Filter by file path (substring match)
        """
        with _conn() as conn:
            return _staleness_check_impl(conn, None, finding_id=finding_id,
                                          status=status, category=category, file=file)
```

Add `import sqlite3` to server.py imports.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_staleness.py -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/codebugs/server.py tests/test_staleness.py
git commit -m "feat(provenance): add staleness_check MCP tool with file-batched git ops"
```
