# Validator Rules Quick Reference

## Overview
The validator layer catches 6 types of issues and adjusts confidence accordingly.

---

## Rule-by-Rule Breakdown

### 🔴 Rule 1: LLM Failure Detection
**Triggers when:**
- `summary` contains `[LLM FAILURE`
- `india_impact_reason` = `[llm_failed]`
- Required fields missing (item_id, headline, summary, trade_decision, etc.)

**Action:**
- `has_error = True`
- `error_type = "llm_missing"`
- `confidence_adjusted = 0`
- `pipeline_status = "llm_error"` (not "success")

**Penalty:** -∞ (max penalty + skip additional checks)

**Example:**
```json
{
  "summary": "[LLM FAILURE: No output generated]",
  "confidence_score": 85,
  "confidence_score_adjusted": 0,
  "error_type": "llm_missing",
  "validation_warnings": ["LLM row failed or missing; no reliable output"]
}
```

---

### 🟠 Rule 2: Sentiment-Trade Decision Contradiction
**Triggers when:**
```python
(negative_sentiment AND trade_decision IN ["buy", "watch"])
  OR
(positive_sentiment AND trade_decision == "avoid")
```

**Detection Keywords:**
- **Negative:** crash, collapse, decline, loss, bleeding, weak, plunge, bearish, ban, default, bankruptcy
- **Positive:** surge, rally, gain, strong, boost, profit, growth, deal, approval, breakthrough

**Action:**
- `confidence_adjusted -= 25-40` (depending on direction)
- Add warning: `"CONTRADICTION: Negative sentiment + bullish trade decision"`
- Populate `contradiction_details` field

**Penalty:** -25 to -40 points

**Examples:**
```json
{
  "summary": "OMCs bleeding with crude at $22",
  "trade_decision": "buy",
  "confidence_score": 70,
  "confidence_score_adjusted": 30,           // -40 penalty
  "validation_warnings": ["CONTRADICTION: Negative sentiment + bullish trade decision"],
  "contradiction_details": {
    "sentiment_trade_mismatch": {
      "sentiment": "negative",
      "decision": "buy"
    }
  }
}
```

---

### 🟠 Rule 3: Trade Rule Validation
**Triggers when:**
- `trade_decision == "buy"` AND
- ANY of these are missing:
  - `entry_plan` (empty or whitespace)
  - `exit_plan` (empty or whitespace)
  - `stop_loss_plan` (empty or whitespace)

**Action:**
- `confidence_adjusted -= 35`
- Add warning: `"BUY signal but missing entry/exit/SL plan"`

**Penalty:** -35 points

**Rationale:** A "buy" signal without an exit strategy is speculation, not trading.

**Example:**
```json
{
  "trade_decision": "buy",
  "entry_plan": "Buy on breakout above 500",
  "exit_plan": "",                          // ❌ MISSING
  "stop_loss_plan": "SL at 450",
  "confidence_score": 80,
  "confidence_score_adjusted": 45,          // -35 penalty
  "validation_warnings": ["BUY signal but missing entry/exit/SL plan"]
}
```

---

### 🟡 Rule 4: Certainty-Confidence Alignment
**Triggers when:**
- `event_certainty == "rumor"` AND
- `confidence_score > 60`

**Action:**
- `confidence_adjusted -= 25`
- Add warning: `"Rumor-level event but high confidence score"`

**Penalty:** -25 points

**Rationale:** Rumors should NOT justify high confidence.

**Example:**
```json
{
  "event_certainty": "rumor",
  "certainty_reason": "Unconfirmed news from financial media",
  "confidence_score": 75,
  "confidence_score_adjusted": 50,          // -25 penalty
  "validation_warnings": ["Rumor-level event but high confidence score"]
}
```

---

### 🟡 Rule 5: India Impact Consistency
**Triggers when:**
- `india_market_impact == "none"` AND
- `trade_decision IN ["buy", "watch"]`

**Action:**
- `confidence_adjusted -= 30`
- Add warning: `"No India impact but positive trade decision"`

**Penalty:** -30 points

**Rationale:** Can't recommend a bullish trade if the event doesn't impact India.

**Example:**
```json
{
  "india_market_impact": "none",
  "trade_decision": "watch",
  "confidence_score": 65,
  "confidence_score_adjusted": 35,          // -30 penalty
  "validation_warnings": ["No India impact but positive trade decision"]
}
```

---

### 🟡 Rule 6: Confidence-Reasoning Integrity
**Triggers when:**
- `confidence_score > 70` AND
- `len(recommendation_reasoning) < 20` characters

**Action:**
- `confidence_adjusted -= 20`
- Add warning: `"HIGH CONFIDENCE but vague reasoning"`

**Penalty:** -20 points

**Rationale:** Confidence >70 needs detailed justification.

**Example:**
```json
{
  "confidence_score": 85,
  "recommendation_reasoning": "Good opportunity",   // Only 19 chars ❌
  "confidence_score_adjusted": 65,                 // -20 penalty
  "validation_warnings": ["HIGH CONFIDENCE but vague reasoning"]
}
```

---

## Cumulative Penalties Example

### Input
```json
{
  "summary": "Oil prices crashing below $20. Energy stocks facing margin compression.",
  "trade_decision": "buy",
  "entry_plan": "",         // Missing ❌
  "exit_plan": "Exit when price recovers",
  "stop_loss_plan": "",     // Missing ❌
  "event_certainty": "rumor",
  "india_market_impact": "none",
  "recommendation_reasoning": "Good entry.",  // Vague, only 14 chars
  "confidence_score": 85
}
```

### Violations Detected
1. Rule 2: Negative "crashing" + "buy" → -40 points
2. Rule 3: Missing entry_plan + missing stop_loss → -35 points
3. Rule 4: Rumor certainty + confidence 85 → -25 points
4. Rule 5: No India impact + "buy" → -30 points
5. Rule 6: High confidence 85 + vague reasoning → -20 points

### Adjusted Output
```json
{
  "confidence_score": 85,
  "confidence_score_adjusted": -65 (capped at 0),  // Total penalties: -150 points
  "pipeline_status": "validation_error",
  "validation_warnings": [
    "CONTRADICTION: Negative sentiment + bullish trade decision",
    "BUY signal but missing entry/exit/SL plan",
    "Rumor-level event but high confidence score",
    "No India impact but positive trade decision",
    "HIGH CONFIDENCE but vague reasoning"
  ]
}
```

**Result:** ❌ You should NOT trade this. Multiple red flags.

---

## Summary Table

| Rule | Trigger | Penalty | Max Confidence | Use Case |
|------|---------|---------|---|----------|
| 1 | LLM failed | -∞ | 0 | System errors |
| 2 | Sentiment mismatch | -25 to -40 | 45 | Contradiction check |
| 3 | Trade rule missing | -35 | 45 | Risk management |
| 4 | Rumor + high conf | -25 | 40 | Certainty alignment |
| 5 | No impact + bullish | -30 | 40 | Relevance check |
| 6 | High conf + vague | -20 | 50 | Reasoning quality |

---

## Interpretation Guide

### Confidence Score Adjusted

- **80-100:** High quality, no violations. Ready to trade.
- **60-79:** Minor issues, but generally OK. Review warnings.
- **40-59:** Multiple issues. Research more before trading.
- **20-39:** Significant problems. Treat as "watch" not "buy".
- **0-19:** Not tradeable. Would be "avoid" or "no_call".

---

## Field Reference

For each row in the output, check these fields:

```python
# System health
pipeline_status          # "success" | "llm_error" | "validation_error"
validation_error_type   # Which rule triggered?
validation_warnings     # Array of specific issues
validation_contradictions_details  # Detailed contradiction info

# Confidence
confidence_score        # Original from LLM
confidence_score_adjusted  # After penalties (use THIS for decisions)

# Use this, not confidence_score!
if row['confidence_score_adjusted'] > 70:
    print("Consider trading")
else:
    print("Wait, gather more data")
```

---

## Action Items When Reviewing

### If `pipeline_status == "llm_error"`
→ Skip completely. Request retranslation of article or manual analysis.

### If `validation_warnings` is not empty
→ Read the warnings. Check `confidence_score_adjusted`.
→ If adjusted < 40, mark as `review_valid=false`.

### If `confidence_score_adjusted` differs from `confidence_score`
→ Penalties applied. Reflect on why LLM overestimated confidence.

### If `trade_decision == "buy"`
→ Verify entry_plan, exit_plan, stop_loss_plan are filled AND sensible.

---

**Version:** V2.0-validator  
**Last Updated:** March 28, 2026  
**Purpose:** Catch bad trades before they happen
