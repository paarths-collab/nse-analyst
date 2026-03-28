

# import os
# import time
# import json
# import cloudscraper
# import fitz  # PyMuPDF
# import hashlib

# # ==============================
# # SETTINGS
# # ==============================
# CHECK_INTERVAL = 30       # seconds
# PDF_DIR = "pdfs"
# PROCESSED_FILE = "processed.json"
# MAX_PROCESSED_MEMORY = 2000   # keep only last N uids to avoid unbounded growth

# os.makedirs(PDF_DIR, exist_ok=True)

# # ==============================
# # NSE SETUP
# # ==============================
# # BUG 3 FIX: Removed webpage scraper entirely — NSE renders tables via JS,
# # so BeautifulSoup always returns 0 rows. The API is the correct data source.
# API_URLS = [
#     "https://www.nseindia.com/api/corporate-announcements?index=equities",
#     "https://www.nseindia.com/api/corporate-announcements?index=sme",
# ]

# HEADERS = {
#     "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
#     "Referer": "https://www.nseindia.com/",
#     "Accept": "application/json, text/plain, */*",
#     "Accept-Language": "en-US,en;q=0.9",
# }

# # ==============================
# # LOAD/SAVE PROCESSED IDs
# # ==============================
# def load_processed():
#     if os.path.exists(PROCESSED_FILE):
#         with open(PROCESSED_FILE, "r") as f:
#             return set(json.load(f))
#     return set()

# def save_processed(data: set):
#     # BUG 4 FIX: cap the set so the file doesn't grow unboundedly
#     trimmed = set(sorted(data)[-MAX_PROCESSED_MEMORY:])
#     with open(PROCESSED_FILE, "w") as f:
#         json.dump(sorted(list(trimmed)), f, indent=2)
#     return trimmed

# def rebuild_processed_from_disk():
#     """Scan PDF directory to find all successfully processed documents."""
#     disk_processed = set()
#     if os.path.exists(PDF_DIR):
#         for fname in os.listdir(PDF_DIR):
#             if fname.endswith(".txt"):
#                 disk_processed.add(fname.replace(".txt", ""))
#     return disk_processed

# processed = load_processed()
# disk_processed = rebuild_processed_from_disk()
# # BUG 6 FIX: merge disk state INTO processed (not the other way around).
# # Previously the json was loaded first then disk added — but if processed.json
# # already contained the uid, the txt-missing case was never caught.
# processed = processed | disk_processed
# if disk_processed:
#     print(f"📂 Found {len(disk_processed)} already processed files on disk")

# # ==============================
# # SESSION MANAGEMENT
# # ==============================
# scraper = None

# def refresh_session():
#     """
#     BUG 7 FIX: NSE session cookies expire in ~30 min.
#     Re-seed the session at the start of every iteration.
#     """
#     global scraper
#     scraper = cloudscraper.create_scraper(
#         browser={"browser": "chrome", "platform": "windows", "mobile": False}
#     )
#     try:
#         scraper.get("https://www.nseindia.com", headers=HEADERS, timeout=15)
#         print("🔑 Session refreshed")
#     except Exception as e:
#         print(f"⚠ Session seed failed: {e}")

# # ==============================
# # BUILD UNIQUE ID FOR AN ANNOUNCEMENT
# # ==============================
# def make_uid(item: dict) -> str:
#     """
#     BUG 2 FIX: The original uid used item["dt"] which is often identical for
#     batched filings from the same company. Hash the PDF URL for true uniqueness;
#     fall back to symbol + subject + an_dt if no URL.
#     """
#     symbol = item.get("symbol", "UNKNOWN")
#     pdf_url = item.get("attchmntFile", "")
#     if pdf_url:
#         h = hashlib.md5(pdf_url.encode()).hexdigest()[:12]
#     else:
#         raw = f"{symbol}_{item.get('subject','')}_{item.get('an_dt','')}"
#         h = hashlib.md5(raw.encode()).hexdigest()[:12]
#     return f"{symbol}_{h}"

# # ==============================
# # FETCH ANNOUNCEMENTS FROM API
# # ==============================
# def fetch_data() -> list:
#     all_items = []
#     for url in API_URLS:
#         try:
#             res = scraper.get(url, headers=HEADERS, timeout=20)
#             if res.status_code != 200:
#                 print(f"⚠ API returned {res.status_code} for {url}")
#                 continue
#             # Guard: NSE sometimes returns HTML error page instead of JSON
#             if not res.headers.get("Content-Type", "").startswith("application/json"):
#                 print(f"⚠ Non-JSON response from {url} — session may have expired")
#                 continue
#             data = res.json()
#             if isinstance(data, list):
#                 print(f"📡 {url.split('=')[1]}: {len(data)} items")
#                 all_items.extend(data)
#             else:
#                 print(f"⚠ Unexpected response shape from {url}")
#         except Exception as e:
#             print(f"❌ Fetch error ({url}): {type(e).__name__} — {e}")
#     return all_items

# # ==============================
# # DOWNLOAD PDF
# # ==============================
# def download_pdf(url: str, name: str):
#     path = os.path.join(PDF_DIR, name)
#     if os.path.exists(path):
#         return path
#     try:
#         res = scraper.get(url, headers=HEADERS, timeout=30)
#         res.raise_for_status()
#         with open(path, "wb") as f:
#             f.write(res.content)
#         size_kb = len(res.content) / 1024
#         print(f"📥 Downloaded: {name} ({size_kb:.1f} KB)")
#         return path
#     except Exception as e:
#         print(f"❌ PDF download error: {type(e).__name__} — {e}")
#         return None

# # ==============================
# # EXTRACT TEXT FROM PDF
# # ==============================
# def extract_text(pdf_path: str):
#     try:
#         doc = fitz.open(pdf_path)
#         text = "".join(page.get_text() for page in doc)
#         doc.close()
#         if not text.strip():
#             print(f"⚠ No text extracted from {os.path.basename(pdf_path)}")
#         return text
#     except Exception as e:
#         print(f"❌ Extraction error: {type(e).__name__} — {e}")
#         return None

# # ==============================
# # PROCESS ONE ANNOUNCEMENT
# # ==============================
# def process(item: dict):
#     uid = make_uid(item)

#     # BUG 1 FIX: compare uid directly, not just the symbol prefix.
#     if uid in processed:
#         return  # already handled — don't print anything, stay quiet

#     symbol = item.get("symbol", "UNKNOWN")
#     an_dt  = item.get("an_dt", "")
#     desc   = item.get("attchmntText") or item.get("desc", "")
#     pdf_url = item.get("attchmntFile", "")

#     print(f"\n🆕 NEW: {symbol}  |  {an_dt}")
#     if desc:
#         print(f"   📌 {desc[:120]}")

#     if not pdf_url:
#         print("   ⚠ No PDF attachment — marking as processed")
#         processed.add(uid)
#         return

#     safe_name = uid.replace(":", "-")
#     pdf_name  = safe_name + ".pdf"
#     txt_name  = safe_name + ".txt"
#     txt_path  = os.path.join(PDF_DIR, txt_name)

#     if os.path.exists(txt_path):
#         processed.add(uid)
#         return

#     pdf_path = download_pdf(pdf_url, pdf_name)
#     if not pdf_path:
#         print("   ⚠ Skipping — download failed")
#         return

#     text = extract_text(pdf_path)
#     if text is None:
#         return

#     if not text.strip():
#         print("   ⚠ PDF has no extractable text — saving empty marker")

#     try:
#         with open(txt_path, "w", encoding="utf-8") as f:
#             f.write(text)
#         print(f"   ✅ Saved: {txt_name}")
#         processed.add(uid)
#     except Exception as e:
#         print(f"   ❌ Could not save text: {e}")

# # ==============================
# # MAIN LOOP
# # ==============================
# def run():
#     print("🚀 NSE Announcement Tracker — Fixed\n")
#     print(f"   Check interval : {CHECK_INTERVAL}s")
#     print(f"   PDF directory  : {PDF_DIR}")
#     print(f"   Processed (mem): {len(processed)}\n")

#     iteration = 0
#     while True:
#         iteration += 1
#         print(f"\n{'='*70}")
#         print(f"[Iter {iteration}]  {time.strftime('%Y-%m-%d %H:%M:%S')}")
#         print(f"{'='*70}")

#         # BUG 7 FIX: refresh session every iteration, not once at startup
#         refresh_session()

#         raw_data = fetch_data()

#         # BUG 8 FIX: dedup by uid (not by URL) so items without a PDF URL
#         # are not all collapsed to a single entry.
#         seen_uids: set = set()
#         data = []
#         for item in raw_data:
#             uid = make_uid(item)
#             if uid not in seen_uids:
#                 seen_uids.add(uid)
#                 data.append(item)

#         if not data:
#             print("⚠ No data fetched")
#         else:
#             new_count  = 0
#             skip_count = 0
#             for item in data:
#                 uid = make_uid(item)
#                 if uid in processed:
#                     skip_count += 1
#                 else:
#                     new_count += 1
#                 process(item)

#             # BUG 4 FIX: trim processed set before saving
#             global processed
#             processed = save_processed(processed)
#             print(f"\n📊  New: {new_count}  |  Already done: {skip_count}  |  Total tracked: {len(processed)}")

#         print(f"⏳ Next check in {CHECK_INTERVAL}s …")
#         time.sleep(CHECK_INTERVAL)

# # ==============================
# # ENTRY POINT
# # ==============================
# if __name__ == "__main__":
#     try:
#         run()
#     except KeyboardInterrupt:
#         print("\n\n🛑 Stopped by user")
#     except Exception as e:
#         import traceback
#         print(f"\n\n❌ FATAL: {type(e).__name__} — {e}")
#         traceback.print_exc()

"""
NSE Filing Analysis Pipeline — Quant Analyst Edition
=====================================================
Flow per announcement:
  1. Scrape PDF from NSE API (equities + sme)
  2. Extract text (PyMuPDF)
  3. Groq (compound-3-mini) → 1-line event summary
  4. Groq web search → recent news & market context
  5. Groq quant analyst → detailed price impact verdict + reasoning
  6. Print structured alert with predictions and sources

Requirements:
    pip install cloudscraper pymupdf groq

Env vars needed:
    GROQ_API_KEY     → https://console.groq.com (with web search enabled)
"""

import os, sys, time, json, hashlib, textwrap, logging, argparse, unicodedata, re
import cloudscraper
import fitz  # PyMuPDF
from groq import Groq
from typing import Optional, List, Dict

os.makedirs("logs", exist_ok=True)
os.makedirs(os.path.join("data", "cache"), exist_ok=True)

# Setup logging (FIX: Proper structured logging instead of print)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join("logs", "nse_filings.log")),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Telegram notifications (optional)
try:
    from telegram_notifier import send_detailed_telegram_alert
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    logger.warning("Telegram notifier not available")

# NEW: Import rate limiter and filtering modules
try:
    from rate_limiter import rate_limiter, with_rate_limit
    RATE_LIMITER_AVAILABLE = True
except ImportError:
    RATE_LIMITER_AVAILABLE = False
    logger.warning("Rate limiter module not available")

try:
    from content_filter import should_process, score_filing
    FILTER_AVAILABLE = True
except ImportError:
    FILTER_AVAILABLE = False
    logger.warning("Content filter module not available")

try:
    from news_source_tracker import extract_verified_sources
    SOURCE_TRACKER_AVAILABLE = True
except ImportError:
    SOURCE_TRACKER_AVAILABLE = False
    logger.warning("News source tracker not available")


def load_env_file(env_path: str = ".env"):
    """Load KEY=VALUE pairs from .env into process environment."""
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        full_path = os.path.join(base_dir, env_path)
        if not os.path.exists(full_path):
            return

        with open(full_path, "r", encoding="utf-8") as f:
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

# ==============================
# CONFIG
# ==============================
CHECK_INTERVAL       = 30
PDF_DIR              = "pdfs"
PROCESSED_FILE       = "processed.json"
MAX_PROCESSED_MEMORY = 2000
GROQ_SUMMARY_MODEL   = "llama-3.1-8b-instant"
GROQ_WEB_MODEL       = "groq/compound-mini"
GROQ_REASONING_MODEL = "openai/gpt-oss-120b"
SUMMARY_MAX_CHARS    = 6000                    # chars of PDF text sent to Groq


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(f"Invalid {name}='{raw}'. Using default={default}")
        return default


# Main pipeline time window for broadcast filtering.
# Override via env: BROADCAST_WINDOW_HOURS=6
BROADCAST_WINDOW_HOURS = _env_int("BROADCAST_WINDOW_HOURS", 6)


def _env_csv(name: str, default_csv: str) -> list[str]:
    raw = os.environ.get(name, default_csv)
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return parts if parts else [p.strip() for p in default_csv.split(",") if p.strip()]


# Web model failover chain. On rate-limit for one model, pipeline tries the next model.
# Override via env, e.g.:
# GROQ_WEB_MODELS=groq/compound-mini,groq/compound
GROQ_WEB_MODELS = _env_csv("GROQ_WEB_MODELS", f"{GROQ_WEB_MODEL},groq/compound")

API_URLS = [
    "https://www.nseindia.com/api/corporate-announcements?index=equities",
    "https://www.nseindia.com/api/corporate-announcements?index=sme",
]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer":    "https://www.nseindia.com/",
    "Accept":     "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

os.makedirs(PDF_DIR, exist_ok=True)

# Scrape-only mode settings
SEEN_SCRAPE_FILE = os.path.join("data", "cache", "seen_live_scraper.json")
SCRAPE_POLL_SECONDS = 30
SCRAPE_DISPLAY_MAX_CHARS = 12000
GARBAGE_CONTROL_THRESHOLD = 0.30
GARBAGE_NON_ASCII_THRESHOLD = 0.35
DEFAULT_SELECTED_SYMBOLS_FILE = "selected_symbols.json"

# ==============================
# CLIENTS
# ==============================
_groq_key = os.environ.get("GROQ_API_KEY", "")
groq_client = Groq(api_key=_groq_key) if _groq_key else None
scraper     = None   # refreshed each iteration

# ==============================
# PROCESSED ID STORE
# ==============================
def load_processed():
    if os.path.exists(PROCESSED_FILE):
        with open(PROCESSED_FILE) as f:
            return set(json.load(f))
    return set()

def save_processed(data: set) -> set:
    trimmed = set(sorted(data)[-MAX_PROCESSED_MEMORY:])
    with open(PROCESSED_FILE, "w") as f:
        json.dump(sorted(trimmed), f, indent=2)
    return trimmed

def rebuild_from_disk() -> set:
    return {f.replace(".txt", "") for f in os.listdir(PDF_DIR) if f.endswith(".txt")}

processed = load_processed() | rebuild_from_disk()

# ==============================
# SESSION
# ==============================
def refresh_session():
    global scraper
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )
    try:
        scraper.get("https://www.nseindia.com", headers=HEADERS, timeout=15)
    except Exception as e:
        print(f"  WARN session seed: {e}")

# ==============================
# UID
# ==============================
def make_uid(item: dict) -> str:
    symbol  = item.get("symbol", "UNKNOWN")
    pdf_url = item.get("attchmntFile", "")
    raw     = pdf_url if pdf_url else f"{symbol}_{item.get('subject','')}_{item.get('an_dt','')}"
    return f"{symbol}_{hashlib.md5(raw.encode()).hexdigest()[:12]}"

# ==============================
# FETCH
# ==============================
@with_rate_limit("nse.fetch", tokens=1) if RATE_LIMITER_AVAILABLE else lambda x: x
def fetch_data(hours: Optional[int] = None) -> list:
    """
    Fetch announcements from NSE API.
    NEW: Now also filters by broadcast time to prioritize latest filings.
    """
    window_hours = BROADCAST_WINDOW_HOURS if hours is None else hours
    items = []
    for url in API_URLS:
        try:
            logger.info(f"Fetching from {url.split('=')[1]}")
            res = scraper.get(url, headers=HEADERS, timeout=20)
            if res.status_code != 200:
                logger.warning(f"API returned {res.status_code}")
                continue
            if not res.headers.get("Content-Type", "").startswith("application/json"):
                logger.warning(f"Non-JSON response — session may have expired")
                continue
            data = res.json()
            if isinstance(data, list):
                logger.info(f"  {len(data)} items from {url.split('=')[1]}")
                # Filter by broadcast time window configured for this pipeline run.
                items.extend(_filter_by_broadcast_time(data, hours=window_hours))
        except Exception as e:
            logger.error(f"Fetch error: {type(e).__name__} — {e}")
            logger.info(f"Broadcast window active: last {window_hours}h")
    return items


def _filter_by_broadcast_time(items: list, hours: int = 6) -> list:
    """Filter filings to only include those broadcast in the last N hours."""
    import datetime
    
    now = datetime.datetime.now()
    cutoff = now - datetime.timedelta(hours=hours)
    cutoff_str = cutoff.strftime("%d-%b-%Y %H:%M:%S").upper()
    cutoff_time = cutoff.replace(microsecond=0)
    
    filtered = []
    for item in items:
        # Parse broadcast datetime from 'an_dt' field (e.g., "26-MAR-2026 14:30:56" or "26-Mar-2026 09:50:49")
        an_dt_str = item.get("an_dt", "")
        if not an_dt_str:
            filtered.append(item)  # Include if no timestamp (safer)
            continue
        
        try:
            # Try parsing with multiple date formats NSE uses
            for fmt in ["%d-%b-%Y %H:%M:%S", "%d-%B-%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S"]:
                try:
                    broadcast_time = datetime.datetime.strptime(an_dt_str.upper(), fmt)
                    if broadcast_time >= cutoff_time:
                        filtered.append(item)
                    break
                except ValueError:
                    continue
        except Exception as e:
            logger.debug(f"Could not parse timestamp {an_dt_str}: {e}")
            filtered.append(item)  # Include on parse error
    
    logger.info(f"Filtered {len(items)} items -> {len(filtered)} recent (last {hours}h)")
    return filtered

# ==============================
# DOWNLOAD + EXTRACT
# ==============================
def download_pdf(url: str, name: str) -> str | None:
    path = os.path.join(PDF_DIR, name)
    if os.path.exists(path):
        return path
    try:
        res = scraper.get(url, headers=HEADERS, timeout=30)
        res.raise_for_status()
        with open(path, "wb") as f:
            f.write(res.content)
        return path
    except Exception as e:
        print(f"  ERROR download: {e}")
        return None

def extract_text(pdf_path: str) -> str:
    try:
        doc  = fitz.open(pdf_path)
        text = "".join(page.get_text() for page in doc)
        doc.close()
        return text.strip()
    except Exception as e:
        print(f"  ERROR extract: {e}")
        return ""

# ==============================
# STEP 1 — 1-LINE SUMMARY
# ==============================
def summarise(symbol: str, desc: str, full_text: str) -> dict:
    """Generate one-line summary and one-line web-search query."""
    content = full_text[:SUMMARY_MAX_CHARS] if full_text else desc
    prompt  = f"""You are a financial news editor. Given this NSE corporate filing for {symbol},
return STRICT JSON with exactly these keys:
- summary: one sentence (max 25 words) summarising the key event
- web_search_line: one sentence (max 20 words) designed as a focused query to search fresh news around this event

Rules:
- Be specific and include names, deal value, dates, stake, order size, guidance, or penalty if available.
- Do not add markdown or extra keys.

Filing text:
{content}"""
    try:
        resp = groq_client.chat.completions.create(
            model=GROQ_SUMMARY_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=180,
            temperature=0.2,
        )
        raw = resp.choices[0].message.content.strip()
        blob = _extract_json_block(raw, "{", "}")
        data = json.loads(blob if blob else raw)
        summary = (data.get("summary") or "").strip()
        web_line = (data.get("web_search_line") or summary).strip()
        if not summary:
            summary = desc[:120] if desc else "No summary available"
        if not web_line:
            web_line = summary
        return {"summary": summary, "web_search_line": web_line}
    except Exception as e:
        print(f"  ERROR summarise: {e}")
        fallback = desc[:120] if desc else "No summary available"
        return {"summary": fallback, "web_search_line": fallback}

# ==============================
# STEP 2 — WEB SEARCH (via Groq)
# ==============================
def _stream_to_text(stream_resp) -> str:
    chunks = []
    for chunk in stream_resp:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            chunks.append(delta)
    return "".join(chunks).strip()


def _extract_json_block(raw: str, start_char: str, end_char: str) -> str:
    start = raw.find(start_char)
    end = raw.rfind(end_char)
    if start == -1 or end == -1 or end <= start:
        return ""
    return raw[start:end + 1]


def _is_rate_limit_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    markers = ["rate limit", "429", "too many requests", "limit reached"]
    return any(m in msg for m in markers)


def _extract_retry_after_seconds(exc: Exception) -> int:
    msg = str(exc).lower()
    # Best effort parse of common messages like: "retry after 30s" or "retry after 30 seconds"
    m = re.search(r"retry\s+after\s+(\d+)", msg)
    if m:
        try:
            return max(1, int(m.group(1)))
        except ValueError:
            pass
    return 60


def web_search(symbol: str, summary: str) -> tuple[list[dict], List[str]]:
    """
    Use Groq to search web for recent news about this company + event.
    NEW: Returns both results and verified source list (no hallucinations).
    """
    # FIX: Apply rate limiting
    if RATE_LIMITER_AVAILABLE:
        rate_limiter.wait("groq.web", tokens=50)
    
    prompt = f"""Search the web for recent news about {symbol} and this event: {summary}

Find 3-5 relevant news articles or market commentary. Return ONLY a JSON array with no markdown:
[
  {{
    "title": "article headline",
    "snippet": "200-char excerpt",
    "url": "full source URL",
    "date": "publication date"
  }},
  ...
]

CRITICAL: Only include URLs that actually exist. Do not fabricate URLs or sources."""

    if groq_client is None:
        logger.warning(f"[{symbol}] Web search skipped: GROQ_API_KEY not set")
        return [], []

    logger.info(f"[{symbol}] Web search: '{summary[:60]}...' | models={GROQ_WEB_MODELS}")
    last_error = None

    for model_name in GROQ_WEB_MODELS:
        try:
            stream_resp = groq_client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=1,
                max_completion_tokens=1024,
                top_p=1,
                stream=True,
                stop=None,
                compound_custom={
                    "tools": {
                        "enabled_tools": ["web_search", "code_interpreter", "visit_website"]
                    }
                },
            )
            raw = _stream_to_text(stream_resp)

            # Try to parse as JSON
            results = []
            try:
                if raw.startswith("["):
                    results = json.loads(raw)
                else:
                    arr = _extract_json_block(raw, "[", "]")
                    if arr:
                        results = json.loads(arr)
            except json.JSONDecodeError:
                pass

            source_names, source_objs = extract_verified_sources(raw, symbol) if SOURCE_TRACKER_AVAILABLE else ([], [])

            if not results and source_objs:
                results = [
                    {
                        "title": s.get("title", ""),
                        "snippet": s.get("snippet", ""),
                        "url": s.get("url", ""),
                        "date": "Unknown"
                    }
                    for s in source_objs
                ]

            if results:
                logger.info(f"[{symbol}] Web search model={model_name} returned {len(results)} results")
                return results, source_names

            logger.warning(f"[{symbol}] Web search model={model_name} returned no parsed results")
            return [], ["No sources found"]

        except Exception as e:
            last_error = e
            if _is_rate_limit_error(e):
                retry_after = _extract_retry_after_seconds(e)
                logger.warning(f"[{symbol}] Web model {model_name} rate-limited, trying fallback. retry_after~{retry_after}s")
                if RATE_LIMITER_AVAILABLE:
                    rate_limiter.on_rate_limit_error("groq.web", retry_after_sec=retry_after)
                continue

            logger.error(f"[{symbol}] Web search model={model_name} error: {e}")
            # Non-rate-limit error: stop cycling to avoid masking real failures.
            break

    logger.error(f"[{symbol}] Web search failed across models. last_error={last_error}")
    return [], []

def format_news(results: list[dict]) -> str:
    """Format web search results for inclusion in analyst prompt."""
    if not results:
        return "No recent news found. Using filing text only."
    
    lines = []
    for r in results[:4]:
        title   = r.get("title", "")
        snippet = r.get("snippet", "")[:250]
        url     = r.get("url", "")
        date    = r.get("date", "")
        if title or snippet:
            lines.append(f"- [{date}] {title}\n  {snippet}\n  Source: {url}")
    
    return "\n\n".join(lines) if lines else "No article details available."

# ==============================
# STEP 3 — QUANT ANALYST REASONING
# ==============================
VERDICT_OPTIONS = ["BULLISH", "BEARISH", "NEUTRAL", "WATCH"]

def analyse(symbol: str, summary: str, filing_text: str, news_text: str) -> dict:
    """
    High-stakes quant analyst assessment with BOTH short & long reasoning.
    
    Returns:
        verdict       : BULLISH | BEARISH | NEUTRAL | WATCH
        confidence    : HIGH | MEDIUM | LOW
        price_impact  : Expected percentage change (e.g., "+2.5%" or "-1.2%")
        time_horizon  : SHORT_TERM | MEDIUM_TERM | LONG_TERM
        reasoning_short : 3-5 sentence executive summary for quick alerts
        reasoning_long  : 10-15 line detailed institutional thesis
        key_catalysts : list of price drivers
        key_risks     : list of downside scenarios
        comparative_context : market precedent analysis
        news_sources  : list of source URLs for further due diligence
    """
    prompt = f"""You are a high-stakes quantitative equity analyst at a major tier-1 investment bank.
Your job is to provide institutional-grade price impact analysis on NSE corporate announcements.

=== ANNOUNCEMENT DETAILS ===
SYMBOL: {symbol}
SUMMARY: {summary}

=== FILING TEXT ===
{filing_text[:4000]}

=== MARKET CONTEXT (Recent News) ===
{news_text}

=== YOUR TASK ===
Provide a detailed institutional-quality equity analysis that answers:
1. Will this move the stock price? If YES, by how much and in what direction?
2. If NO change expected, explain why the market will ignore this.
3. What are the specific catalysts and risks?
4. What is your confidence level and time horizon?

Respond in STRICT JSON format (no markdown, no extra text) with EXACTLY these keys:
{{
  "verdict": "BULLISH or BEARISH or NEUTRAL or WATCH",
  "confidence": "HIGH or MEDIUM or LOW",
  "expected_price_change_percent": "+2.5 or -1.2 or 0.0 (as number, not string)",
  "time_horizon": "SHORT_TERM (1-5 days) or MEDIUM_TERM (1-4 weeks) or LONG_TERM (3+ months)",
  "reasoning_short": "Exactly 3-5 sentences. Executive summary of the investment case for quick alerts. Include verdict and expected price move.",
  "reasoning_long": "10-15 lines (or ~150-250 words). Full institutional analysis with detailed mechanisms, precedents, scenarios, and conviction. Paragraph format.",
  "key_catalysts": ["specific positive driver 1", "specific positive driver 2"],
  "key_risks": ["specific downside risk 1", "specific downside risk 2"],
  "comparative_context": "How does this compare to similar announcements? What happened to peer stocks in similar situations?",
  "news_sources_reviewed": ["URL 1", "URL 2"] 
}}

RULES FOR VERDICTS:
- BULLISH = Net positive impact, higher probability of upside
- BEARISH = Net negative impact, higher probability of downside
- NEUTRAL = Routine/compliance filing, no material impact expected
- WATCH = Significant/unclear event, monitor closely, requires more news

RULES FOR REASONING:
- reasoning_short: Crisp, bullet-style 3-5 sentences. Example: "Acquisition signals entry into $50B BFSI market. Historical precedent (TCS 2022) delivered +9.5% in 6 months. Moderate execution risk given Infosys' track record. Expect +2.5% medium-term."
- reasoning_long: Full paragraph with mechanisms, precedents, risk/reward, timing context, and nuance. Be thorough but stay institutional and data-driven.

Be a critical analyst. Question the obvious narrative. Look for hidden negatives in positive news and vice versa."""

    try:
        # FIX: Apply rate limiting for the bottleneck model (openai/gpt-oss-120b)
        if RATE_LIMITER_AVAILABLE:
            rate_limiter.wait("groq.reasoning", tokens=100)  # Reserve capacity for large analysis
        
        logger.info(f"[{symbol}] Running analyst reasoning...")
        
        stream_resp = groq_client.chat.completions.create(
            model=GROQ_REASONING_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=1,
            max_completion_tokens=8192,
            top_p=1,
            reasoning_effort="medium",
            stream=True,
            stop=None,
        )

        raw = _stream_to_text(stream_resp)
        json_blob = _extract_json_block(raw, "{", "}")
        result = json.loads(json_blob if json_blob else raw)
        logger.info(f"[{symbol}] Analysis complete: {result.get('verdict')}")
        return result
    except json.JSONDecodeError as e:
        logger.error(f"[{symbol}] Analysis JSON parse error: {e}")
        return _fallback_analysis("WATCH", "LOW", 0.0, "Analysis parsing failed")
    except Exception as e:
        logger.error(f"[{symbol}] Analysis error: {type(e).__name__} — {e}")
        return _fallback_analysis("WATCH", "LOW", 0.0, f"Analysis failed: {str(e)[:50]}")


def _fallback_analysis(verdict: str, confidence: str, price_change: float, reason: str) -> dict:
    """Fallback analysis when API fails."""
    return {
        "verdict": verdict,
        "confidence": confidence,
        "expected_price_change_percent": price_change,
        "time_horizon": "SHORT_TERM",
        "reasoning_short": f"{reason}. Review filing manually for investment decision.",
        "reasoning_long": f"Unable to complete full analysis. Reason: {reason}. Please review the filing and recent news context independently before making any trading decisions.",
        "key_catalysts": [],
        "key_risks": [reason],
        "comparative_context": "Analysis unavailable",
        "news_sources_reviewed": []
    }

# ==============================
# PRINT ALERT (Institutional Format)
# ==============================
VERDICT_COLOUR = {
    "BULLISH": "\033[92m",   # green
    "BEARISH": "\033[91m",   # red
    "NEUTRAL": "\033[90m",   # grey
    "WATCH":   "\033[93m",   # yellow
}
RESET = "\033[0m"
BOLD  = "\033[1m"
UNDERLINE = "\033[4m"

def print_alert(symbol: str, an_dt: str, summary: str, analysis: dict, news: list[dict]):
    verdict     = analysis.get("verdict", "WATCH")
    confidence  = analysis.get("confidence", "LOW")
    price_move  = analysis.get("expected_price_change_percent", 0.0)
    horizon     = analysis.get("time_horizon", "")
    reasoning_short = analysis.get("reasoning_short", "")  # 3-5 sentences
    reasoning_long  = analysis.get("reasoning_long", "")   # 10-15 lines full thesis
    catalysts   = analysis.get("key_catalysts", [])
    risks       = analysis.get("key_risks", [])
    context     = analysis.get("comparative_context", "")
    sources     = analysis.get("news_sources_reviewed", [])
    colour      = VERDICT_COLOUR.get(verdict, "")

    # Format price move with +/- and color
    price_str = f"{price_move:+.1f}%" if price_move != 0 else "No material change expected"
    price_colour = "\033[92m" if price_move > 0 else "\033[91m" if price_move < 0 else "\033[90m"

    print()
    print("=" * 80)
    print(f"{BOLD}{colour}[ {verdict:<8} | {confidence:<6} | {horizon:<12} ]{RESET}  {BOLD}{symbol}{RESET}  {an_dt}")
    print(f"  {summary}")
    print()
    
    # Expected Price Impact
    print(f"{BOLD}PRICE IMPACT:{RESET}")
    print(f"  {price_colour}{BOLD}{price_str}{RESET}")
    print()
    
    # Quick Summary (3-5 sentences)
    print(f"{BOLD}QUICK TAKE:{RESET}")
    for line in textwrap.wrap(reasoning_short, 75):
        print(f"  {line}")
    print()
    
    # Detailed Institutional Analysis (10-15 lines)
    if reasoning_long and reasoning_long != "Analysis failed due to API error.":
        print(f"{BOLD}DETAILED INSTITUTIONAL ANALYSIS:{RESET}")
        for line in textwrap.wrap(reasoning_long, 75):
            print(f"  {line}")
        print()
    
    # Catalysts
    if catalysts:
        print(f"{BOLD}POSITIVE CATALYSTS:{RESET}")
        for cat in catalysts:
            print(f"  + {cat}")
        print()
    
    # Risks
    if risks:
        print(f"{BOLD}KEY RISKS:{RESET}")
        for risk in risks:
            print(f"  - {risk}")
        print()
    
    # Comparative Context
    if context and context != "Unable to fetch context":
        print(f"{BOLD}MARKET CONTEXT:{RESET}")
        for line in textwrap.wrap(context, 75):
            print(f"  {line}")
        print()
    
    # News Sources
    if news or sources:
        print(f"{BOLD}SOURCES & REFERENCES:{RESET}")
        shown_sources = set()
        
        # Add news URLs
        for n in news[:3]:
            url = n.get("url", "")
            title = n.get("title", "")
            if url and url not in shown_sources:
                print(f"  → {title[:70]}")
                print(f"    {url}")
                shown_sources.add(url)
        
        # Add analysis sources
        for src in sources:
            if src and src not in shown_sources:
                print(f"  → {src}")
                shown_sources.add(src)
    
    print("=" * 80)

# ==============================
# PROCESS ONE ANNOUNCEMENT
# ==============================
def process(item: dict, force: bool = False, telegram_selected_symbols: Optional[set] = None):
    """
    Process one filing through the full pipeline.
    NEW: Includes content filtering to avoid low-value announcements.
    """
    uid = make_uid(item)
    if (not force) and uid in processed:
        return

    symbol  = item.get("symbol", "UNKNOWN")
    an_dt   = item.get("an_dt", "")
    desc    = item.get("attchmntText") or item.get("desc", "")
    pdf_url = item.get("attchmntFile", "")
    subject = item.get("subject", "")

    logger.info(f"Processing {symbol} | {an_dt}")

    # FIX: Filter low-value filings BEFORE LLM calls
    if FILTER_AVAILABLE and not force:
        filter_result = should_process(symbol, subject, desc)
        if not filter_result["should_process"]:
            logger.info(f"[FILTER] {symbol} skipped: {filter_result['reason']}")
            processed.add(uid)
            return
        else:
            logger.info(f"[FILTER] {symbol} accepted: {filter_result['reason']} (confidence={filter_result['confidence']})")

    # --- get filing text ---
    full_text = ""
    if pdf_url:
        safe     = uid.replace(":", "-")
        txt_path = os.path.join(PDF_DIR, safe + ".txt")

        if os.path.exists(txt_path):
            with open(txt_path, encoding="utf-8") as f:
                full_text = f.read()
        else:
            pdf_path = download_pdf(pdf_url, safe + ".pdf")
            if pdf_path:
                full_text = extract_text(pdf_path)
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(full_text)

    # --- pipeline with rate limiting and source tracking ---
    summary_data = summarise(symbol, desc, full_text)
    summary      = summary_data.get("summary", "")
    web_line     = summary_data.get("web_search_line", summary)

    news, source_names = web_search(symbol, web_line)
    news_txt = format_news(news)
    analysis = analyse(symbol, summary, full_text, news_txt)

    print_alert(symbol, an_dt, summary, analysis, news)

    # --- send Telegram alert (if configured and symbol selected) ---
    send_telegram = TELEGRAM_AVAILABLE and (
        not telegram_selected_symbols or _normalize_symbol(symbol) in telegram_selected_symbols
    )
    if send_telegram:
        send_detailed_telegram_alert(
            symbol=symbol,
            verdict=analysis.get("verdict", "WATCH"),
            price_move=analysis.get("expected_price_change_percent", 0.0),
            time_horizon=analysis.get("time_horizon", ""),
            reasoning_short=analysis.get("reasoning_short", ""),
            reasoning_long=analysis.get("reasoning_long", ""),
            catalysts=analysis.get("key_catalysts", []),
            risks=analysis.get("key_risks", []),
            sources=source_names  # Use verified sources instead of raw URLs
        )
    elif TELEGRAM_AVAILABLE and telegram_selected_symbols:
        logger.info(f"[{symbol}] Telegram skipped (not in selected symbols)")

    # --- save result as JSON alongside txt ---
    try:
        result_path = os.path.join(PDF_DIR, uid.replace(":", "-") + ".json")
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump({
                "uid": uid, 
                "symbol": symbol, 
                "an_dt": an_dt,
                "summary": summary,
                "web_search_line": web_line,
                "analysis": {
                    "verdict": analysis.get("verdict"),
                    "confidence": analysis.get("confidence"),
                    "expected_price_change_percent": analysis.get("expected_price_change_percent"),
                    "time_horizon": analysis.get("time_horizon"),
                    "reasoning_short": analysis.get("reasoning_short"),
                    "reasoning_long": analysis.get("reasoning_long"),
                    "key_catalysts": analysis.get("key_catalysts", []),
                    "key_risks": analysis.get("key_risks", []),
                    "comparative_context": analysis.get("comparative_context"),
                    "news_sources_reviewed": analysis.get("news_sources_reviewed", [])
                },
                "news_titles": [n.get("title","") for n in news],
                "news_urls": [n.get("url","") for n in news]
            }, f, indent=2)
    except Exception:
        pass

    processed.add(uid)


def load_seen_scrape() -> set:
    if os.path.exists(SEEN_SCRAPE_FILE):
        try:
            with open(SEEN_SCRAPE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return set(data) if isinstance(data, list) else set()
        except Exception:
            return set()
    return set()


def save_seen_scrape(seen: set):
    with open(SEEN_SCRAPE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(seen)), f, indent=2)


def make_scrape_key(item: dict) -> str:
    symbol = (item.get("symbol") or "").strip().upper()
    an_dt = (item.get("an_dt") or "").strip()
    subject = (item.get("subject") or "").strip().lower()
    pdf_url = (item.get("attchmntFile") or "").strip().lower()
    base = f"{symbol}|{an_dt}|{subject}|{pdf_url}"
    return base if base.replace("|", "") else make_uid(item)


def _normalize_symbol(sym: str) -> str:
    return (sym or "").strip().upper()


def _parse_symbol_csv(symbols_csv: str) -> set:
    if not symbols_csv:
        return set()
    return {_normalize_symbol(s) for s in symbols_csv.split(",") if _normalize_symbol(s)}


def _load_symbols_file(symbols_file: str) -> set:
    if not symbols_file or not os.path.exists(symbols_file):
        return set()

    try:
        with open(symbols_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            return {_normalize_symbol(s) for s in data if _normalize_symbol(str(s))}

        if isinstance(data, dict):
            arr = data.get("symbols", [])
            if isinstance(arr, list):
                return {_normalize_symbol(s) for s in arr if _normalize_symbol(str(s))}
    except Exception as e:
        logger.warning(f"Could not read symbol selector file '{symbols_file}': {e}")

    return set()


def resolve_selected_symbols(cli_symbols: str = "", cli_symbols_file: str = "") -> tuple[Optional[set], str]:
    """
    Resolve user-selected symbols from CLI/env/file.
    Priority: CLI > ENV > default file.
    Returns (symbols_set_or_None, source_label)
    """
    file_from_env = os.environ.get("SELECTED_SYMBOLS_FILE", "").strip()
    csv_from_env = os.environ.get("SELECTED_SYMBOLS", "").strip()

    symbols_file = (cli_symbols_file or file_from_env or DEFAULT_SELECTED_SYMBOLS_FILE).strip()

    symbols = set()
    source = "none"

    # CLI CSV has highest priority and merges with file when present.
    csv_set = _parse_symbol_csv(cli_symbols)
    if csv_set:
        symbols |= csv_set
        source = "cli"
    elif csv_from_env:
        env_set = _parse_symbol_csv(csv_from_env)
        symbols |= env_set
        source = "env"

    file_set = _load_symbols_file(symbols_file)
    if file_set:
        symbols |= file_set
        source = f"{source}+file" if source != "none" else "file"

    if not symbols:
        return None, "all"

    return symbols, source


def filter_items_by_selected(items: list, selected_symbols: Optional[set], context: str = "") -> list:
    if not selected_symbols:
        return items

    filtered = []
    for item in items:
        sym = _normalize_symbol(item.get("symbol", ""))
        if sym in selected_symbols:
            filtered.append(item)

    if context:
        logger.info(f"[{context}] Symbol selector kept {len(filtered)}/{len(items)} items")
    return filtered


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
    if stats["length"] < 200:
        return False, "too-short-to-classify", stats
    if stats["control_ratio"] > GARBAGE_CONTROL_THRESHOLD:
        return True, "high-control-char-ratio", stats
    if stats["non_ascii_ratio"] > GARBAGE_NON_ASCII_THRESHOLD and stats["alpha_ratio"] < 0.30:
        return True, "high-non-ascii-low-alpha", stats
    return False, "ok", stats


def _write_scanned_marker(uid: str, reason: str, stats: dict, desc: str):
    safe_uid = uid.replace(":", "-")
    marker_path = os.path.join(PDF_DIR, safe_uid + ".scan_marker.txt")
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


def get_filing_text_scrape_only(item: dict, uid: str) -> str:
    pdf_url = item.get("attchmntFile", "")
    desc = item.get("attchmntText") or item.get("desc", "") or ""

    if not pdf_url:
        return desc.strip() or "No PDF URL or text description available."

    safe_uid = uid.replace(":", "-")
    txt_path = os.path.join(PDF_DIR, safe_uid + ".txt")

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

    pdf_path = download_pdf(pdf_url, safe_uid + ".pdf")
    if not pdf_path:
        return "Failed to download PDF."

    text = (extract_text(pdf_path) or "").strip()
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


def display_scraped_filing(item: dict, uid: str, text: str):
    symbol = item.get("symbol", "UNKNOWN")
    an_dt = item.get("an_dt", "")
    subject = item.get("subject", "")

    print("\n" + "=" * 100)
    print(f"NEW FILING DETECTED | {symbol} | {an_dt}")
    if subject:
        print(f"Subject: {subject}")
    print(f"UID: {uid}")
    print("-" * 100)

    if len(text) > SCRAPE_DISPLAY_MAX_CHARS:
        print(text[:SCRAPE_DISPLAY_MAX_CHARS])
        print("\n[Output truncated. Full text saved in pdfs/*.txt]")
    else:
        print(text)

    print("=" * 100 + "\n")


def scan_scrape_once(seen: set, window_hours: int, selected_symbols: Optional[set] = None) -> tuple[int, int]:
    refresh_session()
    raw = fetch_data(hours=window_hours)
    raw = filter_items_by_selected(raw, selected_symbols, context="scrape")

    unique = []
    temp_seen = set()
    for item in raw:
        key = make_scrape_key(item)
        if key not in temp_seen:
            temp_seen.add(key)
            unique.append(item)

    new_count = 0
    for item in unique:
        key = make_scrape_key(item)
        uid = make_uid(item)
        if key in seen:
            continue

        text = get_filing_text_scrape_only(item, uid)
        display_scraped_filing(item, uid, text)
        seen.add(key)
        new_count += 1

    return len(unique), new_count


def run_scrape_only(
    poll_seconds: int = SCRAPE_POLL_SECONDS,
    window_hours: int = BROADCAST_WINDOW_HOURS,
    once: bool = False,
    selected_symbols: Optional[set] = None,
):
    seen = load_seen_scrape()
    logger.info("NSE Scrape-Only Mode (no LLM)")
    logger.info(f"Broadcast window: last {window_hours}h")
    logger.info(f"Poll interval: {poll_seconds}s")
    logger.info(f"Known filings: {len(seen)}")
    if selected_symbols:
        logger.info(f"Selected symbols active: {len(selected_symbols)}")

    while True:
        started = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{started}] Scanning NSE announcements...")
        try:
            total, new_found = scan_scrape_once(seen, window_hours, selected_symbols=selected_symbols)
            save_seen_scrape(seen)
            logger.info(f"Scan complete: {total} unique in window, {new_found} new displayed")
        except Exception as e:
            logger.error(f"Scrape scan error: {type(e).__name__} - {e}")

        if once:
            break
        time.sleep(max(3, poll_seconds))

# ==============================
# MAIN LOOP
# ==============================
def run(selected_symbols: Optional[set] = None):
    """
    Main event loop for NSE filing analysis.
    FIX: Improved error handling, rate limiting, and comprehensive logging.
    """
    global processed
    
    logger.info("="*80)
    logger.info("NSE Filing Analyser — Quant Analyst Edition (REFACTORED)")
    logger.info(f"Summary Model: {GROQ_SUMMARY_MODEL}")
    logger.info(f"Web Search Model: {GROQ_WEB_MODEL}")
    logger.info(f"Reasoning Model: {GROQ_REASONING_MODEL}")
    logger.info(f"Broadcast Window: {BROADCAST_WINDOW_HOURS}h")
    logger.info(f"Check Interval: {CHECK_INTERVAL}s")
    logger.info(f"Tracked Filings: {len(processed)}")
    logger.info(f"Filtering Enabled: {FILTER_AVAILABLE}")
    logger.info(f"Rate Limiting Enabled: {RATE_LIMITER_AVAILABLE}")
    if selected_symbols:
        logger.info(f"Telegram selector active: {len(selected_symbols)} symbols")
    logger.info("="*80)

    iteration = 0
    while True:
        iteration += 1
        logger.info(f"\n[Iteration {iteration}] {time.strftime('%Y-%m-%d %H:%M:%S')}")

        try:
            refresh_session()
            raw = fetch_data()

            if not raw:
                logger.warning("No filings fetched")
                logger.info(f"Sleeping {CHECK_INTERVAL}s before next check...")
                time.sleep(CHECK_INTERVAL)
                continue

            # Dedup by uid
            seen, data = set(), []
            for item in raw:
                uid = make_uid(item)
                if uid not in seen:
                    seen.add(uid)
                    data.append(item)

            logger.info(f"Found {len(data)} unique filings (after dedup)")

            new_count = skip_count = filter_skip = 0
            for item in data:
                uid = make_uid(item)
                if uid in processed:
                    skip_count += 1
                else:
                    try:
                        process(item, telegram_selected_symbols=selected_symbols)
                        new_count += 1
                    except Exception as e:
                        logger.error(f"Error processing {item.get('symbol', '?')}: {e}")
                        # Graceful degradation: continue to next filing
                        continue

            processed = save_processed(processed)
            
            logger.info(f"SUMMARY: {new_count} new | {skip_count} skipped | Total tracked: {len(processed)}")
            logger.info(f"Sleeping {CHECK_INTERVAL}s before next check...")
            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            logger.info("Stopped by user")
            break
        except Exception as e:
            logger.error(f"Fatal iteration error: {type(e).__name__} — {e}")
            logger.info(f"Sleeping {CHECK_INTERVAL}s before retry...")
            time.sleep(CHECK_INTERVAL)

# ==============================
# ENTRY
# ==============================
if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="NSE filings pipeline")
    parser.add_argument("--scrape-only", action="store_true", help="Run live scrape-only mode (no LLM analysis)")
    parser.add_argument("--once", action="store_true", help="Run one scan and exit (for scrape-only mode)")
    parser.add_argument("--poll", type=int, default=SCRAPE_POLL_SECONDS, help="Polling interval seconds (scrape-only mode)")
    parser.add_argument("--hours", type=int, default=BROADCAST_WINDOW_HOURS, help="Broadcast time window in hours")
    parser.add_argument("--symbols", type=str, default="", help="Comma-separated symbols to process, e.g. INFY,TCS,HDFCBANK")
    parser.add_argument("--symbols-file", type=str, default="", help="Path to JSON file with symbols list")
    args = parser.parse_args()

    selected_symbols, selector_source = resolve_selected_symbols(args.symbols, args.symbols_file)
    if selected_symbols:
        logger.info(f"Symbol selector source: {selector_source} | symbols={len(selected_symbols)}")
        logger.info("In analysis mode: all filings are scraped/analyzed; Telegram is sent only for selected symbols")
    else:
        logger.info("Symbol selector source: all (no symbol filter)")

    # For scrape-only mode, GROQ key is not required.
    if args.scrape_only:
        try:
            run_scrape_only(
                poll_seconds=args.poll,
                window_hours=args.hours,
                once=args.once,
                selected_symbols=selected_symbols,
            )
        except KeyboardInterrupt:
            logger.info("Stopped by user")
        sys.exit(0)

    missing = [k for k in ("GROQ_API_KEY",) if not os.environ.get(k)]
    if missing:
        logger.error(f"Missing env vars: {', '.join(missing)}")
        logger.error("  Set them and re-run:")
        logger.error(f"    $env:GROQ_API_KEY = 'your-key-here'   # PowerShell")
        logger.error(f"    export GROQ_API_KEY='your-key-here'   # Bash")
        sys.exit(1)

    # Status report
    logger.info(f"Telegram notifications: {'ENABLED' if TELEGRAM_AVAILABLE else 'DISABLED'}")
    logger.info(f"Content filtering: {'ENABLED' if FILTER_AVAILABLE else 'DISABLED'}")
    logger.info(f"Rate limiting: {'ENABLED' if RATE_LIMITER_AVAILABLE else 'DISABLED'}")
    logger.info(f"News source tracking: {'ENABLED' if SOURCE_TRACKER_AVAILABLE else 'DISABLED'}")

    try:
        run(selected_symbols=selected_symbols)
    except KeyboardInterrupt:
        logger.info("Stopped by user")
    except Exception as e:
        import traceback
        logger.critical(f"FATAL: {type(e).__name__} — {e}")
        traceback.print_exc()