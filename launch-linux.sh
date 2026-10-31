#!/usr/bin/env bash
# One-click launcher for ImageDiet. Binds to 127.0.0.1 only.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
ICON="${ROOT}/art/icon.png"
COVER="${ROOT}/art/cover.png"
if [ ! -f "$ICON" ] || [ ! -f "$COVER" ]; then
  echo "ImageDiet needs art/icon.png and art/cover.png." >&2
  exit 1
fi
export IMAGEDIET_HOST=127.0.0.1
export IMAGEDIET_PORT="${IMAGEDIET_PORT:-8766}"
export IMAGEDIET_ICON="$ICON"
export IMAGEDIET_COVER="$COVER"
if command -v python3 >/dev/null 2>&1; then
  PY=python3
else
  PY=python
fi
if [ ! -d .venv ]; then
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
echo "ImageDiet — dry-run is the default. Nothing is pushed unless you type the domain in the UI."
echo "Icon: $ICON"
echo "Cover: $COVER"
(sleep 1 && (xdg-open "http://127.0.0.1:${IMAGEDIET_PORT}" >/dev/null 2>&1 || true)) &
exec python -m imagediet
