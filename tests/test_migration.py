"""Schema migration: a v1 store (pre-namespace) upgrades to v2 in place."""

from __future__ import annotations

import pytest

from recall import Memory
from recall.schema import META_KEY, SCHEMA_VERSION


def _downgrade_to_v1(db: str) -> None:
    """Turn a fresh v2 store into what Recall v0.1 shipped: no ``ns``
    anywhere, and a vector index that cannot pre-filter by namespace."""
    m = Memory(db)
    try:
        m.store.write("MATCH (f:Fact) REMOVE f.ns")
        m.store.write("MATCH (ep:Episode) REMOVE ep.ns")
        m.store.write("DROP INDEX fact_embedding IF EXISTS")
        m.store.write(
            "CREATE VECTOR INDEX fact_embedding FOR (f:Fact) ON (f.embedding) "
            "WITH [f.closedAt] OPTIONS {indexConfig: {`vector.dimensions`: 256, "
            "`vector.similarity_function`: 'cosine'}}"
        )
        m.store.write(
            "MATCH (m:Meta {key: $key}) SET m.version = 1",
            {"key": META_KEY},
        )
    finally:
        m.close()


def test_v1_store_migrates_on_open(tmp_path):
    db = str(tmp_path / "old.rl")
    m = Memory(db)
    m.remember("Priya lives in Paris.", about=["Priya"], slot="priya/residence", id="f1")
    m.close()
    _downgrade_to_v1(db)

    m2 = Memory(db)  # writable open triggers the migration
    try:
        stats = m2.stats()
        assert stats["facts"]["total"] == 1

        fact = m2.get_fact("f1")
        assert fact["ns"] == "default"  # legacy data joins the opening namespace

        # the rebuilt index pre-filters by ns, so recall sees the fact
        hits = m2.recall("Where does Priya live?", k=3)
        assert [h["id"] for h in hits] == ["f1"]

        version = m2.store.run(
            "MATCH (m:Meta {key: $key}) RETURN m.version AS v", {"key": META_KEY}
        )[0]["v"]
        assert version == SCHEMA_VERSION
    finally:
        m2.close()


def test_migration_is_idempotent(tmp_path):
    db = str(tmp_path / "old.rl")
    m = Memory(db)
    m.remember("Fact one.", id="f1")
    m.close()
    _downgrade_to_v1(db)

    for _ in range(2):
        m2 = Memory(db)
        try:
            assert m2.stats()["facts"]["total"] == 1
        finally:
            m2.close()


def test_migration_adopts_the_opening_namespace(tmp_path):
    db = str(tmp_path / "old.rl")
    m = Memory(db)
    m.remember("Fact one.", id="f1")
    m.close()
    _downgrade_to_v1(db)

    # the first writable open adopts its namespace...
    with Memory(db, namespace="tenant-a") as scoped:
        assert scoped.get_fact("f1")["ns"] == "tenant-a"

    # ...and the fact is invisible to a different namespace afterwards
    with Memory(db, namespace="tenant-b") as other:
        assert other.get_fact("f1") is None
        assert other.stats()["facts"]["total"] == 0


def test_read_only_open_refuses_to_migrate(tmp_path):
    db = str(tmp_path / "old.rl")
    m = Memory(db)
    m.remember("Fact one.", id="f1")
    m.close()
    _downgrade_to_v1(db)

    with pytest.raises(ValueError, match="read_only"):
        Memory(db, read_only=True)
