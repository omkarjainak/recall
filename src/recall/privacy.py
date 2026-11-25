"""Secret filtering: credentials never enter the graph.

``remember`` scrubs its inputs before they are written, so an agent that
ingests logs, tool output or user messages cannot leak API keys, tokens or
passwords into long-term memory — and from there into every future
``recall`` answer, export or explorer screenshot.

Two layers:

1. **Automatic scrubbing** (default on): patterns for the common credential
   shapes are replaced with ``«redacted:kind»`` in both the fact text and
   its evidence episode. Facts that had something scrubbed are flagged
   ``sensitive``.
2. **Explicit sensitivity**: pass ``sensitive=True`` to ``remember`` for
   facts that are private by nature (personal details, internal notes);
   hosts can then exclude them from ``recall`` with
   ``include_sensitive=False``.

The patterns are deliberately conservative — they catch shaped secrets
(AWS/GitHub/Slack/OpenAI keys, JWTs, private keys, ``password=...``) rather
than trying to guess at arbitrary prose.
"""

from __future__ import annotations

import re

# (kind, pattern). Order matters: specific shapes before the generic
# key=value catch-all, so we report the most precise kind.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL)),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("google_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    (
        "assigned_secret",
        re.compile(
            r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)"
            r"\s*[:=]\s*[\"']?[^\s\"',;]{6,}"
        ),
    ),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{16,}={0,2}")),
]

REDACTED = "«redacted:{kind}»"


def find_secrets(text: str) -> list[str]:
    """The kinds of secret-shaped strings in ``text`` (may repeat kinds)."""
    return [kind for kind, pat in _PATTERNS if pat.search(text)]


def scrub(text: str | None) -> tuple[str | None, list[str]]:
    """Replace every secret-shaped substring with ``«redacted:kind»``.

    Returns ``(clean_text, kinds_found)``. ``None`` passes through.
    """
    if not text:
        return text, []
    found: list[str] = []
    out = text
    for kind, pat in _PATTERNS:
        if pat.search(out):
            found.append(kind)
            out = pat.sub(REDACTED.format(kind=kind), out)
    return out, found
