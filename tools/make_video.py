"""Make the Recall demo video (v2 — structured story with chapter cards).

Storyboard (≈105 s, silent-watchable — every beat carries its own caption):

  1. Title
  2. The problem          — agents forget, and remember confidently wrong
  3. The model            — memory is a graph: what connects to what
  4-8. The demo story     — remember → supersede → contradict → resolve →
                           why + the true-vs-believed timeline
  9. The explorer         — the whole memory as a graph
 10. Built on Relate      — how the project uses it
 11. End card             — repo + how to try it

Frames are rendered with Pillow and assembled with ffmpeg. Usage:
    python tools/make_video.py           # writes out/recall-demo.mp4
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
FRAMES = OUT / "frames"

W, H = 1600, 900
BG = (15, 18, 32)
PANEL = (23, 27, 46)
TEXT = (232, 234, 242)
DIM = (122, 128, 148)
GREEN = (79, 209, 165)
YELLOW = (240, 198, 116)
RED = (255, 107, 107)
CYAN = (110, 168, 254)
PURPLE = (182, 140, 255)
BOLD = (255, 255, 255)

MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
MONO_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
SANS_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

ANSI = re.compile(r"\x1b\[([0-9;]*)m")
CODE_MAP = {"0": TEXT, "1": BOLD, "2": DIM, "32": GREEN, "33": YELLOW, "31": RED, "36": CYAN}


def fonts():
    return {
        "mono": ImageFont.truetype(MONO, 21),
        "mono_b": ImageFont.truetype(MONO_BOLD, 21),
        "cap": ImageFont.truetype(SANS_BOLD, 26),
        "title": ImageFont.truetype(SANS_BOLD, 72),
        "h2": ImageFont.truetype(SANS_BOLD, 44),
        "sub": ImageFont.truetype(SANS, 28),
        "small": ImageFont.truetype(SANS, 23),
        "small_b": ImageFont.truetype(SANS_BOLD, 23),
    }


def parse_ansi(line: str) -> list[tuple[str, tuple]]:
    runs = []
    pos = 0
    color = TEXT
    bold = False
    for m in ANSI.finditer(line):
        if m.start() > pos:
            runs.append((line[pos : m.start()], BOLD if bold else color))
        code = m.group(1) or "0"
        if code == "0":
            color, bold = TEXT, False
        elif code == "1":
            bold = True
        elif code == "2":
            bold, color = False, DIM
        elif code in CODE_MAP:
            bold, color = False, CODE_MAP[code]
        pos = m.end()
    if pos < len(line):
        runs.append((line[pos:], BOLD if bold else color))
    return runs


# ---------------------------------------------------------------- frames
def terminal_frame(lines: list[str], caption: str, f) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([40, 30, W - 40, H - 30], 18, fill=PANEL)
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([72 + i * 34, 56, 94 + i * 34, 78], fill=c)
    d.text((W // 2, 67), "recall", font=f["mono"], fill=DIM, anchor="mm")

    # caption bar — why this beat matters, readable without sound
    if caption:
        d.rounded_rectangle([84, 104, W - 84, 152], 10, fill=(30, 36, 60))
        d.ellipse([102, 118, 122, 138], fill=GREEN)
        d.text((138, 117), caption, font=f["cap"], fill=TEXT)

    y = 176
    for line in lines[-28:]:
        x = 84
        for text, color in parse_ansi(line):
            d.text((x, y), text, font=f["mono_b"] if color == BOLD else f["mono"], fill=color)
            x += d.textlength(text, font=f["mono"])
        y += 25
    return img


def card(title: str, bullets: list[tuple[str, tuple]], f, *, sub: str = "") -> Image.Image:
    """A full-screen chapter card: title, optional sub, colored bullets."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([W // 2 - 70, 150, W // 2 + 70, 162], 6, fill=GREEN)
    d.text((W // 2, 250), title, font=f["h2"], fill=TEXT, anchor="mm")
    if sub:
        d.text((W // 2, 330), sub, font=f["sub"], fill=DIM, anchor="mm")
    y = 430 if sub else 380
    for text, color in bullets:
        d.ellipse([260, y + 12, 276, y + 28], fill=color)
        d.text((310, y), text, font=f["sub"], fill=TEXT)
        y += 62
    return img


def title_card(f) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((W // 2, 240), "Recall", font=f["title"], fill=TEXT, anchor="mm")
    d.text((W // 2, 330), "agent memory that argues with itself",
           font=f["sub"], fill=GREEN, anchor="mm")
    d.text((W // 2, 420), "provenance  ·  supersession  ·  contradiction detection",
           font=f["sub"], fill=DIM, anchor="mm")
    d.text((W // 2, 560), "built on Relate — the embedded graph database",
           font=f["small"], fill=CYAN, anchor="mm")
    d.text((W // 2, 620), "a two-minute tour: the problem  →  the model  →  the demo",
           font=f["small"], fill=DIM, anchor="mm")
    d.text((W // 2, 720), "github.com/omkarjainak/recall", font=f["small"], fill=DIM, anchor="mm")
    return img


def model_card(f) -> Image.Image:
    """'What connects to what': the memory graph in one picture."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((W // 2, 110), "The memory model — a graph, not a log",
           font=f["h2"], fill=TEXT, anchor="mm")

    def box(x, y, w, h, label, sub, color):
        d.rounded_rectangle([x, y, x + w, y + h], 14, fill=PANEL, outline=color, width=3)
        d.text((x + w / 2, y + h / 2 - 12), label, font=f["small_b"], fill=color, anchor="mm")
        d.text((x + w / 2, y + h / 2 + 18), sub, font=f["small"], fill=DIM, anchor="mm")

    def arrow(x1, y1, x2, y2, label, color, dash=False, above=True):
        import math

        if dash:
            for i in range(0, 11):
                t0, t1 = i / 11, (i + 0.55) / 11
                d.line([x1 + (x2 - x1) * t0, y1 + (y2 - y1) * t0,
                        x1 + (x2 - x1) * t1, y1 + (y2 - y1) * t1], fill=color, width=3)
        else:
            d.line([x1, y1, x2, y2], fill=color, width=3)
        ang = math.atan2(y2 - y1, x2 - x1)
        for da in (2.6, -2.6):
            d.line([x2, y2, x2 + 16 * math.cos(ang + da), y2 + 16 * math.sin(ang + da)],
                   fill=color, width=3)
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2 - 16 if above else (y1 + y2) / 2 + 6
        d.text((mx, my), label, font=f["small_b"], fill=color, anchor="mm")

    def ledge(x_box, y1, y2, x_out, label, color):
        """L-shaped dashed edge running outside the boxes, label on the outside."""
        for i in range(0, 11):
            t0, t1 = i / 11, (i + 0.55) / 11
            d.line([x_box + (x_out - x_box) * t0, y1, x_box + (x_out - x_box) * t1, y1],
                   fill=color, width=3)
            d.line([x_box + (x_out - x_box) * t0, y2, x_box + (x_out - x_box) * t1, y2],
                   fill=color, width=3)
        for i in range(0, 11):
            t0, t1 = i / 11, (i + 0.55) / 11
            d.line([x_out, y1 + (y2 - y1) * t0, x_out, y1 + (y2 - y1) * t1], fill=color, width=3)
        d.polygon([(x_box, y2), (x_box - 16, y2 - 9), (x_box - 16, y2 + 9)], fill=color)
        side = -1 if x_out < x_box else 1
        d.text((x_out + 12 * side, (y1 + y2) / 2), label,
               font=f["small_b"], fill=color, anchor="lm" if side > 0 else "rm")

    # left cluster: Episode / Fact / Entity
    box(230, 230, 320, 96, "Episode", "what was seen, and when", CYAN)
    box(230, 470, 320, 96, "Fact", "one claim · two time axes", GREEN)
    box(230, 700, 320, 96, "Entity", "what memory is about", PURPLE)
    arrow(380, 470, 380, 336, "FROM  (evidence)", CYAN)
    arrow(440, 566, 440, 700, "ABOUT", PURPLE, above=False)
    ledge(230, 278, 748, 155, "MENTIONS", DIM)

    # right: supersession and contradiction between facts
    box(880, 230, 360, 96, "Fact  (newer)", "the current truth", GREEN)
    box(880, 470, 360, 96, "Fact  (older)", "superseded · never deleted", YELLOW)
    box(880, 700, 360, 96, "Fact  (conflicting)", "can't both be true", RED)
    arrow(1060, 326, 1060, 470, "SUPERSEDES", YELLOW, above=False)
    ledge(1240, 278, 748, 1310, "CONTRADICTS", RED)

    d.text((W // 2, 850),
           "every answer carries its evidence — and its conflicts",
           font=f["small"], fill=DIM, anchor="mm")
    return img


def relate_card(f) -> Image.Image:
    return card(
        "Built on Relate — the embedded graph database",
        [
            ("one .rl file — the memory travels inside the agent's app", TEXT),
            ("runs in-process (relate-db Python) — no server, no signup", TEXT),
            ("vector + full-text hybrid search, fused by reciprocal rank", CYAN),
            ("typed vector() properties, constraints, transactions, Cypher 25", CYAN),
            ("34 automated tests, including MCP over stdio", GREEN),
        ],
        f,
        sub="github.com/omkarjainak/recall",
    )


def end_card(f) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((W // 2, 300), "Every fact kept its evidence.", font=f["h2"], fill=TEXT, anchor="mm")
    d.text((W // 2, 380), "Nothing was ever deleted.", font=f["h2"], fill=TEXT, anchor="mm")
    d.text((W // 2, 460), "Two conflicting reports were found and settled.",
           font=f["h2"], fill=GREEN, anchor="mm")
    d.text((W // 2, 610), "try it:  pip install -e \".[mcp]\"  &&  recall demo",
           font=f["sub"], fill=CYAN, anchor="mm")
    d.text((W // 2, 680), "github.com/omkarjainak/recall", font=f["sub"], fill=DIM, anchor="mm")
    d.text((W // 2, 730), "Relate by Neo4j Hackathon  ·  MIT",
           font=f["small"], fill=DIM, anchor="mm")
    return img


def contradiction_card(f) -> Image.Image:
    """The centerpiece: two facts that cannot both be true."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((W // 2, 120), "Contradiction — detected at write time",
           font=f["h2"], fill=TEXT, anchor="mm")

    def fact_panel(x, y, w, h, text, meta, evidence, border):
        d.rounded_rectangle([x, y, x + w, y + h], 16, fill=PANEL, outline=border, width=3)
        d.text((x + w / 2, y + 62), text, font=f["cap"], fill=TEXT, anchor="mm")
        d.text((x + w / 2, y + 118), meta, font=f["small"], fill=DIM, anchor="mm")
        d.text((x + w / 2, y + 162), evidence, font=f["small"], fill=border, anchor="mm")

    fact_panel(120, 250, 620, 230,
               "“Priya lives in Paris.”",
               "recorded 2026-04-01  ·  slot priya/residence",
               "evidence: Priya mentioned her Paris apartment", GREEN)
    fact_panel(860, 250, 620, 230,
               "“Priya lives in Berlin.”",
               "recorded 2026-04-02  ·  slot priya/relocation",
               "evidence: Dan said Priya relocated to Berlin", GREEN)

    # red clash symbol between the panels
    cx = W // 2
    d.line([740, 365, 860, 365], fill=RED, width=4)
    d.text((800, 330), "≠", font=ImageFont.truetype(SANS_BOLD, 84), fill=RED, anchor="mm")
    d.rounded_rectangle([cx - 190, 520, cx + 190, 596], 14, fill=(56, 24, 34), outline=RED, width=3)
    d.text((cx, 544), "CONTRADICTS", font=f["cap"], fill=RED, anchor="mm")
    d.text((cx, 576), "claim score 0.50", font=f["small"], fill=RED, anchor="mm")

    d.text((cx, 668), "same frame (“lives in”), different claim — they cannot both be true",
           font=f["sub"], fill=TEXT, anchor="mm")
    d.text((cx, 728), "both facts stay current, but every answer that uses them",
           font=f["small"], fill=DIM, anchor="mm")
    d.text((cx, 766), "is flagged DISPUTED until a decision settles it",
           font=f["small"], fill=YELLOW, anchor="mm")
    return img


def resolution_card(f) -> Image.Image:
    """The decision: one kept, one retracted, the reasoning recorded."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((W // 2, 120), "A decision settles it — recorded as memory",
           font=f["h2"], fill=TEXT, anchor="mm")

    def verdict(x, y, w, h, badge, badge_color, text, meta):
        d.rounded_rectangle([x, y, x + w, y + h], 16, fill=PANEL, outline=badge_color, width=3)
        d.rounded_rectangle([x + 28, y + 28, x + 168, y + 72], 10, fill=badge_color)
        d.text((x + 98, y + 50), badge, font=f["small_b"], fill=BG, anchor="mm")
        d.text((x + w / 2, y + 128), text, font=f["cap"], fill=TEXT, anchor="mm")
        d.text((x + w / 2, y + 178), meta, font=f["small"], fill=DIM, anchor="mm")

    verdict(120, 230, 620, 230, "KEEP", GREEN,
            "“Priya lives in Berlin.”",
            "stays current — the answer the agent gives")
    verdict(860, 230, 620, 230, "RETRACT", RED,
            "“Priya lives in Paris.”",
            "closed as retracted — never deleted")

    # the decision episode
    d.rounded_rectangle([280, 530, W - 280, 650], 16, fill=(24, 32, 52), outline=CYAN, width=3)
    d.text((W // 2, 562), "decision episode recorded", font=f["small_b"], fill=CYAN, anchor="mm")
    d.text((W // 2, 600), "“HR confirmed the Berlin move in April.”",
           font=f["sub"], fill=TEXT, anchor="mm")
    d.text((W // 2, 628), "kind: decision  ·  so why() can still explain the outcome",
           font=f["small"], fill=DIM, anchor="mm")

    d.text((W // 2, 720), "contradiction: 1 found  ·  1 resolved  ·  0 open",
           font=f["sub"], fill=GREEN, anchor="mm")
    d.text((W // 2, 776), "nothing was deleted — resolution is a state change, not an erase",
           font=f["small"], fill=DIM, anchor="mm")
    return img


def explorer_frame(path: Path, f) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    if path.exists():
        shot = Image.open(path).convert("RGB")
        # zoom into the graph area, keeping 16:9 (no distortion)
        shot = shot.crop((250, 115, 1450, 790)).resize((W, H))
        img.paste(shot, (0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([150, 812, W - 150, 864], 12, fill=(30, 36, 60))
        d.ellipse([172, 826, 192, 846], fill=GREEN)
        d.text((208, 826),
               "red = contradiction (resolved)  ·  dashed = superseded  ·  grayed = closed — nothing deleted",
               font=f["small_b"], fill=TEXT)
        return img
    d = ImageDraw.Draw(img)
    d.text((W // 2, H // 2), "(explorer screenshot missing)", font=f["sub"], fill=DIM, anchor="mm")
    return img


# ---------------------------------------------------------------- content
def demo_acts() -> list[tuple[str, list[str]]]:
    """Run `recall demo` and split it into captioned acts."""
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    proc = subprocess.run(
        [sys.executable, "-m", "recall.cli", "--db", ":memory:", "demo"],
        capture_output=True, text=True, env=env, cwd=ROOT,
    )
    lines = proc.stdout.splitlines()
    if not lines:
        raise SystemExit(f"demo produced no output:\n{proc.stderr}")

    header = re.compile(r"\x1b\[1m\x1b\[36m━━ (\d+)\.")
    sections: dict[int, list[str]] = {}
    current = 0
    sections[0] = []
    for line in lines:
        m = header.search(line)
        if m:
            current = int(m.group(1))
            sections[current] = []
        sections[current].append(line)

    captions = {
        1: "Remember: every fact is stored with the episode that proves it",
        2: "A newer truth SUPERSEDES the old one — history is kept, never deleted",
        3: "Same frame, different claim — the contradiction is detected at write time",
        4: "Answers come with evidence — and disputed facts are flagged",
        5: "A decision settles it: one kept, one retracted, the reasoning recorded",
        6: "why? — every memory quotes its evidence and its history",
        7: "The wow: what was TRUE vs what the agent BELIEVED on that date",
        8: "One embedded graph — no server, no signup",
    }
    # section 8 prints a JSON blob; the video shows its meaning instead
    stats_summary = [
        "  facts:      3 current  ·  1 superseded  ·  1 retracted  ·  0 deleted",
        "  evidence:   6 episodes  ·  5 entities  ·  4 slots",
        "  conflicts:  1 found  ·  1 resolved  ·  0 open",
    ]
    acts = []
    for n in sorted(sections):
        if n == 0 or not sections[n]:
            continue
        lines = sections[n]
        if n == 8:
            head = [l for l in lines if "━━" in l]
            tail = [l for l in lines if "Every fact kept" in l]
            lines = head + stats_summary + tail
        acts.append((n, captions.get(n, ""), lines))
    return acts


def main() -> None:
    FRAMES.mkdir(parents=True, exist_ok=True)
    for old in FRAMES.glob("*.png"):
        old.unlink()
    f = fonts()
    manifest: list[tuple[Path, float]] = []

    def save(img, seconds, name):
        p = FRAMES / name
        img.save(p)
        manifest.append((p, seconds))

    # -- storyboard ------------------------------------------------------
    save(title_card(f), 5.0, "000-title.png")

    save(card(
        "The problem",
        [
            ("agents forget everything between sessions", DIM),
            ("and when they do remember, they remember confidently wrong:", DIM),
            ("stale facts linger after the world changes", YELLOW),
            ("conflicting reports sit side by side, unnoticed", RED),
            ("and no memory can answer: why do you know that?", RED),
        ],
        f,
        sub="flat memory: a key, a value, and no way to argue",
    ), 7.0, "001-problem.png")

    save(model_card(f), 8.0, "002-model.png")

    step = 10
    shown: list[str] = ["$ recall demo"]
    first = True
    for n, caption, lines in demo_acts():
        if first:
            save(terminal_frame(shown, caption, f), 1.4, f"{step:03d}-prompt.png")
            step += 1
            first = False
        buf: list[str] = []
        for line in lines:
            buf.append(line)
            if len(buf) >= 2:
                shown.extend(buf)
                buf = []
                save(terminal_frame(shown, caption, f), 1.0, f"{step:03d}-term.png")
                step += 1
        if buf:
            shown.extend(buf)
            save(terminal_frame(shown, caption, f), 1.4, f"{step:03d}-term.png")
            step += 1
        save(terminal_frame(shown, caption, f), 1.3, f"{step:03d}-dwell.png")
        step += 1
        # the two beats that must land get their own visual
        if n == 3:
            save(contradiction_card(f), 8.0, f"{step:03d}-conflict.png")
            step += 1
        if n == 5:
            save(resolution_card(f), 8.0, f"{step:03d}-resolve.png")
            step += 1

    save(explorer_frame(OUT / "explorer.png", f), 8.0, "997-explorer.png")
    save(relate_card(f), 7.0, "998-relate.png")
    save(end_card(f), 6.0, "999-end.png")

    total = sum(s for _, s in manifest)
    print(f"{len(manifest)} frames, {total:.0f}s")

    listing = OUT / "frames.txt"
    with listing.open("w") as fh:
        for p, s in manifest:
            fh.write(f"file '{p}'\nduration {s}\n")
        fh.write(f"file '{manifest[-1][0]}'\n")

    out = OUT / "recall-demo.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
            "-vf", "fps=25,format=yuv420p", "-c:v", "libx264", "-preset", "medium",
            "-movflags", "+faststart", str(out),
        ],
        check=True, capture_output=True,
    )
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
