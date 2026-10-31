"""Before/after page-weight reports. Writing a report does not change the site."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from imagediet.config import Settings, Site, reports_root, safe_domain
from imagediet.crawl import RawPage, load_pages
from imagediet.detect import Finding, inspect_image, try_playwright_widths


def format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.2f} MB"


@dataclass
class PageReport:
    url: str
    html_path: str | None
    findings: list[Finding]

    def public(self) -> dict:
        before = sum(item.old_bytes for item in self.findings)
        after = sum(item.new_bytes for item in self.findings)
        savings = round((before - after) / before * 100, 1) if before else 0.0
        return {
            "url": self.url,
            "html_path": self.html_path,
            "bytes_before": before,
            "bytes_after": after,
            "savings_pct": savings,
            "images": [item.public() for item in self.findings],
        }


@dataclass
class Report:
    domain: str
    mode: str
    generated_at: str
    settings: dict
    pages: list[PageReport]
    files_modified: bool
    pushed: bool
    originals_deleted: bool
    notes: list[str] = field(default_factory=list)
    diff_stat: str = ""
    paths: dict[str, str] = field(default_factory=dict)

    def site_totals(self) -> dict:
        unique: dict[str, Finding] = {}
        for page in self.pages:
            for finding in page.findings:
                unique[finding.key] = finding
        before = sum(item.old_bytes for item in unique.values())
        after = sum(item.new_bytes for item in unique.values())
        savings = round((before - after) / before * 100, 1) if before else 0.0
        actions: dict[str, int] = {}
        reasons: dict[str, int] = {}
        for item in unique.values():
            actions[item.action] = actions.get(item.action, 0) + 1
            for reason in item.reasons:
                reasons[reason] = reasons.get(reason, 0) + 1
        return {
            "bytes_before": before,
            "bytes_after": after,
            "savings_pct": savings,
            "images": len(unique),
            "actions": actions,
            "reasons": reasons,
        }

    def public(self) -> dict:
        return {
            "domain": self.domain,
            "mode": self.mode,
            "generated_at": self.generated_at,
            "settings": self.settings,
            "files_modified": self.files_modified,
            "pushed": False if self.mode == "dry-run" else self.pushed,
            "originals_deleted": False,
            "notes": self.notes,
            "diff_stat": self.diff_stat,
            "pages": [page.public() for page in self.pages],
            "totals": self.site_totals(),
            "report": self.paths,
            "safety": {
                "dry_run_default": True,
                "originals_deleted": False,
                "webp_fallback": "The original file stays in <img src> for browsers without WebP.",
                "publish": "A branch is pushed and a pull request is opened only after the domain is typed to confirm.",
            },
        }


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def inspect_pages(pages: list[RawPage], settings: Settings, *, use_playwright: bool = False) -> tuple[list[PageReport], list[str]]:
    notes: list[str] = []
    if use_playwright:
        notes.append("Playwright measurement was requested.")
    reports: list[PageReport] = []
    for page in pages:
        overrides: dict[int, list[float]] | None = None
        if use_playwright and page.url.startswith(("http://", "https://")):
            overrides = try_playwright_widths(page.url, list(settings.viewports))
            if overrides is None:
                notes.append(f"Playwright was unavailable for {page.url}; used HTML and CSS.")
        html_images = [image for image in page.images if image.html_src]
        findings: list[Finding] = []
        for image in page.images:
            override = None
            if overrides and image.html_src:
                html_index = html_images.index(image)
                override = {}
                for viewport, widths in overrides.items():
                    if html_index < len(widths):
                        override[viewport] = int(round(widths[html_index]))
            findings.append(
                inspect_image(
                    src=image.src,
                    key=image.key,
                    page=page.url,
                    data=image.data,
                    attrs=image.attrs,
                    css=page.css,
                    settings=settings,
                    file_path=str(image.file_path) if image.file_path else None,
                    html_src=image.html_src,
                    rendered_override=override,
                )
            )
        reports.append(
            PageReport(
                url=page.url,
                html_path=str(page.html_path) if page.html_path else None,
                findings=findings,
            )
        )
    return reports, notes


def build_report(
    site: Site,
    pages: list[PageReport],
    settings: Settings,
    *,
    mode: str,
    files_modified: bool,
    notes: list[str] | None = None,
) -> Report:
    return Report(
        domain=site.domain,
        mode=mode,
        generated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        settings=settings.to_dict(),
        pages=pages,
        files_modified=files_modified,
        pushed=False,
        originals_deleted=False,
        notes=list(notes or []),
    )


def render_markdown(report: Report) -> str:
    totals = report.site_totals()
    lines = [
        f"# ImageDiet report — {report.domain}",
        "",
        f"Mode: **{report.mode}**. Original images were not deleted.",
        "Browsers without WebP keep loading the original file from `<img src>`.",
        "No branch is pushed and no pull request is opened unless the domain is typed to confirm.",
        "",
        f"- Generated: {report.generated_at}",
        f"- Threshold: {format_bytes(int(report.settings['max_bytes']))}",
        f"- Overscale ratio: {report.settings['ratio']}× the widest rendered size at {', '.join(str(v) for v in report.settings['viewports'])}px",
        f"- WebP quality: {report.settings['quality']}",
        f"- Files modified: {'yes' if report.files_modified else 'no'}",
        f"- Pushed: no" if not report.pushed else "- Pushed: yes",
        "",
        "## Site total (each file once)",
        "",
        f"- Before: {format_bytes(totals['bytes_before'])}",
        f"- After: {format_bytes(totals['bytes_after'])}",
        f"- Savings: {totals['savings_pct']}%",
        "",
    ]
    if report.notes:
        lines.append("## Notes")
        lines.append("")
        for note in report.notes:
            lines.append(f"- {note}")
        lines.append("")
    for page in report.pages:
        block = page.public()
        lines.append(f"## {page.url}")
        lines.append("")
        lines.append(
            f"Before {format_bytes(block['bytes_before'])} · "
            f"after {format_bytes(block['bytes_after'])} · "
            f"savings {block['savings_pct']}%"
        )
        lines.append("")
        lines.append("| Image | Before | After | Intrinsic | Rendered max | Action | Why |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for image in block["images"]:
            intrinsic = image["intrinsic_px"]
            intrinsic_text = (
                f"{intrinsic[0]}×{intrinsic[1]}" if intrinsic[0] else "—"
            )
            rendered = image["max_rendered_px"]
            rendered_text = f"{rendered}px" if rendered else "—"
            why = ", ".join(image["reasons"]) or "—"
            src = str(image["src"]).replace("|", "\\|")
            lines.append(
                f"| `{src}` | {format_bytes(image['old_bytes'])} | {format_bytes(image['new_bytes'])} | "
                f"{intrinsic_text} | {rendered_text} | {image['action']} | {why} |"
            )
        lines.append("")
    if report.diff_stat:
        lines.append("## Diff stat")
        lines.append("")
        lines.append("```")
        lines.append(report.diff_stat.rstrip())
        lines.append("```")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_html(report: Report) -> str:
    totals = report.site_totals()
    rows: list[str] = []
    for page in report.pages:
        block = page.public()
        rows.append(
            f"<h2>{escape(page.url)}</h2>"
            f"<p>Before {escape(format_bytes(block['bytes_before']))} · "
            f"after {escape(format_bytes(block['bytes_after']))} · "
            f"savings {block['savings_pct']}%</p>"
        )
        body = []
        for image in block["images"]:
            intrinsic = image["intrinsic_px"]
            intrinsic_text = f"{intrinsic[0]}×{intrinsic[1]}" if intrinsic[0] else "—"
            rendered = image["max_rendered_px"]
            rendered_text = f"{rendered}px" if rendered else "—"
            why = ", ".join(image["reasons"]) or "—"
            body.append(
                "<tr>"
                f"<td><code>{escape(str(image['src']))}</code></td>"
                f"<td>{escape(format_bytes(image['old_bytes']))}</td>"
                f"<td>{escape(format_bytes(image['new_bytes']))}</td>"
                f"<td>{escape(intrinsic_text)}</td>"
                f"<td>{escape(rendered_text)}</td>"
                f"<td>{escape(image['action'])}</td>"
                f"<td>{escape(why)}</td>"
                "</tr>"
            )
        rows.append(
            "<table><thead><tr><th>Image</th><th>Before</th><th>After</th>"
            "<th>Intrinsic</th><th>Rendered max</th><th>Action</th><th>Why</th></tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table>"
        )
    notes = "".join(f"<li>{escape(note)}</li>" for note in report.notes)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>ImageDiet report — {escape(report.domain)}</title>
<style>
  body {{ margin: 0; font-family: "Segoe UI", sans-serif; background: #07111f; color: #e7f1fb; }}
  main {{ max-width: 1100px; margin: 0 auto; padding: 32px 20px 64px; }}
  h1 {{ font-weight: 650; letter-spacing: -0.03em; }}
  .banner {{ border: 1px solid #1e3d5c; background: #0e1c31; border-radius: 16px; padding: 16px 18px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 12px 0 28px; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid #1e3d5c; vertical-align: top; }}
  th {{ color: #93a8c0; font-size: 12px; letter-spacing: 0.04em; text-transform: uppercase; }}
  code {{ color: #b6f25c; }}
</style>
</head>
<body>
<main>
  <h1>ImageDiet report — {escape(report.domain)}</h1>
  <div class="banner">
    <p>Mode: {escape(report.mode)}. Original images were not deleted.
    Browsers without WebP still load the original <code>&lt;img src&gt;</code>.</p>
    <p>Before {escape(format_bytes(totals['bytes_before']))} ·
    after {escape(format_bytes(totals['bytes_after']))} ·
    savings {totals['savings_pct']}%.</p>
    <p>Pushed: {"yes" if report.pushed else "no"}.</p>
  </div>
  {f"<ul>{notes}</ul>" if notes else ""}
  {''.join(rows)}
</main>
</body>
</html>
"""


def write_report(report: Report, root: Path | None = None) -> dict[str, str]:
    """Write report.md, report.json, and report.html. Does not touch site files."""
    import json

    base = (root or reports_root()) / safe_domain(report.domain) / _stamp()
    base.mkdir(parents=True, exist_ok=True)
    payload = report.public()
    (base / "report.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (base / "report.md").write_text(render_markdown(report), encoding="utf-8")
    (base / "report.html").write_text(render_html(report), encoding="utf-8")
    paths = {
        "dir": str(base),
        "json": str(base / "report.json"),
        "md": str(base / "report.md"),
        "html": str(base / "report.html"),
        "html_url": "/reports/" + safe_domain(report.domain) + "/" + base.name + "/report.html",
    }
    report.paths = paths
    # Rewrite JSON so the paths are included.
    payload = report.public()
    (base / "report.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return paths


def run_scan(site: Site, settings: Settings) -> Report:
    """Dry-run. Crawls and writes a report. Does not modify the site or git."""
    pages = load_pages(site)
    inspected, notes = inspect_pages(pages, settings, use_playwright=settings.use_playwright)
    notes.insert(0, "Dry-run: no image files were written and no git command was run.")
    report = build_report(site, inspected, settings, mode="dry-run", files_modified=False, notes=notes)
    write_report(report)
    return report
