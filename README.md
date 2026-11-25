# Recall

**Contradiction-aware long-term memory for AI agents — on [Relate](https://relatedb.ai).**

Agents forget everything between sessions, and when they do remember, they
remember *confidently wrong*. Recall gives an agent a memory that survives
sessions, quotes the evidence behind every answer, notices when new
information contradicts old, and can explain both what was true on a past date
and what the agent *believed* on that date. All of it lives in one Relate
graph — one file, no server, no signup.

> Built for the [Relate by Neo4j Hackathon](https://relate.devpost.com/).
> An agent's memory is a graph problem: facts are connected to the entities
> they describe, the episodes that prove them, and the facts they replace or
> contradict. Relate is an embedded graph database, so the memory travels
> *inside* the agent's application.

---

## Run it

```bash
git clone https://github.com/omkarjainak/recall
cd recall
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[mcp,dev]"
```

Three commands show everything (each takes seconds):

```bash
recall demo                                                  # the whole story in 60 s
recall --db data/demo-memory --read-only recall "Where does Priya live?"   # the committed memory
python -m pytest && recall eval                              # 74 tests + eval suite: PASS
```

No server, no database install, no API keys — Relate runs inside the
process. Full walkthrough below under [Setup and run](#setup-and-run).

---

## Use case

Three things go wrong with naive agent memory:

1. **Stale facts.** "Grace reviews releases" becomes false when Ada takes
   over — but the old fact lingers and the agent keeps acting on it.
2. **Silent contradictions.** Two teammates say two different things; a
   flat key-value memory stores both and answers with whichever is louder.
3. **No evidence.** A memory says *"Priya lives in Berlin"* with no way to
   ask *why do you know that?*

Recall treats memory as a graph of **entities**, **episodes** (evidence), and
**bi-temporal facts** — and adds contradiction detection on top.

## What Recall does

| Capability | What it means |
|---|---|
| **Provenance** | Every fact points `[:FROM]` the episode that is its evidence (a message, a tool call, a decision). `recall why <id>` walks the whole chain. |
| **Supersession** | A newer fact in the same *slot* closes the old one (`validTo`, `closedAt`) and links `[:SUPERSEDES]→`. Nothing is ever deleted. |
| **Contradiction detection** | When a new fact shares entities with current facts but asserts a *different claim in the same frame* ("Priya lives in Paris" vs "…Berlin"), Recall writes a `[:CONTRADICTS]` edge and flags every affected answer `disputed: true` until a decision settles it. |
| **Resolution** | `resolve_contradiction(keep, drop, note)` keeps one fact, *retracts* (never deletes) the other, and records the decision as an episode — so `why` still explains the outcome. |
| **Hybrid recall** | Vector search + full-text search fused by reciprocal rank **inside one Cypher statement**, then re-ranked by similarity, recency decay, reinforcement (access count) and importance. |
| **Bi-temporal timelines** | Facts carry valid time *and* record time. `timeline --on 2026-03-15` answers "who reviewed releases then" and "who did the agent think did" — two different, both correct, answers. |
| **Namespaces** | One store, many tenants: `Memory(path, namespace="agent-a")` scopes every read and write. Conflicts, supersession and stats never cross a namespace boundary. |
| **Privacy** | Credential-shaped strings (AWS/GitHub/Slack/OpenAI keys, JWTs, `password=…`) are redacted *before* they are written; `sensitive` facts can be hidden from `recall(include_sensitive=False)`. |
| **Pluggable policies** | Slot derivation, the contradiction checker and ranking weights are policy objects (`recall.policies`): swap the lexical checker for an NLI model, or the ranking for your own, without touching the engine. |
| **Memory hygiene** | `consolidate()` closes stale, low-importance, never-recalled facts (`expired`); `forget()` retracts. Both leave the audit trail intact. |
| **MCP server** | Ten semantic tools (`remember`, `recall`, `why`, `contradictions`, `resolve_contradiction`, …) for any MCP host: Claude Code, Cursor, Codex, Gemini CLI, OpenCode. |
| **Graph explorer** | `recall explore` writes a self-contained HTML page: the whole memory as a force-directed graph, contradictions in red, supersessions dashed, provenance on click. |
| **Eval suite** | `recall eval` scores Recall against independent labeled data in [`evals/`](evals/) — contradiction F1 and retrieval recall@k/MRR with enforced thresholds, run in CI. |

## How it uses Relate

- **Mode: directory** (JSON codec) — the committed demo store lives at
  [`data/demo-memory/`](data/demo-memory/), so judges can read the graph as
  plain JSON (`graph.json`) and diffs stay reviewable. A live agent memory
  runs as a **single `.rl` file** (`./memory.rl`) so it travels as one
  portable artifact you can copy, back up with `relate clone`, or hand to
  another agent.
- **Why this mode:** an agent memory must survive restarts and travel with
  the agent. A single file (or a readable directory) does that with zero
  infrastructure. Relate runs *in-process* via the `relate-db` Python
  package — no server to deploy, which is exactly what a memory wants.
- **What the graph models:** `(:Entity)` what memory is about,
  `(:Episode)` where it was learned, `(:Fact)` one bi-temporal statement with
  its embedding — wired together with `[:ABOUT]`, `[:FROM]`, `[:MENTIONS]`,
  `[:SUPERSEDES]` and Recall's own `[:CONTRADICTS]`. The full model:
  [`src/recall/schema.py`](src/recall/schema.py).

### Relate features exercised

- **Cypher 25** for every read and write, with parameters (`$name`) everywhere
  values appear — nothing interpolated into statements.
- **Vector index + `SEARCH`** with `WITH [f.closedAt, f.ns]` pre-filtering, so
  closed facts *and other namespaces* are dropped inside the index before
  `LIMIT` counts to k (schema v2; v1 stores migrate in place on first open).
- **Full-text index + `SEARCH`** (BM25) over fact text, filtered post-search
  (Relate's fulltext `SEARCH` takes no inline `WHERE`).
- **Hybrid search**: vector and full-text fused by reciprocal rank in a
  `CALL () { … UNION ALL … }` subquery — one statement, one round trip.
- **`vector(embedding, 256, FLOAT32)`** typed vectors that fail loudly on
  dimension mismatches.
- **Constraints** (`UNIQUE` on entity key, episode id, fact id) and **range
  indexes** on slot, status, recorded time and namespace.
- **Transactions** (`autocommit=False`) so a multi-statement write (episode +
  fact + supersessions + contradiction edges) is atomic.
- **Read-only opens** for recall-only consumers; **persistent stores** as a
  single file or a JSON-codec directory.
- **One writing open at a time** respected via a dedicated store thread (the
  `relate-db` client is thread-affine; MCP hosts call from worker threads).

## Setup and run

**Prerequisites:** Python 3.10+ (3.12 recommended). Nothing else — Relate
runs in-process. Optionally install the [`relate` CLI](https://relatedb.ai/docs/get-started/install/)
to inspect the store yourself.

### 1. Install (works on macOS, Linux, Windows/WSL)

```bash
git clone https://github.com/omkarjainak/recall
cd recall
python3 -m venv .venv
source .venv/bin/activate          # Windows:  .venv\Scripts\activate
pip install -e ".[mcp]"            # to run the tests too: pip install -e ".[mcp,dev]"
```

Verify:

```bash
recall --help
python -m pytest       # 74 tests, ~15 s
recall eval            # the independent eval suite: PASS
```

### 2. See the whole story in 60 seconds

```bash
recall demo
```

This walks the canonical scenario: learn facts → a correction supersedes the
old fact → two conflicting reports trigger a contradiction → recall answers
with evidence and `[DISPUTED]` flags → a decision settles it → `why` and the
true-vs-believed timeline. It writes to `./memory.rl` (created on first run).

### 3. Ask the committed demo memory

The repo ships a real store with that exact story at
[`data/demo-memory/`](data/demo-memory/) — read it, don't rewrite it
(`--read-only` keeps it byte-for-byte pristine):

```bash
recall --db data/demo-memory --read-only recall "Where does Priya live?" -k 2
recall --db data/demo-memory --read-only contradictions --all
recall --db data/demo-memory --read-only timeline --slot relate/release-reviewer --on 2026-03-15
recall --db data/demo-memory explore --open     # the memory graph in your browser
```

Or inspect the raw graph with the Relate CLI (read-only):

```bash
relate --database ./data/demo-memory --read-only \
  "MATCH (f:Fact)-[:FROM]->(ep:Episode) RETURN f.text AS fact, f.status AS status, ep.kind AS learnedFrom"
```

### 4. Use it from the command line (your own memory)

```bash
recall --db ./memory.rl remember "Ada reviews each relate release." \
    --about Ada --about relate --slot relate/release-reviewer \
    --kind tool_call --evidence "read_file CODEOWNERS: release/ @ada"

recall --db ./memory.rl recall "Who reviews relate releases?"
recall --db ./memory.rl why <fact-id>
recall --db ./memory.rl contradictions
recall --db ./memory.rl resolve <keep-id> <drop-id> --note "HR confirmed the move"
recall --db ./memory.rl timeline --slot relate/release-reviewer --on 2026-03-15
recall --db ./memory.rl stats
recall --db ./memory.rl explore --open     # draw the memory graph
```

### 5. Give an agent the memory (MCP server)

For Claude Code / OpenCode / Cursor (`mcp.json`):

```json
{
  "mcpServers": {
    "recall": {
      "command": "recall",
      "args": ["serve", "--db", "/path/to/memory.rl", "--namespace", "agent-a"]
    }
  }
}
```

The agent gets ten tools: `remember`, `remember_many`, `recall`, `why`,
`contradictions`, `resolve_contradiction`, `forget`, `timeline`, `stats`,
`graph`. `--namespace` scopes the server to one tenant; `--read-only`
serves a store without writes.

### 6. Use it as a library

```python
from recall import Memory

mem = Memory("./memory.rl")
report = mem.remember(
    "Priya lives in Berlin.",
    slot="priya/relocation",
    about=["Priya"],
    evidence="Dan said Priya relocated last month.",
)
print(report["conflicts"])   # any contradicting facts, with scores

for hit in mem.recall("Where does Priya live?"):
    print(hit["score"], hit["text"], hit["disputed"], hit["evidence"])
```

Production knobs — all optional, all defaults reproduce the behavior above:

```python
from recall import Memory
from recall.policies import entity_slot, FunctionChecker, RankingPolicy

mem = Memory(
    "./memory.rl",
    namespace="agent-a",              # multi-tenant scoping (schema v2)
    slot_strategy=entity_slot,        # or "claim" (default) / "unique" / your fn
    checker=FunctionChecker(my_nli),  # swap the lexical conflict scorer
    ranking=RankingPolicy(recency_weight=0.9),
    scrub_secrets=True,               # redact credentials before writing (on)
)
mem.remember("Our anniversary is June 12.", sensitive=True)
mem.recall("anniversary", include_sensitive=False)   # hides it
```

### 7. Run the eval suite

The repo ships **independent labeled data** — a travel-planning memory, not
the demo story — under [`evals/data/`](evals/data/):

```bash
recall eval
```

```
travel-contradictions  (24 cases)
  accuracy               1.000
  macro_f1               1.000
travel-retrieval  (8 queries)
  recall@3               1.000
  mrr                    1.000
thresholds: PASS
```

Two tasks: **contradiction** (predict `contradiction`/`duplicate`/
`unrelated` for a pair, using the exact scoring path `remember` uses) and
**retrieval** (seed a fresh store, ask questions, measure recall@k / MRR).
Every dataset declares thresholds, `tests/test_eval.py` enforces them, and
the whole thing is deterministic — same numbers every run. It caught real
regressions while being built (a negation flip once scored as a
*duplicate*, and single-digit numbers were dropped from claim tokens).

## Where the data lives

| Path | What it is |
|---|---|
| [`data/demo-memory/`](data/demo-memory/) | The committed demo store (directory, JSON codec, schema v2) — the story the demo tells. `graph.json` is the whole memory in readable JSON; `.relate/schema/schema.cypher` is the schema as a file. |
| [`evals/data/`](evals/data/) | The independent eval datasets (JSON, hand-labeled) that `recall eval` scores against. |
| `./memory.rl` | The default live store, created on first write (single file). Not committed. |

Inspect the demo store with the Relate CLI (read-only):

```bash
relate --database ./data/demo-memory --read-only \
  "MATCH (f:Fact)-[:FROM]->(ep:Episode) RETURN f.text AS fact, f.status AS status, ep.kind AS learnedFrom"
```

```
rows[5]{fact,status,learnedFrom}:
  Grace reviews each relate release.,superseded,message
  Noor triages new relate issues on Mondays.,current,message
  Ada reviews each relate release.,current,tool_call
  Priya lives in Paris.,retracted,message
  Priya lives in Berlin.,current,message
```

Every state in that output is a story: **superseded** = a correction replaced
it (and it is still here), **retracted** = a contradiction was settled against
it (and it is still here), **current** = the agent can quote it — with its
evidence.

## The demo story

`recall demo` walks the canonical scenario (also what `data/demo-memory`
holds and what the demo video shows):

1. A session teaches the agent two facts.
2. A tool call **corrects** one — the old fact is superseded, not deleted.
3. Two teammates **disagree** — Recall flags a contradiction (score 0.50)
   and marks every affected answer `disputed`.
4. Recall answers questions **with evidence** and dispute flags.
5. A **decision** settles the conflict: one fact kept, one retracted, the
   reasoning recorded as an episode.
6. `why` explains the correction chain.
7. `timeline --on 2026-03-15` shows what was *true* vs what was *believed*
   that day — the audit trail for "why did the agent ask Grace?".

## Development

```bash
pip install -e ".[dev]"
pytest            # 74 tests: unit, engine, namespaces, privacy, migration, evals, MCP over stdio
recall eval       # the eval suite on independent data (evals/data)
```

```
src/recall/
  embed.py        deterministic offline embeddings (pluggable: bring your own)
  tokens.py       claim/frame analysis — the contradiction heuristics
  policies.py     pluggable slot strategies, contradiction checkers, ranking
  privacy.py      credential redaction before anything is written
  schema.py       the graph schema on Relate (v2: namespaces, migration)
  store.py        thread-safe relate wrapper (one owner thread, one writer)
  memory.py       the engine: remember / recall / why / timeline / resolve
  evaluation.py   the eval runner (datasets live in evals/data/)
  mcp_server.py   the MCP server (10 tools)
  explorer.py     self-contained HTML graph explorer
  demo.py         the scripted walkthrough (and the video script)
  cli.py          the `recall` command
evals/data/       independent labeled datasets (contradiction + retrieval)
tests/            74 tests across 9 files
```

**Plugging in a real embedding model.** The default embedder is deterministic
feature hashing — offline, reproducible, and enough for the claim analysis
and demo. For production recall quality, pass any embedder with
`dimensions` and `embed(text) -> list[float]`:

```python
mem = Memory("./memory.rl", embedder=MyModelEmbedder())
```

The dimension count is fixed when a store is created and enforced afterwards.

## Notes and limits

- Contradiction detection is deterministic and *lexical*: same frame,
  different claim (or a negation flip). It is a strong signal for flagging —
  the final call is the agent's, via `resolve_contradiction`. To go
  model-assisted, wrap an NLI scorer in `recall.policies.FunctionChecker`
  and pass it to `Memory(checker=…)`; the seam is already there.
- One process may open a store at a time (a Relate guarantee). Recall's MCP
  server owns its store; add `--read-only` for recall-only hosts.
  Namespaces still work: open the store once per namespace in turn (that is
  how `tests/test_namespaces.py` drives them).
- Slots group facts that answer the same question; give them explicitly for
  clean supersession. Without one, Recall derives a slot via the memory's
  `slot_strategy` (`claim` by default).
- Entity nodes are shared across namespaces (keys are global); isolation
  applies to facts, episodes, contradictions and every read path.
- Secret redaction is pattern-based (shaped credentials), not a guarantee
  against every private detail — mark what is private with
  `sensitive=True`.

## Credits

- [Relate by Neo4j](https://relatedb.ai) — the embedded graph database that
  makes this possible. The data model follows Relate's
  [agent-memory guide](https://relatedb.ai/docs/guides/agents/model-agent-memory/).
- Built with AI assistance (agentic coding), as disclosed on Devpost.

## License

[PolyForm Noncommercial 1.0.0](LICENSE) — Copyright (c) 2026 Omkar Jainak.

Free for personal, research, educational and other **non-commercial** use:
use it, change it, ship it — just not as part of something you sell or
monetize. For commercial licensing, contact the author via
[GitHub](https://github.com/omkarjainak).
