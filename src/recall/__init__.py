"""Recall — contradiction-aware long-term memory for AI agents, on Relate.

Quick start::

    from recall import Memory

    mem = Memory("./memory.rl")
    mem.remember("Grace reviews each relate release.",
                 slot="relate/release-reviewer",
                 about=["Grace", "relate"])
    hits = mem.recall("Who reviews relate releases?")
    print(hits[0]["text"], hits[0]["evidence"])
"""

from .embed import Embedder, HashingEmbedder, cosine
from .memory import Memory
from .tokens import claim_tokens, contradiction_score, duplicate_score, frame_tokens

__version__ = "0.1.0"

__all__ = [
    "Memory",
    "Embedder",
    "HashingEmbedder",
    "cosine",
    "claim_tokens",
    "frame_tokens",
    "contradiction_score",
    "duplicate_score",
    "__version__",
]
