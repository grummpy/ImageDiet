#!/usr/bin/env python3
"""Regenerate samples/testsite. Synthetic pixels only."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from imagediet.samplegen import build_site

if __name__ == "__main__":
    dest = build_site(ROOT / "samples" / "testsite")
    print(dest)
