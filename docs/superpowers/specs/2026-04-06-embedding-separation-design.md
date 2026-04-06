# ARCH-005: Embedding Separation

**Date:** 2026-04-06
**Status:** Design approved
**Requirement:** ARCH-005
**Depends on:** ARCH-003

## Problem

`reqs.py` is 1141 lines mixing CRUD operations with embedding/similarity search (~166 lines of embedding code). The embedding logic (vector packing, cosine similarity, store, search) is functionally independent from requirements CRUD but lives in the same file.

## Design

### New file: `src/codebugs/embeddings.py`

A focused module for embedding storage and similarity search. It operates on the `requirements` table's `embedding` column but owns no schema — the column is part of `reqs.py`'s REQS_SCHEMA.

**What moves from reqs.py:**
- `_pack_vector()` — float list to BLOB
- `_unpack_vector()` — BLOB to float list
- `_cosine_similarity()` — pure Python cosine similarity
- `store_embedding()` — store single embedding
- `batch_store_embeddings()` — batch store
- `search_similar()` — brute-force similarity search
- `embedding_stats()` — coverage statistics

**MCP tools move too** — the 4 embedding tool wrappers currently inside `reqs.py`'s `register_tools()`:
- `reqs_embed` → stays named `reqs_embed` (no rename — API stability)
- `reqs_batch_embed` → stays named `reqs_batch_embed`
- `reqs_search_similar` → stays named `reqs_search_similar`
- `reqs_embedding_stats` → stays named `reqs_embedding_stats`

### Registry approach

Embeddings are NOT a new domain — they're a code-organization split of the reqs domain. The tools register under "reqs" mode so they appear alongside other reqs tools:

```python
# embeddings.py
register_tool_provider("embeddings", register_tools)
```

In server.py, the "reqs" mode should include both the reqs and embeddings providers. Since `get_tool_providers(mode="reqs")` filters by `provider.name == "reqs"`, we need embeddings to either:
- Register as name "reqs" (but that collides with reqs.py's registration)
- Have server.py handle the grouping

**Simplest solution:** Don't use mode filtering for embeddings. Register as "embeddings" and update the mode dispatch so `--mode reqs` also includes "embeddings". In `get_tool_providers()`, add a mapping: `reqs` mode includes both `reqs` and `embeddings` providers.

Actually even simpler — just add the embedding tools inside reqs.py's `register_tools()` by importing from embeddings.py:

```python
# reqs.py register_tools()
def register_tools(mcp, conn_factory):
    # ... existing 7 CRUD tools ...
    
    # Delegate embedding tools to embeddings module
    from codebugs.embeddings import register_tools as embedding_tools
    embedding_tools(mcp, conn_factory)
```

This way embeddings.py doesn't need its own `register_tool_provider` at all. Reqs owns the tool registration surface, embeddings provides the implementations. Clean separation without mode complications.

### What stays in reqs.py
- All CRUD functions (add, update, query, stats, summary, verify, import)
- 7 CRUD MCP tool wrappers
- All CLI commands
- REQS_SCHEMA (including the `embedding BLOB` column)
- `ensure_schema()` (including the embedding column migration)
- `register_tool_provider("reqs", register_tools)` — now delegates embedding tools

### What does NOT change
- MCP tool names — unchanged
- MCP tool signatures — unchanged
- `--mode reqs` behavior — unchanged (still gets all reqs + embedding tools)
- Database schema — unchanged
- CLI commands — unchanged

## Testing strategy

### New tests: `tests/test_embeddings.py`

Move embedding tests from `test_reqs.py` to `test_embeddings.py`. Test the functions directly:
- `store_embedding` + `search_similar` round-trip
- `batch_store_embeddings`
- `embedding_stats` coverage counting
- `_cosine_similarity` edge cases (zero vectors, identical vectors)

### Existing tests

All 334 tests must pass. Tests that exercise embedding MCP tools through the full stack still work since reqs.py delegates to embeddings.py.
