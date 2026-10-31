from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

from imagediet.config import ROOT, ImageDietError
from imagediet.server import create_server

ROOT_DIR = Path(__file__).resolve().parents[1]


def _request(url: str, method: str = "GET", body: dict | None = None) -> tuple[int, dict | bytes]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
            content_type = response.headers.get("Content-Type", "")
            if "json" in content_type:
                return response.status, json.loads(raw.decode("utf-8"))
            return response.status, raw
    except urllib.error.HTTPError as exc:
        payload = json.loads(exc.read().decode("utf-8"))
        return exc.code, payload


def test_server_refuses_public_bind():
    with pytest.raises(ImageDietError):
        create_server("0.0.0.0", 8766)


def test_ui_scans_synthetic_site_and_blocks_publish(synthetic_site, tmp_path, monkeypatch):
    import functools
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

    static = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(synthetic_site)))
    thread = threading.Thread(target=static.serve_forever, daemon=True)
    thread.start()
    port = static.server_address[1]
    sites = tmp_path / "sites.yaml"
    sites.write_text(
        "sites:\n"
        "  - domain: synthetic.test\n"
        f"    url: http://127.0.0.1:{port}/\n"
        "    note: generated for the test\n"
        "    status: sample\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("IMAGEDIET_SITES_FILE", str(sites))
    monkeypatch.setenv("IMAGEDIET_SAMPLE_ROOT", str(synthetic_site))
    before = {path.name: path.read_bytes() for path in (synthetic_site / "images").iterdir()}
    app = create_server("127.0.0.1", 0)
    app_thread = threading.Thread(target=app.serve_forever, daemon=True)
    app_thread.start()
    base = f"http://127.0.0.1:{app.server_address[1]}"
    try:
        health_code, health = _request(base + "/api/health")
        assert health_code == 200
        assert health["host"] == "127.0.0.1"
        assert health["dry_run_default"] is True
        assert health["deletes_originals"] is False
        page_code, page = _request(base + "/")
        assert page_code == 200
        assert b"Dry-run is the default" in page
        assert b"/art/icon.png" in page
        assert b"/art/cover.png" in page
        icon_code, icon = _request(base + "/art/icon.png")
        cover_code, cover = _request(base + "/art/cover.png")
        assert icon_code == 200 and icon.startswith(b"\x89PNG")
        assert cover_code == 200 and cover.startswith(b"\x89PNG")
        _code, catalog = _request(base + "/api/sites")
        domains = {site["domain"] for site in catalog["sites"]}
        assert "synthetic.test" in domains
        assert "sample.imagediet.test" in domains
        code, report = _request(base + "/api/scan", "POST", {"domain": "synthetic.test"})
        assert code == 200, report
        assert report["mode"] == "dry-run"
        assert report["pushed"] is False
        assert report["files_modified"] is False
        assert report["originals_deleted"] is False
        reasons = {reason for page in report["pages"] for image in page["images"] for reason in image["reasons"]}
        assert "file_size" in reasons and "overscale" in reasons
        assert report["totals"]["bytes_after"] < report["totals"]["bytes_before"]
        assert {path.name: path.read_bytes() for path in (synthetic_site / "images").iterdir()} == before
        for typed in ("", "wrong.test", "synthetic.test"):
            status, payload = _request(base + "/api/confirm", "POST", {"domain": "synthetic.test", "typed": typed})
            assert status == 400
            assert payload["pushed"] is False
            assert payload["originals_deleted"] is False
        status, payload = _request(base + "/api/prepare", "POST", {"domain": "synthetic.test"})
        assert status == 400
        assert payload["pushed"] is False
        assert "report-only" in payload["error"]
        assert {path.name: path.read_bytes() for path in (synthetic_site / "images").iterdir()} == before
    finally:
        app.shutdown()
        app.server_close()
        static.shutdown()
        static.server_close()


def test_cli_confirm_refuses_without_flag(tmp_path):
    env = os.environ.copy()
    env["IMAGEDIET_OFFLINE"] = "1"
    env["IMAGEDIET_DISABLE_HUB"] = "1"
    env["PYTHONPATH"] = str(ROOT_DIR)
    env.pop("GH_TOKEN", None)
    env.pop("GITHUB_TOKEN", None)
    proc = subprocess.run(
        [sys.executable, "-m", "imagediet", "confirm", "--domain", "sample.imagediet.test"],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert "confirm" in (proc.stderr + proc.stdout).lower()


def test_cli_scan_sample_is_dry_run(synthetic_site, tmp_path):
    env = os.environ.copy()
    env.update(
        {
            "IMAGEDIET_OFFLINE": "1",
            "IMAGEDIET_DISABLE_HUB": "1",
            "IMAGEDIET_SAMPLE_ROOT": str(synthetic_site),
            "IMAGEDIET_REPORTS": str(tmp_path / "reports"),
            "IMAGEDIET_WORK": str(tmp_path / "work"),
            "IMAGEDIET_SETTINGS": str(tmp_path / "settings.json"),
            "PYTHONPATH": str(ROOT_DIR),
        }
    )
    before = {path.name: path.read_bytes() for path in (synthetic_site / "images").iterdir()}
    proc = subprocess.run(
        [sys.executable, "-m", "imagediet", "scan", "--domain", "sample.imagediet.test"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Nothing was pushed" in proc.stdout
    assert before == {path.name: path.read_bytes() for path in (synthetic_site / "images").iterdir()}
    reports = list((tmp_path / "reports").rglob("report.json"))
    assert reports
    payload = json.loads(reports[0].read_text(encoding="utf-8"))
    assert payload["pushed"] is False
    assert payload["originals_deleted"] is False
