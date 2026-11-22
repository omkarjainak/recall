"""Export Devpost gallery images: 3:2 (1500x1000) PNGs from the best frames."""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
FRAMES = OUT / "frames"
GALLERY = OUT / "gallery"

W, H = 1500, 1000
BG = (15, 18, 32)

# frame file -> gallery name
SHOTS = [
    ("002-model.png", "01-memory-model.png"),
    ("023-conflict.png", "02-contradiction.png"),
    ("039-resolve.png", "03-resolution.png"),
    ("024-term.png", "04-demo-terminal.png"),
    ("997-explorer.png", "05-graph-explorer.png"),
    ("998-relate.png", "06-built-on-relate.png"),
]


def letterbox(img: Image.Image) -> Image.Image:
    """Fit a 16:9 frame onto a 3:2 canvas without distortion."""
    img = img.convert("RGB")
    scale = min(W / img.width, H / img.height)
    nw, nh = int(img.width * scale), int(img.height * scale)
    img = img.resize((nw, nh))
    canvas = Image.new("RGB", (W, H), BG)
    canvas.paste(img, ((W - nw) // 2, (H - nh) // 2))
    return canvas


def main() -> None:
    GALLERY.mkdir(exist_ok=True)
    # the hero thumbnail is already 3:2
    hero = Image.open(OUT / "thumbnail.png").convert("RGB")
    hero.save(GALLERY / "00-hero.png")
    print("00-hero.png")

    for src, dst in SHOTS:
        p = FRAMES / src
        if not p.exists():
            print("missing", src)
            continue
        letterbox(Image.open(p)).save(GALLERY / dst)
        print(dst)


if __name__ == "__main__":
    main()
