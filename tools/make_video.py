"""Make the Recall demo video.

Renders the `recall demo` walkthrough as terminal-style frames (with the
demo's ANSI colors), a real headless-Chrome screenshot of the graph explorer,
and title/end cards — then assembles them with ffmpeg.

Usage:
    python tools/make_video.py           # writes out/recall-demo.mp4
"""

from __future__ import annotations

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
BOLD = (255, 255, 255)

MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
MONO_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
SANS_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

ANSI = re.compile(r"\x1b\[([0-9;]*)m")
CODE_MAP = {
    "0": TEXT,
    "1": BOLD,
    "2": DIM,
    "32": GREEN,
    "33": YELLOW,
    "31": RED,
    "36": CYAN,
}


def fonts():
    return {
        "mono": ImageFont.truetype(MONO, 22),
        "mono_b": ImageFont.truetype(MONO_BOLD, 22),
        "title": ImageFont.truetype(SANS_BOLD, 64),
        "sub": ImageFont.truetype(SANS, 30),
        "h2": ImageFont.truetype(SANS_BOLD, 40),
    }


def parse_ansi(line: str) -> list[tuple[str, tuple]]:
    """Split a line into (text, color) runs."""
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
            bold = False
            color = DIM
        elif code in CODE_MAP:
            bold = False
            color = CODE_MAP[code]
        pos = m.end()
    if pos < len(line):
        runs.append((line[pos:], BOLD if bold else color))
    return runs


def terminal_frame(lines: list[str], f) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    # window chrome
    d.rounded_rectangle([40, 30, W - 40, H - 30], 18, fill=PANEL)
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([72 + i * 34, 56, 94 + i * 34, 78], fill=c)
    d.text((W // 2 - 130, 56), "recall — demo", font=f["mono"], fill=DIM)

    y = 118
    max_lines = 30
    for line in lines[-max_lines:]:
        x = 84
        for text, color in parse_ansi(line):
            d.text((x, y), text, font=f["mono_b"] if color == BOLD else f["mono"], fill=color)
            x += d.textlength(text, font=f["mono"])
        y += 26
    return img


def title_card(f) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([W // 2 - 90, 200, W // 2 + 90, 214], 7, fill=GREEN)
    d.text((W // 2, 280), "Recall", font=f["title"], fill=TEXT, anchor="mm")
    d.text(
        (W // 2, 380),
        "agent memory that argues with itself",
        font=f["sub"],
        fill=GREEN,
        anchor="mm",
    )
    d.text(
        (W // 2, 470),
        "provenance  ·  supersession  ·  contradiction detection",
        font=f["sub"],
        fill=DIM,
        anchor="mm",
    )
    d.text((W // 2, 620), "built on Relate — the embedded graph database",
           font=f["sub"], fill=CYAN, anchor="mm")
    d.text((W // 2, 700), "github.com/omkarjainak/recall", font=f["sub"], fill=DIM, anchor="mm")
    return img


def end_card(f) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((W // 2, 300), "Every fact kept its evidence.", font=f["h2"], fill=TEXT, anchor="mm")
    d.text((W // 2, 380), "Nothing was ever deleted.", font=f["h2"], fill=TEXT, anchor="mm")
    d.text((W // 2, 460), "Two conflicting reports were found and settled.",
           font=f["h2"], fill=GREEN, anchor="mm")
    d.text((W // 2, 620), "github.com/omkarjainak/recall", font=f["sub"], fill=CYAN, anchor="mm")
    d.text((W // 2, 680), "Relate by Neo4j Hackathon  ·  MIT", font=f["sub"], fill=DIM, anchor="mm")
    return img


def run_demo_lines() -> list[tuple[str, list[str]]]:
    """Run the demo and a few CLI commands; return (command, output lines)."""
    env = {**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")}
    acts = []

    def run(cmd_args, prompt):
        proc = subprocess.run(
            [sys.executable, "-m", "recall.cli", *cmd_args],
            capture_output=True, text=True, env=env, cwd=ROOT,
        )
        out = proc.stdout.splitlines()
        if not out:
            raise SystemExit(f"{prompt} produced no output:\n{proc.stderr}")
        acts.append((prompt, out))

    run(["--db", ":memory:", "demo"], "$ recall demo")
    run(
        ["--db", "data/demo-memory", "recall", "Where does Priya live?", "-k", "2"],
        '$ recall recall "Where does Priya live?"',
    )
    run(["--db", "data/demo-memory", "why", "f3"], "$ recall why f3")
    run(
        ["--db", "data/demo-memory", "timeline", "--slot", "relate/release-reviewer", "--on", "2026-03-15"],
        "$ recall timeline --slot relate/release-reviewer --on 2026-03-15",
    )
    return acts


def explorer_frame(path: Path, f) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    if path.exists():
        shot = Image.open(path).convert("RGB")
        shot = shot.resize((W, int(shot.height * W / shot.width)))
        img.paste(shot.crop((0, 0, W, H)), (0, 0))
        return img
    d = ImageDraw.Draw(img)
    d.text((W // 2, H // 2), "(explorer screenshot missing)", font=f["sub"], fill=DIM, anchor="mm")
    return img


def main() -> None:
    FRAMES.mkdir(parents=True, exist_ok=True)
    f = fonts()
    manifest = []  # (frame_path, seconds)

    def save(img, seconds, name):
        p = FRAMES / name
        img.save(p)
        manifest.append((p, seconds))

    save(title_card(f), 4.0, "000-title.png")

    acts = run_demo_lines()
    shown: list[str] = []
    step = 0
    for prompt, lines in acts:
        shown.append("")
        shown.append(prompt)
        # hold on the prompt so the viewer reads the command
        save(terminal_frame(shown, f), 1.2, f"{step:03d}-prompt.png")
        step += 1
        buf: list[str] = []
        for line in lines:
            buf.append(line)
            if len(buf) >= 2:
                shown.extend(buf)
                buf = []
                save(terminal_frame(shown, f), 1.15, f"{step:03d}-term.png")
                step += 1
        if buf:
            shown.extend(buf)
            save(terminal_frame(shown, f), 1.7, f"{step:03d}-term.png")
            step += 1
        # dwell at the end of each act
        save(terminal_frame(shown, f), 1.5, f"{step:03d}-dwell.png")
        step += 1

    shot = OUT / "explorer.png"
    save(explorer_frame(shot, f), 8.0, "998-explorer.png")
    save(end_card(f), 6.0, "999-end.png")

    total = sum(s for _, s in manifest)
    print(f"{len(manifest)} frames, {total:.0f}s")

    # ffmpeg assembly
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
        check=True,
        capture_output=True,
    )
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
