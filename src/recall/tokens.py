"""Claim analysis: what does a fact *assert*, in what frame?

Recall decides between three relationships for any pair of facts:

- **duplicate** — same claim, same frame: reinforce instead of writing twice.
- **contradiction** — same frame, different claim (or a negation flip):
  "Priya lives in Paris" vs "Priya lives in Berlin"; "the API is public" vs
  "the API is not public".
- **unrelated** — different frames ("Priya lives in Paris" vs "Priya works at
  Acme"): both can be true at once, so no conflict.

Three signals decide, all deterministic and offline:

1. **Embedding similarity** — the facts talk about the same thing.
2. **Claim-token overlap** — the asserted *details* agree or differ. Claim
   tokens are content words minus frame words.
3. **Frame overlap** — the relation words ("lives", "reviews", "owns") must
   match for two facts to even be candidates for conflict.
"""

from __future__ import annotations

import re

from .embed import STOP_WORDS, tokenize

# Words that frame a claim (relations, copulas, aspect) rather than being the
# claim itself. "X lives in Paris" and "X lives in Berlin" share their frame
# and disagree on the claim token.
FRAME_WORDS = frozenset(
    """
    is are was were be been being has have had will would can could should
    does did do done get got set put use used using works work working
    lives live living stays stay based located runs run running made make
    makes made supports support supporting uses owns own owning manages
    manage managing maintains maintain maintaining reviews review reviewing
    deploys deploy deploying triages triage handles handle handling
    decides decide deciding operates operate operating reports report
    reporting belongs belong contains contain includes include holds hold
    rotates rotate moved move moves changes change changed updated update
    """.split()
)

# Determiner-ish words that carry no claim content.
_FILLER_WORDS = frozenset(
    "each every all any both many much several various own same one two three "
    "new old current previous next last first second per annual daily weekly "
    "monthly yearly".split()
)

_CLAIM_STOP = STOP_WORDS | FRAME_WORDS | _FILLER_WORDS

_NEGATION_RE = re.compile(
    r"\b(not|no|never|none|nothing|nobody|neither|nor|cannot|can't|don't|doesn't|"
    r"didn't|isn't|aren't|wasn't|weren't|won't|wouldn't|shouldn't|couldn't|"
    r"no longer|not anymore|no more)\b",
    re.IGNORECASE,
)


def claim_tokens(text: str) -> set[str]:
    """The asserted details: content words minus frame and filler words.

    Single-character tokens are dropped (letters like the "m" of "a.m."),
    but digits are kept: "opens at 9" and "opens at 5" assert different
    things even though each number is one character long.
    """
    return {
        t
        for t in tokenize(text)
        if t not in _CLAIM_STOP and (len(t) > 1 or any(ch.isdigit() for ch in t))
    }


def frame_tokens(text: str) -> set[str]:
    """The relation words of a claim: the frame two conflicting facts share."""
    return {t for t in tokenize(text) if t in FRAME_WORDS}


def has_negation(text: str) -> bool:
    return bool(_NEGATION_RE.search(text))


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def contradiction_score(text_a: str, text_b: str, embedding_similarity: float) -> float:
    """How likely two facts conflict, in [0, 1+]. 0 means no conflict.

    High similarity + low claim overlap + shared frame = contradiction.
    A negation flip is always a contradiction when the claims line up.
    A frame mismatch dampens the score: different relations about the same
    entity are usually both true.
    """
    sim = max(0.0, embedding_similarity)
    overlap = jaccard(claim_tokens(text_a), claim_tokens(text_b))
    frame = jaccard(frame_tokens(text_a), frame_tokens(text_b))
    neg_flip = has_negation(text_a) != has_negation(text_b)
    neg_both = has_negation(text_a) and has_negation(text_b)

    if neg_flip:
        score = sim * max(1.0 - overlap, 0.8)
    elif neg_both and overlap >= 0.4 and frame >= 0.4:
        score = sim * 0.9
    else:
        score = sim * (1.0 - overlap)

    if frame < 0.5:
        score *= 0.3
    return score


def duplicate_score(text_a: str, text_b: str, embedding_similarity: float) -> float:
    """How likely two facts say the same thing, in [0, 1].

    A negation flip can never be a duplicate: "the API is public" and
    "the API is not public" share every token but assert opposite things,
    so they score 0 here and are left to :func:`contradiction_score`.
    """
    if has_negation(text_a) != has_negation(text_b):
        return 0.0
    sim = max(0.0, embedding_similarity)
    overlap = jaccard(claim_tokens(text_a), claim_tokens(text_b))
    frame = jaccard(frame_tokens(text_a), frame_tokens(text_b))
    return sim * overlap * (0.5 + 0.5 * frame)
