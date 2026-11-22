"""Integration tests: drive the Recall MCP server like a real agent host does,
over stdio with the MCP client SDK."""

from __future__ import annotations

import json
import sys
from contextlib import asynccontextmanager

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@asynccontextmanager
async def open_session(tmp_path):
    db = tmp_path / "mcp.rl"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "recall.mcp_server", "--db", str(db)],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            yield s


def _text(result) -> str:
    return "".join(c.text for c in result.content if c.type == "text")


async def test_tools_are_listed(tmp_path):
    async with open_session(tmp_path) as session:
        tools = await session.list_tools()
        names = {t.name for t in tools.tools}
        assert {
            "remember",
            "remember_many",
            "recall",
            "why",
            "contradictions",
            "resolve_contradiction",
            "forget",
            "timeline",
            "stats",
            "graph",
        } == names


async def test_remember_reports_supersession_and_conflicts(tmp_path):
    async with open_session(tmp_path) as session:
        r = await session.call_tool(
            "remember",
            {"text": "Grace reviews each relate release.", "about": ["Grace", "relate"],
             "slot": "relate/release-reviewer"},
        )
        report = json.loads(_text(r))
        assert report["fact"]["id"]

        r = await session.call_tool(
            "remember",
            {"text": "Ada reviews each relate release.", "about": ["Ada", "relate"],
             "slot": "relate/release-reviewer", "kind": "tool_call",
             "evidence": "read_file CODEOWNERS: release/ @ada"},
        )
        report = json.loads(_text(r))
        assert report["superseded"]  # Grace's fact was closed
        assert any("Superseded" in n for n in report["notes"])


async def test_contradiction_flow_over_mcp(tmp_path):
    async with open_session(tmp_path) as session:
        await session.call_tool(
            "remember", {"text": "Priya lives in Paris.", "about": ["Priya"], "slot": "priya/residence"}
        )
        r = await session.call_tool(
            "remember", {"text": "Priya lives in Berlin.", "about": ["Priya"], "slot": "priya/relocation"}
        )
        report = json.loads(_text(r))
        assert report["conflicts"], "the conflict must be reported to the agent"

        r = await session.call_tool("contradictions", {})
        cons = json.loads(_text(r))
        assert len(cons) == 1
        a_id, b_id = cons[0]["a_id"], cons[0]["b_id"]

        keep, drop = (b_id, a_id)  # keep Berlin
        r = await session.call_tool(
            "resolve_contradiction", {"keep_id": keep, "drop_id": drop, "note": "HR confirmed."}
        )
        res = json.loads(_text(r))
        assert res["retracted"]["id"] == drop

        r = await session.call_tool("contradictions", {})
        assert json.loads(_text(r)) == []


async def test_recall_and_why_over_mcp(tmp_path):
    async with open_session(tmp_path) as session:
        await session.call_tool(
            "remember",
            {"text": "The release train runs at noon.", "about": ["release"],
             "slot": "release/train", "evidence": "PM note from standup."},
        )
        r = await session.call_tool("recall", {"query": "When does the release train run?", "k": 3})
        hits = json.loads(_text(r))
        assert hits and hits[0]["text"] == "The release train runs at noon."
        fact_id = hits[0]["id"]

        r = await session.call_tool("why", {"fact_id": fact_id})
        why = json.loads(_text(r))
        assert why["evidence"][0]["text"] == "PM note from standup."


async def test_stats_and_graph_over_mcp(tmp_path):
    async with open_session(tmp_path) as session:
        await session.call_tool(
            "remember", {"text": "Ada uses Rust.", "about": ["Ada"], "slot": "ada/language"}
        )
        r = await session.call_tool("stats", {})
        stats = json.loads(_text(r))
        assert stats["facts"]["current"] == 1

        r = await session.call_tool("graph", {})
        graph = json.loads(_text(r))
        assert {n["type"] for n in graph["nodes"]} >= {"Entity", "Fact"}
