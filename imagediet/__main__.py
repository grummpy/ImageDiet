"""ImageDiet CLI. ``python -m imagediet`` serves the local UI.

Scan is a dry-run. ``confirm`` does nothing unless ``--confirm`` matches the domain.
"""

from __future__ import annotations

import argparse
import sys

from imagediet.config import ImageDietError, load_settings, load_site_list
from imagediet.gitops import ConfirmRequired, ReportOnlyError, prepare, publish
from imagediet.report import format_bytes, run_scan
from imagediet.server import serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="imagediet", description="Find heavy images and prepare WebP fallbacks.")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("serve", help="Serve the local UI on 127.0.0.1:8766 (default).")

    scan = sub.add_parser("scan", help="Dry-run a site and write a report. Does not modify files or git.")
    scan.add_argument("--domain", required=True)

    prep = sub.add_parser("prepare", help="Clone, convert, and commit a local imagediet branch. Does not push.")
    prep.add_argument("--domain", required=True)

    confirm = sub.add_parser(
        "confirm",
        help="Push the prepared branch and open a pull request. Requires --confirm <domain>.",
    )
    confirm.add_argument("--domain", required=True)
    confirm.add_argument(
        "--confirm",
        default=None,
        help="Type the domain to confirm. Without this, ImageDiet refuses to push or open a PR.",
    )

    args = parser.parse_args(argv)
    if args.cmd in (None, "serve"):
        serve()
        return 0
    if args.cmd == "confirm" and not args.confirm:
        print(
            "Refusing to push: pass --confirm <domain> with the site domain.",
            file=sys.stderr,
        )
        return 2
    try:
        catalog = load_site_list()
        site = catalog.by_domain(args.domain)
        if site is None:
            print(f"Unknown domain: {args.domain}", file=sys.stderr)
            return 1
        settings = load_settings()
        if args.cmd == "scan":
            report = run_scan(site, settings)
            totals = report.site_totals()
            print(f"Dry-run {site.domain}: {format_bytes(totals['bytes_before'])} -> {format_bytes(totals['bytes_after'])} ({totals['savings_pct']}% )")
            print(f"Report: {report.paths.get('html', '')}")
            print("No files were modified. Nothing was pushed.")
            return 0
        if args.cmd == "prepare":
            result = prepare(site, settings)
            print(f"Prepared {result.branch} in {result.repo_path}")
            print("Nothing was pushed. Type the domain in the UI, or pass --confirm, to publish.")
            return 0
        if args.cmd == "confirm":
            result = publish(site, args.confirm, settings)
            print(result.get("pr_url") or "Published.")
            return 0
    except (ConfirmRequired, ReportOnlyError, ImageDietError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
