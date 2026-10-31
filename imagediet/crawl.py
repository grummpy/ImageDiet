"""Collect pages and image bytes from a directory or from loopback/HTTP."""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from imagediet.config import ImageDietError, Site, offline, sample_root

MAX_PAGES = 20
MAX_IMAGES = 200
MAX_IMAGE_BYTES = 25 * 1024 * 1024
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "work", "reports"}


class CrawlError(ImageDietError):
    pass


@dataclass
class RawImage:
    src: str
    key: str
    data: bytes
    attrs: dict[str, str]
    file_path: Path | None = None
    html_src: str | None = None
    rendered_override: dict[int, int] | None = None


@dataclass
class RawPage:
    url: str
    html: str
    html_path: Path | None
    css: str
    images: list[RawImage] = field(default_factory=list)


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.images: list[dict[str, str]] = []
        self.links: list[str] = []
        self.stylesheets: list[str] = []
        self.styles: list[str] = []
        self._style: list[str] = []
        self._in_style = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): value for key, value in attrs if key and value is not None}
        if tag == "img":
            self.images.append(attr)
        elif tag == "link" and "stylesheet" in (attr.get("rel") or "").lower() and attr.get("href"):
            self.stylesheets.append(attr["href"])
        elif tag == "a" and attr.get("href"):
            self.links.append(attr["href"])
        elif tag == "style":
            self._in_style = True
            self._style = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "style" and self._in_style:
            self.styles.append("".join(self._style))
            self._in_style = False

    def handle_data(self, data: str) -> None:
        if self._in_style:
            self._style.append(data)


def split_srcset(value: str) -> list[str]:
    urls: list[str] = []
    for part in (value or "").split(","):
        bits = part.strip().split()
        if bits:
            urls.append(bits[0])
    return urls


def _inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _skipped(path: Path, root: Path) -> bool:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        return True
    return any(part in SKIP_DIRS for part in relative.parts)


def _read_capped(path: Path) -> bytes:
    size = path.stat().st_size
    if size > MAX_IMAGE_BYTES:
        raise CrawlError(f"image exceeds {MAX_IMAGE_BYTES} bytes: {path.name}")
    return path.read_bytes()


def _local_path(root: Path, html_path: Path, ref: str) -> Path | None:
    cleaned = ref.split("#", 1)[0].split("?", 1)[0].strip()
    if not cleaned or cleaned.startswith(("data:", "mailto:", "javascript:", "http://", "https://", "//")):
        return None
    candidate = (html_path.parent / cleaned).resolve()
    if not _inside(root, candidate) or _skipped(candidate, root):
        return None
    return candidate


def _build_images(
    parser: _PageParser,
    *,
    page_url: str,
    css: str,
    load_bytes,
    html_path: Path | None,
) -> list[RawImage]:
    images: list[RawImage] = []
    seen: set[str] = set()
    for attrs in parser.images:
        if len(images) >= MAX_IMAGES:
            break
        refs: list[tuple[str, bool]] = []
        src = (attrs.get("src") or "").strip()
        if src and not src.startswith("data:"):
            refs.append((src, True))
        for extra in split_srcset(attrs.get("srcset") or ""):
            if extra and not extra.startswith("data:"):
                refs.append((extra, False))
        for ref, is_html_src in refs:
            if ref in seen:
                continue
            try:
                data, file_path, key = load_bytes(ref)
            except CrawlError:
                continue
            if data is None:
                continue
            seen.add(ref)
            images.append(
                RawImage(
                    src=ref,
                    key=key,
                    data=data,
                    attrs=attrs if is_html_src or ref == src else {"src": ref},
                    file_path=file_path,
                    html_src=src if is_html_src else None,
                )
            )
    return images


def parse_document(
    html: str,
    *,
    page_url: str,
    html_path: Path | None,
    load_bytes,
    load_text,
) -> RawPage:
    parser = _PageParser()
    parser.feed(html)
    chunks = list(parser.styles)
    for href in parser.stylesheets:
        try:
            text = load_text(href)
        except CrawlError:
            text = None
        if text:
            chunks.append(text)
    css = "\n".join(chunks)
    return RawPage(
        url=page_url,
        html=html,
        html_path=html_path,
        css=css,
        images=_build_images(parser, page_url=page_url, css=css, load_bytes=load_bytes, html_path=html_path),
    )


def crawl_tree(root: Path) -> list[RawPage]:
    root = root.resolve()
    if not root.is_dir():
        raise CrawlError(f"Site directory does not exist: {root}")
    html_files = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in {".html", ".htm"}
        and not _skipped(path, root)
    ]
    html_files.sort()
    pages: list[RawPage] = []
    for html_path in html_files[:MAX_PAGES]:
        text = html_path.read_bytes().decode("utf-8", errors="replace")
        relative = html_path.relative_to(root).as_posix()

        def load_bytes(ref: str, html_path: Path = html_path) -> tuple[bytes | None, Path | None, str]:
            path = _local_path(root, html_path, ref)
            if path is None or not path.is_file():
                return None, None, ref
            return _read_capped(path), path, path.resolve().as_posix()

        def load_text(ref: str, html_path: Path = html_path) -> str | None:
            path = _local_path(root, html_path, ref)
            if path is None or not path.is_file():
                return None
            return path.read_bytes().decode("utf-8", errors="replace")

        pages.append(
            parse_document(
                text,
                page_url=relative,
                html_path=html_path,
                load_bytes=load_bytes,
                load_text=load_text,
            )
        )
    return pages


def _allowed_host(host: str | None) -> None:
    if not host:
        raise CrawlError("Refusing a URL with no host.")
    if offline() and host not in {"127.0.0.1", "localhost"}:
        raise CrawlError(f"Offline mode only crawls 127.0.0.1, not {host}.")


def _same_site(url: str, origin_host: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and (parsed.hostname or "") == origin_host


def _http_get(url: str, origin_host: str) -> tuple[bytes, str]:
    _allowed_host(urlparse(url).hostname)
    if not _same_site(url, origin_host):
        raise CrawlError(f"Refusing to leave {origin_host} for {url}")
    request = Request(url, headers={"User-Agent": "ImageDiet/1.0 (local dry-run)"})
    try:
        with urlopen(request, timeout=10) as response:
            final = response.geturl()
            if not _same_site(final, origin_host):
                raise CrawlError(f"Refusing redirect off {origin_host}")
            length = response.headers.get("Content-Length")
            if length and int(length) > MAX_IMAGE_BYTES:
                raise CrawlError("response is too large")
            data = response.read(MAX_IMAGE_BYTES + 1)
            if len(data) > MAX_IMAGE_BYTES:
                raise CrawlError("response is too large")
            content_type = response.headers.get("Content-Type", "")
            return data, content_type
    except CrawlError:
        raise
    except (URLError, TimeoutError, ValueError) as exc:
        raise CrawlError(str(exc)) from exc


def crawl_http(start_url: str) -> list[RawPage]:
    parsed = urlparse(start_url)
    if parsed.scheme not in {"http", "https"}:
        raise CrawlError("Only http and https URLs can be crawled.")
    origin = parsed.hostname or ""
    _allowed_host(origin)
    queue = [start_url]
    seen_pages: set[str] = set()
    pages: list[RawPage] = []
    while queue and len(pages) < MAX_PAGES:
        url = queue.pop(0)
        if url in seen_pages:
            continue
        seen_pages.add(url)
        try:
            body, content_type = _http_get(url, origin)
        except CrawlError:
            continue
        if "html" not in content_type and not url.endswith((".html", ".htm", "/")):
            # A URL with no content type still counts when it looks like a page.
            if not body.lstrip().startswith(b"<"):
                continue
        html = body.decode("utf-8", errors="replace")
        parser = _PageParser()
        parser.feed(html)

        def load_bytes(ref: str, page: str = url) -> tuple[bytes | None, Path | None, str]:
            absolute = urljoin(page, ref)
            if not _same_site(absolute, origin):
                return None, None, absolute
            data, _ctype = _http_get(absolute, origin)
            return data, None, absolute

        def load_text(ref: str, page: str = url) -> str | None:
            absolute = urljoin(page, ref)
            if not _same_site(absolute, origin):
                return None
            data, _ctype = _http_get(absolute, origin)
            return data.decode("utf-8", errors="replace")

        page = parse_document(
            html,
            page_url=url,
            html_path=None,
            load_bytes=load_bytes,
            load_text=load_text,
        )
        pages.append(page)
        for href in parser.links:
            absolute = urljoin(url, href)
            absolute = absolute.split("#", 1)[0]
            if not _same_site(absolute, origin) or absolute in seen_pages:
                continue
            path = urlparse(absolute).path
            if path.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".css", ".js", ".svg")):
                continue
            queue.append(absolute)
    if not pages:
        raise CrawlError(f"No HTML pages found at {start_url}")
    return pages


def load_pages(site: Site) -> list[RawPage]:
    url = site.url
    if url in {"internal://sample", "sample://testsite"}:
        return crawl_tree(sample_root())
    if url.startswith("file://"):
        from urllib.parse import unquote

        return crawl_tree(Path(unquote(urlparse(url).path)))
    if url.startswith("internal://"):
        raise CrawlError(f"Unknown internal site: {url}")
    return crawl_http(url)


