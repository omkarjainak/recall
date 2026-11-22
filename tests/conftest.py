"""Shared fixtures: a fresh in-memory Memory and a scripted history."""

from __future__ import annotations

import pytest

from recall import Memory


@pytest.fixture
def mem() -> Memory:
    m = Memory(":memory:")
    yield m
    m.close()


@pytest.fixture
def loaded(mem: Memory) -> Memory:
    """A memory with the canonical story: a correction and a contradiction."""
    mem.remember(
        "Grace reviews each relate release.",
        slot="relate/release-reviewer",
        about=["Grace", "relate"],
        at="2026-03-02T10:00:00Z",
        valid_from="2026-01-05T00:00:00Z",
        id="f1",
        episode_id="e1",
    )
    mem.remember(
        "Noor triages new relate issues on Mondays.",
        slot="relate/triage",
        about=["Noor", "relate"],
        at="2026-03-02T10:05:00Z",
        valid_from="2026-02-02T00:00:00Z",
        id="f2",
        episode_id="e2",
    )
    mem.remember(
        "Ada reviews each relate release.",
        slot="relate/release-reviewer",
        about=["Ada", "relate"],
        kind="tool_call",
        evidence="read_file CODEOWNERS: release/ @ada",
        at="2026-03-20T09:00:00Z",
        valid_from="2026-03-10T00:00:00Z",
        id="f3",
        episode_id="e3",
    )
    mem.remember(
        "Priya lives in Paris.",
        slot="priya/residence",
        about=["Priya"],
        at="2026-04-01T00:00:00Z",
        id="f4",
        episode_id="e4",
    )
    mem.remember(
        "Priya lives in Berlin.",
        slot="priya/relocation",
        about=["Priya"],
        at="2026-04-02T00:00:00Z",
        id="f5",
        episode_id="e5",
    )
    return mem
