from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from imagediet.config import (
    HUB_PATH,
    HUB_REPO,
    SAMPLE_DOMAIN,
    UNSET,
    fetch_hub_text,
    fetch_via_gh,
    load_site_list,
    merge_sites,
    parse_inventory,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "inventory.yaml"
OVERRIDE = ROOT / "tests" / "fixtures" / "override.yaml"


def test_parse_inventory_fixture():
    drafts = parse_inventory(FIXTURE.read_text(encoding="utf-8"))
    by_domain = {draft.domain: draft for draft in drafts}
    assert set(by_domain) == {"big.example.test", "static.example.test", "paused.example.test"}
    assert by_domain["big.example.test"].github_repo == "grummpy/big-example"
    assert by_domain["static.example.test"].github_repo is UNSET
    assert by_domain["static.example.test"].status == "parked"
    assert by_domain["paused.example.test"].note == "Override me"


def test_override_wins_per_domain_and_sample_is_added():
    hub = parse_inventory(FIXTURE.read_text(encoding="utf-8"))
    local = parse_inventory(OVERRIDE.read_text(encoding="utf-8"))
    sites = {site.domain: site for site in merge_sites(hub, local)}
    paused = sites["paused.example.test"]
    assert paused.note == "local wins"
    assert paused.url == "http://127.0.0.1:9/"
    assert paused.github_repo == "local/paused"
    assert paused.branch_base == "develop"
    assert paused.image_root == "public"
    assert paused.status == "paused"
    assert paused.source == "override"
    assert sites["big.example.test"].github_repo is None
    assert sites["big.example.test"].report_only is True
    assert sites["big.example.test"].note == "cleared repo"
    assert sites["static.example.test"].report_only is True
    assert sites["static.example.test"].source == "hub"
    assert sites["extra.example.test"].source == "override"
    assert sites[SAMPLE_DOMAIN].source == "sample"
    assert sites[SAMPLE_DOMAIN].report_only is True


def test_unsafe_domain_is_dropped():
    drafts = parse_inventory("sites:\n  - domain: ../etc/passwd\n    note: no\n  - domain: ok.example.test\n")
    assert [draft.domain for draft in drafts] == ["ok.example.test"]


def test_load_site_list_reads_fixture_without_network(monkeypatch):
    monkeypatch.setenv("IMAGEDIET_INVENTORY_FILE", str(FIXTURE))

    def boom(*_args, **_kwargs):
        raise AssertionError("network or git was called while reading a fixture")

    monkeypatch.setattr(subprocess, "run", boom)
    catalog = load_site_list()
    assert catalog.hub_status == "file"
    domains = {site.domain for site in catalog.sites}
    assert "big.example.test" in domains
    assert SAMPLE_DOMAIN in domains


def test_offline_hub_fetch_does_not_call_out(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("offline hub fetch tried to run a command")

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(Exception, match="offline"):
        fetch_hub_text()


def test_fetch_via_gh_decodes_fixture(monkeypatch):
    import base64

    payload = base64.b64encode(FIXTURE.read_bytes()).decode("ascii")

    def fake_run(cmd, **_kwargs):
        assert cmd[:3] == ["gh", "api", f"repos/{HUB_REPO}/contents/{HUB_PATH}"]
        return subprocess.CompletedProcess(cmd, 0, payload + "\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    text = fetch_via_gh()
    assert "big.example.test" in text
