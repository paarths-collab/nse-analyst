#!/usr/bin/env python3
"""
End-to-end verification report:
- Uses analyzed news rows (LLM output)
- Scrapes related articles per headline (Google News RSS)
- Pulls stock data from Yahoo Finance from article date
- Checks if price moved in expected direction (bullish/bearish)
- Pulls related NSE filings and includes links
- Writes in-depth JSON + Markdown report
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

import feedparser
import yfinance as yf
import pandas as pd
from symbol_resolver import SymbolResolver

# Local imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fillings


COMMODITY_YF_MAP = {
    "gold": "GC=F",
    "silver": "SI=F",
    "crude": "CL=F",
    "crude oil": "CL=F",
    "oil": "CL=F",
    "natural gas": "NG=F",
    "copper": "HG=F",
    "platinum": "PL=F",
    "palladium": "PA=F",
}

MACRO_YF_MAP = {
    "rupee": "INR=X",
    "usd inr": "INR=X",
    "usdinr": "INR=X",
    "dollar index": "DX-Y.NYB",
    "us 10y": "^TNX",
    "us10y": "^TNX",
    "dow": "^DJI",
    "dow jones": "^DJI",
    "sp500": "^GSPC",
    "s&p 500": "^GSPC",
    "ftse": "^FTSE",
    "nasdaq": "^IXIC",
    "nifty": "^NSEI",
    "sensex": "^BSESN",
}

STOCK_NAME_YF_MAP = {
    "reliance": "RELIANCE.NS",
    "reliance industries": "RELIANCE.NS",
    "sbi": "SBIN.NS",
    "state bank of india": "SBIN.NS",
    "canara bank": "CANBK.NS",
    "uco bank": "UCOBANK.NS",
    "hdfc bank": "HDFCBANK.NS",
    "icici bank": "ICICIBANK.NS",
    "infosys": "INFY.NS",
    "tcs": "TCS.NS",
}

INDEX_ALIAS_MAP = {
    "FTSE": "^FTSE",
    "UKX": "^FTSE",
    "DOW": "^DJI",
    "DOWJONES": "^DJI",
    "DJI": "^DJI",
    "SP500": "^GSPC",
    "S&P500": "^GSPC",
    "NASDAQ": "^IXIC",
    "IXIC": "^IXIC",
    "US10Y": "^TNX",
    "TNX": "^TNX",
    "NIFTY": "^NSEI",
    "SENSEX": "^BSESN",
}

SYMBOL_ALIAS_MAP = {
    "RELIANCE": {"resolved_symbol": "RELIANCE.NS", "label": "stock"},
    "SBI": {"resolved_symbol": "SBIN.NS", "label": "stock"},
    "SBIN": {"resolved_symbol": "SBIN.NS", "label": "stock"},
    "CANARA": {"resolved_symbol": "CANBK.NS", "label": "stock"},
    "CANBK": {"resolved_symbol": "CANBK.NS", "label": "stock"},
    "UCO": {"resolved_symbol": "UCOBANK.NS", "label": "stock"},
    "UCOBANK": {"resolved_symbol": "UCOBANK.NS", "label": "stock"},
    "USDINR": {"resolved_symbol": "INR=X", "label": "fx"},
    "GOLD": {"resolved_symbol": "GC=F", "label": "commodity"},
    "SILVER": {"resolved_symbol": "SI=F", "label": "commodity"},
    "COPPER": {"resolved_symbol": "HG=F", "label": "commodity"},
    "CRUDE": {"resolved_symbol": "CL=F", "label": "commodity"},
    "OIL": {"resolved_symbol": "CL=F", "label": "commodity"},
}


@dataclass
class PriceAnalysis:
    ticker: str
    start_close: Optional[float]
    latest_close: Optional[float]
    max_close_since: Optional[float]
    min_close_since: Optional[float]
    pct_latest: Optional[float]
    pct_max: Optional[float]
    pct_min: Optional[float]
    latest_volume: Optional[float]
    avg_volume_20: Optional[float]
    volume_ratio: Optional[float]
    verdict: str
    market_reaction: str
    position_side: str
    action: str
    suggested_entry: Optional[float]
    suggested_target: Optional[float]
    suggested_stop_loss: Optional[float]
    article_published_at_utc: str
    price_window_start_utc: str
    price_window_end_utc: str
    candle_interval: str
    candle_interval_reason: str
    notes: str


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate verification research report")
    p.add_argument("--review", default="news_llm_review.json", help="LLM review JSON file")
    p.add_argument("--output-json", default="verification_research_report.json", help="Output JSON report")
    p.add_argument("--output-md", default="verification_research_report.md", help="Output markdown report")
    p.add_argument("--max-items", type=int, default=12, help="Max news rows to analyze")
    p.add_argument("--related-limit", type=int, default=5, help="Related news links per headline")
    p.add_argument("--filings-hours", type=int, default=24, help="How many hours of NSE filings to fetch")
    p.add_argument("--min-confidence", type=int, default=50, help="Min adjusted confidence for selecting rows")
    return p.parse_args()


def parse_date_any(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    text = raw.strip()
    text = re.sub(r"([+-]\d{3})$", r"\g<1>0", text)

    # ISO
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    # RFC2822
    try:
        dt2 = parsedate_to_datetime(text)
        if dt2.tzinfo is None:
            dt2 = dt2.replace(tzinfo=timezone.utc)
        return dt2.astimezone(timezone.utc)
    except Exception:
        pass

    # Common fallback
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d-%m-%Y %H:%M:%S", "%d %b %Y %H:%M:%S"]:
        try:
            dt3 = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return dt3
        except Exception:
            continue

    return None


def infer_news_direction(row: Dict[str, Any]) -> str:
    td = str(row.get("trade_decision", "")).lower().strip()
    short_outlook = str(row.get("short_term_outlook", "")).lower()
    reasoning = str(row.get("recommendation_reasoning", "")).lower()

    if td == "buy":
        return "bullish"
    if td == "avoid":
        return "bearish"

    bull_words = ["bull", "up", "rise", "gain", "positive", "strong", "breakout"]
    bear_words = ["bear", "down", "fall", "drop", "negative", "weak", "selloff", "decline"]

    bull_hits = sum(1 for w in bull_words if w in short_outlook or w in reasoning)
    bear_hits = sum(1 for w in bear_words if w in short_outlook or w in reasoning)

    if bull_hits > bear_hits:
        return "bullish"
    if bear_hits > bull_hits:
        return "bearish"
    return "neutral"


def is_pre_listing_ipo_event(row: Dict[str, Any]) -> bool:
    text = " ".join(
        [
            str(row.get("raw_headline", "")),
            str(row.get("model_summary", "")),
            str(row.get("recommendation_reasoning", "")),
        ]
    ).lower()

    pre_listing_markers = [
        "to launch ipo",
        "launch ipo",
        "plans ipo",
        "planning ipo",
        "upcoming ipo",
        "files drhp",
        "filed drhp",
        "draft red herring",
        "sebi nod",
        "sebi approval",
        "initial public offering",
        "ipo",
    ]

    already_listed_markers = [
        "listed on nse",
        "listed on bse",
        "shares listed",
        "post-listing",
        "debuted on",
        "listing day gain",
    ]

    has_ipo = any(k in text for k in pre_listing_markers)
    already_listed = any(k in text for k in already_listed_markers)
    return has_ipo and not already_listed


def normalize_candidate_symbol(raw: str) -> str:
    sym = (raw or "").strip().upper()
    sym = re.sub(r"[^A-Z0-9\.\-]", "", sym)
    return sym


def build_ticker_candidates(row: Dict[str, Any]) -> List[str]:
    cands = row.get("symbol_candidates") or []
    out: List[str] = []
    india_context = any(
        k in _row_text_blob(row)
        for k in ["india", "nse", "bse", "sebi", "rupee", "sensex", "nifty"]
    )

    for c in cands:
        base = normalize_candidate_symbol(str(c))
        if not base:
            continue
        if base in INDEX_ALIAS_MAP:
            out.append(INDEX_ALIAS_MAP[base])
        elif base.startswith("^") or base.endswith("=F") or base.endswith("=X"):
            out.append(base)
        elif "." in base:
            out.append(base)
        elif india_context:
            # Only add NSE/BSE assumptions in clear India context.
            out.append(base + ".NS")
            out.append(base + ".BO")
        else:
            # Keep raw when context is non-India/unknown to avoid wrong suffixing.
            out.append(base)

    # Fallback from source headline tokens if no symbol candidates.
    if not out:
        headline = str(row.get("raw_headline", ""))
        for token in re.findall(r"[A-Z]{3,12}", headline.upper()):
            out.append(token + ".NS")
            out.append(token + ".BO")

    # Unique preserve order
    seen = set()
    uniq: List[str] = []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq[:8]


def verify_symbol_candidate_google(raw_symbol: str, row: Dict[str, Any]) -> Dict[str, str]:
    """Verify each symbol candidate using Google News RSS before assigning label."""
    raw = (raw_symbol or "").strip()
    base = normalize_candidate_symbol(raw)
    if not base:
        return {
            "raw_symbol": raw,
            "resolved_symbol": "",
            "label": "unknown",
            "source": "",
            "note": "empty_symbol",
        }

    # Hard map common aliases up front (primary identity resolver).
    if base in SYMBOL_ALIAS_MAP:
        mapped = SYMBOL_ALIAS_MAP[base]
        # Still run google lookup for evidence link.
        q = quote_plus(f'"{base}" finance')
        u = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
        f = feedparser.parse(u)
        src = ""
        if getattr(f, "entries", None):
            src = str(getattr(f.entries[0], "link", ""))
        return {
            "raw_symbol": raw,
            "resolved_symbol": str(mapped["resolved_symbol"]),
            "label": str(mapped["label"]),
            "source": src,
            "note": "alias_map_verified_with_google",
        }

    # Hard map common index aliases up front.
    if base in INDEX_ALIAS_MAP:
        q = quote_plus(f'"{base}" index')
        u = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
        f = feedparser.parse(u)
        src = ""
        if getattr(f, "entries", None):
            src = str(getattr(f.entries[0], "link", ""))
        return {
            "raw_symbol": raw,
            "resolved_symbol": INDEX_ALIAS_MAP[base],
            "label": "index",
            "source": src,
            "note": "index_alias_verified_with_google",
        }

    if base.startswith("^"):
        return {
            "raw_symbol": raw,
            "resolved_symbol": base,
            "label": "index",
            "source": "symbol_syntax",
            "note": "caret_index",
        }
    if base.endswith("=F"):
        return {
            "raw_symbol": raw,
            "resolved_symbol": base,
            "label": "commodity",
            "source": "symbol_syntax",
            "note": "futures_syntax",
        }
    if base.endswith("=X"):
        return {
            "raw_symbol": raw,
            "resolved_symbol": base,
            "label": "fx",
            "source": "symbol_syntax",
            "note": "fx_syntax",
        }

    query = quote_plus(f'"{base}" finance ticker')
    url = f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
    feed = feedparser.parse(url)

    top_link = ""
    top_title = ""
    if getattr(feed, "entries", None):
        top = feed.entries[0]
        top_title = str(getattr(top, "title", ""))
        top_link = str(getattr(top, "link", ""))

    blob = f"{base} {top_title} {_row_text_blob(row)}".lower()

    # Prefer stock if symbol is clearly company-like in row context, avoid crude keyword bleed.
    stock_focus_hint = any(k in _row_text_blob(row) for k in ["shares", "stock", "m-cap", "market cap", "equity"]) 

    if any(k in blob for k in ["ftse", "dow", "nasdaq", "s&p", "index", "treasury yield", "bond yield"]):
        label = "index"
        resolved = INDEX_ALIAS_MAP.get(base, base)
    elif stock_focus_hint and base.isalpha() and 2 <= len(base) <= 12:
        label = "stock"
        india_context = any(k in blob for k in ["india", "nse", "bse", "sebi", "sensex", "nifty"])
        if india_context:
            resolved = base + ".NS"
        else:
            resolved = base
    elif any(k in blob for k in ["gold", "silver", "crude", "oil", "copper", "commodity", "futures"]):
        label = "commodity"
        resolved = base
    elif any(k in blob for k in ["usd", "inr", "forex", "currency", "exchange rate"]):
        label = "fx"
        resolved = base
    else:
        label = "stock"
        india_context = any(k in blob for k in ["india", "nse", "bse", "sebi", "sensex", "nifty"])
        if "." in base:
            resolved = base
        elif india_context:
            resolved = base + ".NS"
        else:
            resolved = base

    return {
        "raw_symbol": raw,
        "resolved_symbol": resolved,
        "label": label,
        "source": top_link,
        "note": "google_verified",
    }


def verify_all_symbols_for_row(row: Dict[str, Any], resolver: SymbolResolver) -> List[Dict[str, str]]:
    names: List[str] = []

    for c in (row.get("symbol_candidates") or []):
        s = str(c).strip()
        if s:
            names.append(s)

    for s in _extract_named_stock_symbols(row):
        if s:
            names.append(s)

    for c in _extract_commodity_candidates(row):
        if c:
            names.append(c)

    hint = str(row.get("price_lookup_symbol_hint", "")).strip()
    if hint:
        names.append(hint)

    if not names:
        named = _lookup_named_stock_symbol(row)
        if named:
            names.append(named)

    resolved = resolver.resolve_many(names)
    out: List[Dict[str, str]] = []
    for x in resolved:
        out.append(
            {
                "raw_symbol": str(x.get("raw_name", "")),
                "resolved_symbol": str(x.get("resolved_symbol", "")),
                "label": str(x.get("label", "unknown")),
                "exchange": str(x.get("exchange", "")),
                "source": str(x.get("google_link", "")),
                "google_title": str(x.get("google_title", "")),
                "note": str(x.get("source_step", "")),
            }
        )
    return out


def _extract_commodity_candidates(row: Dict[str, Any]) -> List[str]:
    cands = [str(x).strip().lower() for x in (row.get("commodity_names") or [])]
    primary = str(row.get("primary_asset_name", "")).strip().lower()
    if primary:
        cands.insert(0, primary)

    # Also scan headline fallback for common commodity words.
    headline = str(row.get("raw_headline", "")).lower()
    for k in COMMODITY_YF_MAP.keys():
        if k in headline and k not in cands:
            cands.append(k)

    uniq: List[str] = []
    seen = set()
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def _row_text_blob(row: Dict[str, Any]) -> str:
    return " ".join(
        [
            str(row.get("raw_headline", "")),
            str(row.get("model_summary", "")),
            str(row.get("recommendation_reasoning", "")),
            str(row.get("price_lookup_symbol_hint", "")),
        ]
    ).lower()


def _infer_asset_kind_from_text(row: Dict[str, Any]) -> str:
    blob = _row_text_blob(row)
    if any(k in blob for k in COMMODITY_YF_MAP.keys()):
        return "commodity"
    if any(k in blob for k in MACRO_YF_MAP.keys()):
        return "macro"
    return "stock"


def _lookup_named_stock_symbol(row: Dict[str, Any]) -> Optional[str]:
    blob = _row_text_blob(row)
    for name, sym in STOCK_NAME_YF_MAP.items():
        if name in blob:
            return sym
    return None


def _extract_named_stock_symbols(row: Dict[str, Any]) -> List[str]:
    out: List[str] = []

    # From explicit stock_names field when available.
    for name in (row.get("stock_names") or []):
        n = str(name).strip().lower()
        if n in STOCK_NAME_YF_MAP:
            out.append(STOCK_NAME_YF_MAP[n])

    # From known-name scan in free text.
    blob = _row_text_blob(row)
    for name, sym in STOCK_NAME_YF_MAP.items():
        if name in blob:
            out.append(sym)

    # De-dup preserve order.
    seen = set()
    uniq: List[str] = []
    for s in out:
        if s and s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


def _extract_driver_assets(row: Dict[str, Any]) -> List[Dict[str, str]]:
    drivers: List[Dict[str, str]] = []
    for c in _extract_commodity_candidates(row):
        sym = COMMODITY_YF_MAP.get(c, "")
        drivers.append({"name": c, "asset_type": "commodity", "symbol": sym})

    # De-dup by (name, symbol)
    seen = set()
    uniq: List[Dict[str, str]] = []
    for d in drivers:
        key = (d.get("name", ""), d.get("symbol", ""))
        if key not in seen:
            seen.add(key)
            uniq.append(d)
    return uniq


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y"}


def build_price_targets(row: Dict[str, Any]) -> Tuple[List[str], str]:
    # If LLM tagged as commodity, prefer universal commodity symbols first.
    asset_type = str(row.get("asset_type", "")).strip().lower()
    contains_commodity = _as_bool(row.get("contains_commodity", False))
    inferred_kind = _infer_asset_kind_from_text(row)
    stock_candidates = _extract_named_stock_symbols(row)
    verified = row.get("_verified_symbols") or []

    if verified:
        # Choose targets from google-verified symbol list first.
        verified_stock = [x.get("resolved_symbol", "") for x in verified if x.get("label") == "stock" and x.get("resolved_symbol")]
        verified_commodity = [x.get("resolved_symbol", "") for x in verified if x.get("label") == "commodity" and x.get("resolved_symbol")]
        verified_index = [x.get("resolved_symbol", "") for x in verified if x.get("label") == "index" and x.get("resolved_symbol")]
        verified_fx = [x.get("resolved_symbol", "") for x in verified if x.get("label") == "fx" and x.get("resolved_symbol")]

        headline_blob = str(row.get("raw_headline", "")).lower()
        stock_focus_hint = any(k in headline_blob for k in ["shares", "stock", "m-cap", "market cap", "bank", "equity"])

        if stock_focus_hint and verified_stock:
            return verified_stock, "stock"
        if verified_commodity and (asset_type == "commodity" or contains_commodity or inferred_kind == "commodity"):
            return verified_commodity, "commodity"
        if verified_index:
            return verified_index, "macro"
        if verified_fx:
            return verified_fx, "macro"
        if verified_stock:
            return verified_stock, "stock"

    headline_blob = str(row.get("raw_headline", "")).lower()
    stock_focus_hint = any(
        k in headline_blob
        for k in ["shares", "stock", "m-cap", "market cap", "bank", "equity"]
    )

    # If headline is stock-focused and we can map a stock symbol, prefer stock as primary target.
    if stock_focus_hint and stock_candidates:
        return stock_candidates, "stock"

    if asset_type == "commodity" or contains_commodity or inferred_kind == "commodity":
        targets: List[str] = []
        for name in _extract_commodity_candidates(row):
            if name in COMMODITY_YF_MAP:
                targets.append(COMMODITY_YF_MAP[name])
        # Include LLM hint if provided.
        hint = str(row.get("price_lookup_symbol_hint", "")).strip()
        if hint:
            targets.insert(0, hint)

        # Unique preserve order
        uniq: List[str] = []
        seen = set()
        for t in targets:
            if t and t not in seen:
                seen.add(t)
                uniq.append(t)
        if not uniq:
            blob = _row_text_blob(row)
            for k, sym in COMMODITY_YF_MAP.items():
                if k in blob:
                    uniq.append(sym)
        return uniq, "commodity"

    if asset_type in {"index", "macro"} or inferred_kind == "macro":
        targets_macro: List[str] = []
        hint = str(row.get("price_lookup_symbol_hint", "")).strip()
        if hint:
            targets_macro.append(hint)
        blob = _row_text_blob(row)
        for k, sym in MACRO_YF_MAP.items():
            if k in blob:
                targets_macro.append(sym)

        uniqm: List[str] = []
        seenm = set()
        for t in targets_macro:
            if t and t not in seenm:
                seenm.add(t)
                uniqm.append(t)
        return uniqm, "macro"

    # Default stock flow.
    stock_targets = build_ticker_candidates(row)
    hint = str(row.get("price_lookup_symbol_hint", "")).strip()
    if hint:
        stock_targets.insert(0, hint)

    named = _lookup_named_stock_symbol(row)
    if named:
        stock_targets.insert(0, named)

    for s in stock_candidates:
        stock_targets.insert(0, s)

    uniq2: List[str] = []
    seen2 = set()
    for t in stock_targets:
        if t and t not in seen2:
            seen2.add(t)
            uniq2.append(t)
    return uniq2, "stock"


def try_fetch_price_history(ticker: str, start_date: datetime) -> Optional[Dict[str, Any]]:
    start = (start_date - timedelta(days=2)).date().isoformat()
    end = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
    try:
        df = yf.download(
            ticker,
            start=start,
            end=end,
            interval="1d",
            progress=False,
            auto_adjust=False,
            threads=False,
        )
    except Exception:
        return None

    # Fallback when explicit date range has no rows (holiday/date mismatch/API lag).
    if df is None or df.empty:
        try:
            df = yf.download(
                ticker,
                period="1y",
                interval="1d",
                progress=False,
                auto_adjust=False,
                threads=False,
            )
        except Exception:
            return None

        if df is None or df.empty:
            return None

    # yfinance index is timezone-naive date-like in many cases.
    # yfinance may return MultiIndex columns depending on version/setup.
    close_series = df.get("Close")
    volume_series = df.get("Volume")

    if isinstance(close_series, pd.DataFrame):
        close_series = close_series.iloc[:, 0]
    if isinstance(volume_series, pd.DataFrame):
        volume_series = volume_series.iloc[:, 0]

    if close_series is None:
        return None
    if volume_series is None:
        volume_series = pd.Series([0.0] * len(df), index=df.index)

    rows: List[Tuple[datetime, float, float]] = []
    for idx in df.index:
        try:
            d = datetime(idx.year, idx.month, idx.day, tzinfo=timezone.utc)
            close_val = float(close_series.loc[idx])
            vol_val = float(volume_series.loc[idx]) if idx in volume_series.index else 0.0
            if close_val > 0:
                rows.append((d, close_val, vol_val))
        except Exception:
            continue

    if not rows:
        return None

    rows.sort(key=lambda x: x[0])

    on_or_after = [x for x in rows if x[0].date() >= start_date.date()]
    if not on_or_after:
        # Use nearest available trailing session as baseline if article date is beyond available dataset.
        on_or_after = [rows[-1]]

    start_close = on_or_after[0][1]
    latest_close = on_or_after[-1][1]
    max_close = max(v for _, v, _ in on_or_after)
    min_close = min(v for _, v, _ in on_or_after)
    latest_volume = on_or_after[-1][2]

    # Use broader history for volume baseline; avoids trivial ratio=1.0 when on_or_after has 1 row.
    all_last_20 = rows[-20:] if len(rows) >= 20 else rows
    if len(all_last_20) >= 5:
        avg_volume_20 = sum(v for _, _, v in all_last_20) / len(all_last_20)
        volume_ratio = (latest_volume / avg_volume_20) if avg_volume_20 > 0 else None
    else:
        avg_volume_20 = None
        volume_ratio = None

    def pct(a: float, b: float) -> float:
        if a == 0:
            return 0.0
        return ((b - a) / a) * 100.0

    return {
        "ticker": ticker,
        "start_close": start_close,
        "latest_close": latest_close,
        "max_close_since": max_close,
        "min_close_since": min_close,
        "pct_latest": pct(start_close, latest_close),
        "pct_max": pct(start_close, max_close),
        "pct_min": pct(start_close, min_close),
        "latest_volume": latest_volume,
        "avg_volume_20": avg_volume_20,
        "volume_ratio": volume_ratio,
        "window_start": on_or_after[0][0].isoformat(),
        "window_end": on_or_after[-1][0].isoformat(),
        "history_points": len(on_or_after),
    }


def _round_price(v: Optional[float]) -> Optional[float]:
    if v is None:
        return None
    return round(float(v), 2)


def derive_trade_levels(direction: str, verdict: str, latest_close: float) -> Tuple[str, str, Optional[float], Optional[float], Optional[float], str]:
    # Simple deterministic levels for verification; final execution still needs trader discretion.
    if latest_close <= 0:
        return "none", "no_action", None, None, None, "No valid latest close for level derivation."

    if direction == "bullish":
        if verdict == "already_increased":
            entry = latest_close * 0.99
            target = latest_close * 1.03
            sl = latest_close * 0.97
            return "long", "long_watch_pullback", _round_price(entry), _round_price(target), _round_price(sl), "Market already reacted up; prefer pullback entry."
        if verdict in {"not_moved_yet", "neutral_no_big_move"}:
            entry = latest_close * 1.003
            target = latest_close * 1.03
            sl = latest_close * 0.985
            return "long", "long_breakout_buy", _round_price(entry), _round_price(target), _round_price(sl), "Bullish thesis not fully priced; breakout-style entry."
        entry = latest_close * 1.005
        target = latest_close * 1.02
        sl = latest_close * 0.98
        return "long", "long_watch", _round_price(entry), _round_price(target), _round_price(sl), "Bullish thesis but price action not aligned; caution."

    if direction == "bearish":
        if verdict == "already_fallen":
            entry = latest_close * 1.01
            target = latest_close * 0.97
            sl = latest_close * 1.03
            return "short", "short_watch_pullback", _round_price(entry), _round_price(target), _round_price(sl), "Market already reacted down; avoid chasing low."
        if verdict in {"not_moved_yet", "neutral_no_big_move"}:
            entry = latest_close * 0.997
            target = latest_close * 0.97
            sl = latest_close * 1.015
            return "short", "short_breakdown_sell", _round_price(entry), _round_price(target), _round_price(sl), "Bearish thesis not fully priced; breakdown-style entry."
        entry = latest_close * 0.995
        target = latest_close * 0.98
        sl = latest_close * 1.02
        return "short", "short_watch", _round_price(entry), _round_price(target), _round_price(sl), "Bearish thesis but price action not aligned; caution."

    # Neutral
    return "none", "no_action", None, None, None, "Neutral directional signal; avoid forced trade."


def classify_market_reaction(direction: str, verdict: str, volume_ratio: Optional[float]) -> str:
    vol_txt = "normal_volume"
    if volume_ratio is not None:
        if volume_ratio >= 1.5:
            vol_txt = "high_volume"
        elif volume_ratio <= 0.7:
            vol_txt = "low_volume"

    if direction == "bullish" and verdict == "already_increased":
        return f"reacted_up_{vol_txt}"
    if direction == "bearish" and verdict == "already_fallen":
        return f"reacted_down_{vol_txt}"
    if verdict in {"not_moved_yet", "neutral_no_big_move"}:
        return f"not_fully_reacted_{vol_txt}"
    return f"mixed_reaction_{vol_txt}"


def evaluate_price_follow_through(direction: str, price: Dict[str, Any]) -> Tuple[str, str]:
    pct_latest = float(price.get("pct_latest", 0.0))

    if direction == "bullish":
        if pct_latest >= 2.0:
            return "already_increased", f"Price rose {pct_latest:.2f}% after bullish news."
        if pct_latest <= -2.0:
            return "fell_after_bullish_news", f"Price fell {pct_latest:.2f}% after bullish signal."
        return "not_moved_yet", f"Price change is {pct_latest:.2f}% and has not moved meaningfully yet."

    if direction == "bearish":
        if pct_latest <= -2.0:
            return "already_fallen", f"Price fell {pct_latest:.2f}% after bearish news."
        if pct_latest >= 2.0:
            return "rose_after_bearish_news", f"Price rose {pct_latest:.2f}% despite bearish signal."
        return "not_moved_yet", f"Price change is {pct_latest:.2f}% and has not moved meaningfully yet."

    # neutral
    if abs(pct_latest) < 2.0:
        return "neutral_no_big_move", f"Neutral signal with small move ({pct_latest:.2f}%)."
    return "neutral_but_large_move", f"Neutral signal but large move observed ({pct_latest:.2f}%)."


def analyze_price_from_article_date(row: Dict[str, Any]) -> PriceAnalysis:
    if is_pre_listing_ipo_event(row):
        return PriceAnalysis(
            ticker="",
            start_close=None,
            latest_close=None,
            max_close_since=None,
            min_close_since=None,
            pct_latest=None,
            pct_max=None,
            pct_min=None,
            latest_volume=None,
            avg_volume_20=None,
            volume_ratio=None,
            verdict="pre_listing_ipo_not_listed",
            market_reaction="unlisted_pre_ipo",
            position_side="none",
            action="no_action",
            suggested_entry=None,
            suggested_target=None,
            suggested_stop_loss=None,
            article_published_at_utc="",
            price_window_start_utc="",
            price_window_end_utc="",
            candle_interval="1d",
            candle_interval_reason="Daily candles chosen for low-latency and robust cross-asset coverage.",
            notes=(
                "IPO appears pre-listing/unlisted. Spot share price is not available yet. "
                "Track issue price band, subscription data, and post-listing day candles instead."
            ),
        )

    published_at = parse_date_any(str(row.get("published_at", "")))
    if not published_at:
        return PriceAnalysis(
            ticker="",
            start_close=None,
            latest_close=None,
            max_close_since=None,
            min_close_since=None,
            pct_latest=None,
            pct_max=None,
            pct_min=None,
            latest_volume=None,
            avg_volume_20=None,
            volume_ratio=None,
            verdict="no_article_date",
            market_reaction="unknown",
            position_side="none",
            action="no_action",
            suggested_entry=None,
            suggested_target=None,
            suggested_stop_loss=None,
            article_published_at_utc="",
            price_window_start_utc="",
            price_window_end_utc="",
            candle_interval="1d",
            candle_interval_reason="Daily candles chosen for low-latency and robust cross-asset coverage.",
            notes="Could not parse article publish date.",
        )

    tickers, target_kind = build_price_targets(row)
    direction = infer_news_direction(row)

    for t in tickers:
        price = try_fetch_price_history(t, published_at)
        if not price:
            continue

        verdict, notes = evaluate_price_follow_through(direction, price)
        side, action, entry, target, sl, plan_note = derive_trade_levels(direction, verdict, float(price["latest_close"]))
        market_reaction = classify_market_reaction(direction, verdict, price.get("volume_ratio"))
        return PriceAnalysis(
            ticker=t,
            start_close=price["start_close"],
            latest_close=price["latest_close"],
            max_close_since=price["max_close_since"],
            min_close_since=price["min_close_since"],
            pct_latest=price["pct_latest"],
            pct_max=price["pct_max"],
            pct_min=price["pct_min"],
            latest_volume=price.get("latest_volume"),
            avg_volume_20=price.get("avg_volume_20"),
            volume_ratio=price.get("volume_ratio"),
            verdict=verdict,
            market_reaction=market_reaction,
            position_side=side,
            action=action,
            suggested_entry=entry,
            suggested_target=target,
            suggested_stop_loss=sl,
            article_published_at_utc=published_at.isoformat(),
            price_window_start_utc=str(price.get("window_start", "")),
            price_window_end_utc=str(price.get("window_end", "")),
            candle_interval="1d",
            candle_interval_reason="Daily candles chosen for low-latency and robust cross-asset coverage.",
            notes=f"{notes} {plan_note}".strip(),
        )

    return PriceAnalysis(
        ticker="",
        start_close=None,
        latest_close=None,
        max_close_since=None,
        min_close_since=None,
        pct_latest=None,
        pct_max=None,
        pct_min=None,
        latest_volume=None,
        avg_volume_20=None,
        volume_ratio=None,
        verdict="no_price_data",
        market_reaction="unknown",
        position_side="none",
        action="no_action",
        suggested_entry=None,
        suggested_target=None,
        suggested_stop_loss=None,
        article_published_at_utc=published_at.isoformat(),
        price_window_start_utc="",
        price_window_end_utc="",
        candle_interval="1d",
        candle_interval_reason="Daily candles chosen for low-latency and robust cross-asset coverage.",
        notes=f"No Yahoo Finance price series found for inferred {target_kind} symbols.",
    )


def fetch_related_news(headline: str, limit: int) -> List[Dict[str, str]]:
    query = quote_plus(headline)
    url = f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"

    feed = feedparser.parse(url)
    out: List[Dict[str, str]] = []
    for entry in feed.entries[:limit]:
        out.append(
            {
                "title": str(getattr(entry, "title", "")),
                "link": str(getattr(entry, "link", "")),
                "published": str(getattr(entry, "published", "")),
                "source": str(getattr(getattr(entry, "source", None), "title", "Google News")),
            }
        )
    return out


def verify_ticker_with_google_search(ticker: str, resolver: SymbolResolver) -> Dict[str, str]:
    t = (ticker or "").strip()
    if not t:
        return {
            "verified_ticker_label": "unknown",
            "verified_ticker_name": "",
            "verified_ticker_source": "",
            "verification_note": "empty_ticker",
        }

    result = resolver.resolve_symbol(t)
    title = str(result.get("google_title", ""))
    name = title
    for sep in [" - ", " | ", ": "]:
        if sep in name:
            name = name.split(sep)[0].strip()
            break
    if not name:
        name = str(result.get("resolved_symbol", t))

    return {
        "verified_ticker_label": str(result.get("label", "unknown")),
        "verified_ticker_name": name,
        "verified_ticker_source": str(result.get("google_link", "")),
        "verification_note": str(result.get("source_step", "resolver")),
    }


def collect_filings(hours: int) -> List[Dict[str, Any]]:
    try:
        fillings.refresh_session()
        raw = fillings.fetch_data(hours=hours)
    except Exception:
        return []

    out: List[Dict[str, Any]] = []
    for item in raw:
        out.append(
            {
                "symbol": str(item.get("symbol", "")),
                "subject": str(item.get("subject", "")),
                "an_dt": str(item.get("an_dt", "")),
                "desc": str(item.get("attchmntText") or item.get("desc") or ""),
                "pdf_url": str(item.get("attchmntFile", "")),
            }
        )
    return out


def match_filings_for_row(row: Dict[str, Any], filings_rows: List[Dict[str, Any]], limit: int = 5) -> List[Dict[str, Any]]:
    cands = [normalize_candidate_symbol(str(x)) for x in (row.get("symbol_candidates") or [])]
    cands = [c for c in cands if c]

    headline = str(row.get("raw_headline", "")).lower()
    matched: List[Dict[str, Any]] = []

    for f in filings_rows:
        sym = normalize_candidate_symbol(str(f.get("symbol", "")))
        subj = str(f.get("subject", "")).lower()
        desc = str(f.get("desc", "")).lower()

        hit_symbol = sym and (sym in cands)
        hit_text = False
        if not hit_symbol and cands:
            for c in cands:
                if c and (c.lower() in headline or c.lower() in subj or c.lower() in desc):
                    hit_text = True
                    break

        if hit_symbol or hit_text:
            matched.append(f)

    matched.sort(key=lambda x: x.get("an_dt", ""), reverse=True)
    return matched[:limit]


def load_review_rows(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("Review file must be a JSON array")
    return data


def select_rows_for_report(rows: List[Dict[str, Any]], max_items: int, min_conf: int) -> List[Dict[str, Any]]:
    def _to_int(v: Any) -> int:
        try:
            return int(float(v))
        except Exception:
            return 0

    def _pipeline_ok(r: Dict[str, Any]) -> bool:
        ps = str(r.get("pipeline_status", "")).strip().lower()
        # Backward compatibility: legacy files have no pipeline_status.
        return (ps == "") or (ps == "success")

    # Prefer rows already passed through your pipeline quality gates.
    filtered = [
        r
        for r in rows
        if _pipeline_ok(r)
        and bool(r.get("india_filter_pass", False))
        and _to_int(r.get("confidence_score_adjusted", r.get("confidence_score", 0)) or 0) >= min_conf
    ]

    if not filtered:
        filtered = [r for r in rows if _pipeline_ok(r)]

    filtered.sort(
        key=lambda x: _to_int(x.get("confidence_score_adjusted", x.get("confidence_score", 0)) or 0),
        reverse=True,
    )
    return filtered[:max_items]


def build_md_report(report_rows: List[Dict[str, Any]], filings_snapshot_count: int) -> str:
    lines: List[str] = []
    lines.append("# Verification Research Report")
    lines.append("")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"Rows analyzed: {len(report_rows)}")
    lines.append(f"NSE filings snapshot rows scanned: {filings_snapshot_count}")
    lines.append("")

    for i, r in enumerate(report_rows, start=1):
        lines.append(f"## {i}. {r.get('raw_headline', 'Untitled')}")
        lines.append("")
        lines.append(f"- Source article: {r.get('article_url', '')}")
        lines.append(f"- Published at: {r.get('published_at', '')}")
        lines.append(f"- Asset type: {r.get('asset_type', '')}")
        lines.append(f"- Stock names: {', '.join(r.get('stock_names', [])) if r.get('stock_names') else ''}")
        lines.append(f"- Commodity names: {', '.join(r.get('commodity_names', [])) if r.get('commodity_names') else ''}")
        lines.append(f"- Price source suggested: {r.get('price_lookup_provider', '')}")
        lines.append(f"- Price symbol hint: {r.get('price_lookup_symbol_hint', '')}")
        driver_assets = r.get("driver_assets", [])
        if driver_assets:
            driver_text = ", ".join([f"{x.get('name', '')} ({x.get('symbol', '')})" for x in driver_assets])
            lines.append(f"- Driver assets: {driver_text}")
        lines.append(f"- Model trade decision: {r.get('trade_decision', '')}")
        lines.append(f"- Direction inferred: {r.get('direction_inferred', '')}")
        lines.append(f"- Ticker used: {r.get('price_analysis', {}).get('ticker', '')}")
        lines.append(f"- Verified ticker label: {r.get('ticker_verification', {}).get('verified_ticker_label', '')}")
        lines.append(f"- Verified ticker name: {r.get('ticker_verification', {}).get('verified_ticker_name', '')}")
        lines.append(f"- Verification source: {r.get('ticker_verification', {}).get('verified_ticker_source', '')}")
        lines.append(f"- Price verdict: {r.get('price_analysis', {}).get('verdict', '')}")
        lines.append(f"- Market reaction: {r.get('price_analysis', {}).get('market_reaction', '')}")
        lines.append(f"- Position side: {r.get('price_analysis', {}).get('position_side', '')}")
        lines.append(f"- Suggested action: {r.get('price_analysis', {}).get('action', '')}")
        lines.append(f"- Price notes: {r.get('price_analysis', {}).get('notes', '')}")
        lines.append(f"- Article time (UTC): {r.get('price_analysis', {}).get('article_published_at_utc', '')}")
        lines.append(f"- Price window start (UTC): {r.get('price_analysis', {}).get('price_window_start_utc', '')}")
        lines.append(f"- Price window end (UTC): {r.get('price_analysis', {}).get('price_window_end_utc', '')}")
        lines.append(f"- Candle interval: {r.get('price_analysis', {}).get('candle_interval', '')}")
        lines.append(f"- Candle rationale: {r.get('price_analysis', {}).get('candle_interval_reason', '')}")

        pa = r.get("price_analysis", {})
        if pa.get("start_close") is not None:
            lines.append(f"- Start close: {pa.get('start_close'):.2f}")
            lines.append(f"- Latest close: {pa.get('latest_close'):.2f}")
            lines.append(f"- Change since article: {pa.get('pct_latest'):.2f}%")
            lines.append(f"- Max move since article: {pa.get('pct_max'):.2f}%")
            lines.append(f"- Min move since article: {pa.get('pct_min'):.2f}%")
            if pa.get("latest_volume") is not None:
                lines.append(f"- Latest volume: {pa.get('latest_volume'):.0f}")
            if pa.get("avg_volume_20") is not None:
                lines.append(f"- Avg volume (20D): {pa.get('avg_volume_20'):.0f}")
            if pa.get("volume_ratio") is not None:
                lines.append(f"- Volume ratio (latest/20D avg): {pa.get('volume_ratio'):.2f}x")
            if pa.get("suggested_entry") is not None:
                lines.append(f"- Suggested entry: {pa.get('suggested_entry'):.2f}")
            if pa.get("suggested_target") is not None:
                lines.append(f"- Suggested target: {pa.get('suggested_target'):.2f}")
            if pa.get("suggested_stop_loss") is not None:
                lines.append(f"- Suggested stop loss: {pa.get('suggested_stop_loss'):.2f}")

        lines.append("")
        lines.append("### Related News Links")
        related = r.get("related_news", [])
        if not related:
            lines.append("- No related links fetched")
        else:
            for x in related:
                lines.append(f"- [{x.get('title', 'link')}]({x.get('link', '')}) ({x.get('source', 'source')})")

        lines.append("")
        lines.append("### Related NSE Filings")
        mfil = r.get("matched_filings", [])
        if not mfil:
            lines.append("- No matched filing found in snapshot window")
        else:
            for f in mfil:
                link = f.get("pdf_url", "")
                title = f"{f.get('symbol', '')} | {f.get('an_dt', '')} | {f.get('subject', '')}"
                if link:
                    lines.append(f"- [{title}]({link})")
                else:
                    lines.append(f"- {title}")

        lines.append("")
        lines.append("### Analyst Notes")
        lines.append(f"- Model summary: {r.get('model_summary', '')}")
        lines.append(f"- Recommendation reasoning: {r.get('recommendation_reasoning', '')}")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    args = parse_args()

    rows = load_review_rows(args.review)
    selected = select_rows_for_report(rows, max_items=max(1, args.max_items), min_conf=max(0, args.min_confidence))
    shared_cache_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data", "cache")
    os.makedirs(shared_cache_dir, exist_ok=True)
    resolver = SymbolResolver(cache_db_path=os.path.join(shared_cache_dir, "symbol_cache.db"))

    filings_rows = collect_filings(hours=max(1, args.filings_hours))
    ticker_verify_cache: Dict[str, Dict[str, str]] = {}

    output_rows: List[Dict[str, Any]] = []
    for row in selected:
        verified_symbols = verify_all_symbols_for_row(row, resolver)
        row_for_analysis = dict(row)
        row_for_analysis["_verified_symbols"] = verified_symbols

        headline = str(row.get("raw_headline", ""))
        direction = infer_news_direction(row)
        price = analyze_price_from_article_date(row_for_analysis)
        related_news = fetch_related_news(headline=headline, limit=max(1, args.related_limit))
        matched_filings = match_filings_for_row(row, filings_rows)
        driver_assets = _extract_driver_assets(row)

        ticker_key = str(price.ticker or "").strip()
        if ticker_key and ticker_key not in ticker_verify_cache:
            ticker_verify_cache[ticker_key] = verify_ticker_with_google_search(ticker_key, resolver)
        verified = ticker_verify_cache.get(
            ticker_key,
            {
                "verified_ticker_label": "unknown",
                "verified_ticker_name": "",
                "verified_ticker_source": "",
                "verification_note": "no_ticker",
            },
        )

        output_rows.append(
            {
                **row,
                "direction_inferred": direction,
                "price_analysis": {
                    "ticker": price.ticker,
                    "start_close": price.start_close,
                    "latest_close": price.latest_close,
                    "max_close_since": price.max_close_since,
                    "min_close_since": price.min_close_since,
                    "pct_latest": price.pct_latest,
                    "pct_max": price.pct_max,
                    "pct_min": price.pct_min,
                    "latest_volume": price.latest_volume,
                    "avg_volume_20": price.avg_volume_20,
                    "volume_ratio": price.volume_ratio,
                    "verdict": price.verdict,
                    "market_reaction": price.market_reaction,
                    "position_side": price.position_side,
                    "action": price.action,
                    "suggested_entry": price.suggested_entry,
                    "suggested_target": price.suggested_target,
                    "suggested_stop_loss": price.suggested_stop_loss,
                    "article_published_at_utc": price.article_published_at_utc,
                    "price_window_start_utc": price.price_window_start_utc,
                    "price_window_end_utc": price.price_window_end_utc,
                    "candle_interval": price.candle_interval,
                    "candle_interval_reason": price.candle_interval_reason,
                    "notes": price.notes,
                },
                "related_news": related_news,
                "matched_filings": matched_filings,
                "driver_assets": driver_assets,
                "ticker_verification": verified,
                "symbol_candidate_verification": verified_symbols,
            }
        )

    with open(args.output_json, "w", encoding="utf-8") as f:
        json.dump(output_rows, f, indent=2, ensure_ascii=True)

    md = build_md_report(output_rows, filings_snapshot_count=len(filings_rows))
    with open(args.output_md, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"Loaded review rows: {len(rows)}")
    print(f"Selected for deep verification: {len(selected)}")
    print(f"NSE filings snapshot fetched: {len(filings_rows)}")
    print(f"Wrote JSON report: {args.output_json}")
    print(f"Wrote Markdown report: {args.output_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
