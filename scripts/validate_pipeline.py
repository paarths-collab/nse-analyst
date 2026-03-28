"""Step-level validation for legacy and new pipeline foundations.

Usage:
    python scripts/validate_pipeline.py
    python scripts/validate_pipeline.py --skip-legacy
    python scripts/validate_pipeline.py --skip-foundation
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str


def _run_command(cmd: list[str], timeout_s: int) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def check_legacy_pipeline() -> CheckResult:
    cmd = [sys.executable, "fillings.py", "--scrape-only", "--once", "--hours", "1"]
    try:
        code, out, err = _run_command(cmd, timeout_s=180)
    except subprocess.TimeoutExpired:
        return CheckResult(
            name="legacy_pipeline",
            status="failed",
            detail="Timed out while running scrape-only single pass",
        )

    if code == 0:
        return CheckResult(
            name="legacy_pipeline",
            status="passed",
            detail="Scrape-only single pass completed",
        )

    detail = err or out or "Unknown error"
    return CheckResult(name="legacy_pipeline", status="failed", detail=detail[:400])


def check_foundation_pipeline() -> CheckResult:
    cmd = [sys.executable, "scripts/health_check.py"]
    try:
        code, out, err = _run_command(cmd, timeout_s=60)
    except subprocess.TimeoutExpired:
        return CheckResult(
            name="foundation_pipeline",
            status="failed",
            detail="Timed out while checking database and redis health",
        )

    if code == 0 and "HEALTHY" in out:
        return CheckResult(
            name="foundation_pipeline",
            status="passed",
            detail="Database and redis are reachable",
        )

    detail = err or out or "Unknown error"
    if "Missing required env var" in detail:
        return CheckResult(
            name="foundation_pipeline",
            status="skipped",
            detail="Set DATABASE_URL and REDIS_URL to run foundation health checks",
        )
    return CheckResult(name="foundation_pipeline", status="failed", detail=detail[:400])


def check_scraping_pipeline() -> CheckResult:
    cmd = [
        sys.executable,
        "scripts/scrape_sources.py",
        "--shard",
        "1",
        "--live-only",
        "--max-items",
        "1",
        "--concurrency",
        "3",
        "--output",
        "scraped_events_validation.json",
    ]
    try:
        code, out, err = _run_command(cmd, timeout_s=180)
    except subprocess.TimeoutExpired:
        return CheckResult(
            name="scraping_pipeline",
            status="failed",
            detail="Timed out while running shard scrape validation",
        )

    if code == 0:
        return CheckResult(
            name="scraping_pipeline",
            status="passed",
            detail="Shard scrape validation completed",
        )

    detail = err or out or "Unknown error"
    return CheckResult(name="scraping_pipeline", status="failed", detail=detail[:400])


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate legacy and new pipeline foundations")
    parser.add_argument("--skip-legacy", action="store_true", help="Skip legacy pipeline check")
    parser.add_argument("--skip-scraper", action="store_true", help="Skip scraping pipeline check")
    parser.add_argument("--skip-foundation", action="store_true", help="Skip foundation pipeline check")
    args = parser.parse_args()

    results: list[CheckResult] = []

    if not args.skip_legacy:
        results.append(check_legacy_pipeline())

    if not args.skip_scraper:
        results.append(check_scraping_pipeline())

    if not args.skip_foundation:
        results.append(check_foundation_pipeline())

    failed = any(r.status == "failed" for r in results)
    skipped = any(r.status == "skipped" for r in results)

    if failed:
        overall = "failed"
    elif skipped:
        overall = "passed_with_skips"
    else:
        overall = "passed"

    payload = {
        "overall": overall,
        "checks": [asdict(r) for r in results],
    }
    print(json.dumps(payload, indent=2))

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
