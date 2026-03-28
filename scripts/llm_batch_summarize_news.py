#!/usr/bin/env python3
"""
Batch summarize scraped news with an LLM for human validation.

Input: scraped events JSON from scraper.
Output: JSON file with model headline + summary + manual review fields.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List

# Add parent path for local package imports when run as a script.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from groq import Groq
from infra.config import load_env_file
from scripts.llm_validator import validate_batch

DEFAULT_MODEL = "llama-3.1-8b-instant"
DEFAULT_BATCH_SIZE = 20
DEFAULT_SLEEP = 0.8

INDIA_HINT_KEYWORDS = {
    "india",
    "indian",
    "nse",
    "bse",
    "sebi",
    "rbi",
    "sensex",
    "nifty",
    "rupee",
    "inr",
    "mumbai",
    "delhi",
    "adani",
    "reliance",
    "tata",
    "hdfc",
    "icici",
    "sbi",
}


@dataclass
class ItemForModel:
    item_id: str
    source_id: str
    source_name: str
    headline: str
    article_url: str
    published_at: str
    snippet: str


def _load_events(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("Input JSON must be a list of event objects")
    return data


def _to_model_items(events: List[Dict[str, Any]]) -> List[ItemForModel]:
    items: List[ItemForModel] = []
    for idx, e in enumerate(events):
        item_id = str(e.get("event_id") or e.get("dedup_key") or f"row-{idx+1}")
        items.append(
            ItemForModel(
                item_id=item_id,
                source_id=str(e.get("source_id", "")),
                source_name=str(e.get("source_name", "")),
                headline=str(e.get("headline", "")),
                article_url=str(e.get("article_url", "")),
                published_at=str(e.get("published_at", "")),
                snippet=str(e.get("content_snippet", ""))[:350],
            )
        )
    return items


def _chunks(items: List[ItemForModel], size: int) -> List[List[ItemForModel]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _is_likely_india_relevant_fast(event: Dict[str, Any]) -> bool:
    # Fast lexical prefilter to reduce LLM calls and latency.
    blob = " ".join(
        [
            str(event.get("source_id", "")),
            str(event.get("source_name", "")),
            str(event.get("headline", "")),
            str(event.get("content_snippet", ""))[:250],
        ]
    ).lower()
    return any(k in blob for k in INDIA_HINT_KEYWORDS)


def _prefilter_events_for_latency(
    events: List[Dict[str, Any]],
    enabled: bool,
) -> tuple[List[Dict[str, Any]], int]:
    if not enabled:
        return events, 0

    kept: List[Dict[str, Any]] = []
    skipped = 0
    for e in events:
        if _is_likely_india_relevant_fast(e):
            kept.append(e)
        else:
            skipped += 1
    return kept, skipped


def _run_decision_engine(
    row: Dict[str, Any],
    min_confidence: int,
    direct_only: bool,
) -> tuple[str, bool, str]:
    # Deterministic gate before human review to keep triage simple and fast.
    if str(row.get("pipeline_status", "")) != "success":
        return "reject", False, "pipeline_not_success"

    if row.get("validation_warnings"):
        return "review", False, "validation_warnings_present"

    impact = str(row.get("india_market_impact", "none")).lower().strip()
    if direct_only and impact != "direct":
        return "review", False, "india_impact_not_direct"

    conf = int(row.get("confidence_score_adjusted", row.get("confidence_score", 0)) or 0)
    if conf < min_confidence:
        return "review", False, "confidence_below_threshold"

    trade_decision = str(row.get("trade_decision", "no_call")).lower().strip()
    if trade_decision == "buy":
        return "candidate_trade", True, "passes_all_rules"
    if trade_decision == "watch":
        return "watchlist", False, "watch_signal"
    return "reject", False, "trade_decision_not_actionable"


def _make_prompt(batch: List[ItemForModel]) -> str:
    payload = [
        {
            "item_id": x.item_id,
            "source_id": x.source_id,
            "source_name": x.source_name,
            "headline": x.headline,
            "article_url": x.article_url,
            "published_at": x.published_at,
            "snippet": x.snippet,
        }
        for x in batch
    ]

    return (
        "Return strict JSON only.\n"
        "For each input item return one object with EXACT keys:\n"
        "item_id, rewritten_headline, summary, related_country, india_market_impact, india_impact_reason, "
        "event_certainty, certainty_reason, likely_market_relevant, relevance_reason, impact_level, "
        "asset_type, contains_stock_name, stock_names, contains_commodity, commodity_names, primary_asset_name, "
        "price_lookup_provider, price_lookup_symbol_hint, "
        "event_summary_impact, short_term_outlook, medium_term_outlook, long_term_outlook, risk_assessment, data_quality_note, "
        "symbol_candidates, price_action_signal, volume_signal, preferred_chart_timeframes, preferred_candle_setups, "
        "fundamentals_checklist, company_news_checklist, trade_decision, entry_plan, stop_loss_plan, exit_plan, "
        "recommendation_reasoning, confidence_score, needs_additional_data, additional_data_required.\n"
        "Rules: no hallucinations, no fabricated prices, do not skip items, output length must equal input length.\n"
        "trade_decision in {buy,watch,avoid,no_call}. india_market_impact in {direct,indirect,none}.\n"
        "asset_type in {stock,commodity,index,macro,other}.\n"
        "contains_stock_name and contains_commodity are booleans.\n"
        "If commodity detected (e.g., gold, silver, crude), set asset_type=commodity and fill commodity_names.\n"
        "Set price_lookup_provider from {yahoo_finance,nse,bse,mcx,comex,unknown}.\n"
        "Set price_lookup_symbol_hint with practical symbol if known (e.g., RELIANCE.NS, GC=F, SI=F, CL=F).\n"
        "If uncertain use watch/no_call and low confidence.\n"
        "Return as {\"items\": [...]} JSON object.\n"
        "\nINPUT_ITEMS_JSON:\n"
        + json.dumps(payload, ensure_ascii=True)
    )


def _call_model(client: Groq, model: str, batch: List[ItemForModel]) -> List[Dict[str, Any]]:
    prompt = _make_prompt(batch)
    resp = client.chat.completions.create(
        model=model,
        temperature=0.1,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": "Return valid JSON only."},
            {"role": "user", "content": prompt},
        ],
    )
    text = (resp.choices[0].message.content or "").strip()

    obj = json.loads(text)
    if isinstance(obj, dict) and "items" in obj and isinstance(obj["items"], list):
        return obj["items"]
    if isinstance(obj, list):
        return obj
    raise ValueError("Model output JSON must be an array or {\"items\": [...]} object")


def _index_by_item_id(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        key = str(r.get("item_id", "")).strip()
        if key:
            out[key] = r
    return out


def _fallback_row_for_item(x: ItemForModel) -> Dict[str, Any]:
    """Create a fallback row for failed LLM processing."""
    return {
        "item_id": x.item_id,
        "rewritten_headline": x.headline[:100],
        "summary": "[LLM FAILURE: No output generated]",
        "related_country": "unknown",
        "india_market_impact": "unknown",
        "india_impact_reason": "[llm_failed]",
        "event_certainty": "unknown",
        "certainty_reason": "[llm_failed]",
        "likely_market_relevant": None,
        "relevance_reason": "[llm_failed]",
        "impact_level": "unknown",
        "asset_type": "other",
        "contains_stock_name": False,
        "stock_names": [],
        "contains_commodity": False,
        "commodity_names": [],
        "primary_asset_name": "",
        "price_lookup_provider": "unknown",
        "price_lookup_symbol_hint": "",
        "event_summary_impact": "[LLM failed to analyze]",
        "short_term_outlook": "[UNDETERMINED]",
        "medium_term_outlook": "[UNDETERMINED]",
        "long_term_outlook": "[UNDETERMINED]",
        "risk_assessment": "[UNDETERMINED]",
        "data_quality_note": "[LLM processing failed; no analysis available]",
        "symbol_candidates": [],
        "price_action_signal": "unknown",
        "volume_signal": "unknown",
        "preferred_chart_timeframes": [],
        "preferred_candle_setups": [],
        "fundamentals_checklist": [],
        "company_news_checklist": [],
        "trade_decision": "no_call",
        "entry_plan": "[N/A - LLM failed]",
        "stop_loss_plan": "[N/A - LLM failed]",
        "exit_plan": "[N/A - LLM failed]",
        "recommendation_reasoning": "[LLM processing failed; cannot provide analysis]",
        "confidence_score": 0,
        "needs_additional_data": True,
        "additional_data_required": ["full_llm_analysis"],
    }


def build_review_output(
    original_events: List[Dict[str, Any]],
    llm_rows: List[Dict[str, Any]],
    decision_min_confidence: int,
    decision_direct_only: bool,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Build review output with validation layer.
    
    Returns:
        (review_rows, validation_summary)
    """
    out: List[Dict[str, Any]] = []

    # Run validation on all LLM rows
    validated_rows, validation_stats = validate_batch(llm_rows)
    validated_by_id = _index_by_item_id(validated_rows)

    for idx, e in enumerate(original_events):
        item_id = str(e.get("event_id") or e.get("dedup_key") or f"row-{idx+1}")
        m = validated_by_id.get(item_id, {})

        # Pipeline status: separate failures from decisions
        pipeline_status = m.get("pipeline_status", "success")
        
        # India filter: only applies if pipeline succeeded
        india_impact = str(m.get("india_market_impact", "none")).strip().lower()
        india_filter_pass = (
            pipeline_status == "success"
            and india_impact in {"direct", "indirect"}
        )

        row = {
                "item_id": item_id,
                "source_id": e.get("source_id", ""),
                "source_name": e.get("source_name", ""),
                "article_url": e.get("article_url", ""),
                "published_at": e.get("published_at", ""),
                "raw_headline": e.get("headline", ""),
                # MODEL OUTPUT
                "model_headline": m.get("rewritten_headline", ""),
                "model_summary": m.get("summary", ""),
                "model_market_relevant": m.get("likely_market_relevant", None),
                "model_relevance_reason": m.get("relevance_reason", ""),
                "model_impact_level": m.get("impact_level", "unknown"),
                # ASSET CLASSIFICATION
                "asset_type": m.get("asset_type", "other"),
                "contains_stock_name": m.get("contains_stock_name", False),
                "stock_names": m.get("stock_names", []),
                "contains_commodity": m.get("contains_commodity", False),
                "commodity_names": m.get("commodity_names", []),
                "primary_asset_name": m.get("primary_asset_name", ""),
                "price_lookup_provider": m.get("price_lookup_provider", "unknown"),
                "price_lookup_symbol_hint": m.get("price_lookup_symbol_hint", ""),
                # GEOGRAPHIC
                "related_country": m.get("related_country", "unknown"),
                "india_market_impact": m.get("india_market_impact", "unknown"),
                "india_impact_reason": m.get("india_impact_reason", ""),
                "india_filter_pass": india_filter_pass,
                # CERTAINTY
                "event_certainty": m.get("event_certainty", "unknown"),
                "certainty_reason": m.get("certainty_reason", ""),
                "confidence_score": m.get("confidence_score", 0),
                "confidence_score_adjusted": m.get("confidence_score_adjusted", m.get("confidence_score", 0)),
                # STRUCTURED REASONING
                "event_summary_impact": m.get("event_summary_impact", ""),
                "short_term_outlook": m.get("short_term_outlook", ""),
                "medium_term_outlook": m.get("medium_term_outlook", ""),
                "long_term_outlook": m.get("long_term_outlook", ""),
                "risk_assessment": m.get("risk_assessment", ""),
                "data_quality_note": m.get("data_quality_note", ""),
                # TECHNICAL
                "symbol_candidates": m.get("symbol_candidates", []),
                "price_action_signal": m.get("price_action_signal", "unknown"),
                "volume_signal": m.get("volume_signal", "unknown"),
                "preferred_chart_timeframes": m.get("preferred_chart_timeframes", []),
                "preferred_candle_setups": m.get("preferred_candle_setups", []),
                # FUNDAMENTAL
                "fundamentals_checklist": m.get("fundamentals_checklist", []),
                "company_news_checklist": m.get("company_news_checklist", []),
                # TRADE DECISION
                "trade_decision": m.get("trade_decision", "no_call"),
                "entry_plan": m.get("entry_plan", ""),
                "stop_loss_plan": m.get("stop_loss_plan", ""),
                "exit_plan": m.get("exit_plan", ""),
                "recommendation_reasoning": m.get("recommendation_reasoning", ""),
                "needs_additional_data": m.get("needs_additional_data", True),
                "additional_data_required": m.get("additional_data_required", []),
                # VALIDATION LAYER
                "pipeline_status": pipeline_status,
                "validation_error_type": m.get("validation_error_type"),
                "validation_warnings": m.get("validation_warnings", []),
                "validation_contradiction_details": m.get("validation_contradiction_details", {}),
                # USER REVIEW
                "pipeline_filtered_out": not india_filter_pass,
                "review_status": "pending",
                "review_valid": None,
                "review_notes": "",
        }

        de_status, de_candidate, de_reason = _run_decision_engine(
            row,
            min_confidence=decision_min_confidence,
            direct_only=decision_direct_only,
        )
        row["decision_engine_status"] = de_status
        row["decision_engine_candidate"] = de_candidate
        row["decision_engine_reason"] = de_reason

        out.append(row)

    return out, validation_stats


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Batch summarize scraped news with LLM")
    p.add_argument("--input", default="scraped_events_multi_page.json", help="Input scraped events JSON")
    p.add_argument("--output", default="news_llm_review.json", help="Output review JSON")
    p.add_argument("--model", default=DEFAULT_MODEL, help="Groq model name")
    p.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Items per LLM batch")
    p.add_argument("--sleep-sec", type=float, default=DEFAULT_SLEEP, help="Sleep between LLM calls")
    p.add_argument("--max-items", type=int, default=0, help="Optional cap for quick runs (0 = all)")
    p.add_argument(
        "--prefilter-india",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Apply fast India lexical prefilter before LLM to reduce latency (default: on)",
    )
    p.add_argument(
        "--decision-min-confidence",
        type=int,
        default=70,
        help="Minimum adjusted confidence for decision engine candidate_trade",
    )
    p.add_argument(
        "--decision-direct-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Decision engine requires india_market_impact=direct for candidate_trade (default: on)",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    # Allow running directly from local .env without manual shell export.
    load_env_file()

    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        print("ERROR: GROQ_API_KEY is not set.")
        print("Set it before running, e.g. in PowerShell:")
        print("  $env:GROQ_API_KEY='your_key_here'")
        return 2

    events = _load_events(args.input)
    original_count = len(events)
    if args.max_items > 0:
        events = events[: args.max_items]

    events, prefilter_skipped = _prefilter_events_for_latency(events, enabled=args.prefilter_india)
    if not events:
        print("No events found in input.")
        return 0

    items = _to_model_items(events)
    batches = _chunks(items, max(1, args.batch_size))

    client = Groq(api_key=api_key)
    llm_rows: List[Dict[str, Any]] = []

    print(f"Loaded {original_count} items from {args.input}")
    if args.max_items > 0:
        print(f"Applied max-items: processing first {args.max_items}")
    if args.prefilter_india:
        print(f"Prefilter skipped {prefilter_skipped} items before LLM")
    print(f"Items sent to LLM: {len(items)}")
    print(f"Sending {len(batches)} batch(es) to model {args.model}")

    for i, batch in enumerate(batches, start=1):
        try:
            batch_rows = _call_model(client, args.model, batch)
            # Some responses may be partial; backfill missing item_ids.
            got = _index_by_item_id(batch_rows)
            repaired_rows: List[Dict[str, Any]] = []
            for x in batch:
                repaired_rows.append(got.get(x.item_id, _fallback_row_for_item(x)))

            llm_rows.extend(repaired_rows)
            print(f"Batch {i}/{len(batches)} done: {len(repaired_rows)} rows")
        except Exception as e:
            print(f"Batch {i}/{len(batches)} failed: {e}")
            # Soft fallback: generate placeholder rows so review file still aligns.
            for x in batch:
                llm_rows.append(_fallback_row_for_item(x))
        if i < len(batches) and args.sleep_sec > 0:
            time.sleep(args.sleep_sec)

    review_rows, validation_stats = build_review_output(
        events,
        llm_rows,
        decision_min_confidence=max(0, min(100, args.decision_min_confidence)),
        decision_direct_only=args.decision_direct_only,
    )
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(review_rows, f, indent=2, ensure_ascii=True)

    kept = sum(1 for r in review_rows if r.get("india_filter_pass") is True)
    filtered = len(review_rows) - kept
    pipeline_errors = sum(1 for r in review_rows if r.get("pipeline_status") != "success")
    validation_flags = sum(
        1 for r in review_rows if r.get("pipeline_status") == "validation_error" or r.get("validation_warnings")
    )
    candidate_trades = sum(1 for r in review_rows if r.get("decision_engine_candidate") is True)

    print(f"\nWrote review file: {args.output}")
    print(f"\nPIPELINE STATISTICS:")
    print(f"  Total items: {len(review_rows)}")
    print(f"  Pipeline errors (LLM failed): {pipeline_errors}")
    print(f"  India relevance filter: kept={kept}, filtered_out={filtered}")
    print(f"  Validation flags (contradictions/low quality): {validation_flags}")
    print(f"  Decision engine candidates: {candidate_trades}")
    print(f"\nVALIDATION ERRORS:")
    for error_type, count in validation_stats.get("errors", {}).items():
        if count > 0:
            print(f"  {error_type}: {count}")
    print(f"\nWARNING PATTERNS:")
    for warning_type, count in validation_stats.get("warnings_by_type", {}).items():
        print(f"  {warning_type}: {count} items")

    print(f"\nNext steps:")
    print(f"  1. Review {pipeline_errors} items with pipeline errors (pipeline_status != 'success')")
    print(f"  2. Review {validation_flags} items with validation warnings (check validation_warnings field)")
    print(f"  3. Triage {candidate_trades} decision_engine_candidate items first")
    print(f"  4. For remaining {kept} India-relevant items, review and mark with review_valid=true/false")
    return 0


if __name__ == "__main__":
    sys.exit(main())
