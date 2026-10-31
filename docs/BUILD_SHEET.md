# ImageDiet — Build Sheet

Owner: Chris Decker (grummpy). Public repo: grummpy/ImageDiet. Synthetic data only; no secrets.

## Goal
Crawl Chris's Hostinger-hosted sites, find oversized images, convert them to WebP (originals kept + `<picture>` fallback), and for each site prepare a branch + PR with a before/after page-weight report. Nothing is pushed without explicit confirmation in the UI.

## Site list
1. Primary: read `configs/inventory.yaml` from `grummpy/my_domains` (fields `domain`, optional `github_repo`, `status`, `note`) via `gh api` or public raw URL. Sites without `github_repo` are report-only.
2. Fallback / override: local `config/sites.yaml` (`sites: [{domain, url, github_repo, branch_base, image_root}]`). Override wins per domain.
3. Never change DNS, never touch Hostinger panel settings.

## Detection
- File-size: image bytes > `max_bytes` (default 200 KB).
- Rendered vs intrinsic: headless render (Playwright, optional) or HTML/CSS width/height attrs + `srcset`; flag when intrinsic width > `ratio` (default 1.5x) × max rendered width at 1440px and 390px viewports.
- Also flag non-WebP/AVIF JPEG/PNG over threshold, missing width/height attrs.

## Conversion
- Pillow WebP (quality default 80, resize to 2x max rendered width, preserve aspect, strip EXIF/GPS).
- Write `name.webp` beside original; never delete or overwrite originals.
- Patch HTML to `<picture><source type="image/webp" srcset="…webp"><img src="original" …></picture>`.
- Skip if WebP isn't smaller.

## Per-site PR flow
1. Dry-run (default): crawl + report only, writes `reports/<domain>/<timestamp>/report.{md,json,html}`.
2. Prepare: clone repo to `work/`, branch `imagediet/<date>`, apply conversions + HTML patches, commit locally.
3. Confirm: UI shows diff summary + before/after weight table; user types domain to confirm → push branch + `gh pr create` with report as body. No PR without this step. CLI requires `--confirm <domain>`.

## Report
Per page: total image bytes before/after, savings %, per-image rows (path, old/new bytes, intrinsic vs rendered px, action).

## UI (local, 127.0.0.1:8766)
Sites list (source: hub/override), Scan (dry-run), Findings table, Prepare branch, Confirm & open PR (modal with typed confirm), Settings (thresholds, quality). FastAPI or stdlib http.server + vanilla JS.

## Layout
`imagediet/{config,crawl,detect,convert,patch,gitops,report,server}.py`, `web/`, `config/sites.example.yaml`, `samples/testsite/` (synthetic generated site), `tests/`, `launch-mac.command`, `launch-windows.bat`, `launch-linux.sh`, `art/cover.png`, `art/icon.png`.

## Tests (pytest, offline)
Synthetic local test site served on a random port with generated images (big JPEG, PNG, already-small, upscaled-in-CSS). Assert: detection of both kinds; WebP created + smaller; originals byte-identical after run; `<picture>` fallback present; dry-run writes no git changes; push/PR blocked without confirm (gitops mocked); inventory.yaml parsing with fixture.

## Launchers
Create venv, install requirements, run server, open browser. Same pattern as LoraResultLab.

## Done when
Tests green, UI tried end to end on sample site, README with screenshots/art, merged to main.
