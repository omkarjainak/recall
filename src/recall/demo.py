"""The Recall demo: a scripted story that exercises every feature.

Run with ``recall demo``. It is also the demo video's script: the same
walkthrough, live in a terminal.

The story: an agent works with a team across several sessions. It learns
facts, gets corrected (supersession), receives conflicting reports
(contradiction), recalls with evidence, settles the conflict through a
decision, and can finally explain — on a past date — both what was true and
what it believed at the time.
"""

from __future__ import annotations

import json
from typing import Any

from .memory import Memory

BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
RESET = "\033[0m"


def _head(title: str) -> None:
    print(f"\n{BOLD}{CYAN}━━ {title} {'━' * max(0, 60 - len(title))}{RESET}")


def _say(text: str) -> None:
    print(f"{DIM}{text}{RESET}")


def run_demo(db_path: str = ":memory:", *, as_json: bool = False) -> None:
    quiet = as_json
    out: dict[str, Any] = {}

    def head(title: str) -> None:
        if not quiet:
            _head(title)

    def say(text: str) -> None:
        if not quiet:
            _say(text)

    mem = Memory(db_path)

    # ------------------------------------------------------------------
    head("1. A first session — the agent learns two things")
    say("Session s1, March 2. A teammate explains how the team works.")
    r = mem.remember(
        "Grace reviews each relate release.",
        slot="relate/release-reviewer",
        about=["Grace", "relate"],
        session="s1",
        at="2026-03-02T10:00:00Z",
        valid_from="2026-01-05T00:00:00Z",
        evidence="Grace does the release review for relate.",
        importance=0.8,
        id="f1",
        episode_id="e1",
    )
    if not quiet:
        print(f"  remembered: {GREEN}{r['fact']['text']}{RESET} ({r['fact']['id']})")
    r = mem.remember(
        "Noor triages new relate issues on Mondays.",
        slot="relate/triage",
        about=["Noor", "relate"],
        session="s1",
        at="2026-03-02T10:05:00Z",
        valid_from="2026-02-02T00:00:00Z",
        evidence="Noor goes through new relate issues every Monday.",
        importance=0.6,
        id="f2",
        episode_id="e2",
    )
    if not quiet:
        print(f"  remembered: {GREEN}{r['fact']['text']}{RESET} ({r['fact']['id']})")

    # ------------------------------------------------------------------
    head("2. A correction — same slot, newer truth")
    say("Session s2, March 20. A tool call reads CODEOWNERS: Ada took over on March 10.")
    r = mem.remember(
        "Ada reviews each relate release.",
        slot="relate/release-reviewer",
        about=["Ada", "relate"],
        session="s2",
        kind="tool_call",
        evidence="read_file CODEOWNERS: release/ @ada",
        at="2026-03-20T09:00:00Z",
        valid_from="2026-03-10T00:00:00Z",
        importance=0.8,
        id="f3",
        episode_id="e3",
    )
    if not quiet:
        print(f"  remembered: {GREEN}{r['fact']['text']}{RESET} ({r['fact']['id']})")
        for s in r["superseded"]:
            print(f"  {YELLOW}superseded:{RESET} {s['text']} ({s['id']}) — kept in history, not deleted")

    # ------------------------------------------------------------------
    head("3. Conflicting reports — the memory argues with itself")
    say("Session s3, April 1-2. Two teammates disagree about where Priya lives.")
    r = mem.remember(
        "Priya lives in Paris.",
        slot="priya/residence",
        about=["Priya"],
        session="s3",
        at="2026-04-01T00:00:00Z",
        evidence="Priya mentioned her Paris apartment on the call.",
        id="f4",
        episode_id="e4",
    )
    if not quiet:
        print(f"  remembered: {GREEN}{r['fact']['text']}{RESET} ({r['fact']['id']})")
    r = mem.remember(
        "Priya lives in Berlin.",
        slot="priya/relocation",
        about=["Priya"],
        session="s3",
        at="2026-04-02T00:00:00Z",
        evidence="Dan said Priya relocated to Berlin last month.",
        id="f5",
        episode_id="e5",
    )
    if not quiet:
        print(f"  remembered: {GREEN}{r['fact']['text']}{RESET} ({r['fact']['id']})")
        for c in r["conflicts"]:
            print(f"  {RED}CONTRADICTS{RESET} {c['id']}: \"{c['text']}\" (score {c['score']})")

    # ------------------------------------------------------------------
    head("4. Asking memory — answers with evidence and dispute flags")
    say('recall "Who reviews relate releases?"')
    hits = mem.recall("Who reviews relate releases?", k=2)
    out["recall_reviewer"] = hits
    if not quiet:
        for h in hits:
            flag = f" {RED}[DISPUTED]{RESET}" if h["disputed"] else ""
            print(f"  [{h['score']:.3f}] {GREEN}{h['text']}{RESET}{flag}")
            if h["evidence"]:
                print(f"          evidence: {h['evidence'][0]['text']}")
            if h["replaced"]:
                print(f"          replaced: {'; '.join(h['replaced'])}")

    say('recall "Where does Priya live?" — both sides of the conflict surface')
    hits = mem.recall("Where does Priya live?", k=2)
    out["recall_priya"] = hits
    if not quiet:
        for h in hits:
            flag = f" {RED}[DISPUTED]{RESET}" if h["disputed"] else ""
            print(f"  [{h['score']:.3f}] {GREEN}{h['text']}{RESET}{flag}")
            for c in h["conflicts"]:
                state = "resolved" if c["resolved"] else f"{RED}OPEN{RESET}"
                print(f"          conflicts[{state}] with {c['id']}: {c['text']}")

    # ------------------------------------------------------------------
    head("5. Settling the conflict through a decision")
    cons = mem.contradictions()
    out["contradictions"] = cons
    if not quiet:
        for c in cons:
            print(f"  {RED}open contradiction{RESET} (score {c['score']}):")
            print(f"    A {c['a_id']}: {c['a_text']}")
            print(f"    B {c['b_id']}: {c['b_text']}")
    say("The agent checks the relocation policy and decides.")
    res = mem.resolve_contradiction(
        "f5", "f4", note="HR confirmed the Berlin move in April.", resolved_by="agent"
    )
    out["resolution"] = {"kept": res["kept"]["id"], "retracted": res["retracted"]["id"]}
    if not quiet:
        print(f"  {GREEN}kept:{RESET}      {res['kept']['text']}")
        print(f"  {YELLOW}retracted:{RESET} {res['retracted']['text']} — closed, never deleted")
        print(f"  decision recorded as episode {res['decision_episode']}")

    hits = mem.recall("Where does Priya live?", k=2)
    if not quiet:
        say('recall "Where does Priya live?" again — the answer is clean now')
        for h in hits:
            flag = f" {RED}[DISPUTED]{RESET}" if h["disputed"] else ""
            print(f"  [{h['score']:.3f}] {GREEN}{h['text']}{RESET}{flag}")

    # ------------------------------------------------------------------
    head("6. Why? — every memory quotes its evidence")
    why = mem.why("f3")
    out["why_f3"] = why
    if not quiet:
        print(f"  fact: {why['fact']['text']}")
        print(f"  learned from ({why['evidence'][0]['kind']}): {why['evidence'][0]['text']}")
        print(f"  superseded: {[s['text'] for s in why['superseded']]}")
        print(f"  about: {[a['name'] for a in why['about']]}")

    # ------------------------------------------------------------------
    head("7. Two timelines — what was true, and what was believed")
    tl = mem.timeline(slot="relate/release-reviewer", on="2026-03-15T00:00:00Z")
    out["timeline_2026_03_15"] = tl
    if not quiet:
        entry = tl["slots"]["relate/release-reviewer"]
        print(f"  on 2026-03-15, true:      {GREEN}{entry['trueOn']}{RESET}")
        print(f"  on 2026-03-15, believed:  {YELLOW}{entry['believedOn']}{RESET}")
        say("The agent asked Grace for approval on March 15 — now it can explain why.")

    # ------------------------------------------------------------------
    head("8. The shape of the memory")
    stats = mem.stats()
    out["stats"] = stats
    if not quiet:
        print(json.dumps(stats, indent=2, default=str))
        print(f"\n{BOLD}Every fact kept its evidence. Nothing was deleted. "
              f"Two conflicting reports were found and settled.{RESET}")

    if as_json:
        print(json.dumps(out, indent=2, default=str))
    mem.close()
