#!/usr/bin/env python3
"""Compatibility wrapper for moved unified pipeline entrypoint."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipelines.main.unified_news_filings_pipeline import main


if __name__ == "__main__":
    raise SystemExit(main())
