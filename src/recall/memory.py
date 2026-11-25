"""Recall's memory engine: remember, recall, contradict, resolve, forget.

Built on relate's official agent-memory model (entities, episodes, and
bi-temporal facts with provenance) and extended with:

- **Contradiction detection.** New facts are compared against the current
  facts they share entities with. Same frame, different claim → a
  ``:CONTRADICTS`` edge, surfaced in every recall until it is resolved.
- **Supersession with history.** A new fact in the same *slot* closes the old
  one (validTo/closedAt) and links it with ``:SUPERSEDES``. Nothing is lost.
- **Hybrid recall.** Vector + full-text search fused by reciprocal rank, then
  re-ranked by recency decay, reinforcement (access count) and importance.
- **Provenance chains.** Every answer carries the episode it came from and
  what it replaced — ``why()`` walks the whole chain.
- **Bi-temporal timelines.** What was true on a date, and what the agent
  *believed* on that date, are two different questions.
- **Retraction, not deletion.** ``forget()`` and ``consolidate()`` close
  facts; the audit trail survives.
"""

from __future__ import annotations

import math
import re
import uuid
from typing import Any

from . import schema
from .embed import Embedder, HashingEmbedder, cosine
from .policies import (
    SLOT_STRATEGIES,
    ContradictionChecker,
    LexicalContradictionChecker,
    RankingPolicy,
    SlotStrategy,
    claim_slot,
)
from .privacy import scrub
from .store import Store, parse_iso, to_iso, utcnow_iso
from .tokens import duplicate_score

Row = dict[str, Any]

CONFLICT_THRESHOLD = 0.18
DUPLICATE_THRESHOLD = 0.72
DEFAULT_HALFLIFE_DAYS = 30.0


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "x"


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class Memory:
    """A long-term memory for one agent, living in one relate store.

    Parameters
    ----------
    path:
        ``":memory:"``, a directory, or a single ``.rl`` file. A file-backed
        store survives restarts — that is the point.
    embedder:
        Anything with ``dimensions`` and ``embed(text) -> list[float]``.
        Defaults to a deterministic offline hashing embedder.
    read_only:
        Open without writes (recall/why/stats only; no access tracking).
    conflict_threshold / duplicate_threshold:
        Tuning knobs for the claim analysis in ``recall.tokens``.
    namespace:
        Isolates this memory's facts inside a shared store (multi-agent or
        multi-tenant). Facts and episodes carry ``ns``; every read and write
        is scoped to it. The default keeps the whole store.
    slot_strategy:
        How a fact without an explicit ``slot`` derives one — a name from
        ``recall.policies`` (``"claim"``, ``"entity"``, ``"unique"``) or any
        ``(text, entity_keys) -> slot`` callable. Controls supersession.
    checker:
        How candidate pairs are scored for contradiction. Defaults to the
        offline lexical checker; wrap an NLI model with
        ``recall.policies.FunctionChecker`` to go model-assisted.
    ranking:
        The weights behind hybrid recall's final score (see
        ``recall.policies.RankingPolicy``).
    scrub_secrets:
        Redact credential-shaped strings from fact text and evidence before
        they are written (default on). Facts that had something redacted are
        flagged ``sensitive``.
    """

    def __init__(
        self,
        path: str = ":memory:",
        *,
        embedder: Embedder | None = None,
        read_only: bool = False,
        conflict_threshold: float = CONFLICT_THRESHOLD,
        duplicate_threshold: float = DUPLICATE_THRESHOLD,
        halflife_days: float = DEFAULT_HALFLIFE_DAYS,
        namespace: str = "default",
        slot_strategy: str | SlotStrategy = "claim",
        checker: ContradictionChecker | None = None,
        ranking: RankingPolicy | None = None,
        scrub_secrets: bool = True,
    ) -> None:
        self.store = Store(path, read_only=read_only)
        self.embedder = embedder or HashingEmbedder()
        self.dims = self.embedder.dimensions
        self.conflict_threshold = conflict_threshold
        self.duplicate_threshold = duplicate_threshold
        self.halflife_days = halflife_days
        self.namespace = namespace or "default"
        if isinstance(slot_strategy, str):
            try:
                self.slot_strategy: SlotStrategy = SLOT_STRATEGIES[slot_strategy]
            except KeyError:
                raise ValueError(
                    f"unknown slot strategy {slot_strategy!r}; "
                    f"choose from {sorted(SLOT_STRATEGIES)}"
                ) from None
        else:
            self.slot_strategy = slot_strategy
        self.checker = checker or LexicalContradictionChecker()
        self.ranking = ranking or RankingPolicy()
        self.scrub_secrets = scrub_secrets
        self._ensure_schema()

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> "Memory":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # schema
    # ------------------------------------------------------------------
    def _ensure_schema(self) -> None:
        meta = self.store.run(
            "MATCH (m:Meta {key: $key}) RETURN m.dims AS dims, m.version AS version",
            {"key": schema.META_KEY},
        )
        if meta:
            stored = meta[0]
            if stored["dims"] != self.dims:
                raise ValueError(
                    f"store was created with {stored['dims']}-dimension embeddings, "
                    f"but this embedder produces {self.dims}"
                )
            if (stored["version"] or 1) < schema.SCHEMA_VERSION:
                self._migrate(stored["version"] or 1)
            return
        for stmt in schema.schema_statements(self.dims):
            self.store.write(stmt)
        self.store.write(
            "CREATE (:Meta {key: $key, version: $version, dims: $dims, "
            "embedder: $embedder, createdAt: $now})",
            {
                "key": schema.META_KEY,
                "version": schema.SCHEMA_VERSION,
                "dims": self.dims,
                "embedder": type(self.embedder).__name__,
                "now": utcnow_iso(),
            },
        )

    def _migrate(self, from_version: int) -> None:
        """Bring a pre-namespace store up to the current schema.

        v1 -> v2: every existing fact and episode joins the namespace that
        opens the store first (legacy data has no ``ns``), and the vector
        index is rebuilt to declare ``f.ns`` so recall can pre-filter by
        namespace before ``LIMIT`` counts to k. Idempotent; runs inside one
        transaction. Read-only opens cannot migrate and say so.
        """
        if self.store.read_only:
            raise ValueError(
                f"store is at schema v{from_version}, needs v{schema.SCHEMA_VERSION}; "
                "open it once without read_only to migrate"
            )
        ns = self.namespace

        def _tx(st: Store) -> None:
            st.run(
                "MATCH (f:Fact) WHERE f.ns IS NULL SET f.ns = $ns",
                {"ns": ns},
            )
            st.run(
                "MATCH (ep:Episode) WHERE ep.ns IS NULL SET ep.ns = $ns",
                {"ns": ns},
            )
            st.run("DROP INDEX fact_embedding IF EXISTS")
            st.run(schema.vector_index_statement(self.dims))
            st.run(
                "MATCH (m:Meta {key: $key}) SET m.version = $version",
                {"key": schema.META_KEY, "version": schema.SCHEMA_VERSION},
            )

        self.store.transact(_tx)

    # ------------------------------------------------------------------
    # entities
    # ------------------------------------------------------------------
    @staticmethod
    def _entity_key(name: str) -> str:
        return _slug(name)

    def _upsert_entities(self, about: list[str], names: dict[str, str] | None = None) -> list[str]:
        names = names or {}
        keys = []
        for item in about:
            key = self._entity_key(item)
            keys.append(key)
            self.store.write(
                "MERGE (e:Entity {key: $key}) "
                "ON CREATE SET e.name = $name, e.kind = $kind, e.createdAt = $now "
                "ON MATCH SET e.name = coalesce($name, e.name)",
                {
                    "key": key,
                    "name": names.get(key, item if item.lower() != key else key),
                    "kind": "thing",
                    "now": utcnow_iso(),
                },
            )
        return keys

    # ------------------------------------------------------------------
    # remember
    # ------------------------------------------------------------------
    def remember(
        self,
        text: str,
        *,
        slot: str | None = None,
        about: list[str] | None = None,
        session: str = "default",
        kind: str = schema.KIND_MESSAGE,
        evidence: str | None = None,
        at: str | None = None,
        valid_from: str | None = None,
        importance: float = 0.5,
        confidence: float = 1.0,
        tags: list[str] | None = None,
        embedding: list[float] | None = None,
        contradicts: list[str] | None = None,
        id: str | None = None,
        episode_id: str | None = None,
        sensitive: bool = False,
    ) -> Row:
        """Write one fact and report what it replaced, repeated, or conflicts with.

        ``about`` names the entities the fact is about (keys are slugs).
        ``slot`` names the question the fact answers; a fact in an existing
        slot *supersedes* the slot's current fact. Without a slot, Recall
        derives one via the memory's ``slot_strategy``.

        ``sensitive=True`` marks the fact private (or it is marked
        automatically when a credential was redacted); hosts can then hide
        such facts from ``recall`` with ``include_sensitive=False``.

        Returns a report dict: ``fact``, ``superseded``, ``duplicates``,
        ``conflicts`` (contradiction candidates, each with a score),
        ``sensitive`` and ``redacted`` (kinds of credentials scrubbed).
        """
        if not text or not text.strip():
            raise ValueError("text must be non-empty")
        if not 0.0 <= importance <= 1.0:
            raise ValueError("importance must be in [0, 1]")
        at = at or utcnow_iso()
        valid_from = valid_from or at
        about = about or []

        # -- privacy: credentials never enter the graph -------------------
        redacted: list[str] = []
        if self.scrub_secrets:
            text, kinds = scrub(text)
            redacted.extend(kinds)
            if evidence:
                evidence, kinds = scrub(evidence)
                redacted.extend(kinds)
        is_sensitive = bool(redacted) or sensitive

        embedding = embedding or self.embedder.embed(text)
        if len(embedding) != self.dims:
            raise ValueError(f"embedding must have {self.dims} dimensions, got {len(embedding)}")

        entity_keys = self._upsert_entities(about)
        if slot is None:
            slot = self.slot_strategy(text, entity_keys)

        fact_id = id or _new_id("f")
        ep_id = episode_id or _new_id("e")
        now = utcnow_iso()

        # -- analyze against the current memory -------------------------
        candidates = self._conflict_candidates(slot, entity_keys, exclude=())
        duplicates: list[Row] = []
        conflicts: list[Row] = []
        supersede_ids: list[str] = []
        seen: set[str] = set()

        for cand in candidates:
            cid = cand["id"]
            if cid in seen or cid == fact_id:
                continue
            seen.add(cid)
            sim = cosine(embedding, cand["embedding"])
            dup = duplicate_score(text, cand["text"], sim)
            if dup >= self.duplicate_threshold:
                duplicates.append({"id": cid, "text": cand["text"], "score": round(dup, 4)})
                continue
            if cand["slot"] == slot:
                supersede_ids.append(cid)
                continue
            conflict = self.checker.score(text, cand["text"], sim)
            if conflict >= self.conflict_threshold:
                conflicts.append(
                    {
                        "id": cid,
                        "text": cand["text"],
                        "slot": cand["slot"],
                        "score": round(conflict, 4),
                    }
                )

        # explicit contradiction links requested by the caller
        explicit = [c for c in (contradicts or []) if c not in seen and c != fact_id]
        for cid in explicit:
            conflicts.append({"id": cid, "text": "", "slot": "", "score": 1.0})

        # a duplicate: the same claim is already in memory. Reinforce it
        # (access tracking) and write nothing new — its own contradictions
        # were recorded when it was written. Explicit links still apply.
        if duplicates:
            kept = duplicates[0]["id"]
            if explicit:
                now2 = utcnow_iso()
                for cid in explicit:
                    self.store.write(
                        "MATCH (a:Fact {id: $a}), (b:Fact {id: $b}) "
                        "CREATE (a)-[:CONTRADICTS {score: 1.0, detectedAt: datetime($now), "
                        "detectedBy: 'recall', resolved: false, resolution: '', note: ''}]->(b)",
                        {"a": kept, "b": cid, "now": now2},
                    )
            self.store.write(
                "MATCH (f:Fact {id: $id}) "
                "SET f.accessCount = coalesce(f.accessCount, 0) + 1, "
                "f.lastAccessed = datetime($now)",
                {"id": kept, "now": now},
            )
            existing = self.get_fact(kept)
            return {
                "fact": existing,
                "duplicates": duplicates,
                "superseded": [],
                "conflicts": [],
                "reinforced": True,
                "sensitive": is_sensitive,
                "redacted": redacted,
            }

        # -- write: episode, fact, edges, supersessions, contradictions --
        def _tx(st: Store) -> None:
            st.run(
                "CREATE (ep:Episode {id: $id, session: $session, kind: $kind, "
                "at: datetime($at), text: $text, source: $source, ns: $ns})",
                {
                    "id": ep_id,
                    "session": session,
                    "kind": kind,
                    "at": at,
                    "text": evidence or text,
                    "source": "recall",
                    "ns": self.namespace,
                },
            )
            if entity_keys:
                st.run(
                    "MATCH (ep:Episode {id: $ep}) UNWIND $keys AS key "
                    "MATCH (n:Entity {key: key}) CREATE (ep)-[:MENTIONS]->(n)",
                    {"ep": ep_id, "keys": entity_keys},
                )
            st.run(
                "MATCH (ep:Episode {id: $ep}) "
                "CREATE (f:Fact {id: $id, text: $text, slot: $slot, status: $status, "
                "validFrom: datetime($validFrom), recordedAt: datetime($recordedAt), "
                "embedding: vector($embedding, $dims, FLOAT32), "
                "importance: $importance, confidence: $confidence, tags: $tags, "
                "accessCount: 0, lastAccessed: null, createdAt: $now, "
                "ns: $ns, sensitive: $sensitive, redacted: $redacted}) "
                "CREATE (f)-[:FROM]->(ep)",
                {
                    "ep": ep_id,
                    "id": fact_id,
                    "text": text,
                    "slot": slot,
                    "status": schema.STATUS_CURRENT,
                    "validFrom": valid_from,
                    "recordedAt": at,
                    "embedding": list(embedding),
                    "dims": self.dims,
                    "importance": importance,
                    "confidence": confidence,
                    "tags": tags or [],
                    "now": now,
                    "ns": self.namespace,
                    "sensitive": is_sensitive,
                    "redacted": redacted,
                },
            )
            if entity_keys:
                st.run(
                    "MATCH (f:Fact {id: $id}) UNWIND $keys AS key "
                    "MATCH (n:Entity {key: key}) CREATE (f)-[:ABOUT]->(n)",
                    {"id": fact_id, "keys": entity_keys},
                )
            for old_id in supersede_ids:
                st.run(
                    "MATCH (new:Fact {id: $new}), (old:Fact {id: $old}) "
                    "SET old.validTo = datetime($validFrom), old.closedAt = datetime($now), "
                    "old.status = $status "
                    "CREATE (new)-[:SUPERSEDES]->(old)",
                    {
                        "new": fact_id,
                        "old": old_id,
                        "validFrom": valid_from,
                        "now": now,
                        "status": schema.STATUS_SUPERSEDED,
                    },
                )
            for conf in conflicts:
                st.run(
                    "MATCH (a:Fact {id: $a}), (b:Fact {id: $b}) "
                    "CREATE (a)-[:CONTRADICTS {score: $score, detectedAt: datetime($now), "
                    "detectedBy: 'recall', resolved: false, resolution: '', note: ''}]->(b)",
                    {"a": fact_id, "b": conf["id"], "score": conf["score"], "now": now},
                )

        self.store.transact(_tx)

        return {
            "fact": self.get_fact(fact_id),
            "superseded": [self.get_fact(i) for i in supersede_ids],
            "duplicates": duplicates,
            "conflicts": conflicts,
            "reinforced": False,
            "sensitive": is_sensitive,
            "redacted": redacted,
        }

    def remember_batch(self, items: list[Row]) -> list[Row]:
        """``remember`` many facts (each item: keys of :meth:`remember`)."""
        return [self.remember(**item) for item in items]

    # ------------------------------------------------------------------
    # contradiction candidates
    # ------------------------------------------------------------------
    def _conflict_candidates(self, slot: str, entity_keys: list[str], exclude: tuple[str, ...]) -> list[Row]:
        rows = self.store.run(
            "MATCH (f:Fact) "
            "WHERE f.closedAt IS NULL AND f.ns = $ns AND NOT f.id IN $exclude "
            "AND (f.slot = $slot OR EXISTS { MATCH (f)-[:ABOUT]->(e:Entity) WHERE e.key IN $keys }) "
            "RETURN f.id AS id, f.text AS text, f.slot AS slot, f.embedding AS embedding "
            "LIMIT 500",
            {"slot": slot, "keys": entity_keys, "exclude": list(exclude), "ns": self.namespace},
        )
        out = []
        for r in rows:
            emb = r["embedding"]
            out.append(
                {
                    "id": r["id"],
                    "text": r["text"],
                    "slot": r["slot"],
                    "embedding": list(emb.values) if hasattr(emb, "values") else list(emb),
                }
            )
        return out

    # ------------------------------------------------------------------
    # recall
    # ------------------------------------------------------------------
    def recall(
        self,
        query: str,
        *,
        k: int = 5,
        entity: str | None = None,
        slot: str | None = None,
        touch: bool = True,
        include_sensitive: bool = True,
    ) -> list[Row]:
        """Answer a question from memory, ranked, with evidence and conflicts.

        Hybrid retrieval: a vector search and a full-text search fused by
        reciprocal rank inside one Cypher statement, then re-ranked here by
        the memory's ``ranking`` policy (similarity, recency decay,
        reinforcement and importance). Both legs are pre-filtered to this
        memory's namespace *inside* the indexes, before ``LIMIT`` counts to k.

        ``include_sensitive=False`` hides facts flagged sensitive (explicitly
        or because a credential was redacted when they were written).

        Each hit: ``id``, ``text``, ``slot``, ``score`` (and its parts),
        ``evidence`` (the episode it came from), ``about``, ``replaced``,
        ``conflicts`` and a ``disputed`` flag when a conflicting *current*
        fact exists.
        """
        qvec = self.embedder.embed(query)
        k2 = max(k * 3, 10)
        rows = self.store.run(
            """
            CALL () {
              MATCH (f:Fact)
              SEARCH f IN (VECTOR INDEX fact_embedding FOR $vector
                           WHERE f.closedAt IS NULL AND f.ns = $ns LIMIT $k2)
                SCORE AS score
              WITH f ORDER BY score DESC, f.id
              WITH collect(f) AS fs
              UNWIND range(0, size(fs) - 1) AS i
              RETURN fs[i] AS f, 'vector' AS list, i + 1 AS rank
              UNION ALL
              MATCH (f:Fact)
              SEARCH f IN (FULLTEXT INDEX fact_text FOR $q LIMIT $k2) SCORE AS score
              WITH f, score WHERE f.closedAt IS NULL AND f.ns = $ns
              WITH f ORDER BY score DESC, f.id
              WITH collect(f) AS fs
              UNWIND range(0, size(fs) - 1) AS i
              RETURN fs[i] AS f, 'fulltext' AS list, i + 1 AS rank
            }
            WITH f, sum(1.0 / (60 + rank)) AS rrf,
                 min(CASE list WHEN 'vector' THEN rank END) AS vector_rank,
                 min(CASE list WHEN 'fulltext' THEN rank END) AS fulltext_rank
            WHERE ($slot IS NULL OR f.slot = $slot)
              AND ($entity IS NULL OR EXISTS { MATCH (f)-[:ABOUT]->(:Entity {key: $entity}) })
              AND ($includeSensitive OR coalesce(f.sensitive, false) = false)
            ORDER BY rrf DESC, f.id
            LIMIT $k2
            RETURN f.id AS id, f.text AS text, f.slot AS slot, f.status AS status,
                   f.embedding AS embedding, f.importance AS importance,
                   f.accessCount AS accessCount, f.validFrom AS validFrom,
                   f.recordedAt AS recordedAt, f.confidence AS confidence,
                   round(rrf, 6) AS rrf, vector_rank AS vectorRank, fulltext_rank AS fulltextRank,
                   COLLECT { MATCH (f)-[:FROM]->(ep:Episode)
                             RETURN {id: ep.id, kind: ep.kind, text: ep.text, at: ep.at} } AS evidence,
                   COLLECT { MATCH (f)-[:ABOUT]->(e:Entity)
                             RETURN {key: e.key, name: e.name} } AS about,
                   COLLECT { MATCH (f)-[:SUPERSEDES]->(old:Fact)
                             RETURN {id: old.id, text: old.text} } AS replaced,
                   COLLECT { MATCH (f)-[c:CONTRADICTS]-(other:Fact)
                             WHERE other.closedAt IS NULL AND other.ns = $ns
                               AND ($includeSensitive OR coalesce(other.sensitive, false) = false)
                             RETURN {id: other.id, text: other.text,
                                    score: c.score, resolved: c.resolved} } AS conflicts
            """,
            {
                "vector": list(qvec),
                "q": query,
                "k2": k2,
                "slot": slot,
                "entity": entity,
                "ns": self.namespace,
                "includeSensitive": include_sensitive,
            },
        )

        max_rrf = max((r["rrf"] for r in rows), default=1.0) or 1.0
        hits: list[Row] = []
        for r in rows:
            emb = r["embedding"]
            vec = list(emb.values) if hasattr(emb, "values") else list(emb)
            sim = cosine(qvec, vec)
            rrf_norm = r["rrf"] / max_rrf

            recorded = to_iso(r["recordedAt"])
            age_days = self._age_days(recorded)
            recency = 0.5 ** (age_days / self.halflife_days)
            reinforcement = 1.0 + math.log(1.0 + (r["accessCount"] or 0))
            score = self.ranking.score(
                similarity=sim,
                rrf_norm=rrf_norm,
                recency=recency,
                reinforcement=reinforcement,
                importance=r["importance"] or 0.5,
            )

            conflicts = [
                {
                    "id": c["id"],
                    "text": c["text"],
                    "score": c["score"],
                    "resolved": bool(c["resolved"]),
                }
                for c in (r["conflicts"] or [])
            ]
            disputed = any(not c["resolved"] for c in conflicts)
            evidence = [
                {
                    "id": e["id"],
                    "kind": e["kind"],
                    "text": e["text"],
                    "at": to_iso(e["at"]),
                }
                for e in (r["evidence"] or [])
            ]
            hits.append(
                {
                    "id": r["id"],
                    "text": r["text"],
                    "slot": r["slot"],
                    "status": r["status"],
                    "score": round(score, 4),
                    "similarity": round(sim, 4),
                    "recency": round(recency, 4),
                    "reinforcement": round(reinforcement, 4),
                    "importance": r["importance"],
                    "confidence": r["confidence"],
                    "recordedAt": recorded,
                    "validFrom": to_iso(r["validFrom"]),
                    "vectorRank": r["vectorRank"],
                    "fulltextRank": r["fulltextRank"],
                    "evidence": evidence,
                    "about": [a["name"] for a in (r["about"] or [])],
                    "replaced": [x["text"] for x in (r["replaced"] or [])],
                    "conflicts": conflicts,
                    "disputed": disputed,
                }
            )

        hits.sort(key=lambda h: (-h["score"], h["id"]))
        hits = hits[:k]

        if touch and hits and not self.store.read_only:
            now = utcnow_iso()
            self.store.write(
                "UNWIND $ids AS id MATCH (f:Fact {id: id}) "
                "SET f.accessCount = coalesce(f.accessCount, 0) + 1, "
                "f.lastAccessed = datetime($now)",
                {"ids": [h["id"] for h in hits], "now": now},
            )
        return hits

    @staticmethod
    def _age_days(iso: str | None) -> float:
        dt = parse_iso(iso)
        if dt is None:
            return 0.0
        from datetime import datetime, timezone

        delta = datetime.now(timezone.utc) - dt
        return max(delta.total_seconds() / 86400.0, 0.0)

    # ------------------------------------------------------------------
    # why / provenance
    # ------------------------------------------------------------------
    def why(self, fact_id: str) -> Row:
        """The full provenance of a fact: evidence, entities, and history.

        Returns the fact, the episode it was learned from, the entities it is
        about, the chain of facts it superseded, anything that superseded it,
        and every contradiction attached to it.
        """
        fact = self.get_fact(fact_id)
        if fact is None:
            raise KeyError(f"no fact with id {fact_id!r}")

        evidence = self.store.run(
            "MATCH (f:Fact {id: $id})-[:FROM]->(ep:Episode) "
            "RETURN ep.id AS id, ep.session AS session, ep.kind AS kind, "
            "ep.at AS at, ep.text AS text",
            {"id": fact_id},
        )
        about = self.store.run(
            "MATCH (f:Fact {id: $id})-[:ABOUT]->(e:Entity) "
            "RETURN e.key AS key, e.name AS name, e.kind AS kind ORDER BY e.key",
            {"id": fact_id},
        )
        superseded_chain = self.store.run(
            "MATCH path = (f:Fact {id: $id})-[:SUPERSEDES*1..8]->(old:Fact) "
            "RETURN old.id AS id, old.text AS text, old.recordedAt AS recordedAt, "
            "old.closedAt AS closedAt, length(path) AS depth ORDER BY depth",
            {"id": fact_id},
        )
        superseded_by = self.store.run(
            "MATCH path = (newer:Fact)-[:SUPERSEDES*1..8]->(f:Fact {id: $id}) "
            "RETURN newer.id AS id, newer.text AS text, newer.recordedAt AS recordedAt, "
            "length(path) AS depth ORDER BY depth",
            {"id": fact_id},
        )
        contradictions = self.store.run(
            "MATCH (f:Fact {id: $id})-[c:CONTRADICTS]-(other:Fact) "
            "RETURN other.id AS id, other.text AS text, other.status AS status, "
            "c.score AS score, c.resolved AS resolved, c.resolution AS resolution, "
            "c.note AS note, c.detectedAt AS detectedAt",
            {"id": fact_id},
        )
        slot_history = self.store.run(
            "MATCH (f:Fact {slot: $slot}) WHERE f.ns = $ns "
            "RETURN f.id AS id, f.text AS text, f.status AS status, "
            "f.validFrom AS validFrom, f.validTo AS validTo, "
            "f.recordedAt AS recordedAt, f.closedAt AS closedAt "
            "ORDER BY f.recordedAt",
            {"slot": fact["slot"], "ns": self.namespace},
        )

        def _times(r: Row) -> Row:
            out = dict(r)
            for k in ("at", "recordedAt", "closedAt", "validFrom", "validTo", "detectedAt"):
                if k in out:
                    out[k] = to_iso(out[k])
            return out

        return {
            "fact": fact,
            "evidence": [_times(r) for r in evidence],
            "about": about,
            "superseded": [_times(r) for r in superseded_chain],
            "superseded_by": [_times(r) for r in superseded_by],
            "contradictions": [_times(r) for r in contradictions],
            "slot_history": [_times(r) for r in slot_history],
        }

    # ------------------------------------------------------------------
    # facts: read, retract
    # ------------------------------------------------------------------
    def get_fact(self, fact_id: str) -> Row | None:
        rows = self.store.run(
            "MATCH (f:Fact {id: $id}) WHERE f.ns = $ns "
            "RETURN f.id AS id, f.text AS text, f.slot AS slot, f.status AS status, "
            "f.validFrom AS validFrom, f.validTo AS validTo, "
            "f.recordedAt AS recordedAt, f.closedAt AS closedAt, "
            "f.importance AS importance, f.confidence AS confidence, "
            "f.accessCount AS accessCount, f.lastAccessed AS lastAccessed, f.tags AS tags, "
            "f.sensitive AS sensitive, f.redacted AS redacted, f.ns AS ns",
            {"id": fact_id, "ns": self.namespace},
        )
        if not rows:
            return None
        r = rows[0]
        for k in ("validFrom", "validTo", "recordedAt", "closedAt", "lastAccessed"):
            r[k] = to_iso(r[k])
        return r

    def forget(self, fact_id: str, *, reason: str = "forgotten") -> Row:
        """Retract a fact. It is closed, never deleted: history survives."""
        now = utcnow_iso()
        rows = self.store.run(
            "MATCH (f:Fact {id: $id}) WHERE f.ns = $ns "
            "SET f.closedAt = datetime($now), f.status = $status, f.retractedReason = $reason "
            "RETURN f.id AS id, f.text AS text, f.status AS status",
            {"id": fact_id, "now": now, "status": schema.STATUS_RETRACTED, "reason": reason, "ns": self.namespace},
        )
        if not rows:
            raise KeyError(f"no fact with id {fact_id!r}")
        # resolve any open contradictions this fact was part of
        self.store.write(
            "MATCH (f:Fact {id: $id})-[c:CONTRADICTS]->(other) WHERE NOT c.resolved "
            "SET c.resolved = true, c.resolution = 'retracted', c.note = $reason",
            {"id": fact_id, "reason": reason},
        )
        return rows[0]

    def consolidate(self, *, older_than_days: float = 90.0, min_importance: float = 0.3) -> Row:
        """Close stale, unimportant, untouched facts (status ``expired``).

        Memory hygiene without data loss: candidates are current facts older
        than the cutoff whose importance is below the floor and that were
        never recalled. Returns what was closed and what remains.
        """
        now = utcnow_iso()
        closed = self.store.run(
            "MATCH (f:Fact) WHERE f.closedAt IS NULL AND f.ns = $ns "
            "AND f.importance < $min_importance "
            "AND coalesce(f.accessCount, 0) = 0 "
            "AND f.recordedAt < datetime($cutoff) "
            "SET f.closedAt = datetime($now), f.status = $status "
            "RETURN f.id AS id, f.text AS text",
            {
                "min_importance": min_importance,
                "cutoff": self._iso_before(older_than_days),
                "now": now,
                "status": schema.STATUS_EXPIRED,
                "ns": self.namespace,
            },
        )
        stats = self.stats()
        return {"expired": closed, "current_facts": stats["facts"]["current"]}

    @staticmethod
    def _iso_before(days: float) -> str:
        from datetime import datetime, timedelta, timezone

        t = datetime.now(timezone.utc) - timedelta(days=days)
        return t.isoformat(timespec="seconds").replace("+00:00", "Z")

    # ------------------------------------------------------------------
    # contradictions
    # ------------------------------------------------------------------
    def contradictions(self, *, include_resolved: bool = False) -> list[Row]:
        """Every contradiction pair in memory, newest detection first."""
        rows = self.store.run(
            "MATCH (a:Fact)-[c:CONTRADICTS]->(b:Fact) "
            "WHERE a.ns = $ns AND b.ns = $ns AND ($all OR NOT c.resolved) "
            "RETURN a.id AS a_id, a.text AS a_text, a.status AS a_status, "
            "b.id AS b_id, b.text AS b_text, b.status AS b_status, "
            "c.score AS score, c.resolved AS resolved, c.resolution AS resolution, "
            "c.note AS note, c.detectedAt AS detectedAt "
            "ORDER BY c.detectedAt DESC",
            {"all": include_resolved, "ns": self.namespace},
        )
        out = []
        for r in rows:
            r["detectedAt"] = to_iso(r["detectedAt"])
            out.append(r)
        return out

    def resolve_contradiction(
        self,
        keep_id: str,
        drop_id: str,
        *,
        note: str = "",
        resolved_by: str = "agent",
    ) -> Row:
        """Settle a contradiction: keep one fact, retract the other.

        The ``:CONTRADICTS`` edge is marked resolved with the decision, the
        dropped fact is closed as ``retracted`` (not deleted), and a decision
        episode records why — so ``why()`` can still explain the outcome.
        """
        now = utcnow_iso()
        ep_id = _new_id("e")
        keep = self.get_fact(keep_id)
        drop = self.get_fact(drop_id)
        if keep is None or drop is None:
            raise KeyError("both facts must exist")

        def _tx(st: Store) -> None:
            st.run(
                "CREATE (ep:Episode {id: $id, session: 'decisions', kind: $kind, "
                "at: datetime($now), text: $text, source: 'recall', ns: $ns})",
                {
                    "id": ep_id,
                    "kind": schema.KIND_DECISION,
                    "now": now,
                    "text": note or f"Resolved contradiction: kept {keep_id}, retracted {drop_id}.",
                    "ns": self.namespace,
                },
            )
            st.run(
                "MATCH (a:Fact {id: $a})-[c:CONTRADICTS]->(b:Fact {id: $b}) "
                "SET c.resolved = true, c.resolution = $resolution, c.note = $note, "
                "c.resolvedAt = datetime($now), c.resolvedBy = $by",
                {
                    "a": keep_id,
                    "b": drop_id,
                    "resolution": f"keep:{keep_id}",
                    "note": note,
                    "now": now,
                    "by": resolved_by,
                },
            )
            st.run(
                "MATCH (a:Fact {id: $a})<-[c:CONTRADICTS]-(b:Fact {id: $b}) "
                "SET c.resolved = true, c.resolution = $resolution, c.note = $note, "
                "c.resolvedAt = datetime($now), c.resolvedBy = $by",
                {
                    "a": keep_id,
                    "b": drop_id,
                    "resolution": f"keep:{keep_id}",
                    "note": note,
                    "now": now,
                    "by": resolved_by,
                },
            )
            st.run(
                "MATCH (f:Fact {id: $id}) "
                "SET f.closedAt = datetime($now), f.status = $status, "
                "f.retractedReason = $reason, f.retractedIn = $ep",
                {
                    "id": drop_id,
                    "now": now,
                    "status": schema.STATUS_RETRACTED,
                    "reason": note or f"contradiction resolved in favor of {keep_id}",
                    "ep": ep_id,
                },
            )

        self.store.transact(_tx)
        return {
            "kept": self.get_fact(keep_id),
            "retracted": self.get_fact(drop_id),
            "decision_episode": ep_id,
        }

    # ------------------------------------------------------------------
    # timeline (bi-temporal)
    # ------------------------------------------------------------------
    def timeline(self, *, slot: str | None = None, entity: str | None = None, on: str | None = None) -> Row:
        """The history of a slot (or an entity's slots), on both time axes.

        With ``on`` (an ISO date), returns what was *true* then (valid time)
        and what the agent *believed* then (record time) — they differ after a
        late correction, and both answers are right.
        """
        if slot is None and entity is None:
            raise ValueError("give a slot or an entity")
        if slot is not None:
            slots = [slot]
        else:
            rows = self.store.run(
                "MATCH (f:Fact)-[:ABOUT]->(e:Entity {key: $key}) "
                "WHERE f.ns = $ns "
                "RETURN DISTINCT f.slot AS slot ORDER BY slot",
                {"key": self._entity_key(entity), "ns": self.namespace},
            )
            slots = [r["slot"] for r in rows]

        result: Row = {"slots": {}}
        for s in slots:
            facts = self.store.run(
                "MATCH (f:Fact {slot: $slot}) WHERE f.ns = $ns "
                "RETURN f.id AS id, f.text AS text, f.status AS status, "
                "f.validFrom AS validFrom, f.validTo AS validTo, "
                "f.recordedAt AS recordedAt, f.closedAt AS closedAt "
                "ORDER BY f.recordedAt",
                {"slot": s, "ns": self.namespace},
            )
            for f in facts:
                for k in ("validFrom", "validTo", "recordedAt", "closedAt"):
                    f[k] = to_iso(f[k])
            entry: Row = {"facts": facts}
            if on:
                t = parse_iso(on)
                entry["trueOn"] = [
                    f["text"]
                    for f in facts
                    if self._holds(f["validFrom"], f["validTo"], t)
                ]
                entry["believedOn"] = [
                    f["text"]
                    for f in facts
                    if self._holds(f["recordedAt"], f["closedAt"], t)
                ]
            result["slots"][s] = entry
        return result

    @staticmethod
    def _holds(start: str | None, end: str | None, t: Any) -> bool:
        s = parse_iso(start)
        e = parse_iso(end)
        if s is None or t is None:
            return False
        return s <= t and (e is None or t < e)

    # ------------------------------------------------------------------
    # stats / export
    # ------------------------------------------------------------------
    def stats(self) -> Row:
        ns = self.namespace
        counts = self.store.run(
            "MATCH (f:Fact) WHERE f.ns = $ns "
            "RETURN f.status AS status, count(*) AS n ORDER BY status",
            {"ns": ns},
        )
        by_status = {r["status"] or "current": r["n"] for r in counts}
        entities = self.store.run(
            "MATCH (f:Fact)-[:ABOUT]->(e:Entity) WHERE f.ns = $ns "
            "RETURN count(DISTINCT e) AS n",
            {"ns": ns},
        )[0]["n"]
        episodes = self.store.run(
            "MATCH (ep:Episode) WHERE ep.ns = $ns RETURN count(ep) AS n", {"ns": ns}
        )[0]["n"]
        disputed = self.store.run(
            "MATCH (a:Fact)-[c:CONTRADICTS]->(b:Fact) "
            "WHERE a.ns = $ns AND b.ns = $ns AND NOT c.resolved RETURN count(c) AS n",
            {"ns": ns},
        )[0]["n"]
        resolved = self.store.run(
            "MATCH (a:Fact)-[c:CONTRADICTS]->(b:Fact) "
            "WHERE a.ns = $ns AND b.ns = $ns AND c.resolved RETURN count(c) AS n",
            {"ns": ns},
        )[0]["n"]
        slots = self.store.run(
            "MATCH (f:Fact) WHERE f.ns = $ns RETURN count(DISTINCT f.slot) AS n", {"ns": ns}
        )[0]["n"]
        span = self.store.run(
            "MATCH (f:Fact) WHERE f.ns = $ns "
            "RETURN min(f.recordedAt) AS oldest, max(f.recordedAt) AS newest",
            {"ns": ns},
        )[0]
        top = self.store.run(
            "MATCH (f:Fact)-[:ABOUT]->(e:Entity) WHERE f.ns = $ns "
            "RETURN e.name AS entity, count(f) AS facts ORDER BY facts DESC LIMIT 10",
            {"ns": ns},
        )
        sensitive = self.store.run(
            "MATCH (f:Fact) WHERE f.ns = $ns AND coalesce(f.sensitive, false) = true "
            "RETURN count(f) AS n",
            {"ns": ns},
        )[0]["n"]
        return {
            "store": self.store.path,
            "namespace": ns,
            "entities": entities,
            "episodes": episodes,
            "slots": slots,
            "facts": {
                "current": by_status.get(schema.STATUS_CURRENT, 0),
                "superseded": by_status.get(schema.STATUS_SUPERSEDED, 0),
                "retracted": by_status.get(schema.STATUS_RETRACTED, 0),
                "expired": by_status.get(schema.STATUS_EXPIRED, 0),
                "total": sum(by_status.values()),
                "sensitive": sensitive,
            },
            "contradictions": {"open": disputed, "resolved": resolved},
            "oldest_memory": to_iso(span["oldest"]),
            "newest_memory": to_iso(span["newest"]),
            "top_entities": top,
        }

    def export_graph(self) -> Row:
        """Nodes and edges as JSON — feeds the browser explorer."""
        ns = self.namespace
        nodes: list[Row] = []
        seen_entities: set[str] = set()
        for r in self.store.run(
            "MATCH (f:Fact)-[:ABOUT]->(e:Entity) WHERE f.ns = $ns "
            "RETURN DISTINCT e.key AS id, e.name AS name, e.kind AS kind",
            {"ns": ns},
        ):
            seen_entities.add(r["id"])
            nodes.append({"id": r["id"], "type": "Entity", "label": r["name"], **r})
        for r in self.store.run(
            "MATCH (ep:Episode)-[:MENTIONS]->(e:Entity) WHERE ep.ns = $ns "
            "RETURN DISTINCT e.key AS id, e.name AS name, e.kind AS kind",
            {"ns": ns},
        ):
            if r["id"] not in seen_entities:
                seen_entities.add(r["id"])
                nodes.append({"id": r["id"], "type": "Entity", "label": r["name"], **r})
        for r in self.store.run(
            "MATCH (ep:Episode) WHERE ep.ns = $ns "
            "RETURN ep.id AS id, ep.kind AS kind, ep.text AS text, ep.at AS at",
            {"ns": ns},
        ):
            nodes.append(
                {
                    "id": r["id"],
                    "type": "Episode",
                    "label": f"{r['kind']}: {(r['text'] or '')[:60]}",
                    "at": to_iso(r["at"]),
                    "text": r["text"],
                    "kind": r["kind"],
                }
            )
        for r in self.store.run(
            "MATCH (f:Fact) WHERE f.ns = $ns "
            "RETURN f.id AS id, f.text AS text, f.slot AS slot, "
            "f.status AS status, f.recordedAt AS recordedAt",
            {"ns": ns},
        ):
            nodes.append(
                {
                    "id": r["id"],
                    "type": "Fact",
                    "label": (r["text"] or "")[:80],
                    "slot": r["slot"],
                    "status": r["status"],
                    "recordedAt": to_iso(r["recordedAt"]),
                }
            )

        edges: list[Row] = []
        for rel, q in (
            (
                "FROM",
                "MATCH (f:Fact)-[:FROM]->(ep:Episode) WHERE f.ns = $ns "
                "RETURN f.id AS source, ep.id AS target",
            ),
            (
                "ABOUT",
                "MATCH (f:Fact)-[:ABOUT]->(e:Entity) WHERE f.ns = $ns "
                "RETURN f.id AS source, e.key AS target",
            ),
            (
                "MENTIONS",
                "MATCH (ep:Episode)-[:MENTIONS]->(e:Entity) WHERE ep.ns = $ns "
                "RETURN ep.id AS source, e.key AS target",
            ),
            (
                "SUPERSEDES",
                "MATCH (a:Fact)-[:SUPERSEDES]->(b:Fact) WHERE a.ns = $ns "
                "RETURN a.id AS source, b.id AS target",
            ),
            (
                "CONTRADICTS",
                "MATCH (a:Fact)-[c:CONTRADICTS]->(b:Fact) WHERE a.ns = $ns "
                "RETURN a.id AS source, b.id AS target, c.resolved AS resolved, c.score AS score",
            ),
        ):
            for r in self.store.run(q, {"ns": ns}):
                edges.append({"type": rel, **r})
        return {"nodes": nodes, "edges": edges}

    # ------------------------------------------------------------------
    # raw escape hatch
    # ------------------------------------------------------------------
    def query(self, statement: str, params: Row | None = None) -> list[Row]:
        """Run raw Cypher against the memory store (escape hatch)."""
        rows = self.store.run(statement, params)
        for r in rows:
            for k, v in list(r.items()):
                r[k] = to_iso(v) if type(v).__name__ == "Temporal" else v
        return rows
