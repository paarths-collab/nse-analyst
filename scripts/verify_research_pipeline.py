#!/usr/bin/env python3
"""Compatibility wrapper for moved verification pipeline script."""

import runpy
from pathlib import Path


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "qa" / "verify" / "verify_research_pipeline.py"
    runpy.run_path(str(target), run_name="__main__")
