"""
Live NSE filings scraper (no LLM analysis).

Goal:
- Detect new filings quickly
- Download filing PDF as soon as available
- Extract and print content to terminal

Usage:
    .\.venv\Scripts\Activate.ps1; python .\live_filings_scraper.py
    .\.venv\Scripts\Activate.ps1; python .\live_filings_scraper.py --once
"""

import os
import sys
import json
import time
import argparse
import unicodedata

import fillings

SEEN_FILE = "seen_live_scraper.json"
POLL_SECONDS = 30
DISPLAY_MAX_CHARS = 12000
DEFAULT_WINDOW_HOURS = 6
GARBAGE_CONTROL_THRESHOLD = 0.30
GARBAGE_NON_ASCII_THRESHOLD = 0.35


def load_seen() -> set:
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return set(data) if isinstance(data, list) else set()
        except Exception:
            return set()
    return set()


def save_seen(seen: set):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(seen)), f, indent=2)


def make_live_key(item: dict) -> str:
    """Stable key for dedup across scans and mixed API payload shapes."""
    symbol = (item.get("symbol") or "").strip().upper()
    an_dt = (item.get("an_dt") or "").strip()
    subject = (item.get("subject") or "").strip().lower()
    pdf_url = (item.get("attchmntFile") or "").strip().lower()

    # Prefer broadcast-time identity; include PDF URL to avoid collisions when available.
    base = f"{symbol}|{an_dt}|{subject}|{pdf_url}"
    if base.replace("|", ""):
        return base

    # Last-resort fallback to pipeline UID.
    return fillings.make_uid(item)


def _text_quality_stats(text: str) -> dict:
    if not text:
        return {
            "length": 0,
            "control_ratio": 1.0,
            "non_ascii_ratio": 1.0,
            "alpha_ratio": 0.0,
        }

    total = len(text)
    control = 0
    non_ascii = 0
    alpha = 0

    for ch in text:
        if ch.isalpha():
            alpha += 1
        if ord(ch) > 126:
            non_ascii += 1
        cat = unicodedata.category(ch)
        if cat.startswith("C") and ch not in ("\n", "\r", "\t"):
            control += 1

    return {
        "length": total,
        "control_ratio": control / total,
        "non_ascii_ratio": non_ascii / total,
        "alpha_ratio": alpha / total,
    }


def _is_probably_scanned_or_garbage(text: str) -> tuple[bool, str, dict]:
    stats = _text_quality_stats(text)

    # Very short text can be noisy; do not classify as garbage too aggressively.
    if stats["length"] < 200:
        return False, "too-short-to-classify", stats

    if stats["control_ratio"] > GARBAGE_CONTROL_THRESHOLD:
        return True, "high-control-char-ratio", stats

    # If non-ascii is very high and alphabetic signal is weak, treat as unreadable extraction.
    if stats["non_ascii_ratio"] > GARBAGE_NON_ASCII_THRESHOLD and stats["alpha_ratio"] < 0.30:
        return True, "high-non-ascii-low-alpha", stats

    return False, "ok", stats


def _write_scanned_marker(uid: str, reason: str, stats: dict, desc: str):
    safe_uid = uid.replace(":", "-")
    marker_path = os.path.join(fillings.PDF_DIR, safe_uid + ".scan_marker.txt")
    try:
        with open(marker_path, "w", encoding="utf-8") as f:
            f.write("SCANNED_OR_UNREADABLE_PDF\n")
            f.write(f"reason={reason}\n")
            f.write(f"length={stats.get('length', 0)}\n")
            f.write(f"control_ratio={stats.get('control_ratio', 0.0):.4f}\n")
            f.write(f"non_ascii_ratio={stats.get('non_ascii_ratio', 0.0):.4f}\n")
            f.write(f"alpha_ratio={stats.get('alpha_ratio', 0.0):.4f}\n")
            if desc:
                f.write("\nAPI_DESCRIPTION:\n")
                f.write(desc.strip() + "\n")
    except Exception:
        pass


def _fallback_text_for_scanned(uid: str, desc: str, reason: str, stats: dict) -> str:
    _write_scanned_marker(uid, reason, stats, desc)
    desc_text = desc.strip() if desc else "No API description available."
    return (
        "[Scanned/Image-only or unreadable PDF detected]\n"
        f"Reason: {reason}\n"
        f"Stats: len={stats.get('length', 0)}, control={stats.get('control_ratio', 0.0):.2%}, "
        f"non_ascii={stats.get('non_ascii_ratio', 0.0):.2%}, alpha={stats.get('alpha_ratio', 0.0):.2%}\n\n"
        "Using NSE API description fallback:\n"
        f"{desc_text}"
    )


def get_filing_text(item: dict, uid: str) -> str:
    pdf_url = item.get("attchmntFile", "")
    desc = item.get("attchmntText") or item.get("desc", "") or ""

    if not pdf_url:
        return desc.strip() or "No PDF URL or text description available."

    safe_uid = uid.replace(":", "-")
    txt_path = os.path.join(fillings.PDF_DIR, safe_uid + ".txt")

    if os.path.exists(txt_path):
        try:
            with open(txt_path, "r", encoding="utf-8") as f:
                cached = f.read().strip()
            is_bad, reason, stats = _is_probably_scanned_or_garbage(cached)
            if is_bad:
                return _fallback_text_for_scanned(uid, desc, reason, stats)
            return cached
        except Exception:
            pass

    pdf_path = fillings.download_pdf(pdf_url, safe_uid + ".pdf")
    if not pdf_path:
        return "Failed to download PDF."

    text = fillings.extract_text(pdf_path)
    text = (text or "").strip()

    is_bad, reason, stats = _is_probably_scanned_or_garbage(text)
    if is_bad:
        return _fallback_text_for_scanned(uid, desc, reason, stats)

    if text:
        try:
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            pass

    return text or "PDF downloaded but no extractable text found."


def display_filing(item: dict, uid: str, text: str):
    symbol = item.get("symbol", "UNKNOWN")
    an_dt = item.get("an_dt", "")
    subject = item.get("subject", "")

    print("\n" + "=" * 100)
    print(f"NEW FILING DETECTED | {symbol} | {an_dt}")
    if subject:
        print(f"Subject: {subject}")
    print(f"UID: {uid}")
    print("-" * 100)

    if len(text) > DISPLAY_MAX_CHARS:
        print(text[:DISPLAY_MAX_CHARS])
        print("\n[Output truncated. Full text saved in pdfs/*.txt]")
    else:
        print(text)

    print("=" * 100 + "\n")


def scan_once(seen: set, window_hours: int) -> tuple[int, int]:
    fillings.refresh_session()
    raw = fillings.fetch_data(hours=window_hours)

    unique = []
    temp_seen = set()
    for item in raw:
        key = make_live_key(item)
        if key not in temp_seen:
            temp_seen.add(key)
            unique.append(item)

    new_count = 0
    for item in unique:
        key = make_live_key(item)
        uid = fillings.make_uid(item)
        if key in seen:
            continue

        text = get_filing_text(item, uid)
        display_filing(item, uid, text)

        seen.add(key)
        new_count += 1

    return len(unique), new_count


def main():
    parser = argparse.ArgumentParser(description="Live NSE filing scraper (PDF text only)")
    parser.add_argument("--once", action="store_true", help="Run one scan and exit")
    parser.add_argument("--poll", type=int, default=POLL_SECONDS, help="Polling interval in seconds")
    parser.add_argument("--hours", type=int, default=DEFAULT_WINDOW_HOURS, help="Broadcast filter window in hours")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    os.makedirs(fillings.PDF_DIR, exist_ok=True)

    seen = load_seen()

    print("Live NSE Filings Scraper (No LLM)")
    print(f"Broadcast window : last {args.hours}h")
    print(f"Poll interval    : {args.poll}s")
    print(f"Known filings    : {len(seen)}")

    while True:
        started = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{started}] Scanning NSE announcements...")

        try:
            total, new_found = scan_once(seen, args.hours)
            save_seen(seen)
            print(f"Scan complete: {total} unique in window, {new_found} new displayed")
        except Exception as e:
            print(f"Scan error: {type(e).__name__} - {e}")

        if args.once:
            break

        time.sleep(max(3, args.poll))


if __name__ == "__main__":
    main()
