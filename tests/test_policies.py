"""Pluggable policies: slot strategies, contradiction checkers, ranking."""

from __future__ import annotations

import dataclasses

import pytest

from recall import Memory
from recall.policies import (
    SLOT_STRATEGIES,
    FunctionChecker,
    LexicalContradictionChecker,
    RankingPolicy,
    claim_slot,
    entity_slot,
    unique_slot,
)
from recall.tokens import contradiction_score


# ------------------------------------------------------------------
# slot strategies
# ------------------------------------------------------------------
def test_claim_slot_is_deterministic_and_namespaced():
    a = claim_slot("Priya lives in Paris.", ["priya"])
    b = claim_slot("Priya lives in Paris.", ["priya"])
    assert a == b
    assert a.startswith("priya/")
    assert "paris" in a


def test_entity_slot_collapses_to_the_entity():
    assert entity_slot("anything at all", ["ada"]) == "ada"
    assert entity_slot("anything at all", []) == "memory"


def test_unique_slot_never_repeats():
    assert unique_slot("same text", ["ada"]) != unique_slot("same text", ["ada"])


def test_strategies_are_registered():
    assert set(SLOT_STRATEGIES) == {"claim", "entity", "unique"}


def test_memory_rejects_unknown_strategy(tmp_path):
    with pytest.raises(ValueError, match="unknown slot strategy"):
        Memory(str(tmp_path / "x.rl"), slot_strategy="nope")


def test_entity_strategy_supersedes_per_entity(mem: Memory):
    mem.slot_strategy = entity_slot
    mem.remember("Priya lives in Paris.", about=["Priya"], id="f1")
    mem.remember("Priya works at Acme.", about=["Priya"], id="f2")
    # one slot per entity: the newer fact supersedes, even across frames
    assert mem.get_fact("f1")["status"] == "superseded"
    assert mem.get_fact("f2")["status"] == "current"


def test_custom_callable_strategy(mem: Memory):
    mem.slot_strategy = lambda text, keys: "fixed/slot"
    mem.remember("First claim.", id="f1")
    mem.remember("Second claim.", id="f2")
    assert mem.get_fact("f1")["status"] == "superseded"


# ------------------------------------------------------------------
# contradiction checkers
# ------------------------------------------------------------------
def test_lexical_checker_matches_the_heuristic():
    c = LexicalContradictionChecker()
    assert c.name == "lexical"
    assert c.score("Priya lives in Paris.", "Priya lives in Berlin.", 0.6) == contradiction_score(
        "Priya lives in Paris.", "Priya lives in Berlin.", 0.6
    )


def test_function_checker_wraps_a_callable():
    seen: list[tuple[str, str]] = []

    def always(a: str, b: str, sim: float) -> float:
        seen.append((a, b))
        return 1.0

    c = FunctionChecker(always, name="stub")
    assert c.score("x", "y", 0.1) == 1.0
    assert seen == [("x", "y")]
    assert c.name == "stub"


def test_memory_uses_the_injected_checker(mem: Memory):
    mem.checker = FunctionChecker(lambda a, b, sim: 1.0, name="stub")
    mem.remember("Priya lives in Paris.", slot="priya/residence", about=["Priya"], id="f1")
    report = mem.remember("Priya lives in Berlin.", slot="priya/other", about=["Priya"], id="f2")
    # the stub scores every pair as a guaranteed conflict
    assert [c["id"] for c in report["conflicts"]] == ["f1"]


# ------------------------------------------------------------------
# ranking policy
# ------------------------------------------------------------------
def test_default_ranking_reproduces_the_original_formula():
    p = RankingPolicy()
    sim, rrf, rec, imp, access = 0.8, 0.6, 0.5, 0.7, 3
    import math

    reinforcement = 1.0 + math.log(1.0 + access)
    old = (0.7 * sim + 0.3 * rrf) * (0.5 + 0.5 * rec) * min(reinforcement, 2.0) * (0.5 + imp)
    assert p.score(
        similarity=sim, rrf_norm=rrf, recency=rec, reinforcement=reinforcement, importance=imp
    ) == pytest.approx(old)


def test_ranking_policy_is_frozen():
    p = RankingPolicy()
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.similarity_weight = 0.1


def test_ranking_weights_change_the_score():
    base = RankingPolicy()
    flat = RankingPolicy(recency_weight=0.0)
    assert base.recency_factor(0.0) < flat.recency_factor(0.0)
    assert RankingPolicy(reinforcement_cap=1.0).reinforcement_factor(5.0) == 1.0
