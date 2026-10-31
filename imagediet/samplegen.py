"""Build the synthetic site used by the UI and the tests. No real website is involved."""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw

MAX_BYTES = 200 * 1024


def _scene(width: int, height: int, seed: int) -> Image.Image:
    rng = random.Random(seed)
    image = Image.new("RGB", (width, height), (24, 40, 62))
    draw = ImageDraw.Draw(image)
    for _ in range(500):
        x = rng.randrange(width)
        y = rng.randrange(height)
        w = rng.randrange(30, 280)
        h = rng.randrange(20, 180)
        color = (rng.randrange(40, 240), rng.randrange(60, 230), rng.randrange(40, 220))
        draw.ellipse([x, y, x + w, y + h], fill=color)
    return image


def _speckle(image: Image.Image, seed: int, count: int) -> Image.Image:
    rng = random.Random(seed)
    copy = image.copy()
    pixels = copy.load()
    width, height = copy.size
    for _ in range(count):
        pixels[rng.randrange(width), rng.randrange(height)] = (
            rng.randrange(256),
            rng.randrange(256),
            rng.randrange(256),
        )
    return copy


def _gradient(width: int, height: int) -> Image.Image:
    small = Image.new("RGB", (180, 100))
    pixels = small.load()
    for y in range(100):
        for x in range(180):
            pixels[x, y] = ((x * 17) // 180, (y * 40) // 100 + 80, 160)
    return small.resize((width, height), Image.Resampling.BICUBIC)


INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>ImageDiet synthetic site</title>
  <link rel="stylesheet" href="site.css">
</head>
<body>
  <h1>Synthetic gallery</h1>
  <p>This page is generated locally for ImageDiet. It is not a real website.</p>
  <img class="hero" src="images/big.jpg" alt="Oversized JPEG" width="800" height="560">
  <img src="images/big.png" alt="Oversized PNG" width="640" height="448">
  <img src="images/small.jpg" alt="Already small" width="80" height="60">
  <img class="thumb" src="images/wide.jpg" alt="High resolution, displayed small">
  <p><a href="about.html">About this synthetic page</a></p>
</body>
</html>
"""

ABOUT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>About the synthetic site</title>
  <link rel="stylesheet" href="site.css">
</head>
<body>
  <h1>About</h1>
  <p>Still synthetic. Still not a real website.</p>
  <img src="images/small.jpg" alt="Small again" width="80" height="60">
  <p><a href="index.html">Back</a></p>
</body>
</html>
"""

SITE_CSS = """body { font-family: Georgia, serif; margin: 24px; background: #f4f7f8; color: #14202b; }
img { border: 0; }
.hero { width: 100%; max-width: 800px; height: auto; }
.thumb { width: 200px; height: auto; }
"""

README = """Synthetic ImageDiet fixture.

big.jpg and big.png are over the default 200 KB threshold.
wide.jpg is under that threshold and is displayed at 200px, far below its pixel width.
small.jpg is already small and has width and height attributes.
Nothing here is a live site.
"""


def build_site(dest: Path) -> Path:
    """Write HTML, CSS, and generated images. Raises if the size invariants slip."""
    dest.mkdir(parents=True, exist_ok=True)
    images = dest / "images"
    images.mkdir(exist_ok=True)
    scene = _scene(2000, 1400, seed=1)
    big_jpg = images / "big.jpg"
    scene.save(big_jpg, format="JPEG", quality=97, subsampling=0)
    big_png = images / "big.png"
    _speckle(scene, seed=2, count=80_000).save(big_png, format="PNG", compress_level=3)
    wide = images / "wide.jpg"
    _gradient(1800, 1000).save(wide, format="JPEG", quality=75)
    small = images / "small.jpg"
    Image.new("RGB", (80, 60), (20, 120, 90)).save(small, format="JPEG", quality=70)
    (dest / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (dest / "about.html").write_text(ABOUT_HTML, encoding="utf-8")
    (dest / "site.css").write_text(SITE_CSS, encoding="utf-8")
    (dest / "README.txt").write_text(README, encoding="utf-8")
    (dest / "notes.txt").write_text("do not delete\n", encoding="utf-8")
    sizes = {path.name: path.stat().st_size for path in images.iterdir()}
    if sizes["big.jpg"] <= MAX_BYTES or sizes["big.png"] <= MAX_BYTES:
        raise RuntimeError(f"synthetic oversized images are too small: {sizes}")
    if sizes["wide.jpg"] > MAX_BYTES or sizes["small.jpg"] > MAX_BYTES:
        raise RuntimeError(f"synthetic small images are too large: {sizes}")
    return dest
