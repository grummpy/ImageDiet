"""Write a WebP next to an original. Never delete or overwrite the original."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError

CONVERTIBLE = {".jpg", ".jpeg", ".png"}


class ConvertError(Exception):
    pass


def suffix(path: str | Path) -> str:
    return Path(str(path).split("?", 1)[0].split("#", 1)[0]).suffix.lower()


def is_convertible(path: str | Path) -> bool:
    return suffix(path) in CONVERTIBLE


def peek_size(data: bytes) -> tuple[int, int] | None:
    try:
        with Image.open(BytesIO(data)) as image:
            return int(image.size[0]), int(image.size[1])
    except (UnidentifiedImageError, OSError, ValueError):
        return None


def _clean(image: Image.Image, target_width: int | None) -> Image.Image:
    if image.mode == "P":
        image = image.convert("RGBA" if "transparency" in image.info else "RGB")
    elif image.mode in {"RGBA", "LA"}:
        image = image.convert("RGBA")
    elif image.mode != "RGB":
        image = image.convert("RGB")
    if target_width and image.width > target_width:
        height = max(1, round(image.height * (target_width / image.width)))
        image = image.resize((target_width, height), Image.Resampling.LANCZOS)
    # frombytes drops EXIF, GPS, and any other metadata Pillow was holding.
    return Image.frombytes(image.mode, image.size, image.tobytes())


def encode_webp(data: bytes, *, quality: int, max_rendered_width: int | None) -> bytes | None:
    """Return WebP bytes, or None when the input is not an image we can encode."""
    try:
        with Image.open(BytesIO(data)) as image:
            image.load()
            target = None
            if max_rendered_width and max_rendered_width > 0:
                target = max(1, int(round(max_rendered_width * 2)))
            clean = _clean(image, target)
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    buffer = BytesIO()
    clean.save(buffer, format="WEBP", quality=int(quality), method=6)
    return buffer.getvalue()


def webp_destination(src: Path) -> Path:
    return src.with_suffix(".webp")


def write_webp_if_smaller(src: Path, payload: bytes, dest: Path | None = None) -> Path | None:
    """Write ``payload`` beside ``src`` when it is strictly smaller.

    Returns the path written, or None when the WebP is not smaller, the
    destination already exists, or the destination is the original file.
    This function never unlinks or opens the original for writing.
    """
    if not payload or len(payload) >= src.stat().st_size:
        return None
    dest = dest or webp_destination(src)
    if dest.resolve() == src.resolve():
        return None
    if dest.exists():
        return None
    dest.write_bytes(payload)
    return dest
