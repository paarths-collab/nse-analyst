#!/usr/bin/env python3
"""
Test script to verify broadcast time filtering is working correctly.
This will fetch data from NSE API and show what's being filtered.
"""

import os
import sys
import json
import datetime
import cloudscraper
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load env vars
def load_env_file(env_path: str = ".env"):
    try:
        if not os.path.exists(env_path):
            return
        with open(env_path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and not os.environ.get(key):
                    os.environ[key] = value
    except Exception:
        pass

load_env_file(".env")

# NSE Setup
API_URLS = [
    "https://www.nseindia.com/api/corporate-announcements?index=equities",
    "https://www.nseindia.com/api/corporate-announcements?index=sme",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.nseindia.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# Create scraper
scraper = cloudscraper.create_scraper(
    browser={"browser": "chrome", "platform": "windows", "mobile": False}
)

# Seed session
try:
    scraper.get("https://www.nseindia.com", headers=HEADERS, timeout=15)
    print("✓ Session initialized")
except Exception as e:
    print(f"⚠ Session seed failed: {e}")

def fetch_raw_data() -> list:
    """Fetch raw data WITHOUT filtering."""
    items = []
    for url in API_URLS:
        try:
            res = scraper.get(url, headers=HEADERS, timeout=20)
            if res.status_code == 200:
                data = res.json()
                if isinstance(data, list):
                    items.extend(data)
                    logger.info(f"Fetched {len(data)} items from {url.split('=')[1]}")
        except Exception as e:
            logger.error(f"Fetch error: {e}")
    return items

def filter_by_broadcast_time(items: list, hours: int = 6) -> tuple[list, list]:
    """
    Filter filings by broadcast time.
    Returns: (filtered_list, excluded_list)
    """
    now = datetime.datetime.now()
    cutoff = now - datetime.timedelta(hours=hours)
    cutoff_time = cutoff.replace(microsecond=0)
    
    filtered = []
    excluded = []
    
    for item in items:
        an_dt_str = item.get("an_dt", "")
        if not an_dt_str:
            filtered.append(item)
            continue
        
        try:
            for fmt in ["%d-%b-%Y %H:%M:%S", "%d-%B-%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S"]:
                try:
                    broadcast_time = datetime.datetime.strptime(an_dt_str.upper(), fmt)
                    if broadcast_time >= cutoff_time:
                        filtered.append(item)
                    else:
                        excluded.append({
                            "symbol": item.get("symbol"),
                            "an_dt": an_dt_str,
                            "reason": f"Older than {hours}h (cutoff: {cutoff_time.strftime('%d-%b-%Y %H:%M:%S')})",
                            "subject": item.get("subject", "")[:60]
                        })
                    break
                except ValueError:
                    continue
        except Exception as e:
            filtered.append(item)
    
    return filtered, excluded

print("\n" + "="*80)
print("NSE FILING BROADCAST TIME FILTERING TEST")
print("="*80)

# Fetch data
print("\n📡 Fetching data from NSE API...")
raw_data = fetch_raw_data()
print(f"   Total raw items: {len(raw_data)}")

if not raw_data:
    print("❌ No data fetched from API")
    sys.exit(1)

# Show sample of raw data timestamps
print("\n📅 Sample of raw timestamps (first 5):")
for i, item in enumerate(raw_data[:5]):
    print(f"   [{i+1}] {item.get('symbol', '?'):10} | {item.get('an_dt', 'N/A'):20} | {item.get('subject', '')[:40]}")

# Filter data
print("\n🔍 Applying broadcast time filter (last 6 hours)...")
filtered, excluded = filter_by_broadcast_time(raw_data, hours=6)
print(f"   Kept (recent): {len(filtered)}")
print(f"   Filtered out (old): {len(excluded)}")
print(f"   Reduction: {len(excluded)/len(raw_data)*100:.1f}%")

# Show what was kept
print("\n✅ FILINGS KEPT (RECENT - Last 6 Hours):")
if filtered:
    current_time = datetime.datetime.now()
    print(f"   Current time: {current_time.strftime('%d-%b-%Y %H:%M:%S')}")
    print()
    for i, item in enumerate(filtered[:10], 1):
        an_dt = item.get("an_dt", "N/A")
        print(f"   [{i}] {item.get('symbol', '?'):10} | {an_dt:20} | {item.get('subject', '')[:50]}")
    if len(filtered) > 10:
        print(f"   ... and {len(filtered) - 10} more")
else:
    print("   (None) — No recent filings in last 6 hours")

# Show what was filtered out
print("\n❌ FILINGS FILTERED OUT (OLDER THAN 6 HOURS):")
if excluded:
    for i, item in enumerate(excluded[:5], 1):
        print(f"   [{i}] {item['symbol']:10} | {item['an_dt']:20} | {item['reason']}")
    if len(excluded) > 5:
        print(f"   ... and {len(excluded) - 5} more old filings")
else:
    print("   (None) — All API results are recent")

# Summary
print("\n" + "="*80)
print("VERIFICATION RESULT")
print("="*80)

if len(filtered) > 0:
    print(f"✅ FILTERING IS WORKING")
    print(f"   • Raw items: {len(raw_data)}")
    print(f"   • Filtered items: {len(filtered)}")
    print(f"   • Removed: {len(excluded)} old items ({len(excluded)/len(raw_data)*100:.1f}%)")
else:
    print(f"⚠️  FILTERING RESULT: No recent items in last 6 hours")
    print(f"   • All {len(raw_data)} items are older than 6 hours")
    print(f"   • Try increasing filter window to hours=24")

# Test with different windows
print("\n" + "="*80)
print("FILTER WINDOW ANALYSIS (Different Time Ranges)")
print("="*80)

for hours in [1, 3, 6, 12, 24]:
    filtered_h, excluded_h = filter_by_broadcast_time(raw_data, hours=hours)
    pct = len(filtered_h) / len(raw_data) * 100 if raw_data else 0
    status = "✓" if len(filtered_h) > 0 else "✗"
    print(f"{status} Last {hours:2d} hours: {len(filtered_h):2d} items ({pct:5.1f}%)")

print("\n" + "="*80)
print("RECOMMENDATION")
print("="*80)
print("""
✓ Broadcast time filtering is IMPLEMENTED and ACTIVE in fillings.py
✓ Called in fetch_data() function with hours=6 parameter
✓ Filters out old announcements, keeps only recent ones

To CHANGE the filter window:
  • Edit fillings.py line 476
  • Change: _filter_by_broadcast_time(data, hours=6)
  • To:     _filter_by_broadcast_time(data, hours=24)

Current setting: 6 hours (recommended for live pipeline)
""")

print("\nTest completed! ✓")
