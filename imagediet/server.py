"""Local UI on 127.0.0.1:8766. Dry-run unless the domain is typed to confirm."""

from __future__ import annotations

import json
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from imagediet.config import (
    ROOT,
    ImageDietError,
    art_paths,
    load_settings,
    load_site_list,
    offline,
    reports_root,
    sample_root,
    save_settings,
    Settings,
)
from imagediet.gitops import ConfirmRequired, ReportOnlyError, prepare, publish
from imagediet.report import run_scan

STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
}


class State:
    def __init__(self) -> None:
        self.settings: Settings = load_settings()


def _safe_file(root: Path, relative: str) -> Path | None:
    raw = unquote(relative).lstrip("/")
    if not raw or raw.endswith("/"):
        raw = raw + "index.html"
    parts = Path(raw).parts
    if ".." in parts or raw.startswith("/"):
        return None
    candidate = (root / raw).resolve()
    base = root.resolve()
    if candidate != base and base not in candidate.parents:
        return None
    if not candidate.is_file():
        return None
    return candidate


class Handler(BaseHTTPRequestHandler):
    server_version = "ImageDiet/1.0"

    @property
    def state(self) -> State:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("ImageDiet %s\n" % (fmt % args))

    def do_GET(self) -> None:
        try:
            self._get()
        except Exception as exc:
            self._error(500, str(exc))

    def do_POST(self) -> None:
        try:
            self._post()
        except (ConfirmRequired, ReportOnlyError, ImageDietError) as exc:
            self._error(400, str(exc))
        except Exception as exc:
            self._error(500, str(exc))

    def _get(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path in {"/", "/index.html"}:
            self._send_file(ROOT / "web" / "index.html")
            return
        if path == "/app.js":
            self._send_file(ROOT / "web" / "app.js")
            return
        if path == "/styles.css":
            self._send_file(ROOT / "web" / "styles.css")
            return
        if path in {"/art/icon.png", "/favicon.ico"}:
            self._send_file(art_paths()[0])
            return
        if path == "/art/cover.png":
            self._send_file(art_paths()[1])
            return
        if path == "/api/health":
            host, port = self.server.server_address[:2]
            self._send_json(
                200,
                {
                    "ok": True,
                    "host": host,
                    "port": port,
                    "dry_run_default": True,
                    "deletes_originals": False,
                    "offline": offline(),
                    "confirm_required": True,
                },
            )
            return
        if path == "/api/sites":
            self._send_json(200, load_site_list().public())
            return
        if path == "/api/settings":
            self._send_json(200, self.state.settings.to_dict())
            return
        if path.startswith("/sample/"):
            target = _safe_file(sample_root(), path[len("/sample/") :])
            if target is None:
                self._error(404, "Sample file not found.")
                return
            self._send_file(target)
            return
        if path.startswith("/reports/"):
            target = _safe_file(reports_root(), path[len("/reports/") :])
            if target is None:
                self._error(404, "Report not found.")
                return
            self._send_file(target)
            return
        self._error(404, "Not found.")

    def _post(self) -> None:
        parsed = urlparse(self.path)
        body = self._read_json()
        if parsed.path == "/api/settings":
            current = self.state.settings.to_dict()
            for key in ("max_bytes", "ratio", "quality", "use_playwright"):
                if key in body:
                    current[key] = body[key]
            self.state.settings = Settings.from_dict(current)
            save_settings(self.state.settings)
            self._send_json(200, self.state.settings.to_dict())
            return
        domain = str(body.get("domain") or "").strip()
        catalog = load_site_list()
        site = catalog.by_domain(domain)
        if site is None:
            self._error(404, f"Unknown domain: {domain}")
            return
        if parsed.path == "/api/scan":
            report = run_scan(site, self.state.settings)
            payload = report.public()
            payload["pushed"] = False
            payload["files_modified"] = False
            payload["originals_deleted"] = False
            self._send_json(200, payload)
            return
        if parsed.path == "/api/prepare":
            result = prepare(site, self.state.settings)
            self._send_json(200, result.public())
            return
        if parsed.path == "/api/confirm":
            result = publish(site, body.get("typed"), self.state.settings)
            result["originals_deleted"] = False
            self._send_json(200, result)
            return
        self._error(404, "Not found.")

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            raise ImageDietError("Request body is too large.")
        raw = self.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ImageDietError("JSON body must be an object.")
        return data

    def _send_file(self, path: Path) -> None:
        if not path.is_file():
            self._error(404, "File not found.")
            return
        data = path.read_bytes()
        content_type = STATIC_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0]
        self._send_bytes(200, content_type or "application/octet-stream", data)

    def _send_json(self, code: int, payload: dict) -> None:
        self._send_bytes(code, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))

    def _send_bytes(self, code: int, content_type: str, data: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _error(self, code: int, message: str) -> None:
        self._send_json(
            code,
            {"error": message, "pushed": False, "originals_deleted": False},
        )


def create_server(host: str = "127.0.0.1", port: int = 8766) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost"}:
        raise ImageDietError("ImageDiet only binds to 127.0.0.1.")
    icon, cover = art_paths()
    if not icon.is_file() or not cover.is_file():
        raise ImageDietError(f"Missing branding art. Expected {icon} and {cover}.")
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.state = State()  # type: ignore[attr-defined]
    return server


def serve(host: str | None = None, port: int | None = None) -> None:
    chosen_host = host or os.environ.get("IMAGEDIET_HOST", "127.0.0.1")
    chosen_port = int(port if port is not None else os.environ.get("IMAGEDIET_PORT", "8766"))
    server = create_server(chosen_host, chosen_port)
    icon, cover = art_paths()
    bound_host, bound_port = server.server_address[:2]
    print(f"ImageDiet listening on http://{bound_host}:{bound_port}")
    print(f"Icon: {icon}")
    print(f"Cover: {cover}")
    print("Dry-run is the default. No branch is pushed and no pull request is opened")
    print("unless you type the domain to confirm.")
    print("Original images are never deleted. WebP is an extra file; older browsers")
    print("keep receiving the original.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nImageDiet stopped.")
    finally:
        server.server_close()
