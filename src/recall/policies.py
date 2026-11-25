"""Pluggable memory policies: how slots are derived, how conflicts are
scored, and how recall ranks its answers.

Recall's defaults are deterministic and offline (they run with no API keys),
but every decision above is a policy object you can swap:

- **SlotStrategy** — how a fact without an explicit ``slot`` finds the
  question it answers (which decides what *supersedes* what).
- **ContradictionChecker** — how two candidate facts are scored against
  each other. The default is lexical; plug in an NLI model by wrapping a
  callable.
- **RankingPolicy** — the weights hybrid recall uses to turn similarity,
  RRF, recency, reinforcement and importance into one score.

Example::

    from recall import Memory
    from recall.policies import entity_slot, FunctionChecker

    mem = Memory(":memory:",
                 slot_strategy=entity_slot,          # coarser supersession
                 checker=FunctionChecker(my_nli))    # model-assisted conflicts
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable

from .tokens import contradiction_score

# ----------------------------------------------------------------------
# slot strategies: (text, entity_keys) -> slot
# ----------------------------------------------------------------------

SlotStrategy = Callable[[str, list[str]], str]


def claim_slot(text: str, entity_keys: list[str]) -> str:
    """Default: ``entity/claim-words`` — the question the fact answers.

    Two facts land in the same slot when they share a leading entity and
    similar claim words, so a correction *supersedes* what it replaces.
    """
    import re

    from .tokens import claim_tokens

    def _slug(t: str) -> str:
        s = re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")
        return s or "x"

    primary = entity_keys[0] if entity_keys else "memory"
    claim = "-".join(sorted(claim_tokens(text))[:3])
    return f"{primary}/{claim or _slug(text[:40])}"


def entity_slot(text: str, entity_keys: list[str]) -> str:
    """Coarse: one slot per entity — the newest fact about an entity always
    supersedes the previous one. Use when memory is opinion-like (beliefs
    about one subject), not additive."""
    return entity_keys[0] if entity_keys else "memory"


def unique_slot(text: str, entity_keys: list[str]) -> str:
    """Never supersede: every fact gets its own slot, so history only ever
    grows and differences surface as contradictions, not closures."""
    import uuid

    primary = entity_keys[0] if entity_keys else "memory"
    return f"{primary}/~{uuid.uuid4().hex[:8]}"


SLOT_STRATEGIES: dict[str, SlotStrategy] = {
    "claim": claim_slot,
    "entity": entity_slot,
    "unique": unique_slot,
}


# ----------------------------------------------------------------------
# contradiction checkers: (a, b, embedding_similarity) -> score
# ----------------------------------------------------------------------


@runtime_checkable
class ContradictionChecker(Protocol):
    """Scores how likely two facts contradict, in ``[0, 1+]``."""

    name: str

    def score(self, text_a: str, text_b: str, embedding_similarity: float) -> float: ...


@dataclass
class LexicalContradictionChecker:
    """Recall's default: frame/claim/negation analysis, fully offline."""

    name: str = "lexical"

    def score(self, text_a: str, text_b: str, embedding_similarity: float) -> float:
        return contradiction_score(text_a, text_b, embedding_similarity)


@dataclass
class FunctionChecker:
    """Wrap any callable ``(a, b, sim) -> score`` as a checker.

    This is the seam for model-assisted contradiction detection — pass an
    NLI model's scoring function and it replaces the lexical heuristic
    everywhere ``Memory`` compares facts::

        def nli(a: str, b: str, sim: float) -> float:
            label, prob = model.predict(f"{a} [SEP] {b}")
            return prob if label == "contradiction" else 0.0

        mem = Memory(":memory:", checker=FunctionChecker(nli, name="nli"))
    """

    fn: Callable[[str, str, float], float]
    name: str = "function"

    def score(self, text_a: str, text_b: str, embedding_similarity: float) -> float:
        return self.fn(text_a, text_b, embedding_similarity)


# ----------------------------------------------------------------------
# ranking policy: the weights behind one recall score
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class RankingPolicy:
    """Weights for hybrid recall. Defaults reproduce Recall's original
    ranking exactly:

    ``score = relevance * recency * reinforcement * importance``

    - ``similarity_weight``: share of relevance from cosine vs. RRF rank.
    - ``recency_weight``: 0 disables age decay, 1 follows it fully.
    - ``reinforcement_cap``: ceiling on ``1 + ln(1 + accesses)``; 1.0
      disables access-count reinforcement.
    - ``importance_weight``: 0 ignores importance, 1 uses
      ``0.5 + importance`` as the original formula did.
    """

    similarity_weight: float = 0.7
    recency_weight: float = 0.5
    reinforcement_cap: float = 2.0
    importance_weight: float = 1.0

    def relevance(self, similarity: float, rrf_norm: float) -> float:
        w = self.similarity_weight
        return w * similarity + (1.0 - w) * rrf_norm

    def recency_factor(self, recency: float) -> float:
        w = self.recency_weight
        return (1.0 - w) + w * recency

    def reinforcement_factor(self, reinforcement: float) -> float:
        return min(reinforcement, self.reinforcement_cap)

    def importance_factor(self, importance: float) -> float:
        w = self.importance_weight
        return (1.0 - w) + w * (0.5 + importance)

    def score(
        self,
        *,
        similarity: float,
        rrf_norm: float,
        recency: float,
        reinforcement: float,
        importance: float,
    ) -> float:
        return (
            self.relevance(similarity, rrf_norm)
            * self.recency_factor(recency)
            * self.reinforcement_factor(reinforcement)
            * self.importance_factor(importance)
        )
