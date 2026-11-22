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

## The problem

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
| **Memory hygiene** | `consolidate()` closes stale, low-importance, never-recalled facts (`expired`); `forget()` retracts. Both leave the audit trail intact. |
| **MCP server** | Ten semantic tools (`remember`, `recall`, `why`, `contradictions`, `resolve_contradiction`, …) for any MCP host: Claude Code, Cursor, Codex, Gemini CLI, OpenCode. |
| **Graph explorer** | `recall explore` writes a self-contained HTML page: the whole memory as a force-directed graph, contradictions in red, supersessions dashed, provenance on click. |

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
- **Vector index + `SEARCH`** with `WITH [f.closedAt]` pre-filtering, so
  closed facts are dropped *inside* the index before `LIMIT` counts to k.
- **Full-text index + `SEARCH`** (BM25) over fact text.
- **Hybrid search**: vector and full-text fused by reciprocal rank in a
  `CALL () { … UNION ALL … }` subquery — one statement, one round trip.
- **`vector(embedding, 256, FLOAT32)`** typed vectors that fail loudly on
  dimension mismatches.
- **Constraints** (`UNIQUE` on entity key, episode id, fact id) and **range
  indexes** on slot, status and recorded time.
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
pip install -e ".[mcp]"
```

Verify:

```bash
recall --help
python -m pytest                   # 34 tests, ~10 s (needs: pip install -e ".[dev]")
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
[`data/demo-memory/`](data/demo-memory/) — read it, don't rewrite it:

```bash
recall --db data/demo-memory recall "Where does Priya live?" -k 2
recall --db data/demo-memory contradictions --all
recall --db data/demo-memory timeline --slot relate/release-reviewer --on 2026-03-15
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
      "args": ["serve", "--db", "/path/to/memory.rl"]
    }
  }
}
```

The agent gets ten tools: `remember`, `remember_many`, `recall`, `why`,
`contradictions`, `resolve_contradiction`, `forget`, `timeline`, `stats`,
`graph`.

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

## Where the data lives

| Path | What it is |
|---|---|
| [`data/demo-memory/`](data/demo-memory/) | The committed demo store (directory, JSON codec) — the story the demo tells. `graph.json` is the whole memory in readable JSON; `.relate/schema/schema.cypher` is the schema as a file. |
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
pytest            # 34 tests: unit, engine end-to-end, and MCP over stdio
```

```
src/recall/
  embed.py       deterministic offline embeddings (pluggable: bring your own)
  tokens.py      claim/frame analysis — the contradiction heuristics
  schema.py      the graph schema on Relate
  store.py       thread-safe relate wrapper (one owner thread, one writer)
  memory.py      the engine: remember / recall / why / timeline / resolve
  mcp_server.py  the MCP server (10 tools)
  explorer.py    self-contained HTML graph explorer
  demo.py        the scripted walkthrough (and the video script)
  cli.py         the `recall` command
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
  the final call is the agent's, via `resolve_contradiction`.
- One process may write a store at a time (a Relate guarantee). Recall's MCP
  server owns its store; add `--read-only` for recall-only hosts.
- Slots group facts that answer the same question; give them explicitly for
  clean supersession. Without one, Recall derives a slot from the entity and
  claim words.

## Credits

- [Relate by Neo4j](https://relatedb.ai) — the embedded graph database that
  makes this possible. The data model follows Relate's
  [agent-memory guide](https://relatedb.ai/docs/guides/agents/model-agent-memory/).
- Built with AI assistance (agentic coding), as disclosed on Devpost.

## License

MIT
