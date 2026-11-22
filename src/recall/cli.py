"""recall — the command line for agent memory.

    recall remember "Ada reviews each relate release." --about Ada --about relate \
        --slot relate/release-reviewer
    recall recall "Who reviews relate releases?"
    recall why f3
    recall contradictions
    recall resolve f5 f4 --note "She moved to Berlin."
    recall timeline --slot relate/release-reviewer --on 2026-03-15
    recall stats
    recall serve            # the MCP server for agent hosts
    recall demo             # scripted end-to-end walkthrough
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from .memory import Memory

DEFAULT_DB = os.environ.get("RECALL_DB", "./memory.rl")


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


def _print_hits(hits: list[dict[str, Any]]) -> None:
    if not hits:
        print("(no memories matched)")
        return
    for i, h in enumerate(hits, 1):
        flag = "  [DISPUTED]" if h["disputed"] else ""
        print(f"{i}. [{h['score']:.3f}] {h['text']}{flag}")
        print(f"   id={h['id']}  slot={h['slot']}  recorded={h['recordedAt']}")
        if h["evidence"]:
            e = h["evidence"][0]
            print(f"   evidence ({e['kind']}): {e['text']}")
        if h["replaced"]:
            print(f"   replaced: {'; '.join(h['replaced'])}")
        for c in h["conflicts"]:
            state = "resolved" if c["resolved"] else "OPEN"
            print(f"   conflicts[{state}] with {c['id']}: {c['text']} (score {c['score']})")
        print()


def cmd_init(mem: Memory, args: argparse.Namespace) -> None:
    stats = mem.stats()
    print(f"store ready: {mem.store.path} ({stats['facts']['total']} facts)")


def cmd_remember(mem: Memory, args: argparse.Namespace) -> None:
    report = mem.remember(
        args.text,
        about=args.about or [],
        slot=args.slot,
        session=args.session,
        kind=args.kind,
        evidence=args.evidence,
        at=args.at,
        valid_from=args.valid_from,
        importance=args.importance,
    )
    if args.json:
        _print_json(report)
        return
    f = report["fact"]
    print(f"remembered: {f['text']}  ({f['id']}, slot {f['slot']})")
    for s in report["superseded"]:
        print(f"  superseded: {s['text']} ({s['id']})")
    for d in report["duplicates"]:
        print(f"  duplicate of {d['id']} — reinforced instead")
    for c in report["conflicts"]:
        print(f"  CONTRADICTS {c['id']}: \"{c['text']}\" (score {c['score']}) — run: recall resolve <keep> {c['id']}")


def cmd_recall(mem: Memory, args: argparse.Namespace) -> None:
    hits = mem.recall(args.query, k=args.k, entity=args.entity, slot=args.slot)
    if args.json:
        _print_json(hits)
    else:
        _print_hits(hits)


def cmd_why(mem: Memory, args: argparse.Namespace) -> None:
    try:
        result = mem.why(args.fact_id)
    except KeyError as e:
        sys.exit(str(e))
    if args.json:
        _print_json(result)
        return
    f = result["fact"]
    print(f"fact: {f['text']}  ({f['id']}, {f['status']}, slot {f['slot']})")
    for e in result["evidence"]:
        print(f"  learned from ({e['kind']}, session {e['session']}, {e['at']}): {e['text']}")
    print(f"  about: {', '.join(a['name'] for a in result['about'])}")
    for s in result["superseded"]:
        print(f"  superseded: {s['text']} ({s['id']})")
    for s in result["superseded_by"]:
        print(f"  superseded by: {s['text']} ({s['id']})")
    for c in result["contradictions"]:
        state = "resolved" if c["resolved"] else "OPEN"
        print(f"  contradiction[{state}] ({c['score']}): {c['text']} ({c['id']})")
        if c["note"]:
            print(f"    note: {c['note']}")


def cmd_contradictions(mem: Memory, args: argparse.Namespace) -> None:
    cons = mem.contradictions(include_resolved=args.all)
    if args.json:
        _print_json(cons)
        return
    if not cons:
        print("(no contradictions)")
        return
    for c in cons:
        state = "resolved" if c["resolved"] else "OPEN"
        print(f"[{state}] score {c['score']}:")
        print(f"  A {c['a_id']}: {c['a_text']} ({c['a_status']})")
        print(f"  B {c['b_id']}: {c['b_text']} ({c['b_status']})")
        if c["note"]:
            print(f"  note: {c['note']}")
        print(f"  resolve: recall resolve <keep_id> <drop_id> --note 'why'")
        print()


def cmd_resolve(mem: Memory, args: argparse.Namespace) -> None:
    try:
        result = mem.resolve_contradiction(args.keep_id, args.drop_id, note=args.note)
    except KeyError as e:
        sys.exit(str(e))
    if args.json:
        _print_json(result)
        return
    print(f"kept:      {result['kept']['text']} ({result['kept']['id']})")
    print(f"retracted: {result['retracted']['text']} ({result['retracted']['id']})")
    print(f"decision recorded as episode {result['decision_episode']}")


def cmd_forget(mem: Memory, args: argparse.Namespace) -> None:
    try:
        r = mem.forget(args.fact_id, reason=args.reason)
    except KeyError as e:
        sys.exit(str(e))
    print(f"retracted: {r['text']} ({r['id']}) — closed, not deleted")


def cmd_timeline(mem: Memory, args: argparse.Namespace) -> None:
    try:
        result = mem.timeline(slot=args.slot, entity=args.entity, on=args.on)
    except ValueError as e:
        sys.exit(str(e))
    if args.json:
        _print_json(result)
        return
    for slot, entry in result["slots"].items():
        print(f"slot: {slot}")
        for f in entry["facts"]:
            print(f"  [{f['status']:<10}] {f['text']}")
            print(f"     true {f['validFrom']} → {f['validTo'] or 'now'}")
            print(f"     believed {f['recordedAt']} → {f['closedAt'] or 'now'}")
        if "trueOn" in entry:
            print(f"  true on {args.on}:      {entry['trueOn']}")
            print(f"  believed on {args.on}:  {entry['believedOn']}")
        print()


def cmd_consolidate(mem: Memory, args: argparse.Namespace) -> None:
    report = mem.consolidate(older_than_days=args.older_than_days, min_importance=args.min_importance)
    if args.json:
        _print_json(report)
        return
    for r in report["expired"]:
        print(f"expired: {r['text']} ({r['id']})")
    print(f"current facts remaining: {report['current_facts']}")


def cmd_stats(mem: Memory, args: argparse.Namespace) -> None:
    _print_json(mem.stats())


def cmd_export(mem: Memory, args: argparse.Namespace) -> None:
    graph = mem.export_graph()
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(graph, fh, indent=2)
        print(f"wrote {len(graph['nodes'])} nodes, {len(graph['edges'])} edges to {args.out}")
    else:
        _print_json(graph)


def cmd_explore(mem: Memory, args: argparse.Namespace) -> None:
    from .explorer import render_explorer

    html = render_explorer(mem.export_graph())
    out = args.out or "recall-graph.html"
    with open(out, "w") as fh:
        fh.write(html)
    print(f"explorer written to {out}")
    if args.open:
        import webbrowser

        webbrowser.open(f"file://{os.path.abspath(out)}")


def cmd_query(mem: Memory, args: argparse.Namespace) -> None:
    params = json.loads(args.params) if args.params else None
    _print_json(mem.query(args.cypher, params))


def cmd_serve(mem: Memory, args: argparse.Namespace) -> None:
    from .mcp_server import build_server

    mem.close()  # the server opens its own store
    server = build_server(args.db, read_only=args.read_only)
    server.run(transport=args.transport)


def cmd_demo(mem: Memory, args: argparse.Namespace) -> None:
    """A scripted end-to-end walkthrough of everything Recall can do."""
    from .demo import run_demo

    mem.close()
    run_demo(args.db, as_json=args.json)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="recall", description="Contradiction-aware agent memory on Relate")
    p.add_argument("--db", default=DEFAULT_DB, help=f"store path (default: {DEFAULT_DB})")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the store and schema")

    sp = sub.add_parser("remember", help="store one fact")
    sp.add_argument("text", help="the fact, one clear statement")
    sp.add_argument("--about", action="append", help="entity it is about (repeatable)")
    sp.add_argument("--slot", help="the question this fact answers")
    sp.add_argument("--session", default="default")
    sp.add_argument("--kind", default="message", choices=["message", "tool_call", "decision", "import"])
    sp.add_argument("--evidence", help="the raw text it was extracted from")
    sp.add_argument("--at", help="ISO time it was learned")
    sp.add_argument("--valid-from", help="ISO time it became true")
    sp.add_argument("--importance", type=float, default=0.5)

    sp = sub.add_parser("recall", help="ask a question of memory")
    sp.add_argument("query")
    sp.add_argument("-k", type=int, default=5)
    sp.add_argument("--entity")
    sp.add_argument("--slot")

    sp = sub.add_parser("why", help="provenance of a fact")
    sp.add_argument("fact_id")

    sp = sub.add_parser("contradictions", help="list contradictions")
    sp.add_argument("--all", action="store_true", help="include resolved")

    sp = sub.add_parser("resolve", help="settle a contradiction")
    sp.add_argument("keep_id")
    sp.add_argument("drop_id")
    sp.add_argument("--note", default="")

    sp = sub.add_parser("forget", help="retract a fact (never deleted)")
    sp.add_argument("fact_id")
    sp.add_argument("--reason", default="forgotten")

    sp = sub.add_parser("timeline", help="a slot or entity through time")
    sp.add_argument("--slot")
    sp.add_argument("--entity")
    sp.add_argument("--on", help="ISO date: what was true then vs believed then")

    sp = sub.add_parser("consolidate", help="close stale, unimportant facts")
    sp.add_argument("--older-than-days", type=float, default=90.0)
    sp.add_argument("--min-importance", type=float, default=0.3)

    sub.add_parser("stats", help="memory statistics")

    sp = sub.add_parser("export", help="export the graph as JSON")
    sp.add_argument("--out", help="write to a file")

    sp = sub.add_parser("explore", help="write a self-contained HTML graph explorer")
    sp.add_argument("--out", help="HTML output path (default: recall-graph.html)")
    sp.add_argument("--open", action="store_true", help="open it in a browser")

    sp = sub.add_parser("query", help="run raw Cypher")
    sp.add_argument("cypher")
    sp.add_argument("--params", help="JSON object of parameters")

    sp = sub.add_parser("serve", help="run the MCP server for agent hosts")
    sp.add_argument("--read-only", action="store_true")
    sp.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http"])

    sub.add_parser("demo", help="scripted end-to-end walkthrough")

    return p


COMMANDS = {
    "init": cmd_init,
    "remember": cmd_remember,
    "recall": cmd_recall,
    "why": cmd_why,
    "contradictions": cmd_contradictions,
    "resolve": cmd_resolve,
    "forget": cmd_forget,
    "timeline": cmd_timeline,
    "consolidate": cmd_consolidate,
    "stats": cmd_stats,
    "export": cmd_export,
    "explore": cmd_explore,
    "query": cmd_query,
    "serve": cmd_serve,
    "demo": cmd_demo,
}


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    mem = Memory(args.db)
    try:
        COMMANDS[args.command](mem, args)
    finally:
        mem.close()


if __name__ == "__main__":
    main()
