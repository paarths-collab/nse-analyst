#!/usr/bin/env python3
"""
Deterministic symbol resolver (zero LLM calls).
Resolution order:
1) Alias map
2) NSE autocomplete
3) OpenFIGI search
4) yfinance suffix probe
Then attach Google News RSS evidence for every resolved symbol.

Includes SQLite cache to avoid repeated network calls.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

import cloudscraper
import feedparser
import httpx
import yfinance as yf


ALIAS_MAP: Dict[str, Dict[str, str]] = {
    # India stocks
    "RELIANCE": {"resolved_symbol": "RELIANCE.NS", "label": "stock", "exchange": "NSE"},
    "SBI": {"resolved_symbol": "SBIN.NS", "label": "stock", "exchange": "NSE"},
    "SBIN": {"resolved_symbol": "SBIN.NS", "label": "stock", "exchange": "NSE"},
    "CANBANK": {"resolved_symbol": "CANBK.NS", "label": "stock", "exchange": "NSE"},
    "CANARA": {"resolved_symbol": "CANBK.NS", "label": "stock", "exchange": "NSE"},
    "CANBK": {"resolved_symbol": "CANBK.NS", "label": "stock", "exchange": "NSE"},
    "UCO": {"resolved_symbol": "UCOBANK.NS", "label": "stock", "exchange": "NSE"},
    "UCOBANK": {"resolved_symbol": "UCOBANK.NS", "label": "stock", "exchange": "NSE"},
    # Indian bank aliases (non-obvious ticker mappings)
    "CANARA BANK": {"resolved_symbol": "CANBK.NS", "label": "stock", "exchange": "NSE"},
    "STATE BANK": {"resolved_symbol": "SBIN.NS", "label": "stock", "exchange": "NSE"},
    "STATE BANK OF INDIA": {"resolved_symbol": "SBIN.NS", "label": "stock", "exchange": "NSE"},
    "PUNJAB NATIONAL": {"resolved_symbol": "PNB.NS", "label": "stock", "exchange": "NSE"},
    "PUNJAB NATIONAL BANK": {"resolved_symbol": "PNB.NS", "label": "stock", "exchange": "NSE"},
    "BANK OF BARODA": {"resolved_symbol": "BANKBARODA.NS", "label": "stock", "exchange": "NSE"},
    "KOTAK MAHINDRA": {"resolved_symbol": "KOTAKBANK.NS", "label": "stock", "exchange": "NSE"},
    "KOTAK MAHINDRA BANK": {"resolved_symbol": "KOTAKBANK.NS", "label": "stock", "exchange": "NSE"},
    "AXIS BANK": {"resolved_symbol": "AXISBANK.NS", "label": "stock", "exchange": "NSE"},
    "ICICI BANK": {"resolved_symbol": "ICICIBANK.NS", "label": "stock", "exchange": "NSE"},
    "HDFC BANK": {"resolved_symbol": "HDFCBANK.NS", "label": "stock", "exchange": "NSE"},
    "INDUSIND": {"resolved_symbol": "INDUSINDBK.NS", "label": "stock", "exchange": "NSE"},
    "INDUSIND BANK": {"resolved_symbol": "INDUSINDBK.NS", "label": "stock", "exchange": "NSE"},
    "UCO BANK": {"resolved_symbol": "UCOBANK.NS", "label": "stock", "exchange": "NSE"},
    # Commodities
    "GOLD": {"resolved_symbol": "GC=F", "label": "commodity", "exchange": "COMEX"},
    "SILVER": {"resolved_symbol": "SI=F", "label": "commodity", "exchange": "COMEX"},
    "CRUDE": {"resolved_symbol": "CL=F", "label": "commodity", "exchange": "NYMEX"},
    "CRUDE OIL": {"resolved_symbol": "CL=F", "label": "commodity", "exchange": "NYMEX"},
    "OIL": {"resolved_symbol": "CL=F", "label": "commodity", "exchange": "NYMEX"},
    "COPPER": {"resolved_symbol": "HG=F", "label": "commodity", "exchange": "COMEX"},
    "NATURAL GAS": {"resolved_symbol": "NG=F", "label": "commodity", "exchange": "NYMEX"},
    # FX
    "USDINR": {"resolved_symbol": "INR=X", "label": "fx", "exchange": "FX"},
    "INRUSD": {"resolved_symbol": "INR=X", "label": "fx", "exchange": "FX"},
    "EURUSD": {"resolved_symbol": "EURUSD=X", "label": "fx", "exchange": "FX"},
    "GBPUSD": {"resolved_symbol": "GBPUSD=X", "label": "fx", "exchange": "FX"},
    # Indices
    "NIFTY": {"resolved_symbol": "^NSEI", "label": "index", "exchange": "NSE"},
    "SENSEX": {"resolved_symbol": "^BSESN", "label": "index", "exchange": "BSE"},
    "DOW": {"resolved_symbol": "^DJI", "label": "index", "exchange": "US"},
    "DOW JONES": {"resolved_symbol": "^DJI", "label": "index", "exchange": "US"},
    "NASDAQ": {"resolved_symbol": "^IXIC", "label": "index", "exchange": "US"},
    "SP500": {"resolved_symbol": "^GSPC", "label": "index", "exchange": "US"},
    "S&P 500": {"resolved_symbol": "^GSPC", "label": "index", "exchange": "US"},
    "FTSE": {"resolved_symbol": "^FTSE", "label": "index", "exchange": "LSE"},
    "UKX": {"resolved_symbol": "^FTSE", "label": "index", "exchange": "LSE"},
    "US10Y": {"resolved_symbol": "^TNX", "label": "index", "exchange": "US"},
    # Crypto
    "BITCOIN": {"resolved_symbol": "BTC-USD", "label": "crypto", "exchange": "CRYPTO"},
    "BTC": {"resolved_symbol": "BTC-USD", "label": "crypto", "exchange": "CRYPTO"},
    "ETHEREUM": {"resolved_symbol": "ETH-USD", "label": "crypto", "exchange": "CRYPTO"},
    "ETH": {"resolved_symbol": "ETH-USD", "label": "crypto", "exchange": "CRYPTO"},
}


class SymbolResolver:
    def __init__(self, cache_db_path: str = "symbol_cache.db"):
        self.cache_db_path = cache_db_path
        self.openfigi_api_key = os.getenv("OPENFIGI_API_KEY", "").strip()
        self._last_openfigi_call_ts = 0.0
        self.scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        self.nse_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.nseindia.com/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        }
        self._init_db()
        self._seed_nse_session()

    def _init_db(self) -> None:
        cache_dir = os.path.dirname(self.cache_db_path)
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
        conn = sqlite3.connect(self.cache_db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS symbol_cache (
                    raw_name TEXT PRIMARY KEY,
                    resolved_symbol TEXT NOT NULL,
                    exchange TEXT,
                    asset_class TEXT,
                    source_step TEXT,
                    google_title TEXT,
                    google_link TEXT,
                    updated_at_utc TEXT NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def _cache_get(self, raw_name: str) -> Optional[Dict[str, str]]:
        conn = sqlite3.connect(self.cache_db_path)
        try:
            cur = conn.execute(
                "SELECT raw_name,resolved_symbol,exchange,asset_class,source_step,google_title,google_link,updated_at_utc "
                "FROM symbol_cache WHERE raw_name=?",
                (raw_name,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "raw_name": row[0],
                "resolved_symbol": row[1],
                "exchange": row[2] or "",
                "label": row[3] or "unknown",
                "source_step": row[4] or "cache",
                "google_title": row[5] or "",
                "google_link": row[6] or "",
                "updated_at_utc": row[7] or "",
                "from_cache": "true",
            }
        finally:
            conn.close()

    def _cache_set(self, payload: Dict[str, str]) -> None:
        conn = sqlite3.connect(self.cache_db_path)
        try:
            conn.execute(
                """
                INSERT INTO symbol_cache (
                    raw_name,resolved_symbol,exchange,asset_class,source_step,google_title,google_link,updated_at_utc
                ) VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(raw_name) DO UPDATE SET
                    resolved_symbol=excluded.resolved_symbol,
                    exchange=excluded.exchange,
                    asset_class=excluded.asset_class,
                    source_step=excluded.source_step,
                    google_title=excluded.google_title,
                    google_link=excluded.google_link,
                    updated_at_utc=excluded.updated_at_utc
                """,
                (
                    payload.get("raw_name", ""),
                    payload.get("resolved_symbol", ""),
                    payload.get("exchange", ""),
                    payload.get("label", "unknown"),
                    payload.get("source_step", ""),
                    payload.get("google_title", ""),
                    payload.get("google_link", ""),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _seed_nse_session(self) -> None:
        try:
            self.scraper.get("https://www.nseindia.com", headers=self.nse_headers, timeout=15)
        except Exception:
            pass

    @staticmethod
    def _norm(raw: str) -> str:
        return " ".join((raw or "").strip().upper().split())

    @staticmethod
    def _clean_symbol(raw: str) -> str:
        # Keep only left-most candidate before pipe/space and enforce a strict symbol alphabet.
        text = (raw or "").strip().upper()
        if not text:
            return ""
        text = text.split("|")[0].strip()
        text = text.split(" ")[0].strip()
        if re.match(r"^[A-Z0-9\.\=\^\-]{1,15}$", text):
            return text
        return ""

    @staticmethod
    def _is_valid_ticker_candidate(candidate: str) -> bool:
        return bool(re.match(r"^[A-Z0-9\.\=\^\-]{1,15}$", (candidate or "").strip().upper()))

    @staticmethod
    def _normalize_name_for_lookup(raw: str) -> str:
        text = " ".join((raw or "").strip().upper().split())
        if not text:
            return ""

        # Remove punctuation noise but keep token boundaries.
        text = text.replace(".", " ").replace(",", " ").replace("-", " ")
        tokens = [t for t in text.split() if t]
        if not tokens:
            return ""

        # Strip legal/entity suffixes and common trailing descriptors.
        trailing_drop = {
            "LIMITED",
            "LTD",
            "CORPORATION",
            "CORP",
            "INDUSTRIES",
            "INDUSTRY",
            "BANK",
        }
        while len(tokens) > 1 and tokens[-1] in trailing_drop:
            tokens.pop()

        return " ".join(tokens)

    def _lookup_keys(self, raw_name: str) -> List[str]:
        base = self._norm(raw_name)
        normalized = self._normalize_name_for_lookup(raw_name)
        out: List[str] = []
        for k in [base, normalized]:
            if k and k not in out:
                out.append(k)
        return out

    def _alias_map(self, raw_name: str) -> Optional[Dict[str, str]]:
        for key in self._lookup_keys(raw_name):
            if key in ALIAS_MAP:
                v = ALIAS_MAP[key]
                return {
                    "raw_name": raw_name,
                    "resolved_symbol": v["resolved_symbol"],
                    "label": v["label"],
                    "exchange": v.get("exchange", ""),
                    "source_step": "alias_map",
                }
        return None

    def _syntax_fast_path(self, raw_name: str) -> Optional[Dict[str, str]]:
        key = self._norm(raw_name)
        if not key:
            return None

        if key.startswith("^"):
            return {
                "raw_name": raw_name,
                "resolved_symbol": key,
                "label": "index",
                "exchange": "SYNTAX",
                "source_step": "syntax_fast_path",
            }

        if key.endswith("=F"):
            return {
                "raw_name": raw_name,
                "resolved_symbol": key,
                "label": "commodity",
                "exchange": "SYNTAX",
                "source_step": "syntax_fast_path",
            }

        if key.endswith("=X"):
            return {
                "raw_name": raw_name,
                "resolved_symbol": key,
                "label": "fx",
                "exchange": "SYNTAX",
                "source_step": "syntax_fast_path",
            }

        if "." in key:
            return {
                "raw_name": raw_name,
                "resolved_symbol": key,
                "label": "stock",
                "exchange": "SYNTAX",
                "source_step": "syntax_fast_path",
            }

        return None

    def _nse_autocomplete(self, query: str) -> Optional[Dict[str, str]]:
        q = (query or "").strip()
        if not q:
            return None
        url = f"https://www.nseindia.com/api/search/autocomplete?q={quote_plus(q)}"
        try:
            res = self.scraper.get(url, headers=self.nse_headers, timeout=15)
            if res.status_code != 200:
                return None
            data = res.json()
        except Exception:
            return None

        candidates = data.get("symbols") if isinstance(data, dict) else None
        if not isinstance(candidates, list) or not candidates:
            return None

        first = candidates[0]
        symbol = str(first.get("symbol", "")).strip().upper()
        if not symbol:
            return None

        return {
            "raw_name": query,
            "resolved_symbol": symbol + ".NS",
            "label": "stock",
            "exchange": "NSE",
            "source_step": "nse_autocomplete",
        }

    def _openfigi_search(self, query: str, retries: int = 3) -> Optional[Dict[str, str]]:
        q = (query or "").strip()
        if not q:
            return None

        headers: Dict[str, str] = {}
        if self.openfigi_api_key:
            headers["X-OPENFIGI-APIKEY"] = self.openfigi_api_key

        for i in range(max(1, retries)):
            elapsed = time.time() - self._last_openfigi_call_ts
            if elapsed < 2.0:
                time.sleep(2.0 - elapsed)

            try:
                with httpx.Client(timeout=12.0) as client:
                    # /v3/search endpoint with key header when available.
                    res = client.post(
                        "https://api.openfigi.com/v3/search",
                        json={"query": q},
                        headers=headers,
                    )
                self._last_openfigi_call_ts = time.time()
            except Exception:
                return None

            if res.status_code == 200:
                try:
                    data = res.json()
                    break
                except Exception:
                    return None

            if res.status_code == 429:
                # Free-tier limit handling: pause a full minute before retry.
                time.sleep(60)
                continue

            return None
        else:
            return None

        if not isinstance(data, list) or not data:
            return None

        first = data[0] if isinstance(data[0], dict) else None
        if not first:
            return None

        ticker = str(first.get("ticker") or "").strip().upper()
        exch = str(first.get("exchCode") or "").strip().upper()
        sec_type = str(first.get("securityType") or "").strip().lower()
        if not ticker:
            return None

        resolved = ticker
        label = "stock"
        if "index" in sec_type:
            label = "index"
        elif "future" in sec_type or "commodity" in sec_type:
            label = "commodity"
        elif "currency" in sec_type:
            label = "fx"

        return {
            "raw_name": query,
            "resolved_symbol": resolved,
            "label": label,
            "exchange": exch,
            "source_step": "openfigi",
        }

    def _yfinance_probe(self, query: str) -> Optional[Dict[str, str]]:
        base = self._clean_symbol(self._norm(query).replace(" ", ""))
        if not base:
            return None

        candidates = [
            base,
            f"{base}.NS",
            f"{base}.BO",
            f"{base}=F",
            f"{base}=X",
            f"^{base}",
        ]

        for c in candidates:
            if not self._is_valid_ticker_candidate(c):
                continue
            try:
                df = yf.download(c, period="5d", interval="1d", progress=False, auto_adjust=False, threads=False)
                if df is not None and not df.empty:
                    label = "stock"
                    if c.startswith("^"):
                        label = "index"
                    elif c.endswith("=F"):
                        label = "commodity"
                    elif c.endswith("=X"):
                        label = "fx"
                    return {
                        "raw_name": query,
                        "resolved_symbol": c,
                        "label": label,
                        "exchange": "YF",
                        "source_step": "yfinance_probe",
                    }
            except Exception:
                continue

        return None

    def _google_evidence(self, raw_name: str, resolved_symbol: str) -> Dict[str, str]:
        q = quote_plus(f'"{raw_name}" "{resolved_symbol}" finance')
        url = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
        feed = feedparser.parse(url)
        if not getattr(feed, "entries", None):
            return {"google_title": "", "google_link": ""}

        top = feed.entries[0]
        return {
            "google_title": str(getattr(top, "title", "")),
            "google_link": str(getattr(top, "link", "")),
        }

    def resolve_symbol(self, raw_name: str) -> Dict[str, str]:
        cleaned = self._clean_symbol(raw_name)
        key = cleaned or self._norm(raw_name)
        if not key:
            return {
                "raw_name": raw_name,
                "resolved_symbol": "",
                "label": "unknown",
                "exchange": "",
                "source_step": "none",
                "google_title": "",
                "google_link": "",
            }

        normalized_key = self._normalize_name_for_lookup(key)

        deterministic = self._alias_map(key) or self._syntax_fast_path(key)
        cached = self._cache_get(key)
        if not cached and normalized_key and normalized_key != key:
            cached = self._cache_get(normalized_key)
            if cached:
                cached = {
                    **cached,
                    "raw_name": raw_name,
                }

        # Prefer cache on repeat lookups, but auto-correct stale cache when deterministic mapping disagrees.
        if cached:
            if deterministic:
                same_symbol = str(cached.get("resolved_symbol", "")) == str(deterministic.get("resolved_symbol", ""))
                same_label = str(cached.get("label", "")) == str(deterministic.get("label", ""))
                if same_symbol and same_label:
                    return {
                        **cached,
                        "source_step": "cache",
                        "from_cache": "true",
                    }

                evidence = self._google_evidence(key, deterministic.get("resolved_symbol", ""))
                payload = {
                    **deterministic,
                    **evidence,
                }
                self._cache_set(payload)
                return payload

            return {
                **cached,
                "source_step": "cache",
                "from_cache": "true",
            }

        if deterministic:
            evidence = self._google_evidence(key, deterministic.get("resolved_symbol", ""))
            payload = {
                **deterministic,
                **evidence,
            }
            self._cache_set(payload)
            return payload

        resolved = (
            self._nse_autocomplete(normalized_key or key)
            or self._openfigi_search(normalized_key or key)
            or self._yfinance_probe(normalized_key or key)
            or {
                "raw_name": key,
                "resolved_symbol": key,
                "label": "unknown",
                "exchange": "",
                "source_step": "unresolved",
            }
        )

        evidence = self._google_evidence(key, resolved.get("resolved_symbol", ""))
        payload = {
            **resolved,
            **evidence,
        }
        self._cache_set(payload)
        return payload

    def resolve_many(self, names: List[str]) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        seen = set()
        for n in names:
            r = self.resolve_symbol(n)
            rs = r.get("resolved_symbol", "")
            if rs and rs not in seen:
                seen.add(rs)
                out.append(r)
        return out
