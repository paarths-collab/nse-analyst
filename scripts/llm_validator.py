#!/usr/bin/env python3
"""
Validation layer for LLM output.

Checks:
1. LLM failures (missing fields, parse errors)
2. Logical contradictions (negative sentiment + "buy")
3. Confidence integrity (high confidence but vague reasoning)
4. Data quality flags

Separates SYSTEM FAILURES from MARKET DECISIONS.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List

@dataclass
class ValidationResult:
    is_valid: bool
    has_error: bool
    error_type: str | None  # "llm_missing", "llm_malformed", "logical_contradiction", "insufficient_data"
    confidence_adjusted: int
    warnings: List[str]
    contradiction_details: Dict[str, Any]

class LLMValidator:
    """Validate and repair LLM output."""

    REQUIRED_FIELDS = [
        "item_id",
        "rewritten_headline",
        "summary",
        "india_market_impact",
        "event_certainty",
        "trade_decision",
        "confidence_score",
    ]

    NEGATIVE_SENTIMENT_KEYWORDS = [
        "crash",
        "collapse",
        "decline",
        "loss",
        "bleeding",
        "weak",
        "plunge",
        "tumble",
        "sell-off",
        "bearish",
        "downward",
        "negative",
        "shortage",
        "ban",
        "sanctions",
        "default",
        "bankruptcy",
    ]

    POSITIVE_SENTIMENT_KEYWORDS = [
        "surge",
        "rally",
        "gain",
        "bullish",
        "strong",
        "boost",
        "rally",
        "jump",
        "profit",
        "growth",
        "expansion",
        "deal",
        "partnership",
        "approval",
        "record",
        "breakthrough",
    ]

    def __init__(self):
        pass

    def validate(self, row: Dict[str, Any]) -> ValidationResult:
        """Validate a single LLM output row."""
        warnings: List[str] = []
        contradiction_details: Dict[str, Any] = {}
        has_error = False
        error_type = None
        confidence_adjusted = row.get("confidence_score", 0)

        # 1. Check for LLM missing row
        summary = str(row.get("summary", "")).strip()
        impact_reason = str(row.get("india_impact_reason", "")).lower()
        
        is_llm_missing = (
            "[LLM FAILURE" in summary
            or "[LLM failed" in summary
            or "llm_missing_row" in impact_reason
            or "llm_failed" in impact_reason
        )

        if is_llm_missing:
            has_error = True
            error_type = "llm_missing"
            confidence_adjusted = 0
            return ValidationResult(
                is_valid=False,
                has_error=True,
                error_type="llm_missing",
                confidence_adjusted=0,
                warnings=["LLM row failed or missing; no reliable output"],
                contradiction_details={},
            )

        # 2. Check required fields
        missing_fields = [f for f in self.REQUIRED_FIELDS if f not in row or not str(row.get(f, "")).strip()]
        if missing_fields:
            has_error = True
            error_type = "llm_malformed"
            warnings.append(f"Missing required fields: {', '.join(missing_fields)}")
            confidence_adjusted = max(0, confidence_adjusted - 30)

        # 3. Check for logical contradictions
        summary_lower = summary.lower()
        reasoning = str(row.get("recommendation_reasoning", "")).lower()
        trade_decision = str(row.get("trade_decision", "")).lower().strip()

        has_negative_signal = any(kw in summary_lower or kw in reasoning for kw in self.NEGATIVE_SENTIMENT_KEYWORDS)
        has_positive_signal = any(kw in summary_lower or kw in reasoning for kw in self.POSITIVE_SENTIMENT_KEYWORDS)

        # Contradiction: negative sentiment + buy/watch
        if has_negative_signal and trade_decision in ["buy", "watch"]:
            warnings.append("CONTRADICTION: Negative sentiment + bullish trade decision")
            contradiction_details["sentiment_trade_mismatch"] = {
                "sentiment": "negative",
                "decision": trade_decision,
            }
            confidence_adjusted = max(0, confidence_adjusted - 40)

        # Contradiction: positive sentiment + avoid
        if has_positive_signal and trade_decision in ["avoid"]:
            warnings.append("CONTRADICTION: Positive sentiment + bearish trade decision")
            contradiction_details["sentiment_trade_mismatch"] = {
                "sentiment": "positive",
                "decision": trade_decision,
            }
            confidence_adjusted = max(0, confidence_adjusted - 25)

        # 4. Check confidence integrity
        if confidence_adjusted > 70 and len(reasoning.strip()) < 20:
            warnings.append("HIGH CONFIDENCE but vague reasoning")
            confidence_adjusted = max(0, confidence_adjusted - 20)

        # 5. Check for empty trade plan when decision is "buy"
        if trade_decision == "buy":
            entry_plan = str(row.get("entry_plan", "")).strip()
            exit_plan = str(row.get("exit_plan", "")).strip()
            sl_plan = str(row.get("stop_loss_plan", "")).strip()

            if not entry_plan or not exit_plan or not sl_plan:
                warnings.append("BUY signal but missing entry/exit/SL plan")
                confidence_adjusted = max(0, confidence_adjusted - 35)

        # 6. Check certainty vs confidence alignment
        certainty = str(row.get("event_certainty", "")).lower().strip()
        if certainty == "rumor" and confidence_adjusted > 60:
            warnings.append("Rumor-level event but high confidence score")
            confidence_adjusted = max(0, confidence_adjusted - 25)

        # 7. Validate India impact logic
        india_impact = str(row.get("india_market_impact", "")).lower().strip()
        if india_impact == "none" and trade_decision in ["buy", "watch"]:
            warnings.append("No India impact but positive trade decision")
            confidence_adjusted = max(0, confidence_adjusted - 30)

        # Overall validity
        is_valid = (
            not has_error
            and len(missing_fields) == 0
            and not contradiction_details
            and len(warnings) == 0
        )

        return ValidationResult(
            is_valid=is_valid,
            has_error=has_error,
            error_type=error_type,
            confidence_adjusted=confidence_adjusted,
            warnings=warnings,
            contradiction_details=contradiction_details,
        )


def validate_batch(rows: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Validate a batch of LLM rows and return enriched rows + stats."""
    validator = LLMValidator()
    validated_rows: List[Dict[str, Any]] = []
    stats = {
        "total": len(rows),
        "valid": 0,
        "errors": {
            "llm_missing": 0,
            "llm_malformed": 0,
            "logical_contradiction": 0,
            "insufficient_data": 0,
        },
        "warnings_by_type": {},
    }

    for row in rows:
        result = validator.validate(row)

        # Add validation metadata
        enriched_row = {
            **row,
            "validation_status": "valid" if result.is_valid else "flagged",
            "pipeline_status": (
                "llm_error"
                if result.has_error
                else ("validation_error" if not result.is_valid else "success")
            ),
            "validation_error_type": result.error_type,
            "validation_warnings": result.warnings,
            "validation_contradiction_details": result.contradiction_details,
            "confidence_score_adjusted": result.confidence_adjusted,
        }

        validated_rows.append(enriched_row)

        # Update stats
        if result.has_error or not result.is_valid:
            stats["errors"][result.error_type or "insufficient_data"] += 1
        else:
            stats["valid"] += 1

        # Track warnings
        for warning in result.warnings:
            warning_key = warning.split(":")[0]
            stats["warnings_by_type"][warning_key] = stats["warnings_by_type"].get(warning_key, 0) + 1

    return validated_rows, stats


if __name__ == "__main__":
    # Simple test
    test_row = {
        "item_id": "test-1",
        "rewritten_headline": "Test headline",
        "summary": "OMCs are bleeding; this is bearish.",
        "recommendation_reasoning": "Company losing money.",
        "trade_decision": "buy",
        "confidence_score": 85,
        "event_certainty": "confirmed",
    }

    validator = LLMValidator()
    result = validator.validate(test_row)
    print(f"Valid: {result.is_valid}")
    print(f"Pipeline Status: {result.error_type if result.has_error else 'success'}")
    print(f"Confidence Adjusted: {result.confidence_adjusted} (from 85)")
    print(f"Warnings: {result.warnings}")
    print(f"Contradiction Details: {result.contradiction_details}")
