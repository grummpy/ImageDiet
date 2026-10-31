from __future__ import annotations

import hashlib
from pathlib import Path

from imagediet.config import Settings
from imagediet.crawl import crawl_tree
from imagediet.patch import patch_html
from imagediet.report import inspect_pages
from imagediet.gitops import apply_findings


def _hashes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".txt"}
    }


def test_picture_fallback_keeps_original_src_and_is_idempotent():
    html = '<p><img class="hero" src="images/big.jpg" alt="Big" width="800" height="560"></p>'
    once = patch_html(html, {"images/big.jpg": "images/big.webp"})
    twice = patch_html(once, {"images/big.jpg": "images/big.webp"})
    assert once == twice
    assert '<source type="image/webp" srcset="images/big.webp">' in once
    assert 'src="images/big.jpg"' in once
    assert once.count("<picture>") == 1
    untouched = patch_html(html, {})
    assert untouched == html
    quoted = patch_html("<img src='images/big.jpg' alt='x'>", {"images/big.jpg": "images/big.webp"})
    assert "images/big.webp" in quoted
    assert "src='images/big.jpg'" in quoted


def test_apply_writes_smaller_webp_and_leaves_originals(synthetic_site):
    before = _hashes(synthetic_site)
    notes = (synthetic_site / "notes.txt").read_bytes()
    pages = crawl_tree(synthetic_site)
    reports, _notes = inspect_pages(pages, Settings())
    written = apply_findings(synthetic_site, pages, reports)
    assert written
    after = _hashes(synthetic_site)
    assert after == before
    assert (synthetic_site / "notes.txt").read_bytes() == notes
    webps = list(synthetic_site.rglob("*.webp"))
    assert webps
    for webp in webps:
        original = None
        for ext in (".jpg", ".jpeg", ".png"):
            candidate = webp.with_suffix(ext)
            if candidate.exists():
                original = candidate
                break
        assert original is not None
        assert webp.stat().st_size < original.stat().st_size
        assert hashlib.sha256(original.read_bytes()).hexdigest() == hashlib.sha256(before[original.relative_to(synthetic_site).as_posix()]).hexdigest()
    html = (synthetic_site / "index.html").read_text(encoding="utf-8")
    assert "<picture>" in html
    assert 'type="image/webp"' in html
    assert 'src="images/big.jpg"' in html
    assert 'src="images/big.png"' in html
    assert 'src="images/wide.jpg"' in html
    assert 'srcset="images/big.webp"' in html
    assert 'srcset="images/wide.webp"' in html
    # The small image is not wrapped.
    assert 'src="images/small.jpg"' in html
    small_at = html.index('src="images/small.jpg"')
    picture_before = html.rfind("<picture>", 0, small_at)
    picture_close = html.rfind("</picture>", 0, small_at)
    assert picture_close >= picture_before
    again_pages = crawl_tree(synthetic_site)
    again_reports, _notes = inspect_pages(again_pages, Settings())
    apply_findings(synthetic_site, again_pages, again_reports)
    assert (synthetic_site / "index.html").read_text(encoding="utf-8").count("<picture>") == html.count("<picture>")
    assert _hashes(synthetic_site) == before
