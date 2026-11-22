"""End-to-end tests for the memory engine on a real relate store."""

from __future__ import annotations

import pytest

from recall import HashingEmbedder, Memory


def test_remember_creates_fact_with_provenance(mem: Memory):
    report = mem.remember(
        "Grace reviews each relate release.",
        slot="relate/release-reviewer",
        about=["Grace", "relate"],
        id="f1",
        episode_id="e1",
    )
    fact = report["fact"]
    assert fact["text"] == "Grace reviews each relate release."
    assert fact["status"] == "current"
    assert fact["validFrom"] is not None and fact["recordedAt"] is not None

    why = mem.why("f1")
    assert why["evidence"][0]["id"] == "e1"
    assert {a["key"] for a in why["about"]} == {"grace", "relate"}


def test_same_slot_supersedes_and_keeps_history(loaded: Memory):
    old = loaded.get_fact("f1")
    assert old["status"] == "superseded"
    assert old["closedAt"] is not None
    assert old["validTo"] is not None  # closed on the new fact's validFrom

    why = loaded.why("f3")
    assert [s["id"] for s in why["superseded"]] == ["f1"]
    # the superseded fact stays queryable
    assert loaded.get_fact("f1")["text"] == "Grace reviews each relate release."


def test_contradiction_detected_on_write(loaded: Memory):
    cons = loaded.contradictions()
    assert len(cons) == 1
    pair = {cons[0]["a_id"], cons[0]["b_id"]}
    assert pair == {"f4", "f5"}
    assert cons[0]["score"] >= 0.18
    assert cons[0]["resolved"] is False


def test_duplicate_is_reinforced_not_rewritten(loaded: Memory):
    before = loaded.stats()["facts"]["total"]
    report = loaded.remember(
        "Priya lives in Berlin.", slot="priya/relocation", about=["Priya"]
    )
    assert report["reinforced"] is True
    assert report["duplicates"][0]["id"] == "f5"
    assert loaded.stats()["facts"]["total"] == before


def test_recall_returns_ranked_facts_with_evidence(loaded: Memory):
    hits = loaded.recall("Who reviews relate releases?")
    assert hits
    top = hits[0]
    assert top["id"] == "f3"  # the current reviewer, not the superseded one
    assert top["evidence"][0]["text"] == "read_file CODEOWNERS: release/ @ada"
    texts = [h["text"] for h in hits]
    assert "Grace reviews each relate release." not in texts  # closed facts never surface


def test_recall_flags_disputed_facts(loaded: Memory):
    hits = loaded.recall("Where does Priya live?")
    disputed = [h for h in hits if h["disputed"]]
    assert len(disputed) == 2
    assert all(h["conflicts"] for h in disputed)


def test_recall_touch_reinforces(loaded: Memory):
    loaded.recall("Who reviews relate releases?", k=1)
    f = loaded.get_fact("f3")
    assert f["accessCount"] == 1
    assert f["lastAccessed"] is not None


def test_why_walks_provenance(loaded: Memory):
    why = loaded.why("f3")
    assert why["fact"]["id"] == "f3"
    assert why["evidence"][0]["kind"] == "tool_call"
    assert [s["id"] for s in why["superseded"]] == ["f1"]
    assert len(why["slot_history"]) == 2  # f1 and f3 share the slot


def test_timeline_bi_temporal(loaded: Memory):
    tl = loaded.timeline(slot="relate/release-reviewer", on="2026-03-15T00:00:00Z")
    entry = tl["slots"]["relate/release-reviewer"]
    assert entry["trueOn"] == ["Ada reviews each relate release."]  # true since 3/10
    assert entry["believedOn"] == ["Grace reviews each relate release."]  # learned 3/20


def test_timeline_by_entity(loaded: Memory):
    tl = loaded.timeline(entity="relate")
    assert set(tl["slots"]) == {"relate/release-reviewer", "relate/triage"}


def test_timeline_accepts_date_only(loaded: Memory):
    tl = loaded.timeline(slot="relate/release-reviewer", on="2026-03-15")
    assert tl["slots"]["relate/release-reviewer"]["trueOn"] == ["Ada reviews each relate release."]


def test_resolve_contradiction(loaded: Memory):
    result = loaded.resolve_contradiction("f5", "f4", note="She moved in April.")
    assert result["kept"]["id"] == "f5"
    assert result["retracted"]["id"] == "f4"
    assert result["retracted"]["status"] == "retracted"

    cons = loaded.contradictions()
    assert cons == []  # nothing open
    cons_all = loaded.contradictions(include_resolved=True)
    assert cons_all[0]["resolved"] is True

    # the retracted fact is closed, and the decision is recorded
    why = loaded.why("f4")
    assert why["fact"]["closedAt"] is not None
    hits = loaded.recall("Where does Priya live?")
    assert all(h["id"] != "f4" for h in hits)
    assert any(h["id"] == "f5" for h in hits)


def test_forget_retracts_but_never_deletes(loaded: Memory):
    loaded.forget("f2", reason="was a joke")
    f = loaded.get_fact("f2")
    assert f["status"] == "retracted"
    assert loaded.stats()["facts"]["current"] == 3  # f3, f4, f5 (disputed facts stay current)


def test_consolidate_expires_stale_unimportant_facts(mem: Memory):
    mem.remember(
        "The build was green on Tuesday.",
        slot="build/status",
        about=["build"],
        importance=0.1,
        at="2025-01-01T00:00:00Z",
        id="f-old",
    )
    mem.remember(
        "The release train runs at noon.",
        slot="release/train",
        about=["release"],
        importance=0.9,
        at="2025-01-01T00:00:00Z",
        id="f-important",
    )
    report = mem.consolidate(older_than_days=30, min_importance=0.3)
    assert [r["id"] for r in report["expired"]] == ["f-old"]
    assert mem.get_fact("f-old")["status"] == "expired"
    assert mem.get_fact("f-important")["status"] == "current"


def test_stats_and_export(loaded: Memory):
    stats = loaded.stats()
    assert stats["facts"]["current"] == 4
    assert stats["facts"]["superseded"] == 1
    assert stats["contradictions"]["open"] == 1
    assert stats["entities"] == 5

    graph = loaded.export_graph()
    types = {n["type"] for n in graph["nodes"]}
    assert {"Entity", "Episode", "Fact"} <= types
    edge_types = {e["type"] for e in graph["edges"]}
    assert {"FROM", "ABOUT", "SUPERSEDES", "MENTIONS", "CONTRADICTS"} <= edge_types


def test_remember_validates_input(mem: Memory):
    with pytest.raises(ValueError):
        mem.remember("")
    with pytest.raises(ValueError):
        mem.remember("x", importance=2.0)
    with pytest.raises(ValueError):
        mem.remember("x", embedding=[1.0, 2.0])  # wrong dimensions


def test_store_persists_and_reopens(tmp_path):
    path = str(tmp_path / "memory.rl")
    with Memory(path) as m:
        m.remember("Ada uses Rust.", slot="ada/language", about=["Ada"], id="f1")
    with Memory(path) as m2:
        hits = m2.recall("What language does Ada use?")
        assert hits and hits[0]["id"] == "f1"


def test_read_only_store_refuses_writes(tmp_path):
    path = str(tmp_path / "memory.rl")
    with Memory(path) as m:
        m.remember("Ada uses Rust.", slot="ada/language", about=["Ada"], id="f1")
    import relate

    with Memory(path, read_only=True) as ro:
        hits = ro.recall("Ada", touch=True)  # touch is skipped, not an error
        assert hits
        with pytest.raises(relate.Error):
            ro.remember("Nope.", slot="x", about=["x"])


def test_custom_embedder_rejected_on_dimension_mismatch(tmp_path):
    path = str(tmp_path / "mem.rl")
    with Memory(path, embedder=HashingEmbedder(64)) as m:
        m.remember("x", slot="s", about=["x"])
    with pytest.raises(ValueError, match="64"):
        Memory(path, embedder=HashingEmbedder(128))
