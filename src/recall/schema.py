"""The Recall graph schema, layered on relate's official agent-memory model.

Nodes
    (:Entity {key, name, kind, createdAt})
        What memory is about. ``key`` is unique, so writes always find the
        same node.

    (:Episode {id, session, kind, at, text, source})
        Where and when something was learned: a message, a tool call, or a
        decision. Every fact points at the episode that is its evidence.

    (:Fact {id, text, slot, status, validFrom, validTo, recordedAt, closedAt,
            embedding, importance, accessCount, lastAccessed, ...})
        One statement about one or more entities, with two time axes:
        valid time (when it held in the world) and record time (when the
        agent believed it). ``closedAt IS NULL`` means the fact is current.

    (:Meta {key, ...})
        Store metadata: embedder name and dimensions, schema version.

Relationships
    (:Episode)-[:MENTIONS]->(:Entity)
    (:Fact)-[:FROM]->(:Episode)          provenance / evidence
    (:Fact)-[:ABOUT]->(:Entity)          what the fact describes
    (:Fact)-[:SUPERSEDES]->(:Fact)       a correction replaced the older fact
    (:Fact)-[:CONTRADICTS]->(:Fact)      Recall's addition: two current facts
                                         conflict; kept until resolved

Nothing is ever deleted. A correction supersedes, a resolution retracts, and
consolidation closes — each sets ``closedAt`` and a ``status``, and the
history stays queryable.
"""

from __future__ import annotations

SCHEMA_VERSION = 2

# status values on :Fact
STATUS_CURRENT = "current"
STATUS_SUPERSEDED = "superseded"
STATUS_RETRACTED = "retracted"
STATUS_EXPIRED = "expired"  # closed by consolidation (stale, low importance)

# episode kinds
KIND_MESSAGE = "message"
KIND_TOOL_CALL = "tool_call"
KIND_DECISION = "decision"
KIND_IMPORT = "import"


def vector_index_statement(dimensions: int) -> str:
    """The vector index, declaring the pre-filter properties recall uses."""
    return (
        "CREATE VECTOR INDEX fact_embedding IF NOT EXISTS "
        "FOR (f:Fact) ON (f.embedding) WITH [f.closedAt, f.ns] "
        "OPTIONS {indexConfig: {`vector.dimensions`: "
        f"{dimensions}, `vector.similarity_function`: 'cosine'}}}}"
    )


def schema_statements(dimensions: int) -> list[str]:
    """Idempotent DDL for a fresh or existing store."""
    return [
        "CREATE CONSTRAINT entity_key IF NOT EXISTS "
        "FOR (e:Entity) REQUIRE e.key IS UNIQUE",
        "CREATE CONSTRAINT episode_id IF NOT EXISTS "
        "FOR (ep:Episode) REQUIRE ep.id IS UNIQUE",
        "CREATE CONSTRAINT fact_id IF NOT EXISTS "
        "FOR (f:Fact) REQUIRE f.id IS UNIQUE",
        "CREATE INDEX fact_slot IF NOT EXISTS FOR (f:Fact) ON (f.slot)",
        "CREATE INDEX fact_status IF NOT EXISTS FOR (f:Fact) ON (f.status)",
        "CREATE INDEX fact_recorded IF NOT EXISTS FOR (f:Fact) ON (f.recordedAt)",
        "CREATE INDEX fact_ns IF NOT EXISTS FOR (f:Fact) ON (f.ns)",
        # The vector index declares f.closedAt and f.ns so a recall search can
        # drop closed facts *and other namespaces* inside the index, before
        # LIMIT counts to k.
        vector_index_statement(dimensions),
        "CREATE FULLTEXT INDEX fact_text IF NOT EXISTS "
        "FOR (f:Fact) ON EACH [f.text]",
    ]


META_KEY = "recall.meta"
