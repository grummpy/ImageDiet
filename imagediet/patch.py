"""Wrap converted ``<img>`` tags in ``<picture>`` and keep the original ``src``."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

_TOKEN = re.compile(r"<!--.*?-->|<[^>]+>", re.DOTALL)
_ATTR = re.compile(
    r"""([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?""",
    re.IGNORECASE,
)


def webp_url(src: str) -> str | None:
    """Swap a JPEG/PNG URL's extension for ``.webp``. Query and fragment stay."""
    if not src or src.startswith("data:"):
        return None
    path = src
    suffix = ""
    if "#" in path:
        path, frag = path.split("#", 1)
        suffix = "#" + frag + suffix
    if "?" in path:
        path, query = path.split("?", 1)
        suffix = "?" + query + suffix
    pure = PurePosixPath(path)
    ext = pure.suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png"}:
        return None
    return str(pure.with_suffix(".webp")) + suffix


def _attrs(tag: str) -> dict[str, str]:
    body = tag.strip()
    if body.endswith("/>"):
        body = body[:-2]
    elif body.endswith(">"):
        body = body[:-1]
    body = re.sub(r"^<\s*/?\s*[\w:-]+", "", body, count=1)
    found: dict[str, str] = {}
    for match in _ATTR.finditer(body):
        value = match.group(2)
        if value is None:
            value = match.group(3)
        if value is None:
            value = match.group(4) or ""
        found[match.group(1).lower()] = value
    return found


def _escape_attr(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def patch_html(html: str, src_to_webp: dict[str, str]) -> str:
    """Insert a WebP ``<source>`` for each converted image.

    The original ``<img src>`` is left in place so browsers without WebP still
    receive the original file. Tags that are already inside a ``<picture>``
    with a WebP source are left alone. Calling this twice does not nest
    another picture around the same image.
    """
    if not src_to_webp or not html:
        return html

    picture_depth = 0
    picture_has_webp = False

    def replace(match: re.Match[str]) -> str:
        nonlocal picture_depth, picture_has_webp
        token = match.group(0)
        if token.startswith("<!--"):
            return token
        if re.match(r"<\s*/\s*picture\b", token, re.IGNORECASE):
            picture_depth = max(0, picture_depth - 1)
            if picture_depth == 0:
                picture_has_webp = False
            return token
        if re.match(r"<\s*picture\b", token, re.IGNORECASE):
            picture_depth += 1
            picture_has_webp = False
            return token
        if picture_depth and re.match(r"<\s*source\b", token, re.IGNORECASE):
            if re.search(r"image/webp|\.webp\b", token, re.IGNORECASE):
                picture_has_webp = True
            return token
        if not re.match(r"<\s*img\b", token, re.IGNORECASE):
            return token
        src = _attrs(token).get("src")
        if not src or src not in src_to_webp:
            return token
        if picture_depth and picture_has_webp:
            return token
        source = (
            f'<source type="image/webp" srcset="{_escape_attr(src_to_webp[src])}">'
        )
        if picture_depth:
            picture_has_webp = True
            return source + token
        return f"<picture>{source}{token}</picture>"

    return _TOKEN.sub(replace, html)
