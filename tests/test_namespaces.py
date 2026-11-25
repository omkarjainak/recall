"""Namespaces: two agents share one store without seeing each other's memory.

Relate allows one open connection per store file, so the two namespaces are
driven by opening the store once per namespace, in turn — exactly how a
multi-tenant host would use it (one handle per tenant at a time).
"""

from __future__ import annotations

import pytest

from recall import Memory


@pytest.fixture
def db(tmp_path) -> str:
    return str(tmp_path / "shared.rl")


def test_recall_is_scoped_to_the_namespace(db: str):
    with Memory(db, namespace="alpha") as a:
        a.remember("Priya lives in Paris.", about=["Priya"], slot="priya/residence", id="a1")
    with Memory(db, namespace="beta") as b:
        b.remember("Priya lives in Berlin.", about=["Priya"], slot="priya/residence", id="b1")

    with Memory(db, namespace="alpha") as a:
        assert {h["id"] for h in a.recall("Where does Priya live?", k=5)} == {"a1"}
    with Memory(db, namespace="beta") as b:
        assert {h["id"] for h in b.recall("Where does Priya live?", k=5)} == {"b1"}


def test_conflicts_never_cross_namespaces(db: str):
    # the same contradiction written in both namespaces stays local
    for ns in ("alpha", "beta"):
        with Memory(db, namespace=ns) as m:
            m.remember("Priya lives in Paris.", about=["Priya"], slot="s1", id=f"{ns}1")
            m.remember("Priya lives in Berlin.", about=["Priya"], slot="s2", id=f"{ns}2")

    with Memory(db, namespace="alpha") as a:
        cons = a.contradictions()
        assert len(cons) == 1
        assert {cons[0]["a_id"], cons[0]["b_id"]} == {"alpha1", "alpha2"}
    with Memory(db, namespace="beta") as b:
        assert len(b.contradictions()) == 1


def test_get_fact_and_forget_are_scoped(db: str):
    with Memory(db, namespace="alpha") as a:
        a.remember("The office moves to Lisbon.", id="a1")
    with Memory(db, namespace="alpha") as a:
        assert a.get_fact("a1") is not None
    with Memory(db, namespace="beta") as b:
        assert b.get_fact("a1") is None  # exists, but not in this namespace
        with pytest.raises(KeyError):
            b.forget("a1")


def test_stats_and_timeline_are_scoped(db: str):
    with Memory(db, namespace="alpha") as a:
        a.remember("Flight departs at 7 a.m.", slot="flight/depart", id="a1")
    with Memory(db, namespace="beta") as b:
        b.remember("Flight departs at 9 a.m.", slot="flight/depart", id="b1")

    with Memory(db, namespace="alpha") as a:
        stats = a.stats()
        assert stats["facts"]["total"] == 1
        assert stats["namespace"] == "alpha"
        t = a.timeline(slot="flight/depart")
        assert [f["id"] for f in t["slots"]["flight/depart"]["facts"]] == ["a1"]


def test_supersession_stays_inside_a_namespace(db: str):
    with Memory(db, namespace="alpha") as a:
        a.remember("Grace reviews each release.", slot="release/reviewer", id="a1")
        a.remember("Ada reviews each release.", slot="release/reviewer", id="a2")
    with Memory(db, namespace="beta") as b:
        b.remember("Noor reviews each release.", slot="release/reviewer", id="b1")

    with Memory(db, namespace="alpha") as a:
        assert a.get_fact("a1")["status"] == "superseded"
    with Memory(db, namespace="beta") as b:
        assert b.get_fact("b1")["status"] == "current"  # untouched by alpha's write
        assert b.get_fact("a1") is None


def test_export_graph_is_scoped(db: str):
    with Memory(db, namespace="alpha") as a:
        a.remember("The museum opens at 9 a.m.", about=["museum"], id="a1")
    with Memory(db, namespace="beta") as b:
        b.remember("The zoo opens at 8 a.m.", about=["zoo"], id="b1")

    with Memory(db, namespace="alpha") as a:
        graph = a.export_graph()
        ids = {n["id"] for n in graph["nodes"]}
        assert {"a1", "museum"} <= ids
        assert "b1" not in ids and "zoo" not in ids


def test_namespaces_persist_independently(db: str):
    with Memory(db, namespace="alpha") as a:
        a.remember("Alpha only fact.", id="a1")
    with Memory(db, namespace="beta") as b:
        b.remember("Beta only fact.", id="b1")
    with Memory(db, namespace="alpha") as a:
        assert a.stats()["facts"]["total"] == 1
        assert a.get_fact("a1") is not None
        assert a.get_fact("b1") is None
