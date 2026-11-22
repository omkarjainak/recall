"""The Recall explorer: a self-contained HTML page that draws the memory graph.

``recall explore`` writes one HTML file with the graph JSON embedded — no
server, no build step, no CDN: it opens from the filesystem and draws a
force-directed view of entities, episodes and facts, with contradictions in
red, supersessions dashed, and every fact clickable for its provenance.
"""

from __future__ import annotations

import json
from typing import Any

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Recall — memory graph</title>
<style>
  :root {
    --bg: #0f1220; --panel: #171b2e; --text: #e8eaf2; --dim: #9aa0b4;
    --fact: #4fd1a5; --episode: #6ea8fe; --entity: #b68cff;
    --conflict: #ff6b6b; --supersede: #f0c674; --current: #4fd1a5;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text);
         font: 14px/1.5 -apple-system, "Segoe UI", Roboto, sans-serif; }
  header { padding: 18px 26px 8px; }
  h1 { margin: 0; font-size: 21px; letter-spacing: .3px; }
  h1 span { color: var(--fact); }
  .sub { color: var(--dim); margin-top: 2px; }
  #wrap { display: flex; height: calc(100vh - 92px); }
  #canvas { flex: 1; position: relative; }
  svg { width: 100%; height: 100%; display: block; }
  #panel { width: 340px; background: var(--panel); padding: 18px 20px;
           overflow-y: auto; border-left: 1px solid #262b45; }
  #panel h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 1px;
              color: var(--dim); margin: 18px 0 6px; }
  #panel h2:first-child { margin-top: 0; }
  .chip { display: inline-block; padding: 2px 9px; border-radius: 999px;
          font-size: 11px; font-weight: 600; margin-right: 6px; }
  .chip.fact { background: rgba(79,209,165,.15); color: var(--fact); }
  .chip.episode { background: rgba(110,168,254,.15); color: var(--episode); }
  .chip.entity { background: rgba(182,140,255,.15); color: var(--entity); }
  .chip.current { background: rgba(79,209,165,.15); color: var(--current); }
  .chip.superseded { background: rgba(240,198,116,.15); color: var(--supersede); }
  .chip.retracted, .chip.expired { background: rgba(255,107,107,.15); color: var(--conflict); }
  .detail { background: #12162a; border: 1px solid #262b45; border-radius: 10px;
            padding: 12px 14px; margin-top: 8px; }
  .detail .t { font-weight: 600; }
  .detail .m { color: var(--dim); font-size: 12px; margin-top: 4px; word-break: break-word; }
  .legend { display: flex; gap: 14px; flex-wrap: wrap; margin-top: 10px;
            color: var(--dim); font-size: 12px; }
  .legend i { width: 10px; height: 10px; border-radius: 50%; display: inline-block;
              margin-right: 5px; }
  .edge-l { stroke-width: 1.6; fill: none; }
  text { pointer-events: none; font-size: 11px; fill: var(--text); }
  .node { cursor: pointer; }
  .node:hover circle { stroke: #fff; stroke-width: 2; }
  .hint { position: absolute; bottom: 14px; left: 18px; color: var(--dim);
          font-size: 12px; }
  .empty { color: var(--dim); font-size: 13px; }
</style>
</head>
<body>
<header>
  <h1>Recall <span>·</span> memory graph</h1>
  <div class="sub" id="summary"></div>
  <div class="legend">
    <span><i style="background:var(--fact)"></i>Fact</span>
    <span><i style="background:var(--episode)"></i>Episode (evidence)</span>
    <span><i style="background:var(--entity)"></i>Entity</span>
    <span><i style="background:var(--conflict)"></i>CONTRADICTS</span>
    <span><i style="background:var(--supersede)"></i>SUPERSEDES</span>
  </div>
</header>
<div id="wrap">
  <div id="canvas">
    <svg id="svg"></svg>
    <div class="hint">drag nodes · click for provenance · scroll to zoom</div>
  </div>
  <div id="panel">
    <h2>Selection</h2>
    <div id="detail"><div class="empty">Click a node to see its provenance.</div></div>
  </div>
</div>
<script>
const GRAPH = __GRAPH_JSON__;

const svg = document.getElementById("svg");
const NS = "http://www.w3.org/2000/svg";
const color = { Fact: "#4fd1a5", Episode: "#6ea8fe", Entity: "#b68cff" };
const edgeColor = {
  FROM: "#6ea8fe", ABOUT: "#b68cff", MENTIONS: "#7d86aa",
  SUPERSEDES: "#f0c674", CONTRADICTS: "#ff6b6b",
};

// ---- summary -------------------------------------------------------------
const counts = { Fact: 0, Episode: 0, Entity: 0 };
GRAPH.nodes.forEach(n => counts[n.type]++);
document.getElementById("summary").textContent =
  `${counts.Fact} facts · ${counts.Episode} episodes · ${counts.Entity} entities · ` +
  `${GRAPH.edges.filter(e => e.type === "CONTRADICTS").length} contradictions`;

// ---- force layout (tiny, dependency-free) --------------------------------
const nodes = GRAPH.nodes.map(n => ({ ...n }));
const byId = Object.fromEntries(nodes.map(n => [n.id, n]));
const edges = GRAPH.edges.filter(e => byId[e.source] && byId[e.target])
  .map(e => ({ ...e }));

const W = () => svg.clientWidth || 900;
const H = () => svg.clientHeight || 600;
nodes.forEach((n, i) => {
  n.x = W() / 2 + 260 * Math.cos((2 * Math.PI * i) / nodes.length);
  n.y = H() / 2 + 220 * Math.sin((2 * Math.PI * i) / nodes.length);
  n.vx = 0; n.vy = 0;
});

function step() {
  // repulsion
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i], b = nodes[j];
      let dx = b.x - a.x, dy = b.y - a.y;
      let d2 = dx * dx + dy * dy || 1;
      const f = 2600 / d2;
      const d = Math.sqrt(d2);
      dx /= d; dy /= d;
      a.vx -= dx * f; a.vy -= dy * f;
      b.vx += dx * f; b.vy += dy * f;
    }
  }
  // springs
  for (const e of edges) {
    const a = byId[e.source], b = byId[e.target];
    let dx = b.x - a.x, dy = b.y - a.y;
    const d = Math.sqrt(dx * dx + dy * dy) || 1;
    const f = (d - 150) * 0.02;
    dx /= d; dy /= d;
    a.vx += dx * f; a.vy += dy * f;
    b.vx -= dx * f; b.vy -= dy * f;
  }
  // centering + integrate
  for (const n of nodes) {
    n.vx += (W() / 2 - n.x) * 0.004;
    n.vy += (H() / 2 - n.y) * 0.004;
    n.x += n.vx * 0.5; n.y += n.vy * 0.5;
    n.vx *= 0.85; n.vy *= 0.85;
    n.x = Math.max(30, Math.min(W() - 30, n.x));
    n.y = Math.max(30, Math.min(H() - 30, n.y));
  }
}

// pre-settle: run the simulation synchronously so the graph is laid out
// on first paint (and in a screenshot), then keep animating gently.
for (let i = 0; i < 400; i++) step();

// ---- drawing -------------------------------------------------------------
function draw() {
  svg.innerHTML = "";
  for (const e of edges) {
    const a = byId[e.source], b = byId[e.target];
    const line = document.createElementNS(NS, "line");
    line.setAttribute("x1", a.x); line.setAttribute("y1", a.y);
    line.setAttribute("x2", b.x); line.setAttribute("y2", b.y);
    line.setAttribute("class", "edge-l");
    line.setAttribute("stroke", edgeColor[e.type] || "#4a5275");
    if (e.type === "SUPERSEDES") line.setAttribute("stroke-dasharray", "5 4");
    if (e.type === "CONTRADICTS" && !e.resolved) line.setAttribute("stroke-width", "2.6");
    if (e.type === "CONTRADICTS" && e.resolved) line.setAttribute("stroke-dasharray", "2 3");
    svg.appendChild(line);
  }
  for (const n of nodes) {
    const g = document.createElementNS(NS, "g");
    g.setAttribute("class", "node");
    const c = document.createElementNS(NS, "circle");
    const r = n.type === "Fact" ? 13 : n.type === "Episode" ? 10 : 8;
    c.setAttribute("cx", n.x); c.setAttribute("cy", n.y); c.setAttribute("r", r);
    c.setAttribute("fill", color[n.type]);
    if (n.type === "Fact" && n.status && n.status !== "current") {
      c.setAttribute("fill", "#3a4062");
      c.setAttribute("stroke", color[n.type]); c.setAttribute("stroke-width", 2);
    }
    g.appendChild(c);
    const t = document.createElementNS(NS, "text");
    t.setAttribute("x", n.x + r + 5); t.setAttribute("y", n.y + 4);
    t.textContent = (n.label || n.id).slice(0, 42);
    g.appendChild(t);
    g.addEventListener("click", () => select(n));
    svg.appendChild(g);
  }
}

// ---- interaction ---------------------------------------------------------
let dragging = null;
svg.addEventListener("mousedown", ev => {
  const el = ev.target.closest(".node");
  if (!el) return;
  const idx = [...svg.querySelectorAll(".node")].indexOf(el);
  dragging = nodes[idx];
});
svg.addEventListener("mousemove", ev => {
  if (!dragging) return;
  const rect = svg.getBoundingClientRect();
  dragging.x = ev.clientX - rect.left; dragging.y = ev.clientY - rect.top;
  dragging.vx = 0; dragging.vy = 0;
});
svg.addEventListener("mouseup", () => {
  dragging = null;
  settleFrames = 150;  // let the layout re-settle after a drag
});

let zoom = 1;
svg.addEventListener("wheel", ev => {
  ev.preventDefault();
  zoom = Math.max(0.4, Math.min(2.5, zoom * (ev.deltaY < 0 ? 1.1 : 0.9)));
  svg.style.transform = `scale(${zoom})`;
}, { passive: false });

function select(n) {
  const out = [];
  out.push(`<div class="detail"><span class="chip ${n.type.toLowerCase()}">${n.type}</span>` +
    (n.status ? `<span class="chip ${n.status}">${n.status}</span>` : "") +
    `<div class="t" style="margin-top:8px">${esc(n.text || n.label || n.id)}</div>`);
  if (n.slot) out.push(`<div class="m">slot: ${esc(n.slot)}</div>`);
  if (n.kind) out.push(`<div class="m">kind: ${esc(n.kind)}</div>`);
  if (n.recordedAt) out.push(`<div class="m">recorded: ${esc(n.recordedAt)}</div>`);
  if (n.at) out.push(`<div class="m">at: ${esc(n.at)}</div>`);
  out.push(`<div class="m">id: ${esc(n.id)}</div></div>`);

  const prov = edges.filter(e => e.target === n.id || e.source === n.id);
  out.push("<h2>Connections</h2>");
  if (!prov.length) out.push('<div class="empty">none</div>');
  for (const e of prov) {
    const other = byId[e.source === n.id ? e.target : e.source];
    const dir = e.source === n.id ? "→" : "←";
    const label = e.type === "CONTRADICTS"
      ? `CONTRADICTS ${e.resolved ? "(resolved)" : "(OPEN)"}`
      : e.type;
    out.push(`<div class="detail"><span class="chip ${other.type.toLowerCase()}">${other.type}</span>` +
      `<span class="m">${dir} ${esc(label)}</span>` +
      `<div class="t">${esc(other.text || other.label || other.id)}</div>` +
      `<div class="m">${esc(other.id)}</div></div>`);
  }
  document.getElementById("detail").innerHTML = out.join("");
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

let settleFrames = 150;
function frame() {
  if (!dragging && settleFrames > 0) {
    step();
    settleFrames--;
  }
  draw();
  requestAnimationFrame(frame);
}
frame();
</script>
</body>
</html>
"""


def render_explorer(graph: dict[str, Any]) -> str:
    """Return the self-contained explorer page for an exported graph."""
    data = json.dumps(graph, default=str)
    return HTML_TEMPLATE.replace("__GRAPH_JSON__", data)
