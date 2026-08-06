# Codesweep Design

Batch iteration utility for the codebugs MCP server. Creates ordered lists of arbitrary items, then iterates through them in batches without double-passes or misses.

## Data Model

### `codesweep_sweeps` table

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `sweep_id` | TEXT UNIQUE | `SW-{id}` format |
| `name` | TEXT | Optional human-readable name (unique if provided) |
| `description` | TEXT | What this sweep is for |
| `default_batch_size` | INTEGER | Default items per `next_batch` call (default: 10) |
| `status` | TEXT | `active` or `archived` |
| `created_at` | TEXT | ISO8601 timestamp |
| `updated_at` | TEXT | ISO8601 timestamp |

### `codesweep_items` table

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `sweep_id` | TEXT FK | References `codesweep_sweeps.sweep_id` |
| `item` | TEXT | Arbitrary string identifier |
| `tags` | TEXT | JSON array, e.g. `["critical", "frontend"]` |
| `processed` | INTEGER | 0 or 1 |
| `position` | INTEGER | Insertion order within the sweep |
| `created_at` | TEXT | ISO8601 timestamp |
| `processed_at` | TEXT | Nullable, set when marked processed |

Unique constraint on `(sweep_id, item)` to prevent duplicates within a sweep.

### Indexes

- `idx_codesweep_items_sweep` on `(sweep_id, processed, position)` — covers the `next_batch` query
- `idx_codesweep_items_sweep_item` on `(sweep_id, item)` — covers the uniqueness check and mark lookups

## MCP Tools

Seven tools, registered via `register_sweep_tools(mcp)`:

### `codesweep_create`

Creates a new sweep.

**Parameters:**
- `name` (str, optional): Human-readable name. Must be unique if provided.
- `description` (str, optional): What this sweep is for.
- `default_batch_size` (int, optional): Default items per batch. Default: 10.

**Returns:** `{sweep_id: "SW-1", name: "...", status: "active", default_batch_size: 10}`

### `codesweep_add`

Adds items to a sweep. Duplicates within the sweep are silently skipped.

**Parameters:**
- `sweep` (str): Sweep reference — accepts `SW-N` ID or name.
- `items` (list[str]): Item identifiers to add.
- `tags` (list[str], optional): Tags applied to all items in this batch.

**Returns:** `{sweep_id: "SW-1", added: 5, duplicates_skipped: 1}`

**Errors:** `ValueError` if sweep not found or archived.

### `codesweep_next`

Returns the next batch of unprocessed items in insertion order.

**Parameters:**
- `sweep` (str): Sweep reference.
- `limit` (int, optional): Batch size. Overrides `default_batch_size`.
- `tags` (list[str], optional): Filter to items matching any of these tags.

**Returns:** `{sweep_id: "SW-1", items: [{item: "src/foo.py", tags: ["critical"], position: 3}, ...], remaining: 42}`

Tag filtering uses `json_each()` — an item matches if it has any of the requested tags.

### `codesweep_mark`

Marks items as processed or unprocessed.

**Parameters:**
- `sweep` (str): Sweep reference.
- `items` (list[str]): Item identifiers to mark.
- `processed` (bool, optional): True to mark processed (default), False to unmark.

**Returns:** `{sweep_id: "SW-1", updated: 3}`

**Errors:** `KeyError` if any item not found in the sweep.

### `codesweep_status`

Returns sweep overview with progress and per-tag breakdown.

**Parameters:**
- `sweep` (str): Sweep reference.

**Returns:**
```json
{
  "sweep_id": "SW-1",
  "name": "lint-pass",
  "status": "active",
  "default_batch_size": 10,
  "total": 50,
  "processed": 23,
  "remaining": 27,
  "by_tag": {
    "critical": {"total": 5, "processed": 2},
    "frontend": {"total": 12, "processed": 8}
  }
}
```

### `codesweep_archive`

Archives a sweep. Archived sweeps are excluded from `codesweep_list` by default.

**Parameters:**
- `sweep` (str): Sweep reference.

**Returns:** `{sweep_id: "SW-1", status: "archived"}`

### `codesweep_list`

Lists all sweeps with summary counts.

**Parameters:**
- `include_archived` (bool, optional): Include archived sweeps. Default: false.

**Returns:**
```json
{
  "sweeps": [
    {"sweep_id": "SW-1", "name": "lint-pass", "status": "active", "total": 50, "processed": 23, "remaining": 27}
  ]
}
```

## Module Structure

### New file: `src/codebugs/sweep.py`

- `SCHEMA` constant with both CREATE TABLE statements and indexes
- `ensure_schema(conn)` — executes SCHEMA, called from `db.connect()`
- `_resolve_sweep(conn, ref)` — internal helper that resolves a sweep reference (ID or name) to a `sweep_id`, raises `ValueError` if not found
- `_next_position(conn, sweep_id)` — returns next insertion position for a sweep
- Functions: `create_sweep()`, `add_items()`, `next_batch()`, `mark_items()`, `get_status()`, `archive_sweep()`, `list_sweeps()`
- All functions take `conn: sqlite3.Connection` as first arg, return dicts
- Uses `_now()` from `db` module (or local copy)
- Raises `ValueError` for invalid sweep references, adding to archived sweeps, batch size < 1
- Raises `KeyError` for marking items that don't exist in the sweep

### Modified: `src/codebugs/db.py`

In `connect()`, add after existing module initializations:

```python
from codebugs import sweep
sweep.ensure_schema(conn)
```

### Modified: `src/codebugs/server.py`

- Add `"sweep"` to `--mode` choices
- Add `"sweep": "codesweep"` to the name mapping dict
- New `register_sweep_tools(mcp)` function with 7 `@mcp.tool()` nested functions
- Each tool function uses lazy import: `from codebugs import sweep`
- Mode selection: `if args.mode in ("sweep", "all"): register_sweep_tools(server)`

### Modified: `src/codebugs/cli.py`

- Add `"sweep"` to `--mode` choices in both `pre_parser` and `parser`
- New `_register_sweep_subcommands(sub, commands)` function
- Commands: `sweep-create`, `sweep-add`, `sweep-next`, `sweep-mark`, `sweep-status`, `sweep-archive`, `sweep-list`
- Each `cmd_sweep_*` function: `conn = db.connect()` / call sweep function / `conn.close()`
- Mode selection: `if pre_args.mode in ("sweep", "all"): _register_sweep_subcommands(sub, commands)`

### New file: `tests/test_sweep.py`

- `conn` fixture: in-memory SQLite with `sweep.ensure_schema(conn)`
- Class-based test organization:
  - `TestCreateSweep` — basic creation, auto-ID, optional name, name uniqueness
  - `TestAddItems` — adding items, dedup counting, tags, append after creation, reject archived sweep
  - `TestNextBatch` — insertion order, batch size (default and override), tag filtering, empty when all processed
  - `TestMarkItems` — mark processed, unmark, KeyError for missing items
  - `TestGetStatus` — counts, per-tag breakdown
  - `TestArchiveSweep` — status change, excluded from list
  - `TestListSweeps` — active only by default, include_archived flag

## Error Handling

| Condition | Exception | Message |
|-----------|-----------|---------|
| Sweep not found (by ID or name) | `ValueError` | `"Sweep not found: {ref}"` |
| Adding to archived sweep | `ValueError` | `"Cannot add items to archived sweep: {sweep_id}"` |
| Batch size < 1 | `ValueError` | `"Batch size must be at least 1"` |
| Marking item not in sweep | `KeyError` | `"Item not found in sweep {sweep_id}: {item}"` |
| Duplicate sweep name | `ValueError` | `"Sweep name already exists: {name}"` |

## Key Design Decisions

1. **Arbitrary string items** — not limited to file paths; can hold finding IDs, URLs, or any identifier.
2. **JSON tags with `json_each()` filtering** — matches existing codebugs pattern for tags in findings/requirements.
3. **No auto-complete** — sweeps stay active until explicitly archived. Simpler mental model.
4. **Appendable** — items can be added to active sweeps after creation.
5. **Duplicate skipping** — `INSERT OR IGNORE` on the unique constraint; counted in return value. If an item already exists in the sweep, re-adding it with different tags does not update the existing tags.
6. **Position-based ordering** — `next_batch` always returns unprocessed items in insertion order, providing deterministic iteration.
7. **Standalone + combined** — runs as `codesweep` via `--mode sweep` or as part of the combined `codebugs` server via `--mode all`.
