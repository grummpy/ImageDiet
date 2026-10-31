"""Local branch preparation, and publish only after a typed domain confirmation.

``run_git`` refuses ``push`` unless ``require_confirm`` has returned a token
for that same domain. ``execute_publish`` refuses again when
``IMAGEDIET_OFFLINE=1``, which the test suite always sets.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from imagediet.config import ROOT, ImageDietError, Settings, Site, safe_domain, work_root
from imagediet.crawl import RawPage, crawl_tree
from imagediet.patch import patch_html
from imagediet.report import PageReport, Report, build_report, inspect_pages, render_markdown, write_report

_BRANCH = re.compile(r"imagediet/\d{4}-\d{2}-\d{2}(?:-\d+)?$")
_OWNER_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


class ConfirmRequired(ImageDietError):
    """Push and pull-request creation stay closed."""


class ReportOnlyError(ImageDietError):
    pass


@dataclass(frozen=True)
class ConfirmToken:
    domain: str


def require_confirm(domain: str, typed: str | None) -> ConfirmToken:
    official = (domain or "").strip()
    given = (typed or "").strip()
    if not official or given.casefold() != official.casefold():
        raise ConfirmRequired(
            "Type the domain to confirm before ImageDiet will push a branch or open a pull request."
        )
    return ConfirmToken(official)


def run_git(
    args: list[str],
    cwd: Path,
    *,
    token: ConfirmToken | None = None,
    domain: str | None = None,
) -> subprocess.CompletedProcess[str]:
    if any(arg == "push" or arg == "--force" or arg == "-f" for arg in args):
        if token is None or domain is None or token.domain.casefold() != domain.strip().casefold():
            raise ConfirmRequired("Refusing to push: type the domain to confirm.")
        if not any(_BRANCH.fullmatch(arg) for arg in args):
            raise ConfirmRequired("Refusing to push a branch that is not imagediet/<date>.")
        if "--force" in args or "-f" in args:
            raise ConfirmRequired("Refusing force-push.")
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def plan_publish(*, domain: str, typed: str | None, branch: str, title: str, body: str) -> list[list[str]]:
    token = require_confirm(domain, typed)
    if token.domain != domain.strip():
        raise ConfirmRequired("Confirmation does not match the site domain.")
    if not _BRANCH.fullmatch(branch):
        raise ConfirmRequired("Refusing to publish: branch is not imagediet/<date>.")
    return [
        ["git", "push", "-u", "origin", branch],
        ["gh", "pr", "create", "--title", title, "--body", body],
    ]


def _command_allowed(cmd: list[str]) -> bool:
    if len(cmd) == 5 and cmd[:4] == ["git", "push", "-u", "origin"] and bool(_BRANCH.fullmatch(cmd[4])):
        return "--force" not in cmd and "-f" not in cmd
    if (
        len(cmd) == 7
        and cmd[0:4] == ["gh", "pr", "create", "--title"]
        and cmd[5] == "--body"
        and bool(cmd[4].strip())
        and bool(cmd[6].strip())
    ):
        return True
    return False


def execute_publish(
    plan: list[list[str]],
    cwd: Path,
    *,
    token: ConfirmToken | None,
    domain: str,
) -> str:
    """Run a plan from ``plan_publish``. Offline mode and a missing token both refuse."""
    if token is None or token.domain.casefold() != domain.strip().casefold():
        raise ConfirmRequired("Refusing to publish without a typed domain confirmation.")
    if os.environ.get("IMAGEDIET_OFFLINE") == "1":
        raise ConfirmRequired("Offline mode refuses push and pull requests.")
    if len(plan) != 2 or any(not _command_allowed(cmd) for cmd in plan):
        raise ConfirmRequired("Refusing an unexpected publish command.")
    push = subprocess.run(plan[0], cwd=str(cwd), check=True, capture_output=True, text=True)
    pr = subprocess.run(plan[1], cwd=str(cwd), check=True, capture_output=True, text=True)
    url = (pr.stdout or "").strip()
    return url or (push.stdout or "").strip()


def _git_identity() -> list[str]:
    return ["-c", "user.email=imagediet@localhost", "-c", "user.name=ImageDiet"]


def _branch_name(dest: Path) -> str:
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    branch = f"imagediet/{date}"
    existing = run_git(["branch", "--list", branch], dest).stdout
    if existing.strip():
        stamp = datetime.now(timezone.utc).strftime("%H%M%S")
        branch = f"imagediet/{date}-{stamp}"
    return branch


def _source_for(site: Site) -> str:
    repo = (site.github_repo or "").strip()
    if not repo:
        raise ReportOnlyError(f"{site.domain} has no github_repo. It is report-only.")
    if repo.startswith("file://"):
        return unquote(urlparse(repo).path)
    local = Path(repo).expanduser()
    if local.exists():
        return str(local)
    if _OWNER_REPO.fullmatch(repo):
        return f"https://github.com/{repo}.git"
    raise ImageDietError("github_repo must be owner/name or a local git path.")


def _remove_work_dir(dest: Path) -> None:
    resolved = dest.resolve()
    root = work_root().resolve()
    if resolved != root and root not in resolved.parents:
        raise ImageDietError("Refusing to delete a directory outside work/.")
    if dest.exists():
        shutil.rmtree(dest)


def _clone(site: Site, dest: Path) -> None:
    source = _source_for(site)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _remove_work_dir(dest)
    try:
        run_git(["clone", "--branch", site.branch_base, "--", source, str(dest)], dest.parent)
    except subprocess.CalledProcessError:
        _remove_work_dir(dest)
        run_git(["clone", "--", source, str(dest)], dest.parent)
        run_git(["checkout", site.branch_base], dest)


def apply_findings(root: Path, pages: list[RawPage], page_reports: list[PageReport]) -> list[Path]:
    """Write WebP siblings and patch HTML. Original bytes are not opened for writing."""
    written: list[Path] = []
    by_page = {item.url: item for item in page_reports}
    for page in pages:
        report = by_page.get(page.url)
        if report is None:
            continue
        src_map: dict[str, str] = {}
        for finding in report.findings:
            if finding.action != "convert" or not finding.webp_bytes or not finding.file_path:
                continue
            src = Path(finding.file_path)
            dest = src.with_suffix(".webp")
            if dest.exists() or dest.resolve() == src.resolve():
                finding.action = "skip_existing_webp"
                finding.new_bytes = finding.old_bytes
                finding.webp_bytes = None
                finding.webp_src = None
                continue
            if len(finding.webp_bytes) >= finding.old_bytes:
                finding.action = "skip_not_smaller"
                finding.new_bytes = finding.old_bytes
                finding.webp_bytes = None
                continue
            before = src.read_bytes()
            dest.write_bytes(finding.webp_bytes)
            if src.read_bytes() != before:
                raise ImageDietError(f"Original changed while writing {dest.name}; this is a bug.")
            written.append(dest)
            if finding.html_src and finding.webp_src:
                src_map[finding.html_src] = finding.webp_src
        if src_map and page.html_path:
            updated = patch_html(page.html, src_map)
            if updated != page.html:
                page.html_path.write_bytes(updated.encode("utf-8"))
    return written


def _image_root(repo: Path, site: Site) -> Path:
    relative = (site.image_root or ".").strip() or "."
    candidate = (repo / relative).resolve()
    if candidate != repo.resolve() and repo.resolve() not in candidate.parents:
        raise ImageDietError("image_root escapes the cloned repository.")
    return candidate if candidate.is_dir() else repo


@dataclass
class PrepareResult:
    domain: str
    branch: str
    repo_path: str
    committed: bool
    diff_stat: str
    report: Report
    pushed: bool = False

    def public(self) -> dict:
        payload = self.report.public()
        payload.update(
            {
                "branch": self.branch,
                "repo_path": self.repo_path,
                "committed": self.committed,
                "diff_stat": self.diff_stat,
                "pushed": False,
                "files_modified": True,
            }
        )
        return payload


def prepare(site: Site, settings: Settings) -> PrepareResult:
    """Clone, convert, and commit locally. This function does not push."""
    if site.report_only:
        raise ReportOnlyError(f"{site.domain} has no github_repo. It is report-only.")
    safe = safe_domain(site.domain)
    dest = (work_root() / safe).resolve()
    root = work_root().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if root != dest and root not in dest.parents:
        raise ImageDietError("Refusing to prepare outside work/.")
    _clone(site, dest)
    branch = _branch_name(dest)
    run_git(["checkout", "-b", branch], dest)
    scan_root = _image_root(dest, site)
    pages = crawl_tree(dest)
    inspected, notes = inspect_pages(pages, settings, use_playwright=False)
    if scan_root.resolve() != dest.resolve():
        for page_report in inspected:
            for finding in page_report.findings:
                if not finding.file_path:
                    continue
                path = Path(finding.file_path).resolve()
                root_path = scan_root.resolve()
                if path != root_path and root_path not in path.parents:
                    finding.action = "keep"
                    finding.new_bytes = finding.old_bytes
                    finding.webp_bytes = None
                    finding.webp_src = None
    notes.insert(0, "Prepared a local branch only. Nothing has been pushed.")
    apply_findings(dest, pages, inspected)
    report = build_report(
        site,
        inspected,
        settings,
        mode="prepare",
        files_modified=True,
        notes=notes,
    )
    run_git(["add", "-A"], dest)
    status = run_git(["status", "--porcelain"], dest).stdout.strip()
    committed = False
    if status:
        message = (
            f"ImageDiet: convert oversized images to WebP\n\n"
            f"Keep originals and add picture fallbacks for {site.domain}.\n"
        )
        run_git([*_git_identity(), "commit", "-m", message], dest)
        committed = True
        report.diff_stat = run_git(["show", "--stat", "--format=", "HEAD"], dest).stdout
    else:
        report.notes.append("Nothing to commit. No file was smaller as WebP.")
    write_report(report)
    meta = work_root() / f"{safe}.prepare.json"
    import json

    meta.write_text(
        json.dumps(
            {
                "domain": site.domain,
                "branch": branch,
                "repo": str(dest),
                "report_md": render_markdown(report),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return PrepareResult(
        domain=site.domain,
        branch=branch,
        repo_path=str(dest),
        committed=committed,
        diff_stat=report.diff_stat,
        report=report,
        pushed=False,
    )


def current_branch(repo: Path) -> str:
    return run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo).stdout.strip()


def publish(site: Site, typed: str | None, settings: Settings | None = None) -> dict:
    """Push the prepared imagediet branch and open a PR. Requires a typed domain."""
    token = require_confirm(site.domain, typed)
    if site.report_only:
        raise ReportOnlyError(f"{site.domain} has no github_repo. It is report-only.")
    import json

    meta_path = work_root() / f"{safe_domain(site.domain)}.prepare.json"
    if not meta_path.is_file():
        raise ConfirmRequired("Prepare a local imagediet branch before confirming a pull request.")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if str(meta.get("domain", "")).casefold() != site.domain.casefold():
        raise ConfirmRequired("Prepared branch does not match this domain.")
    repo = Path(str(meta["repo"]))
    if not (repo / ".git").exists():
        raise ConfirmRequired("Prepared repository is missing. Run Prepare again.")
    branch = current_branch(repo)
    if branch != meta.get("branch"):
        raise ConfirmRequired("HEAD is not the prepared imagediet branch.")
    title = f"ImageDiet: lighter images for {site.domain}"
    body = str(meta.get("report_md") or "")
    if len(body) > 60000:
        body = body[:60000] + "\n\n…report truncated for the pull request body.\n"
    plan = plan_publish(domain=site.domain, typed=typed, branch=branch, title=title, body=body)
    url = execute_publish(plan, repo, token=token, domain=site.domain)
    return {"domain": site.domain, "branch": branch, "pushed": True, "pr_url": url, "originals_deleted": False}


def repo_root() -> Path:
    return ROOT
