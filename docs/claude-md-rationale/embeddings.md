# Rationale — embeddings

Biography for the corresponding rules in `CLAUDE.md`: review rounds, reproduced incidents,
rejected forms and the measurements a decision was made on. **No rule lives here.** A line
in this file that reads as an instruction is a defect, and its place is the rules layer.

---

### CB-174 / CB-190 — vectors and the network gate {#cb-174-и-cb-190-вектора-и-сетевой-гейт}

**Justifies the rules** in `CLAUDE.md` → `## Embeddings`.

**A measurement sentence that was itself wider than its measurement (CB-190).** The opening claim
used to end "imports anything that could open a socket", which is a claim about every socket-opener
and not about a list. Measured against the gate's own function, `from logging.handlers import SocketHandler`, the same module's `HTTPHandler` and `from multiprocessing.connection import Client`
all returned an empty result — so the wider spelling was false in the paragraph that calls itself a
measurement.

**Why no module count is quoted.** The first draft quoted one and it was wrong: the measuring
predicate matched the prefix `mcp` without the dot and swept in `mcp_types`, a separate distribution
— the exact confusion the gate's own table is written to prevent.

**How the enumeration was measured to be insufficient.** `cohere`, `ollama` and `httplib2` were green
against it, and a planted module carrying all three left the file reporting 25 passed.

**The top-level names, and a slip worth keeping.** The three foreign top-level names (`mcp`, `mcp_types`, `pydantic`) are foreign on every admitted version, measured on 3.11 as well as on the pinned interpreter. `codebugs` is the fourth top-level name the tree imports and is deliberately
absent from that list — it is excluded by DERIVATION rather than by foreignness, and conflating the
two is what an earlier draft did: it said "four" and then listed three.

**The version-dependent five, measured.** `telnetlib`, `nntplib`, `asyncore`, `asynchat` and `smtpd` are all in `sys.stdlib_module_names` on 3.11 (3.12 keeps the first two), and none is on 3.13 or 3.14. **The provider rule's trigger used to be broken (CB-190).** It named `DECLARED_EXCEPTIONS` alone, so
a provider built on a client the enumeration never listed would have needed no row at all and the
rule would have sat there un-armed while the provider landed.

**Where the BYTES decision was found.** By reading the change end to end as one thing rather than
section by section — the CB-22/CB-52 "two copies of one precedence table" shape in a new place.

**The uniform-tracker defect was measured by adversarial review**, after the fix that introduced it.

**The migration cost was zero when the width rule landed.** Measured 2026-08-25 across every
reachable tracker — codebugs 6 requirements, both autosorter trackers 1401 each — the embedded count
is **0**, so CB-174 was a dormant breach rather than live damage.
