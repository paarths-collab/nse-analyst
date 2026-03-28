#!/usr/bin/env python3
"""Unified pipeline: news sources + real-time NSE filings + verdict agent + Telegram."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fillings
from groq import Groq
from infra.config import load_env_file
from infra.source_registry import filter_sources, load_sources
from scripts.scrape_sources import run_scrape
from scripts.symbol_resolver import SymbolResolver
from pipelines.main.verdict_agent import VerdictAgent
from telegram_notifier import send_detailed_telegram_alert

try:
    from content_filter import should_process
    CONTENT_FILTER_AVAILABLE = True
except Exception:
    CONTENT_FILTER_AVAILABLE = False


# Generic words that should never be treated as tradable tickers.
_GENERIC_TICKER_BLOCKLIST = {
    "CHART", "CHARTS", "MARKET", "MARKETS", "NEWS", "LIVE", "UPDATE", "UPDATES",
    "VOLATILITY", "VOLUME", "STOCK", "STOCKS", "SHARE", "SHARES", "BUSINESS", "FINANCE",
}

# Cache failed yfinance lookups to avoid retrying the same bad symbol every loop.
_FAILED_TICKERS: Dict[str, float] = {}
_FAILED_TICKER_TTL_SECONDS = 6 * 3600


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def _is_rate_limit_error(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status == 429:
        return True
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "too many requests" in msg


def _retry_after_seconds(exc: Exception) -> Optional[int]:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    raw = headers.get("retry-after") if hasattr(headers, "get") else None
    if not raw:
        return None
    try:
        return max(1, int(str(raw).strip()))
    except Exception:
        return None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Unified news + filings + verdict + Telegram pipeline")
    p.add_argument("--registry", default=os.environ.get("PIPELINE_REGISTRY", "sources/sources_registry.json"), help="Source registry file")
    p.add_argument("--shard", type=int, default=_env_int("PIPELINE_SHARD", 1), help="News shard")
    p.add_argument("--live-only", action="store_true", default=_env_bool("PIPELINE_LIVE_ONLY", False), help="Scrape only live sources")
    p.add_argument("--news-max-items", type=int, default=_env_int("PIPELINE_NEWS_MAX_ITEMS", 3), help="Max news items per source")
    p.add_argument("--news-concurrency", type=int, default=_env_int("PIPELINE_NEWS_CONCURRENCY", 5), help="Concurrent source fetches")
    p.add_argument("--filings-hours", type=int, default=_env_int("PIPELINE_FILINGS_HOURS", 6), help="Filings lookback window")
    p.add_argument("--filings-max-items", type=int, default=_env_int("PIPELINE_FILINGS_MAX_ITEMS", 20), help="Max filings to include")
    p.add_argument("--max-analysis", type=int, default=_env_int("PIPELINE_MAX_ANALYSIS", 20), help="Max unified events to analyze")
    p.add_argument("--model", default=os.environ.get("PIPELINE_MODEL", "openai/gpt-oss-120b"), help="Verdict model")
    p.add_argument("--send-telegram", action="store_true", default=_env_bool("PIPELINE_SEND_TELEGRAM", False), help="Send verdicts to Telegram")
    p.add_argument("--output", default=os.environ.get("PIPELINE_OUTPUT", "data/json/unified_pipeline_output_live.json"), help="Output JSON path")
    p.add_argument("--continuous", action="store_true", default=_env_bool("PIPELINE_CONTINUOUS", False), help="Run continuously with polling")
    p.add_argument("--poll-seconds", type=int, default=_env_int("PIPELINE_POLL_SECONDS", 60), help="Polling interval in seconds for --continuous mode")
    p.add_argument("--seen-state", default=os.environ.get("PIPELINE_SEEN_STATE", "data/cache/unified_seen_events.json"), help="Persistent duplicate-blocker state file")
    p.add_argument("--seen-max", type=int, default=_env_int("PIPELINE_SEEN_MAX", 50000), help="Max seen event fingerprints to retain")
    p.add_argument("--max-loops", type=int, default=_env_int("PIPELINE_MAX_LOOPS", 0), help="Optional loop cap for --continuous mode (0 = infinite)")
    return p.parse_args()


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _clean_symbol(raw: str) -> str:
    text = (raw or "").strip().upper()
    if not text:
        return ""
    text = text.split("|")[0].split(" ")[0].strip()
    if re.match(r"^[A-Z0-9\.\=\^\-]{1,20}$", text):
        return text
    return ""


def _is_valid_ticker_candidate(sym: str) -> bool:
    t = (sym or "").strip().upper()
    if not t:
        return False
    if t in _GENERIC_TICKER_BLOCKLIST:
        return False
    # Reject malformed starts like ".IE-NETWORK-FE".
    if not re.match(r"^[A-Z\^]", t):
        return False
    # Common supported patterns for this pipeline.
    patterns = [
        r"^\^[A-Z][A-Z0-9]{1,15}$",            # indices, e.g. ^NSEI
        r"^[A-Z][A-Z0-9]{1,14}$",               # plain equity symbols
        r"^[A-Z][A-Z0-9]{1,14}\.(NS|BO)$",     # NSE/BSE equities
        r"^[A-Z]{1,8}=F$",                      # futures, e.g. CL=F
        r"^[A-Z]{3,8}=X$",                      # FX, e.g. INR=X
        r"^[A-Z]{2,10}-USD$",                   # crypto, e.g. BTC-USD
    ]
    return any(re.match(p, t) for p in patterns)


def _is_rate_limit_error(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status == 429:
        return True
    response = getattr(exc, "response", None)
    if response is not None and getattr(response, "status_code", None) == 429:
        return True
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "too many requests" in msg


def _retry_after_seconds(exc: Exception) -> Optional[int]:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    raw = headers.get("retry-after") if hasattr(headers, "get") else None
    if not raw:
        return None
    try:
        return max(1, int(str(raw).strip()))
    except Exception:
        return None


def _llm_find_stock(event: Dict[str, Any], client: Optional[Groq]) -> str:
    if client is None:
        return ""

    prompt = (
        "Find the most likely tradable stock symbol for this event. Return strict JSON only: "
        "{\"symbol\":\"...\",\"company\":\"...\",\"confidence\":\"HIGH|MEDIUM|LOW\"}. "
        "If unknown, set symbol to empty string. Keep symbol short and exchange-friendly (e.g., ATGL, RELIANCE, SBIN).\n\n"
        "INPUT_EVENT:\n" + json.dumps(
            {
                "source_type": event.get("source_type", ""),
                "symbol": event.get("symbol", ""),
                "headline": event.get("headline", ""),
                "filing_subject": event.get("filing_subject", ""),
                "filing_description": event.get("filing_description", ""),
            },
            ensure_ascii=True,
        )
    )

    max_retries = max(1, _env_int("PIPELINE_GROQ_MAX_RETRIES", 4))
    base_wait = max(2, _env_int("PIPELINE_GROQ_WAIT_BASE", 8))

    for attempt in range(1, max_retries + 1):
        try:
            resp = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                temperature=0.0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "Return valid JSON only."},
                    {"role": "user", "content": prompt},
                ],
            )
            raw = (resp.choices[0].message.content or "").strip()
            obj = json.loads(raw)
            symbol = _clean_symbol(str(obj.get("symbol", "")))
            return symbol if symbol else ""
        except Exception as exc:
            if _is_rate_limit_error(exc) and attempt < max_retries:
                retry_after = _retry_after_seconds(exc)
                wait_seconds = retry_after if retry_after else min(60, base_wait * (2 ** (attempt - 1)))
                time.sleep(wait_seconds)
                continue
            return ""

    return ""


def _parse_date_any(raw: str) -> Optional[datetime]:
    if not raw:
        return None

    text = raw.strip()
    text = re.sub(r"([+-]\d{3})$", r"\g<1>0", text)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    try:
        dt2 = parsedate_to_datetime(text)
        if dt2.tzinfo is None:
            dt2 = dt2.replace(tzinfo=timezone.utc)
        return dt2.astimezone(timezone.utc)
    except Exception:
        pass

    for fmt in ["%d-%b-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def _infer_event_polarity(event: Dict[str, Any]) -> str:
    blob = " ".join(
        [
            str(event.get("headline", "")),
            str(event.get("filing_subject", "")),
            str(event.get("filing_description", "")),
            str(event.get("content_text", ""))[:1200],
        ]
    ).lower()

    pos_words = [
        "wins order",
        "order win",
        "approval",
        "acquisition",
        "partnership",
        "raises guidance",
        "dividend",
        "buyback",
        "profit rise",
        "record",
    ]
    neg_words = [
        "penalty",
        "fraud",
        "default",
        "downgrade",
        "loss",
        "resignation",
        "cessation",
        "fall",
        "decline",
        "investigation",
        "windfall tax",
    ]

    pos_hits = sum(1 for w in pos_words if w in blob)
    neg_hits = sum(1 for w in neg_words if w in blob)
    if pos_hits > neg_hits:
        return "positive"
    if neg_hits > pos_hits:
        return "negative"
    return "neutral"


def _resolve_event_symbol(event: Dict[str, Any], resolver: SymbolResolver) -> Dict[str, str]:
    candidates: List[str] = []

    direct_symbol = _clean_symbol(str(event.get("symbol", "")))
    if direct_symbol:
        candidates.append(direct_symbol)

    for raw in [
        str((event.get("raw_payload") or {}).get("sm_name", "")).strip(),
        str(event.get("headline", "")).strip(),
    ]:
        if raw:
            candidates.append(raw)

    upper_tokens = re.findall(r"\b[A-Z]{2,12}\b", str(event.get("headline", "")))
    blocked = {
        "IPO", "SEBI", "NSE", "BSE", "US", "UK", "INDIA", "MARKET", "MARKETS",
        "STOCK", "STOCKS", "SHARE", "SHARES", "VOLUME", "VOLATILITY", "CHART", "CHARTS",
        "TODAY", "LIVE", "UPDATE", "UPDATES", "NEWS", "BUSINESS", "FINANCE", "ETF", "FII", "DII",
    }
    for t in upper_tokens:
        if t not in blocked:
            candidates.append(t)

    # Strictly clean and filter candidates before resolver/network calls.
    cleaned_candidates: List[str] = []
    for c in candidates:
        cs = _clean_symbol(c)
        if cs and _is_valid_ticker_candidate(cs) and cs not in cleaned_candidates:
            cleaned_candidates.append(cs)

    if not cleaned_candidates:
        return {
            "raw_name": str(event.get("symbol", "")),
            "resolved_symbol": "",
            "label": "unknown",
            "note": "no_clean_symbol_candidate",
        }

    resolved = resolver.resolve_many(cleaned_candidates)
    if not resolved:
        return {
            "raw_name": str(event.get("symbol", "")),
            "resolved_symbol": "",
            "label": "unknown",
            "note": "no_symbol_resolved",
        }

    trusted_steps = {"alias_map", "syntax_fast_path", "nse_autocomplete", "openfigi", "yfinance_probe", "cache"}
    preferred = [
        x
        for x in resolved
        if str(x.get("label", "")) in {"stock", "commodity", "index", "fx"}
        and str(x.get("source_step", "")) in trusted_steps
    ]

    if not preferred:
        return {
            "raw_name": str(event.get("symbol", "")),
            "resolved_symbol": "",
            "label": "unknown",
            "note": "no_trusted_symbol_resolution",
        }

    pick = preferred[0]
    return {
        "raw_name": str(pick.get("raw_name", "")),
        "resolved_symbol": str(pick.get("resolved_symbol", "")),
        "label": str(pick.get("label", "unknown")),
        "note": str(pick.get("source_step", "")),
    }


def _fetch_price_volume_snapshot(ticker: str, published_at: Optional[datetime]) -> Dict[str, Any]:
    if not ticker:
        return {"status": "no_ticker"}

    clean_ticker = _clean_symbol(ticker)
    if not clean_ticker or not _is_valid_ticker_candidate(clean_ticker):
        return {"status": "invalid_ticker", "ticker": ticker}

    now_ts = time.time()
    failed_at = _FAILED_TICKERS.get(clean_ticker)
    if failed_at and (now_ts - failed_at) < _FAILED_TICKER_TTL_SECONDS:
        return {"status": "skipped_recent_failure", "ticker": clean_ticker}

    anchor = published_at or datetime.now(timezone.utc)
    start = (anchor - timedelta(days=3)).date().isoformat()
    end = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()

    try:
        df = yf.download(
            clean_ticker,
            start=start,
            end=end,
            interval="1d",
            progress=False,
            auto_adjust=False,
            threads=False,
        )
    except Exception:
        df = None

    if df is None or df.empty:
        try:
            df = yf.download(
                clean_ticker,
                period="6mo",
                interval="1d",
                progress=False,
                auto_adjust=False,
                threads=False,
            )
        except Exception:
            df = None

    if df is None or df.empty:
        _FAILED_TICKERS[clean_ticker] = now_ts
        return {"status": "no_price_data", "ticker": clean_ticker}

    close_series = df.get("Close")
    vol_series = df.get("Volume")
    if isinstance(close_series, pd.DataFrame):
        close_series = close_series.iloc[:, 0]
    if isinstance(vol_series, pd.DataFrame):
        vol_series = vol_series.iloc[:, 0]

    if close_series is None:
        return {"status": "no_close_series", "ticker": clean_ticker}
    if vol_series is None:
        vol_series = pd.Series([0.0] * len(df), index=df.index)

    rows = []
    for idx in df.index:
        try:
            day = datetime(idx.year, idx.month, idx.day, tzinfo=timezone.utc)
            c = float(close_series.loc[idx])
            v = float(vol_series.loc[idx]) if idx in vol_series.index else 0.0
            if c > 0:
                rows.append((day, c, v))
        except Exception:
            continue

    if not rows:
        return {"status": "empty_rows", "ticker": clean_ticker}

    rows.sort(key=lambda x: x[0])
    on_or_after = [x for x in rows if x[0].date() >= anchor.date()]
    if not on_or_after:
        on_or_after = [rows[-1]]

    start_close = on_or_after[0][1]
    latest_close = on_or_after[-1][1]
    latest_volume = on_or_after[-1][2]
    avg_volume_20 = None
    vol_ratio = None

    tail = rows[-20:] if len(rows) >= 20 else rows
    if len(tail) >= 5:
        avg_volume_20 = sum(v for _, _, v in tail) / len(tail)
        vol_ratio = (latest_volume / avg_volume_20) if avg_volume_20 > 0 else None

    pct_change = ((latest_close - start_close) / start_close) * 100.0 if start_close else 0.0

    if pct_change >= 1.5:
        px_signal = "up"
    elif pct_change <= -1.5:
        px_signal = "down"
    else:
        px_signal = "flat"

    if vol_ratio is None:
        vol_signal = "unknown"
    elif vol_ratio >= 1.25:
        vol_signal = "high"
    elif vol_ratio <= 0.85:
        vol_signal = "low"
    else:
        vol_signal = "normal"

    return {
        "status": "ok",
        "ticker": clean_ticker,
        "start_close": start_close,
        "latest_close": latest_close,
        "pct_change_since_event": pct_change,
        "latest_volume": latest_volume,
        "avg_volume_20": avg_volume_20,
        "volume_ratio": vol_ratio,
        "price_signal": px_signal,
        "volume_signal": vol_signal,
    }


def _extract_filing_text(item: Dict[str, Any]) -> str:
    uid = fillings.make_uid(item)
    safe = uid.replace(":", "-")
    txt_path = os.path.join(fillings.PDF_DIR, safe + ".txt")

    if os.path.exists(txt_path):
        try:
            with open(txt_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass

    pdf_url = str(item.get("attchmntFile", "") or "").strip()
    if not pdf_url:
        return _clean_text(str(item.get("attchmntText") or item.get("desc") or ""))

    pdf_path = fillings.download_pdf(pdf_url, safe + ".pdf")
    if not pdf_path:
        return _clean_text(str(item.get("attchmntText") or item.get("desc") or ""))

    text = fillings.extract_text(pdf_path).strip()
    if text:
        try:
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            pass
        return text

    return _clean_text(str(item.get("attchmntText") or item.get("desc") or ""))


def _build_filing_events(hours: int, max_items: int) -> List[Dict[str, Any]]:
    fillings.refresh_session()
    rows = fillings.fetch_data(hours=max(1, hours))

    events: List[Dict[str, Any]] = []
    seen = set()
    for row in rows:
        uid = fillings.make_uid(row)
        if uid in seen:
            continue
        seen.add(uid)

        symbol = _clean_text(str(row.get("symbol", "")))
        subject = _clean_text(str(row.get("subject", "")))
        desc = _clean_text(str(row.get("attchmntText") or row.get("desc") or ""))

        # Hard pre-gate for routine compliance filings.
        routine_blob = f"{subject} {desc}".lower()
        routine_markers = [
            "trading window closure",
            "closure of trading window",
            "code of conduct of insider trading",
            "pursuant to sebi (prohibition of insider trading)",
            "insider trading regulations, 2015",
        ]
        if any(m in routine_blob for m in routine_markers):
            continue

        if CONTENT_FILTER_AVAILABLE:
            gate = should_process(symbol, subject, desc)
            if not bool(gate.get("should_process", True)):
                continue

        text = _extract_filing_text(row)
        events.append(
            {
                "event_id": uid,
                "source_type": "filing",
                "source_id": "nse_filings",
                "source_name": "NSE Filings",
                "symbol": symbol,
                "headline": f"{symbol} | {subject or desc[:120]}".strip(" |"),
                "article_url": str(row.get("attchmntFile", "") or "https://www.nseindia.com/"),
                "published_at": str(row.get("an_dt", "")),
                "filing_subject": subject,
                "filing_description": desc,
                "content_text": text[:8000],
                "raw_payload": row,
            }
        )

        if len(events) >= max(1, max_items):
            break

    return events


def _build_news_events(registry: str, shard: int, live_only: bool, max_items: int, concurrency: int) -> List[Dict[str, Any]]:
    sources = load_sources(registry)
    selected = filter_sources(sources, shard=shard, live_only=live_only)
    if not selected:
        return []

    scraped = asyncio.run(
        run_scrape(
            sources=selected,
            max_items=max(1, max_items),
            concurrency=max(1, concurrency),
            use_playwright_fallback=False,
        )
    )

    events: List[Dict[str, Any]] = []
    for e in scraped:
        if e.event_type != "news_event":
            continue

        # Best-effort symbol extraction from uppercase tokens in headline.
        tokens = re.findall(r"\b[A-Z]{2,12}\b", str(e.headline or ""))
        symbol = tokens[0] if tokens else ""

        events.append(
            {
                "event_id": e.event_id,
                "source_type": "news",
                "source_id": e.source_id,
                "source_name": e.source_name,
                "symbol": symbol,
                "headline": e.headline,
                "article_url": e.article_url,
                "published_at": e.published_at,
                "content_text": _clean_text(str((e.payload_json or {}).get("snippet", "")))[:2000],
                "raw_payload": asdict(e),
            }
        )

    return events


def _attach_news_context(filing_events: List[Dict[str, Any]], news_events: List[Dict[str, Any]]) -> None:
    news_lines = [f"- {n.get('headline', '')} | {n.get('article_url', '')}" for n in news_events[:60]]
    news_digest = "\n".join(news_lines)
    for f in filing_events:
        f["news_context"] = news_digest[:4000]


def _event_fingerprint(event: Dict[str, Any]) -> str:
    src_type = str(event.get("source_type", ""))
    src_id = str(event.get("source_id", ""))

    # Prefer stable dedup key from news scraper payload when present.
    raw_payload = event.get("raw_payload") or {}
    if src_type == "news":
        rk = str(raw_payload.get("dedup_key", "")).strip()
        if rk:
            return f"news:{rk}"

    if src_type == "filing":
        uid = str(event.get("event_id", "")).strip()
        if uid:
            return f"filing:{uid}"

    basis = "|".join(
        [
            src_type,
            src_id,
            str(event.get("article_url", "")).strip().lower(),
            str(event.get("published_at", "")).strip(),
            str(event.get("headline", "")).strip().lower(),
        ]
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _load_seen(path: str) -> set[str]:
    if not path:
        return set()
    try:
        if not os.path.exists(path):
            return set()
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return {str(x) for x in data if str(x).strip()}
    except Exception:
        return set()
    return set()


def _save_seen(path: str, seen: set[str], seen_max: int) -> set[str]:
    if not path:
        return seen
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    trimmed = set(sorted(seen)[-max(1, seen_max):])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(list(trimmed)), f, ensure_ascii=True, indent=2)
    return trimmed


def _filter_new_events(events: List[Dict[str, Any]], seen: set[str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    local_seen: set[str] = set()
    for e in events:
        fp = _event_fingerprint(e)
        e["event_fingerprint"] = fp
        if not fp:
            continue
        # Block duplicates both across polling cycles and within the same cycle.
        if fp in seen or fp in local_seen:
            continue
        local_seen.add(fp)
        out.append(e)
    return out


def _to_agent_input(event: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "source_type": event.get("source_type", ""),
        "symbol": event.get("symbol", ""),
        "resolved_symbol": event.get("resolved_symbol", ""),
        "resolved_symbol_label": event.get("resolved_symbol_label", ""),
        "headline": event.get("headline", ""),
        "published_at": event.get("published_at", ""),
        "article_url": event.get("article_url", ""),
        "filing_subject": event.get("filing_subject", ""),
        "filing_description": event.get("filing_description", ""),
        # Keep prompt bounded to reduce malformed/oversized request risk.
        "content_text": str(event.get("content_text", ""))[:3500],
        "news_context": str(event.get("news_context", ""))[:1800],
        "event_polarity": event.get("event_polarity", "neutral"),
        "market_data": event.get("market_data", {}),
    }


def _select_events_for_analysis(events: List[Dict[str, Any]], max_analysis: int) -> List[Dict[str, Any]]:
    max_n = max(1, max_analysis)
    if len(events) <= max_n:
        return events

    filings = [e for e in events if str(e.get("source_type", "")) == "filing"]
    news = [e for e in events if str(e.get("source_type", "")) == "news"]
    others = [e for e in events if str(e.get("source_type", "")) not in {"filing", "news"}]

    # If both streams exist, alternate picks so one stream cannot starve the other.
    if filings and news:
        selected: List[Dict[str, Any]] = []
        i = 0
        j = 0
        while len(selected) < max_n and (i < len(filings) or j < len(news)):
            if i < len(filings):
                selected.append(filings[i])
                i += 1
                if len(selected) >= max_n:
                    break
            if j < len(news):
                selected.append(news[j])
                j += 1

        # Fill any remaining quota with whichever bucket still has items.
        remaining = filings[i:] + news[j:] + others
        if len(selected) < max_n and remaining:
            selected.extend(remaining[: max_n - len(selected)])
        return selected

    return events[:max_n]


def _run_once(args: argparse.Namespace, resolver: SymbolResolver, stock_finder_client: Optional[Groq], agent: VerdictAgent, seen: set[str]) -> List[Dict[str, Any]]:
    news_events = _build_news_events(
        registry=args.registry,
        shard=args.shard,
        live_only=args.live_only,
        max_items=args.news_max_items,
        concurrency=args.news_concurrency,
    )
    filing_events = _build_filing_events(hours=args.filings_hours, max_items=args.filings_max_items)
    _attach_news_context(filing_events, news_events)

    unified = filing_events + news_events
    unified = _filter_new_events(unified, seen)
    if not unified:
        print("No new events available from news or filings")
        return []

    out_rows: List[Dict[str, Any]] = []

    selected_events = _select_events_for_analysis(unified, args.max_analysis)

    for event in selected_events:
        symbol_info = _resolve_event_symbol(event, resolver)

        if not symbol_info.get("resolved_symbol"):
            llm_symbol = _llm_find_stock(event, stock_finder_client)
            if llm_symbol:
                event["llm_stock_candidate"] = llm_symbol
                symbol_info = _resolve_event_symbol({**event, "symbol": llm_symbol}, resolver)

        published_dt = _parse_date_any(str(event.get("published_at", "")))
        trusted_labels = {"stock", "commodity", "index", "fx"}
        trusted_notes = {"alias_map", "syntax_fast_path", "nse_autocomplete", "openfigi", "yfinance_probe", "cache"}
        resolved_symbol = str(symbol_info.get("resolved_symbol", "")).strip()
        if (
            resolved_symbol
            and str(symbol_info.get("label", "")) in trusted_labels
            and str(symbol_info.get("note", "")) in trusted_notes
        ):
            market_data = _fetch_price_volume_snapshot(resolved_symbol, published_dt)
        else:
            market_data = {
                "status": "skipped_untrusted_symbol",
                "ticker": resolved_symbol,
                "label": str(symbol_info.get("label", "")),
                "note": str(symbol_info.get("note", "")),
            }
        event_polarity = _infer_event_polarity(event)

        event["resolved_symbol"] = symbol_info.get("resolved_symbol", "")
        event["resolved_symbol_label"] = symbol_info.get("label", "unknown")
        event["symbol_resolution_note"] = symbol_info.get("note", "")
        event["event_polarity"] = event_polarity
        event["market_data"] = market_data

        agent_input = _to_agent_input(event)
        verdict = agent.analyze(agent_input)

        row = {
            **event,
            "agent_input": agent_input,
            "analysis": verdict,
        }
        out_rows.append(row)

        if args.send_telegram:
            symbol = str(
                event.get("resolved_symbol", "")
                or event.get("symbol", "")
                or event.get("source_id", "UNKNOWN")
            )
            sent = send_detailed_telegram_alert(
                symbol=symbol,
                verdict=str(verdict.get("verdict", "WATCH")),
                price_move=float(verdict.get("expected_price_change_percent", 0.0) or 0.0),
                time_horizon=str(verdict.get("time_horizon", "SHORT_TERM")),
                reasoning_short=str(verdict.get("reasoning_short", "")),
                reasoning_long=str(verdict.get("reasoning_long", "")),
                catalysts=[str(x) for x in (verdict.get("key_catalysts") or [])],
                risks=[str(x) for x in (verdict.get("key_risks") or [])],
                sources=[str(event.get("article_url", ""))],
            )
            row["telegram_sent"] = bool(sent)

        fp = str(event.get("event_fingerprint", "")).strip()
        if fp:
            seen.add(fp)

    print(f"News events: {len(news_events)}")
    print(f"Filing events: {len(filing_events)}")
    print(f"New events after duplicate blocker: {len(unified)}")
    print(f"Analyzed events: {len(out_rows)}")
    if args.send_telegram:
        sent_count = sum(1 for r in out_rows if r.get("telegram_sent"))
        print(f"Telegram sent: {sent_count}")

    return out_rows


def main() -> int:
    load_env_file()
    args = parse_args()

    # content_filter logs include Unicode arrows that can break cp1252 terminals.
    logging.getLogger("content_filter").setLevel(logging.WARNING)
    logging.getLogger("pipelines.filings.content_filter").setLevel(logging.WARNING)
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)

    os.makedirs(fillings.PDF_DIR, exist_ok=True)

    shared_cache_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data", "cache")
    os.makedirs(shared_cache_dir, exist_ok=True)
    resolver = SymbolResolver(cache_db_path=os.path.join(shared_cache_dir, "symbol_cache.db"))
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    stock_finder_client = Groq(api_key=groq_key) if groq_key else None
    agent = VerdictAgent(model=args.model)
    seen = _load_seen(args.seen_state)
    loops = 0

    while True:
        loops += 1
        out_rows = _run_once(args, resolver, stock_finder_client, agent, seen)

        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(out_rows, indent=2, ensure_ascii=True), encoding="utf-8")
        seen = _save_seen(args.seen_state, seen, args.seen_max)

        print(f"Output: {out_path}")
        print(f"Seen fingerprints tracked: {len(seen)}")

        if not args.continuous:
            break

        if args.max_loops > 0 and loops >= args.max_loops:
            break

        time.sleep(max(3, args.poll_seconds))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
