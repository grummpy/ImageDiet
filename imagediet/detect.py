"""Flag heavy files and images whose pixels dwarf the box CSS actually paints."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from imagediet.config import Settings
from imagediet.convert import encode_webp, is_convertible, peek_size

_LENGTH = re.compile(r"^(-?\d+(?:\.\d+)?)(px|%|vw)?$", re.IGNORECASE)
_COMPOUND = re.compile(
    r"^(?P<tag>[a-zA-Z*][\w-]*)?(?P<id>#[\w-]+)?(?P<classes>(?:\.[\w-]+)*)(?P<rest>.*)$"
)
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")


@dataclass
class Finding:
    src: str
    key: str
    page: str
    old_bytes: int
    new_bytes: int
    intrinsic_w: int | None
    intrinsic_h: int | None
    rendered: dict[str, int | None]
    max_rendered: int | None
    reasons: list[str] = field(default_factory=list)
    action: str = "keep"
    webp_src: str | None = None
    webp_bytes: bytes | None = None
    file_path: str | None = None
    html_src: str | None = None

    def public(self) -> dict:
        before = self.old_bytes
        after = self.new_bytes
        savings = round((before - after) / before * 100, 1) if before else 0.0
        return {
            "src": self.src,
            "key": self.key,
            "page": self.page,
            "old_bytes": before,
            "new_bytes": after,
            "savings_pct": savings,
            "intrinsic_px": [self.intrinsic_w, self.intrinsic_h],
            "rendered_px": self.rendered,
            "max_rendered_px": self.max_rendered,
            "reasons": list(self.reasons),
            "action": self.action,
            "webp_src": self.webp_src,
            "missing_dimensions": "missing_dimensions" in self.reasons,
        }


def length_to_px(value: str | None, viewport: int) -> int | None:
    if not value:
        return None
    text = value.strip().lower()
    if text in {"auto", "inherit", "initial", "unset", "none", "max-content", "min-content"}:
        return None
    match = _LENGTH.fullmatch(text)
    if not match:
        return None
    number = float(match.group(1))
    unit = (match.group(2) or "px").lower()
    if unit == "%":
        return int(round(viewport * number / 100))
    if unit == "vw":
        return int(round(viewport * number / 100))
    return int(round(number))


def media_matches(prelude: str, viewport: int) -> bool:
    text = prelude.lower()
    if "print" in text and "screen" not in text:
        return False
    maxes = [int(item) for item in re.findall(r"max-width\s*:\s*(\d+)\s*px", text)]
    mins = [int(item) for item in re.findall(r"min-width\s*:\s*(\d+)\s*px", text)]
    if any(viewport > limit for limit in maxes):
        return False
    if any(viewport < limit for limit in mins):
        return False
    return True


def applicable_css(css: str, viewport: int) -> str:
    """Keep rules whose ``@media`` block matches ``viewport``. Nested media is flattened."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    out: list[str] = []
    index = 0
    length = len(css)
    while index < length:
        if css.startswith("@media", index):
            brace = css.find("{", index)
            if brace < 0:
                break
            depth = 0
            cursor = brace
            while cursor < length:
                if css[cursor] == "{":
                    depth += 1
                elif css[cursor] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                cursor += 1
            prelude = css[index:brace]
            body = css[brace + 1 : cursor]
            if media_matches(prelude, viewport):
                out.append(applicable_css(body, viewport))
            index = cursor + 1
            continue
        nxt = css.find("@media", index)
        if nxt < 0:
            out.append(css[index:])
            break
        out.append(css[index:nxt])
        index = nxt
    return "".join(out)


def _declarations(block: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for part in block.split(";"):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        found[key.strip().lower()] = value.strip()
    return found


def _compound_matches(selector: str, classes: set[str], element_id: str | None) -> tuple[int, int, int] | None:
    raw = selector.strip()
    if not raw or raw.startswith("@") or raw.startswith(":"):
        return None
    compound = re.split(r"\s*(?:>|\+|~|\s)\s*", raw)[-1].strip()
    if ":" in compound:
        compound = compound.split(":", 1)[0]
    match = _COMPOUND.fullmatch(compound)
    if not match or match.group("rest"):
        return None
    tag = (match.group("tag") or "").lower()
    if tag and tag not in {"img", "*"}:
        return None
    sel_id = (match.group("id") or "").lstrip("#")
    if sel_id and sel_id != (element_id or ""):
        return None
    sel_classes = [item for item in (match.group("classes") or "").split(".") if item]
    if any(item not in classes for item in sel_classes):
        return None
    if not tag and not sel_id and not sel_classes:
        return None
    return (1 if sel_id else 0, len(sel_classes), 1 if tag and tag != "*" else 0)


def css_box(attrs: dict[str, str], css: str, viewport: int) -> tuple[int | None, int | None]:
    """Return ``(width_px, max_width_px)`` for an img at ``viewport``."""
    classes = set((attrs.get("class") or "").split())
    element_id = attrs.get("id") or None
    chosen_width: str | None = None
    chosen_max: str | None = None
    best: tuple[int, int, int] | None = None
    for match in _RULE.finditer(applicable_css(css, viewport)):
        selectors = [item.strip() for item in match.group(1).split(",") if item.strip()]
        decls = _declarations(match.group(2))
        for selector in selectors:
            spec = _compound_matches(selector, classes, element_id)
            if spec is None:
                continue
            if best is not None and spec < best:
                continue
            best = spec
            if "width" in decls:
                chosen_width = decls["width"]
            if "max-width" in decls:
                chosen_max = decls["max-width"]
    inline = _declarations(attrs.get("style") or "")
    if "width" in inline:
        chosen_width = inline["width"]
    if "max-width" in inline:
        chosen_max = inline["max-width"]
    return length_to_px(chosen_width, viewport), length_to_px(chosen_max, viewport)


def sizes_at_viewport(sizes: str, viewport: int) -> int | None:
    for part in [item.strip() for item in sizes.split(",") if item.strip()]:
        if part.startswith("(") and " " in part:
            condition, length = part.rsplit(" ", 1)
            if media_matches(condition, viewport):
                return length_to_px(length, viewport)
            continue
        return length_to_px(part, viewport)
    return None


def rendered_width(attrs: dict[str, str], css: str, viewport: int) -> int | None:
    """CSS wins over the HTML width attribute. ``sizes`` is the next fallback.

    The result is the layout width at this viewport, not the file's pixel width.
    A fixed pixel width is kept even when it is wider than the viewport, because
    that is how wide the browser paints it. Percentages resolve against the viewport.
    """
    width, max_width = css_box(attrs, css, viewport)
    if width is None and attrs.get("width"):
        width = length_to_px(str(attrs["width"]), viewport)
    if width is None and attrs.get("sizes"):
        width = sizes_at_viewport(str(attrs["sizes"]), viewport)
    if width is None:
        return max_width
    if max_width is not None:
        return min(width, max_width)
    return width


def classify(
    *,
    byte_size: int,
    intrinsic_w: int | None,
    rendered_by_viewport: dict[int, int | None],
    convertible: bool,
    has_width_attr: bool,
    has_height_attr: bool,
    settings: Settings,
) -> list[str]:
    reasons: list[str] = []
    if byte_size > settings.max_bytes:
        reasons.append("file_size")
        if convertible:
            reasons.append("needs_webp")
    known = [width for width in rendered_by_viewport.values() if width and width > 0]
    if intrinsic_w and known:
        limit = settings.ratio * max(known)
        if intrinsic_w > limit:
            reasons.append("overscale")
    if not has_width_attr or not has_height_attr:
        reasons.append("missing_dimensions")
    return reasons


def inspect_image(
    *,
    src: str,
    key: str,
    page: str,
    data: bytes,
    attrs: dict[str, str],
    css: str,
    settings: Settings,
    file_path: str | None,
    html_src: str | None,
    rendered_override: dict[int, int] | None = None,
) -> Finding:
    size = peek_size(data) if data else None
    intrinsic_w = size[0] if size else None
    intrinsic_h = size[1] if size else None
    rendered: dict[str, int | None] = {}
    for viewport in settings.viewports:
        if rendered_override and viewport in rendered_override:
            rendered[str(viewport)] = int(rendered_override[viewport])
        else:
            rendered[str(viewport)] = rendered_width(attrs, css, int(viewport))
    by_viewport = {int(key_): value for key_, value in rendered.items()}
    convertible = is_convertible(src)
    reasons = classify(
        byte_size=len(data),
        intrinsic_w=intrinsic_w,
        rendered_by_viewport=by_viewport,
        convertible=convertible,
        has_width_attr=bool(attrs.get("width")),
        has_height_attr=bool(attrs.get("height")),
        settings=settings,
    )
    known = [width for width in rendered.values() if width and width > 0]
    max_rendered = max(known) if known else None
    finding = Finding(
        src=src,
        key=key,
        page=page,
        old_bytes=len(data),
        new_bytes=len(data),
        intrinsic_w=intrinsic_w,
        intrinsic_h=intrinsic_h,
        rendered=rendered,
        max_rendered=max_rendered,
        reasons=reasons,
        file_path=file_path,
        html_src=html_src,
    )
    should_try = convertible and ("file_size" in reasons or "overscale" in reasons)
    if not should_try:
        finding.action = "keep"
        return finding
    payload = encode_webp(data, quality=settings.quality, max_rendered_width=max_rendered)
    if not payload:
        finding.action = "keep"
        return finding
    if len(payload) >= len(data):
        finding.action = "skip_not_smaller"
        return finding
    from imagediet.patch import webp_url

    finding.action = "convert"
    finding.new_bytes = len(payload)
    finding.webp_bytes = payload
    finding.webp_src = webp_url(html_src or src)
    return finding


def try_playwright_widths(url: str, viewports: list[int]) -> dict[int, list[float]] | None:
    """Optional headless measurement. Returns None when Playwright is unavailable."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    measured: dict[int, list[float]] = {}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                for viewport in viewports:
                    page = browser.new_page(viewport={"width": int(viewport), "height": 900})
                    page.goto(url, wait_until="load", timeout=15000)
                    widths = page.eval_on_selector_all(
                        "img",
                        "els => els.map(el => el.getBoundingClientRect().width)",
                    )
                    measured[int(viewport)] = [float(item) for item in widths]
                    page.close()
            finally:
                browser.close()
    except Exception:
        return None
    return measured
