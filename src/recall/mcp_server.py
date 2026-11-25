"""The Recall MCP server: agent-facing memory tools over the MCP protocol.

Run it with ``recall serve`` (or ``python -m recall.mcp_server``). It exposes
semantic memory operations — remember, recall, why, resolve — not raw Cypher,
so any MCP host (Claude Code, Cursor, Codex, Gemini CLI, OpenCode, ...) gets a
memory that argues with itself instead of a database to drive.

Tools
-----
remember      Write one fact; reports supersessions, duplicates, conflicts.
remember_many Write a batch of facts from a session.
recall        Answer a question from memory with evidence and dispute flags.
why           Full provenance of a fact: evidence, entities, history.
contradictions List open (or all) contradictions.
resolve_contradiction  Keep one fact, retract the other, record the decision.
forget        Retract a fact (closed, never deleted).
timeline      What was true on a date, and what was believed on it.
stats         Memory statistics.
graph         The whole memory graph as JSON (for visualization).
"""

from __future__ import annotations

import json
import os
from typing import Any

from mcp.server.mcpserver import MCPServer

from .memory import Memory

DEFAULT_DB = os.environ.get("RECALL_DB", "./memory.rl")
DEFAULT_NAMESPACE = os.environ.get("RECALL_NAMESPACE", "default")


def build_server(
    db_path: str = DEFAULT_DB,
    *,
    read_only: bool = False,
    namespace: str = DEFAULT_NAMESPACE,
) -> MCPServer:
    mem = Memory(db_path, read_only=read_only, namespace=namespace)

    server = MCPServer(
        name="recall",
        version="0.1.0",
        instructions=(
            "Recall is the agent's long-term memory, backed by a Relate graph. "
            "Call remember() after learning anything worth keeping (facts carry "
            "provenance: the episode they came from). Call recall() before "
            "answering questions about past context. When recall() returns "
            "disputed=true, the memory holds conflicting facts — use "
            "contradictions() and resolve_contradiction() to settle them. "
            "why() explains where a memory came from. Credentials are "
            "redacted before they are written; pass sensitive=true for "
            "private facts and include_sensitive=false to hide them from "
            "recall()."
        ),
    )

    def _dump(result: Any) -> str:
        return json.dumps(result, indent=2, default=str)

    @server.tool()
    def remember(
        text: str,
        about: list[str] | None = None,
        slot: str | None = None,
        session: str = "default",
        kind: str = "message",
        evidence: str | None = None,
        at: str | None = None,
        valid_from: str | None = None,
        importance: float = 0.5,
        contradicts: list[str] | None = None,
        sensitive: bool = False,
    ) -> str:
        """Store one fact in long-term memory.

        Args:
            text: The fact, one clear statement ("Ada reviews each release.").
            about: Entity names the fact is about, e.g. ["Ada", "relate"].
            slot: The question this fact answers, e.g. "relate/release-reviewer".
                A fact in an existing slot supersedes the slot's current fact.
                Without a slot, one is derived from the entities and claim.
            session: Session or conversation id for the evidence episode.
            kind: "message", "tool_call", "decision" or "import".
            evidence: The raw text the fact was extracted from (defaults to text).
            at: ISO time the fact was learned (default: now).
            valid_from: ISO time the fact became true (default: at).
            importance: 0..1 how much this matters (default 0.5).
            contradicts: Fact ids this explicitly conflicts with.
            sensitive: Mark as private — hidden from recall() when the host
                passes include_sensitive=false. Credential-shaped strings are
                always redacted before writing, whatever this flag says.
        """
        report = mem.remember(
            text,
            about=about,
            slot=slot,
            session=session,
            kind=kind,
            evidence=evidence,
            at=at,
            valid_from=valid_from,
            importance=importance,
            contradicts=contradicts,
            sensitive=sensitive,
        )
        notes = []
        if report["reinforced"]:
            notes.append("This duplicates an existing memory, which was reinforced.")
        if report.get("redacted"):
            notes.append(
                f"Redacted before writing: {', '.join(report['redacted'])} — fact flagged sensitive."
            )
        for s in report["superseded"]:
            notes.append(f"Superseded: {s['text']} ({s['id']})")
        for d in report["duplicates"]:
            notes.append(f"Duplicate of {d['id']} (score {d['score']})")
        for c in report["conflicts"]:
            notes.append(
                f"CONTRADICTS {c['id']} \"{c['text']}\" (score {c['score']}) — "
                "call resolve_contradiction() to settle this."
            )
        return _dump(
            {
                "fact": report["fact"],
                "notes": notes,
                "conflicts": report["conflicts"],
                "superseded": [s["id"] for s in report["superseded"]],
                "sensitive": report["sensitive"],
            }
        )

    @server.tool()
    def remember_many(items: list[dict[str, Any]]) -> str:
        """Store several facts at once. Each item has the same keys as remember().

        Use this to write the extractable facts from one message, tool result
        or document in a single call.
        """
        reports = mem.remember_batch(items)
        return _dump(
            [
                {
                    "fact": r["fact"]["id"],
                    "text": r["fact"]["text"],
                    "conflicts": [c["id"] for c in r["conflicts"]],
                    "superseded": [s["id"] for s in r["superseded"]],
                    "reinforced": r["reinforced"],
                }
                for r in reports
            ]
        )

    @server.tool()
    def recall(
        query: str,
        k: int = 5,
        entity: str | None = None,
        slot: str | None = None,
        include_sensitive: bool = True,
    ) -> str:
        """Answer a question from long-term memory.

        Returns ranked memories, each with its score, the episode it was
        learned from (evidence), the entities it is about, what it replaced,
        and any open contradictions (disputed=true when unresolved).

        Args:
            query: The question in natural language.
            k: How many memories to return.
            entity: Only memories about this entity.
            slot: Only memories answering this slot.
            include_sensitive: Set false to exclude facts flagged sensitive
                (private notes and anything whose credentials were redacted).
        """
        return _dump(
            mem.recall(query, k=k, entity=entity, slot=slot, include_sensitive=include_sensitive)
        )

    @server.tool()
    def why(fact_id: str) -> str:
        """Explain a memory: where it came from, what it replaced, and its history.

        Returns the fact, its evidence episode, the entities it is about, the
        chain of facts it superseded (and anything that superseded it), its
        contradictions, and the full history of its slot on both time axes.
        """
        return _dump(mem.why(fact_id))

    @server.tool()
    def contradictions(include_resolved: bool = False) -> str:
        """List contradictions between memories: two facts that cannot both hold.

        Each pair carries a score and is waiting for a decision — resolve it
        with resolve_contradiction().
        """
        return _dump(mem.contradictions(include_resolved=include_resolved))

    @server.tool()
    def resolve_contradiction(keep_id: str, drop_id: str, note: str = "") -> str:
        """Settle a contradiction: keep one fact and retract the other.

        Args:
            keep_id: The fact that survives.
            drop_id: The fact that is retracted (closed, never deleted).
            note: Why this decision was made — recorded as a decision episode.
        """
        return _dump(mem.resolve_contradiction(keep_id, drop_id, note=note))

    @server.tool()
    def forget(fact_id: str, reason: str = "forgotten") -> str:
        """Retract a memory. It is closed and stays in the audit trail, never deleted."""
        return _dump(mem.forget(fact_id, reason=reason))

    @server.tool()
    def timeline(slot: str | None = None, entity: str | None = None, on: str | None = None) -> str:
        """Walk memory through time, on two axes: what was true, and what was believed.

        Args:
            slot: The slot to walk (e.g. "relate/release-reviewer").
            entity: Alternative: every slot of this entity.
            on: ISO date — return what was true then (valid time) and what the
                agent believed then (record time). They differ after late
                corrections.
        """
        return _dump(mem.timeline(slot=slot, entity=entity, on=on))

    @server.tool()
    def stats() -> str:
        """Memory statistics: entities, episodes, facts by status, contradictions."""
        return _dump(mem.stats())

    @server.tool()
    def graph() -> str:
        """The whole memory graph as JSON nodes and edges (for visualization)."""
        return _dump(mem.export_graph())

    return server


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run the Recall MCP server")
    parser.add_argument("--db", default=DEFAULT_DB, help="store path (default: ./memory.rl)")
    parser.add_argument("--read-only", action="store_true", help="serve without writes")
    parser.add_argument(
        "--namespace",
        default=DEFAULT_NAMESPACE,
        help=f"namespace this server scopes to (default: {DEFAULT_NAMESPACE})",
    )
    parser.add_argument(
        "--transport", default="stdio", choices=["stdio", "streamable-http"], help="MCP transport"
    )
    args = parser.parse_args()
    server = build_server(args.db, read_only=args.read_only, namespace=args.namespace)
    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
