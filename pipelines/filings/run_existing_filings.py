"""
Replay existing NSE filings through the full analysis pipeline.

Usage:
    .\\.venv\\Scripts\\Activate.ps1; python .\\run_existing_filings.py
"""

import time
import argparse
import fillings


def run_existing_once(selected_symbols=None):
    print("Replay Mode: Existing Filings -> Full Pipeline\n")
    fillings.refresh_session()
    raw = fillings.fetch_data(hours=fillings.BROADCAST_WINDOW_HOURS)
    print(f"Using broadcast time window: last {fillings.BROADCAST_WINDOW_HOURS}h")
    if selected_symbols:
        print(f"Telegram selector active: {len(selected_symbols)} symbols")
        print("Replay will analyze all filings; Telegram will be sent only for selected symbols")

    seen = set()
    data = []
    for item in raw:
        uid = fillings.make_uid(item)
        if uid not in seen:
            seen.add(uid)
            data.append(item)

    print(f"Found {len(data)} unique filings. Reprocessing all with force=True.\n")

    ok = 0
    failed = 0
    for i, item in enumerate(data, start=1):
        symbol = item.get("symbol", "UNKNOWN")
        print(f"[{i}/{len(data)}] Reprocessing {symbol}...")
        try:
            fillings.process(item, force=True, telegram_selected_symbols=selected_symbols)
            ok += 1
        except Exception as e:
            failed += 1
            print(f"  ERROR replay item: {e}")

    fillings.processed = fillings.save_processed(fillings.processed)

    print("\nReplay Complete")
    print(f"  Success: {ok}")
    print(f"  Failed : {failed}")
    print(f"  Total tracked: {len(fillings.processed)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replay filings through full analysis pipeline")
    parser.add_argument("--symbols", type=str, default="", help="Comma-separated symbols to process")
    parser.add_argument("--symbols-file", type=str, default="", help="Path to JSON file with symbols list")
    args = parser.parse_args()

    selected_symbols, _ = fillings.resolve_selected_symbols(args.symbols, args.symbols_file)

    start = time.time()
    run_existing_once(selected_symbols=selected_symbols)
    elapsed = time.time() - start
    print(f"Elapsed: {elapsed:.1f}s")
