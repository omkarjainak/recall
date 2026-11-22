"""Generate the Devpost thumbnail (3:2, 1500x1000 PNG)."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"

W, H = 1500, 1000
BG = (15, 18, 32)
PANEL = (23, 27, 46)
TEXT = (232, 234, 242)
DIM = (122, 128, 148)
GREEN = (79, 209, 165)
YELLOW = (240, 198, 116)
RED = (255, 107, 107)
CYAN = (110, 168, 254)
PURPLE = (182, 140, 255)

SANS_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def dashed(d, x1, y1, x2, y2, color, width=5, seg=16):
    import math
    dist = math.hypot(x2 - x1, y2 - y1)
    n = max(int(dist / (seg * 1.6)), 1)
    for i in range(n):
        t0, t1 = i / n, (i + 0.55) / n
        d.line([x1 + (x2 - x1) * t0, y1 + (y2 - y1) * t0,
                x1 + (x2 - x1) * t1, y1 + (y2 - y1) * t1], fill=color, width=width)


def main() -> None:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    title = ImageFont.truetype(SANS_BOLD, 130)
    tag = ImageFont.truetype(SANS, 40)
    chip = ImageFont.truetype(SANS_BOLD, 30)
    small = ImageFont.truetype(SANS, 30)
    small_b = ImageFont.truetype(SANS_BOLD, 30)
    label = ImageFont.truetype(SANS_BOLD, 26)

    # soft glow behind the graph side
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([880, 100, 1520, 760], fill=(26, 44, 66))
    glow = glow.filter(ImageFilter.GaussianBlur(70))
    img.paste(Image.blend(img, glow, 0.55), (0, 0))
    d = ImageDraw.Draw(img)

    # ---- left: brand -------------------------------------------------
    d.rounded_rectangle([90, 150, 130, 420], 8, fill=GREEN)
    d.text((180, 190), "Recall", font=title, fill=TEXT)
    d.text((180, 360), "agent memory that argues", font=tag, fill=GREEN)
    d.text((180, 415), "with itself", font=tag, fill=GREEN)
    d.text((180, 530), "provenance · supersession ·", font=small, fill=DIM)
    d.text((180, 572), "contradiction detection", font=small, fill=DIM)

    chips = [("Relate", CYAN), ("agent memory", GREEN), ("MCP", PURPLE)]
    x = 180
    for text, color in chips:
        w = d.textlength(text, font=chip)
        d.rounded_rectangle([x, 650, x + w + 36, 708], 14, fill=PANEL, outline=color, width=2)
        d.text((x + 18, 662), text, font=chip, fill=color)
        x += w + 52

    d.text((180, 860), "built on Relate — the embedded graph database",
           font=small_b, fill=TEXT)

    # ---- right: mini memory graph ------------------------------------
    def node(x, y, r, color, ring=False):
        if ring:
            d.ellipse([x - r, y - r, x + r, y + r], fill=(58, 64, 98), outline=color, width=5)
        else:
            d.ellipse([x - r, y - r, x + r, y + r], fill=color)

    e1 = (1120, 190)   # episode (evidence)
    f1 = (980, 400)    # fact (current)
    f2 = (1330, 400)   # fact (conflicting)
    f0 = (1330, 650)   # fact (older, superseded)
    en = (1130, 700)   # entity

    # edges first (routed clear of the labels below the nodes)
    dashed(d, f1[0] + 40, f1[1] - 30, e1[0] - 30, e1[1] + 30, CYAN)   # FROM
    dashed(d, f2[0] - 40, f2[1] - 30, e1[0] + 30, e1[1] + 30, CYAN)
    d.line([f1[0] + 50, f1[1] + 32, en[0] - 8, en[1] - 40], fill=PURPLE, width=5)  # ABOUT
    d.line([f2[0] - 40, f2[1] + 45, en[0] + 30, en[1] - 38], fill=PURPLE, width=5)
    dashed(d, f1[0] + 52, f1[1] + 20, f0[0] - 42, f0[1] - 38, YELLOW)  # SUPERSEDES
    d.line([f1[0] + 62, f1[1], f2[0] - 62, f2[1]], fill=RED, width=8)  # CONTRADICTS

    node(*e1, 42, CYAN)
    node(*f1, 52, GREEN, ring=True)
    node(*f2, 52, GREEN)
    node(*f0, 46, YELLOW, ring=True)
    node(*en, 40, PURPLE)

    # labels
    d.text((e1[0] + 60, e1[1] - 18), "evidence", font=small_b, fill=CYAN)
    d.text((f1[0], f1[1] + 74), "Berlin", font=small_b, fill=TEXT, anchor="ma")
    d.text((f2[0], f2[1] + 74), "Paris", font=small_b, fill=DIM, anchor="ma")
    d.text((f0[0], f0[1] + 72), "superseded", font=small_b, fill=YELLOW, anchor="ma")
    d.text((en[0], en[1] + 66), "entity", font=small_b, fill=PURPLE, anchor="ma")

    # the clash
    cx = (f1[0] + f2[0]) // 2
    d.text((cx, f1[1] - 96), "≠", font=ImageFont.truetype(SANS_BOLD, 92), fill=RED, anchor="mm")
    d.text((cx, f1[1] + 38), "CONTRADICTS", font=label, fill=RED, anchor="mm")

    d.text((cx, 838), "“Priya lives in …” — two claims, one truth",
           font=label, fill=DIM, anchor="mm")

    OUT.mkdir(exist_ok=True)
    img.save(OUT / "thumbnail.png")
    print("wrote", OUT / "thumbnail.png")


if __name__ == "__main__":
    main()
