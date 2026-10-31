"""Site list, thresholds, and paths.

The site list comes from grummpy/my_domains ``configs/inventory.yaml`` (via
``gh api`` or the public raw URL). ``config/sites.yaml`` overrides it per
domain. Tests point ``IMAGEDIET_INVENTORY_FILE`` at a fixture and never call
the network.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
HUB_REPO = "grummpy/my_domains"
HUB_PATH = "configs/inventory.yaml"
SAMPLE_DOMAIN = "sample.imagediet.test"
SAFE_DOMAIN = re.compile(r"[A-Za-z0-9._-]+")
VIEWPORTS = (1440, 390)


class ImageDietError(Exception):
    """Expected failure the UI and CLI can show to a person."""


class InventoryError(ImageDietError):
    pass


class _Unset:
    def __repr__(self) -> str:
        return "UNSET"


UNSET = _Unset()


@dataclass
class SiteDraft:
    domain: str
    url: Any = UNSET
    github_repo: Any = UNSET
    branch_base: Any = UNSET
    image_root: Any = UNSET
    status: Any = UNSET
    note: Any = UNSET
    source: str = "hub"


@dataclass(frozen=True)
class Site:
    domain: str
    url: str
    github_repo: str | None
    branch_base: str
    image_root: str
    status: str
    note: str
    source: str

    @property
    def report_only(self) -> bool:
        return not (self.github_repo or "").strip()

    def public(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "url": self.url,
            "github_repo": self.github_repo,
            "branch_base": self.branch_base,
            "image_root": self.image_root,
            "status": self.status,
            "note": self.note,
            "source": self.source,
            "report_only": self.report_only,
        }


@dataclass
class Settings:
    max_bytes: int = 200 * 1024
    ratio: float = 1.5
    quality: int = 80
    viewports: tuple[int, ...] = VIEWPORTS
    use_playwright: bool = False

    def validate(self) -> None:
        if self.max_bytes < 1:
            raise ImageDietError("max_bytes must be at least 1.")
        if self.ratio <= 0:
            raise ImageDietError("ratio must be greater than 0.")
        if not 1 <= int(self.quality) <= 100:
            raise ImageDietError("quality must be from 1 to 100.")
        if not self.viewports or any(int(v) < 1 for v in self.viewports):
            raise ImageDietError("viewports must be positive widths.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_bytes": self.max_bytes,
            "ratio": self.ratio,
            "quality": self.quality,
            "viewports": list(self.viewports),
            "use_playwright": self.use_playwright,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Settings:
        viewports = data.get("viewports") or VIEWPORTS
        settings = cls(
            max_bytes=int(data.get("max_bytes", 200 * 1024)),
            ratio=float(data.get("ratio", 1.5)),
            quality=int(data.get("quality", 80)),
            viewports=tuple(int(v) for v in viewports),
            use_playwright=bool(data.get("use_playwright", False)),
        )
        settings.validate()
        return settings


def reports_root() -> Path:
    return Path(os.environ.get("IMAGEDIET_REPORTS", ROOT / "reports"))


def work_root() -> Path:
    return Path(os.environ.get("IMAGEDIET_WORK", ROOT / "work"))


def settings_path() -> Path:
    return Path(os.environ.get("IMAGEDIET_SETTINGS", ROOT / "config" / "settings.json"))


def sites_file() -> Path | None:
    override = os.environ.get("IMAGEDIET_SITES_FILE")
    if override:
        return Path(override)
    path = ROOT / "config" / "sites.yaml"
    return path if path.is_file() else None


def sample_root() -> Path:
    override = os.environ.get("IMAGEDIET_SAMPLE_ROOT")
    if override:
        return Path(override)
    return ROOT / "samples" / "testsite"


def art_paths() -> tuple[Path, Path]:
    icon = Path(os.environ.get("IMAGEDIET_ICON", ROOT / "art" / "icon.png"))
    cover = Path(os.environ.get("IMAGEDIET_COVER", ROOT / "art" / "cover.png"))
    return icon, cover


def load_settings() -> Settings:
    path = settings_path()
    if not path.is_file():
        return Settings()
    data = json.loads(path.read_text(encoding="utf-8"))
    return Settings.from_dict(data)


def save_settings(settings: Settings) -> None:
    settings.validate()
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings.to_dict(), indent=2) + "\n", encoding="utf-8")


def offline() -> bool:
    return os.environ.get("IMAGEDIET_OFFLINE") == "1"


def hub_disabled() -> bool:
    return offline() or os.environ.get("IMAGEDIET_DISABLE_HUB") == "1"


def _items_from_yaml(data: Any) -> list[Any]:
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("sites", "domains", "inventory"):
            if isinstance(data.get(key), list):
                return data[key]
        if "domain" in data:
            return [data]
    raise InventoryError(
        "Inventory must be a list, or a mapping with a sites/domains/inventory list. "
        "Each entry needs a domain field and may include github_repo, status, and note."
    )


def _draft_from_item(item: dict[str, Any]) -> SiteDraft | None:
    if "domain" not in item:
        return None
    domain = str(item.get("domain") or "").strip()
    if not SAFE_DOMAIN.fullmatch(domain):
        return None

    def take(key: str) -> Any:
        if key not in item:
            return UNSET
        return item[key]

    return SiteDraft(
        domain=domain,
        url=take("url"),
        github_repo=take("github_repo"),
        branch_base=take("branch_base"),
        image_root=take("image_root"),
        status=take("status"),
        note=take("note"),
    )


def parse_inventory(text: str) -> list[SiteDraft]:
    """Parse inventory.yaml or sites.yaml text. Does not touch the network."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise InventoryError(f"Could not parse inventory YAML: {exc}") from exc
    drafts: list[SiteDraft] = []
    for item in _items_from_yaml(data):
        if not isinstance(item, dict):
            continue
        draft = _draft_from_item(item)
        if draft is not None:
            drafts.append(draft)
    return drafts


def _text_or_unset(value: Any, default: str) -> str:
    if value is UNSET or value is None or value == "":
        return default
    return str(value)


def resolve_draft(draft: SiteDraft) -> Site:
    repo_val = draft.github_repo
    if repo_val is UNSET or repo_val is None or str(repo_val).strip() == "":
        repo: str | None = None
    else:
        repo = str(repo_val).strip()
    url_val = draft.url
    if url_val is UNSET or url_val is None or str(url_val).strip() == "":
        url = f"https://{draft.domain}"
    else:
        url = str(url_val).strip()
    return Site(
        domain=draft.domain,
        url=url,
        github_repo=repo,
        branch_base=_text_or_unset(draft.branch_base, "main"),
        image_root=_text_or_unset(draft.image_root, "."),
        status="" if draft.status is UNSET or draft.status is None else str(draft.status),
        note="" if draft.note is UNSET or draft.note is None else str(draft.note),
        source=draft.source,
    )


def _overlay(base: SiteDraft, over: SiteDraft) -> SiteDraft:
    def pick(field: str) -> Any:
        value = getattr(over, field)
        return getattr(base, field) if value is UNSET else value

    return SiteDraft(
        domain=over.domain.strip() or base.domain,
        url=pick("url"),
        github_repo=pick("github_repo"),
        branch_base=pick("branch_base"),
        image_root=pick("image_root"),
        status=pick("status"),
        note=pick("note"),
        source="override",
    )


def sample_site() -> Site:
    return Site(
        domain=SAMPLE_DOMAIN,
        url="internal://sample",
        github_repo=None,
        branch_base="main",
        image_root=".",
        status="sample",
        note="Synthetic site shipped with ImageDiet. Report only. Not a real website.",
        source="sample",
    )


def merge_sites(hub: list[SiteDraft], local: list[SiteDraft]) -> list[Site]:
    """Local entries win per domain. The synthetic sample is added if missing."""
    by: dict[str, SiteDraft] = {}
    order: list[str] = []
    for draft in hub:
        draft.source = "hub"
        key = draft.domain.casefold()
        by[key] = draft
        order.append(key)
    for draft in local:
        key = draft.domain.casefold()
        if key in by:
            by[key] = _overlay(by[key], draft)
        else:
            draft.source = "override"
            by[key] = draft
            order.append(key)
    sites = [resolve_draft(by[key]) for key in order]
    if not any(site.domain.casefold() == SAMPLE_DOMAIN for site in sites):
        sites.append(sample_site())
    return sites


def fetch_via_gh(repo: str = HUB_REPO, path: str = HUB_PATH) -> str:
    import subprocess

    proc = subprocess.run(
        ["gh", "api", f"repos/{repo}/contents/{path}", "--jq", ".content"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "gh api failed").strip()
        raise InventoryError(detail)
    raw = proc.stdout.strip()
    try:
        return base64.b64decode(raw).decode("utf-8")
    except Exception as exc:
        if "domain:" in raw:
            return raw
        raise InventoryError("gh api did not return base64 inventory content") from exc


def fetch_via_raw(repo: str = HUB_REPO, path: str = HUB_PATH) -> str:
    errors: list[str] = []
    for branch in ("main", "master"):
        url = f"https://raw.githubusercontent.com/{repo}/{branch}/{path}"
        request = urllib.request.Request(url, headers={"User-Agent": "ImageDiet"})
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                return response.read().decode("utf-8")
        except Exception as exc:
            errors.append(f"{branch}: {exc}")
    raise InventoryError("public inventory URL unavailable (" + "; ".join(errors) + ")")


def fetch_hub_text(repo: str = HUB_REPO, path: str = HUB_PATH) -> str:
    if offline():
        raise InventoryError("offline: not fetching hub inventory")
    errors: list[str] = []
    try:
        return fetch_via_gh(repo, path)
    except Exception as exc:
        errors.append(f"gh: {exc}")
    try:
        return fetch_via_raw(repo, path)
    except Exception as exc:
        errors.append(f"raw: {exc}")
    raise InventoryError("; ".join(errors))


@dataclass
class SiteCatalog:
    sites: list[Site]
    hub_status: str
    hub_error: str
    sites_file: str

    def public(self) -> dict[str, Any]:
        return {
            "hub": {
                "repo": HUB_REPO,
                "path": HUB_PATH,
                "status": self.hub_status,
                "error": self.hub_error,
            },
            "sites_file": self.sites_file,
            "sites": [site.public() for site in self.sites],
        }

    def by_domain(self, domain: str) -> Site | None:
        wanted = domain.strip().casefold()
        for site in self.sites:
            if site.domain.casefold() == wanted:
                return site
        return None


def load_site_list() -> SiteCatalog:
    hub_sites: list[SiteDraft] = []
    hub_status = "skipped"
    hub_error = ""
    inventory_file = os.environ.get("IMAGEDIET_INVENTORY_FILE")
    if inventory_file:
        hub_sites = parse_inventory(Path(inventory_file).read_text(encoding="utf-8"))
        hub_status = "file"
    elif hub_disabled():
        hub_status = "disabled"
    else:
        try:
            hub_sites = parse_inventory(fetch_hub_text())
            hub_status = "ok"
        except Exception as exc:
            hub_status = "unavailable"
            hub_error = str(exc)
    local_path = sites_file()
    local: list[SiteDraft] = []
    if local_path is not None and local_path.is_file():
        local = parse_inventory(local_path.read_text(encoding="utf-8"))
    sites = merge_sites(hub_sites, local)
    return SiteCatalog(
        sites=sites,
        hub_status=hub_status,
        hub_error=hub_error,
        sites_file=str(local_path) if local_path else "",
    )


def safe_domain(domain: str) -> str:
    cleaned = domain.strip()
    if not SAFE_DOMAIN.fullmatch(cleaned):
        raise ImageDietError(f"Refusing unsafe domain name: {domain!r}")
    return cleaned
