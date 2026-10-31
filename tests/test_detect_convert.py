from __future__ import annotations

import hashlib
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from imagediet.config import Settings
from imagediet.convert import encode_webp, write_webp_if_smaller
from imagediet.crawl import crawl_http
from imagediet.detect import inspect_image, media_matches, rendered_width, sizes_at_viewport
from imagediet.report import inspect_pages


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return


def _hashes(root: Path) -> dict[str, str]:
    found = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            found[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def _serve(root: Path):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Quiet, directory=str(root)))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    return httpd, f"http://127.0.0.1:{port}/"


def test_css_widths_at_both_viewports():
    css = ".hero { width: 100%; max-width: 800px; } .thumb { width: 200px; }"
    hero = {"class": "hero", "width": "800", "height": "560"}
    thumb = {"class": "thumb"}
    assert rendered_width(hero, css, 1440) == 800
    assert rendered_width(hero, css, 390) == 390
    assert rendered_width(thumb, css, 1440) == 200
    assert rendered_width(thumb, css, 390) == 200
    assert sizes_at_viewport("(max-width: 600px) 390px, 800px", 390) == 390
    assert sizes_at_viewport("(max-width: 600px) 390px, 800px", 1440) == 800
    assert media_matches("@media (max-width: 600px)", 390) is True
    assert media_matches("@media (max-width: 600px)", 1440) is False


def test_http_scan_detects_file_size_and_overscale(synthetic_site):
    before = _hashes(synthetic_site)
    httpd, url = _serve(synthetic_site)
    try:
        pages = crawl_http(url)
        reports, _notes = inspect_pages(pages, Settings())
    finally:
        httpd.shutdown()
        httpd.server_close()
    findings = [finding for page in reports for finding in page.findings]
    by_src = {}
    for finding in findings:
        by_src.setdefault(finding.src, []).append(finding)
    big = next(item for item in findings if item.src.endswith("big.jpg"))
    png = next(item for item in findings if item.src.endswith("big.png"))
    wide = next(item for item in findings if item.src.endswith("wide.jpg"))
    small = [item for item in findings if item.src.endswith("small.jpg")]
    assert "file_size" in big.reasons
    assert "needs_webp" in big.reasons
    assert "file_size" in png.reasons
    assert "overscale" in wide.reasons
    assert "file_size" not in wide.reasons
    assert "missing_dimensions" in wide.reasons
    assert wide.action == "convert"
    assert big.action == "convert"
    assert all(item.action == "keep" and "file_size" not in item.reasons and "overscale" not in item.reasons for item in small)
    reasons = {reason for finding in findings for reason in finding.reasons}
    assert "file_size" in reasons and "overscale" in reasons
    assert not list(synthetic_site.rglob("*.webp"))
    assert _hashes(synthetic_site) == before


def test_skip_when_encoded_webp_is_not_smaller(monkeypatch):
    monkeypatch.setattr("imagediet.detect.encode_webp", lambda *args, **kwargs: b"x" * 50)
    finding = inspect_image(
        src="tiny.jpg",
        key="tiny.jpg",
        page="index.html",
        data=b"small-jpeg",
        attrs={"width": "8", "height": "8"},
        css="",
        settings=Settings(max_bytes=1),
        file_path=None,
        html_src="tiny.jpg",
    )
    assert finding.action == "skip_not_smaller"
    assert finding.webp_bytes is None
    assert finding.new_bytes == len(b"small-jpeg")


def test_write_webp_never_replaces_original_or_existing(tmp_path):
    src = tmp_path / "photo.jpg"
    payload = b"original-photo-bytes"
    src.write_bytes(payload)
    assert write_webp_if_smaller(src, b"original-photo-bytes-bigger") is None
    assert not (tmp_path / "photo.webp").exists()
    assert src.read_bytes() == payload
    existing = tmp_path / "photo.webp"
    existing.write_bytes(b"keep-me")
    assert write_webp_if_smaller(src, b"tiny") is None
    assert existing.read_bytes() == b"keep-me"
    assert write_webp_if_smaller(src, b"tiny", dest=src) is None
    assert src.read_bytes() == payload


def test_webp_is_smaller_and_strips_exif(tmp_path):
    src = tmp_path / "camera.jpg"
    image = Image.new("RGB", (320, 200), (20, 90, 140))
    exif = image.getexif()
    exif[0x010F] = "SecretCam"
    exif[0x8825] = {
        1: "N",
        2: (IFDRational(37, 1), IFDRational(48, 1), IFDRational(0, 1)),
        3: "W",
        4: (IFDRational(122, 1), IFDRational(25, 1), IFDRational(0, 1)),
    }
    image.save(src, format="JPEG", quality=95, exif=exif)
    original = src.read_bytes()
    encoded = encode_webp(original, quality=80, max_rendered_width=80)
    assert encoded is not None and len(encoded) < len(original)
    dest = write_webp_if_smaller(src, encoded)
    assert dest is not None and dest.name == "camera.webp"
    assert src.read_bytes() == original
    opened = Image.open(dest)
    assert opened.getexif().get(0x010F) in (None, "")
    assert 0x8825 not in opened.getexif()
    assert Image.open(src).getexif().get(0x010F) == "SecretCam"
