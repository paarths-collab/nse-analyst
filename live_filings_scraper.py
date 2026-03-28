#!/usr/bin/env python3
"""Compatibility wrapper for moved live filings scraper script."""

import runpy
from pathlib import Path

if __name__ == "__main__":
    target = Path(__file__).resolve().parent / "pipelines" / "filings" / "live_filings_scraper.py"
    runpy.run_path(str(target), run_name="__main__")
