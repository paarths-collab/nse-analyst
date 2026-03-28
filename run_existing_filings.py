#!/usr/bin/env python3
"""Compatibility wrapper for moved replay script."""

import runpy
from pathlib import Path

if __name__ == "__main__":
    target = Path(__file__).resolve().parent / "pipelines" / "filings" / "run_existing_filings.py"
    runpy.run_path(str(target), run_name="__main__")
