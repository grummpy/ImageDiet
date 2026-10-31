from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from imagediet.config import Settings, Site
from imagediet.gitops import ConfirmRequired, plan_publish, prepare, publish, require_confirm, run_git


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", *args],
        cwd=str(repo),
        check=check,
        capture_output=True,
        text=True,
    )


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=str(root), check=True, capture_output=True, text=True)
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init")


def _branches(repo: Path) -> str:
    return _git(repo, "branch", "--list").stdout


def _global_email() -> str:
    proc = subprocess.run(["git", "config", "--global", "--get", "user.email"], capture_output=True, text=True)
    return proc.stdout.strip()


def _site(root: Path) -> Site:
    return Site(
        domain="synthetic.test",
        url=root.as_uri(),
        github_repo=str(root),
        branch_base="main",
        image_root=".",
        status="test",
        note="synthetic",
        source="override",
    )


def test_require_confirm_rejects_anything_but_the_domain():
    with pytest.raises(ConfirmRequired):
        require_confirm("synthetic.test", None)
    with pytest.raises(ConfirmRequired):
        require_confirm("synthetic.test", "")
    with pytest.raises(ConfirmRequired):
        require_confirm("synthetic.test", "other.test")
    token = require_confirm("synthetic.test", "  Synthetic.TEST  ")
    assert token.domain == "synthetic.test"
    with pytest.raises(ConfirmRequired):
        plan_publish(domain="synthetic.test", typed="nope", branch="imagediet/2026-10-31", title="t", body="b")


def test_run_git_refuses_push_without_token(monkeypatch, tmp_path):
    def boom(*_args, **_kwargs):
        raise AssertionError("git push ran without a confirmation token")

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(ConfirmRequired):
        run_git(["push", "-u", "origin", "imagediet/2026-10-31"], tmp_path, token=None, domain="synthetic.test")
    with pytest.raises(ConfirmRequired):
        run_git(["push", "--force", "origin", "imagediet/2026-10-31"], tmp_path, token=require_confirm("synthetic.test", "synthetic.test"), domain="synthetic.test")


def test_dry_run_scan_writes_no_git_changes(synthetic_site, monkeypatch):
    _init_repo(synthetic_site)
    head = _git(synthetic_site, "rev-parse", "HEAD").stdout.strip()
    status = _git(synthetic_site, "status", "--porcelain").stdout
    email = _global_email()
    from imagediet.report import run_scan

    real_run = subprocess.run

    def boom(*_args, **_kwargs):
        raise AssertionError("dry-run invoked a subprocess")

    monkeypatch.setattr(subprocess, "run", boom)
    report = run_scan(_site(synthetic_site), Settings())
    monkeypatch.setattr(subprocess, "run", real_run)
    assert report.files_modified is False
    assert report.pushed is False
    assert report.originals_deleted is False
    assert report.public()["pushed"] is False
    reasons = {reason for page in report.pages for image in page.findings for reason in image.reasons}
    assert "file_size" in reasons and "overscale" in reasons
    assert _git(synthetic_site, "rev-parse", "HEAD").stdout.strip() == head
    assert _git(synthetic_site, "status", "--porcelain").stdout == status
    assert not list(synthetic_site.rglob("*.webp"))
    assert _global_email() == email
    assert Path(report.paths["json"]).is_file()
    assert "Dry-run" in Path(report.paths["md"]).read_text(encoding="utf-8")


def test_prepare_commits_locally_and_does_not_push(synthetic_site):
    _init_repo(synthetic_site)
    before = {path.relative_to(synthetic_site).as_posix(): path.read_bytes() for path in synthetic_site.rglob("*") if path.is_file() and ".git" not in path.parts}
    branches = _branches(synthetic_site)
    email = _global_email()
    result = prepare(_site(synthetic_site), Settings())
    assert result.pushed is False
    assert result.committed is True
    assert result.branch.startswith("imagediet/")
    assert _branches(synthetic_site) == branches
    assert _global_email() == email
    clone = Path(result.repo_path)
    names = _git(clone, "diff-tree", "--no-commit-id", "--name-status", "-r", "HEAD").stdout.splitlines()
    assert any(line.startswith("A\t") and line.endswith(".webp") for line in names)
    assert not any(line.startswith("D\t") and Path(line.split("\t", 1)[1]).suffix.lower() in {".jpg", ".jpeg", ".png"} for line in names)
    for relative, data in before.items():
        if Path(relative).suffix.lower() in {".jpg", ".jpeg", ".png"}:
            assert (synthetic_site / relative).read_bytes() == data
            assert (clone / relative).read_bytes() == data
    html = (clone / "index.html").read_text(encoding="utf-8")
    assert "<picture>" in html and 'src="images/big.jpg"' in html
    assert (clone / "images" / "big.webp").stat().st_size < (clone / "images" / "big.jpg").stat().st_size


def _fake_gh(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "gh.log"
    script = bin_dir / "gh"
    script.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$GH_LOG\"\necho https://example.test/pr/1\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return log


def test_publish_blocked_until_domain_is_typed(synthetic_site, tmp_path, monkeypatch):
    _init_repo(synthetic_site)
    result = prepare(_site(synthetic_site), Settings())
    log = _fake_gh(tmp_path)
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    monkeypatch.setenv("PATH", str(tmp_path / "bin") + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("GH_LOG", str(log))
    site = _site(synthetic_site)
    with pytest.raises(ConfirmRequired):
        publish(site, None)
    with pytest.raises(ConfirmRequired):
        publish(site, "wrong.test")
    assert not log.exists() or log.read_text(encoding="utf-8").strip() == ""
    assert "imagediet/" not in _branches(synthetic_site)
    clone = Path(result.repo_path)
    _git(clone, "remote", "set-url", "origin", str(bare))
    monkeypatch.delenv("IMAGEDIET_OFFLINE")
    published = publish(site, "synthetic.test")
    assert published["pushed"] is True
    assert published["pr_url"] == "https://example.test/pr/1"
    assert published["originals_deleted"] is False
    assert "pr create" in log.read_text(encoding="utf-8")
    remote_heads = subprocess.run(["git", "ls-remote", "--heads", str(bare)], check=True, capture_output=True, text=True).stdout
    assert "imagediet/" in remote_heads
    assert "imagediet/" not in _branches(synthetic_site)
