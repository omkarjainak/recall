"""Embeddings for Recall.

Recall needs a vector per fact and per recall query. The default embedder is
deterministic and fully offline — feature hashing over word unigrams, bigrams
and character trigrams — so the whole project runs with no API keys and
reproducible results.

Any object with ``dimensions: int`` and ``embed(text) -> list[float]`` works as
a drop-in replacement (OpenAI, sentence-transformers, ...): pass it to
``Memory(..., embedder=my_embedder)``. Whatever embeds the facts must also
embed the queries, and the dimension count is fixed when the store is created.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol, runtime_checkable

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# A small stop-word list. Stop words are not dropped from embeddings (they help
# distinguish "works at X" from "lives in X"), but claim analysis in
# ``recall.tokens`` uses this list too.
STOP_WORDS = frozenset(
    """
    a an the and or but if then else when while for to of in on at by with
    from as is are was were be been being am do does did done have has had
    it its this that these those there here who whom whose which what where
    why how not no nor so too very can will just about into over under out
    up down again once own same than because during before after above below
    between both each few more most other some such only s t don now
    """.split()
)


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens (letters and digits)."""
    return _TOKEN_RE.findall(text.lower())


@runtime_checkable
class Embedder(Protocol):
    """Anything that maps text to a fixed-length unit vector."""

    dimensions: int

    def embed(self, text: str) -> list[float]: ...


class HashingEmbedder:
    """Deterministic feature-hashing embedder.

    Word unigrams and bigrams plus character trigrams are hashed with a keyed
    BLAKE2b digest into ``dimensions`` buckets with a random sign, weighted by
    augmented term frequency, then L2-normalized. Identical text always gives
    the identical vector, on any machine, with no model download.

    It is a lexical model: it captures shared vocabulary, which is exactly what
    Recall's contradiction analysis needs ("same subject, different claim"),
    but it is not a semantic model. Swap in a real embedding model for
    production recall quality.
    """

    def __init__(self, dimensions: int = 256, *, seed: bytes = b"recall-v1") -> None:
        if dimensions < 8:
            raise ValueError("dimensions must be >= 8")
        self.dimensions = dimensions
        self._seed = seed

    def _features(self, text: str) -> list[str]:
        words = tokenize(text)
        feats = list(words)
        feats.extend(f"{a}_{b}" for a, b in zip(words, words[1:]))
        for word in words:
            padded = f"^{word}$"
            feats.extend(padded[i : i + 3] for i in range(len(padded) - 2))
        return feats

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        feats = self._features(text)
        if not feats:
            return vec
        counts: dict[str, int] = {}
        for f in feats:
            counts[f] = counts.get(f, 0) + 1
        for feat, count in counts.items():
            digest = hashlib.blake2b(feat.encode("utf-8"), digest_size=8, key=self._seed).digest()
            h = int.from_bytes(digest, "big")
            sign = 1.0 if h & 1 else -1.0
            weight = 1.0 + math.log(count)  # augmented term frequency
            vec[h % self.dimensions] += sign * weight
        norm = math.sqrt(sum(x * x for x in vec))
        if norm == 0.0:
            return vec
        return [x / norm for x in vec]


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two vectors (both assumed non-zero-length)."""
    if len(a) != len(b):
        raise ValueError(f"vector dimension mismatch: {len(a)} != {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
