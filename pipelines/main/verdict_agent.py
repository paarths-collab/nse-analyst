#!/usr/bin/env python3
"""LLM verdict agent for unified news + filings events."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List

from groq import Groq


DEFAULT_AGENT_MODEL = "openai/gpt-oss-120b"


def _is_rate_limit_error(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status == 429:
        return True
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "too many requests" in msg


def _retry_after_seconds(exc: Exception) -> int | None:
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


class VerdictAgent:
    def __init__(self, model: str = DEFAULT_AGENT_MODEL):
        self.model = model
        api_key = os.environ.get("GROQ_API_KEY", "").strip()
        self.client = Groq(api_key=api_key) if api_key else None
        self.max_retries = max(1, int(os.environ.get("PIPELINE_GROQ_MAX_RETRIES", "4")))
        self.base_wait_seconds = max(2, int(os.environ.get("PIPELINE_GROQ_WAIT_BASE", "8")))

    def _fallback(self, reason: str) -> Dict[str, Any]:
        return {
            "verdict": "WATCH",
            "confidence": "LOW",
            "event_sentiment": "NEUTRAL",
            "price_volume_indication": "insufficient_data",
            "expected_price_change_percent": 0.0,
            "time_horizon": "SHORT_TERM",
            "reasoning_short": f"Automatic fallback used: {reason}",
            "reasoning_long": "Insufficient model output for full institutional analysis.",
            "key_catalysts": [],
            "key_risks": [reason],
            "news_sources_reviewed": [],
        }

    @staticmethod
    def _extract_json_object(raw: str) -> str:
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return ""
        return raw[start : end + 1]

    def analyze(self, event: Dict[str, Any]) -> Dict[str, Any]:
        if self.client is None:
            return self._fallback("missing_groq_api_key")

        prompt = (
            "You are a quantitative market analyst. Analyze this event and return strict JSON only.\n"
            "Required JSON keys: verdict, confidence, event_sentiment, price_volume_indication, expected_price_change_percent, time_horizon, "
            "reasoning_short, reasoning_long, key_catalysts, key_risks, news_sources_reviewed.\n"
            "Allowed verdict: BULLISH, BEARISH, NEUTRAL, WATCH.\n"
            "Allowed confidence: HIGH, MEDIUM, LOW.\n"
            "Allowed event_sentiment: POSITIVE, NEGATIVE, NEUTRAL.\n"
            "price_volume_indication must explain what price movement + volume suggest versus event_sentiment.\n"
            "expected_price_change_percent must be a number.\n"
            "reasoning_short must be 3-5 sentences.\n"
            "reasoning_long should be detailed but concise.\n"
            "key_catalysts and key_risks must be arrays of short strings.\n"
            "news_sources_reviewed must be an array of URLs from inputs where available.\n"
            "Use INPUT_EVENT_JSON.event_polarity and INPUT_EVENT_JSON.market_data to determine sentiment alignment.\n"
            "If data is sparse or uncertain, use WATCH with LOW confidence.\n\n"
            "INPUT_EVENT_JSON:\n"
            + json.dumps(event, ensure_ascii=True)
        )

        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    temperature=0.2,
                    max_completion_tokens=1400,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": "Return valid JSON only."},
                        {"role": "user", "content": prompt},
                    ],
                )
                raw = (resp.choices[0].message.content or "").strip()
                blob = self._extract_json_object(raw)
                obj = json.loads(blob if blob else raw)
                if not isinstance(obj, dict):
                    return self._fallback("non_object_response")

                # Normalize minimal schema.
                return {
                    "verdict": str(obj.get("verdict", "WATCH")).upper(),
                    "confidence": str(obj.get("confidence", "LOW")).upper(),
                    "event_sentiment": str(obj.get("event_sentiment", "NEUTRAL")).upper(),
                    "price_volume_indication": str(obj.get("price_volume_indication", "")),
                    "expected_price_change_percent": float(obj.get("expected_price_change_percent", 0.0) or 0.0),
                    "time_horizon": str(obj.get("time_horizon", "SHORT_TERM")),
                    "reasoning_short": str(obj.get("reasoning_short", "")),
                    "reasoning_long": str(obj.get("reasoning_long", "")),
                    "key_catalysts": [str(x) for x in (obj.get("key_catalysts") or [])][:6],
                    "key_risks": [str(x) for x in (obj.get("key_risks") or [])][:6],
                    "news_sources_reviewed": [str(x) for x in (obj.get("news_sources_reviewed") or [])][:6],
                }
            except Exception as exc:
                if _is_rate_limit_error(exc) and attempt < self.max_retries:
                    retry_after = _retry_after_seconds(exc)
                    wait_seconds = retry_after if retry_after else min(60, self.base_wait_seconds * (2 ** (attempt - 1)))
                    time.sleep(wait_seconds)
                    continue
                return self._fallback(f"analysis_error:{type(exc).__name__}")

        return self._fallback("analysis_error:max_retries_exhausted")
