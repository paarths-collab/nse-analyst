# Production-Grade LLM Pipeline: Architecture & Validation Layer

## 🚀 What Changed

This document describes the **validator layer** and **reasoning structure improvements** that fix the critical logical flaws in your trading analysis pipeline.

---

## 📋 Problem Summary (From User Review)

Your previous pipeline had **3 critical issues**:

### 1️⃣ **LLM Failures Mixed with Trading Decisions**
```python
# BEFORE (DANGEROUS):
if llm_failed:
    trade_decision = "no_call"  # ❌ Looks like a market signal!
    
# AFTER (CORRECT):
if llm_failed:
    pipeline_status = "llm_error"  # ✅ Clearly marked as system failure
    trade_decision = "no_call"      # Different meaning!
```

### 2️⃣ **Logical Contradictions Not Caught**
```python
# BEFORE:
# "OMCs are bleeding" → LLM outputs "buy" ❌
# No validation layer catches this nonsense

# AFTER:
# Validator detects: NEGATIVE_SENTIMENT + "buy" 
# → Flags as contradiction, reduces confidence to 0
```

### 3️⃣ **Shallow Reasoning**
```python
# BEFORE:
"reasoning": "Reliance Jio IPO is positive"  # ❌ Too vague

# AFTER:
"event_summary_impact": "Jio IPO could unlock standalone valuation for Reliance",
"short_term_outlook": "Bullish: retail participation in IPO could drive indices higher",
"medium_term_outlook": "Watch for post-IPO lock-up expirations; potential volatility",
"long_term_outlook": "Positive for Reliance fundamentals if capex efficiency improves",
"risk_assessment": "Downside if IPO demand disappoints; watch subscription rates"
```

---

## ✅ Solution: Validation Layer Architecture

### 3-Layer Pipeline
```
INPUT
  ↓
LLM Model (Llama 3.1-8b)
  ↓
[ VALIDATOR LAYER ] ← NEW: Catches errors, contradictions, low-quality output
  ↓
[ DECISION ENGINE ] ← IMPROVED: Uses adjusted confidence + structured reasoning
  ↓
OUTPUT (with pipeline_status + validation_warnings)
```

---

## 🔍 Validator Features

### File: `scripts/llm_validator.py`

**What it does:**
1. **Detects LLM Failures**
   - Missing fields
   - `[LLM FAILURE: ...]` markers
   - Malformed JSON responses

2. **Catches Logical Contradictions**
   - Negative sentiment + "buy" decision
   - Positive sentiment + "avoid" decision
   - High confidence + vague reasoning

3. **Validates Trade Rules**
   - "buy" signal must have entry_plan + exit_plan + stop_loss_plan
   - "rumor"-level certainty → max confidence 40%
   - No India impact → no positive trade decision

4. **Adjusts Confidence Scores**
   - Reduces confidence for each violation (0-40 points per issue)
   - Final score reflects actual data quality

### Example Output
```json
{
  "item_id": "reliance_ipo",
  "pipeline_status": "success",           // ✅ Pipeline succeeded
  "validation_warnings": [
    "HIGH CONFIDENCE but vague reasoning",
    "BUY signal but missing entry/exit/SL plan"
  ],
  "confidence_score": 85,                  // Original from LLM
  "confidence_score_adjusted": 45,         // After validation penalties
  "validation_contradiction_details": {
    "high_confidence_vague_reasoning": true
  }
}
```

---

## 📐 Improved LLM Prompt Structure

### File: `scripts/llm_batch_summarize_news.py`

**New Fields (in addition to old ones):**

#### 1. **Structured Reasoning** (Replaces vague "recommendation_reasoning")
```json
{
  "event_summary_impact": "What actually happened (pure fact)",
  "short_term_outlook": "Will price go up/down in 0-5 days? Why?",
  "medium_term_outlook": "Likely trend over 1-3 weeks",
  "long_term_outlook": "Fundamental impact if any",
  "risk_assessment": "What could break this thesis?"
}
```

#### 2. **Enhanced Confidence Factors**
```json
{
  "confidence_score": 75,          // 0-100: Based on data quality + certainty
  "certainty_reason": "Event confirmed by multiple RTI disclosures",
  "data_quality_note": "Source is official; reliable"
}
```

#### 3. **Trade Plan Rules** (No fabricated prices)
```json
{
  "entry_plan": "Buy on break above 10-day high on 2x volume",
  "stop_loss_plan": "SL placed below the 20-day low",
  "exit_plan": "Exit 50% at 2% gain, trail remainder by 1% from peak"
}
```

---

## 🎯 Usage: Running the New Pipeline

### Command:
```bash
python scripts/llm_batch_summarize_news.py \
  --input scraped_events_multi_page.json \
  --output news_llm_review_v2.json \
  --batch-size 10
```

### Output Statistics:
```
📊 PIPELINE STATISTICS:
  Total items: 157
  Pipeline errors (LLM failed): 5        ← Items where LLM didn't respond
  India relevance filter: kept=32, filtered_out=125
  Validation flags (contradictions/low quality): 12  ← Items to review manually

⚠️  VALIDATION ERRORS:
  llm_missing: 5
  logical_contradiction: 7

⚠️  WARNING PATTERNS:
  HIGH CONFIDENCE but vague reasoning: 4 items
  CONTRADICTION: Negative sentiment + bullish trade decision: 3 items
  BUY signal but missing entry/exit/SL plan: 2 items

✅ Next steps:
  1. Review 5 items with pipeline errors
  2. Review 12 items with validation warnings
  3. For remaining 20 approved items, validate against live data
```

---

## 🔑 Key Output Fields (Per Row)

### Identification
```json
{
  "item_id": "unique_identifier",
  "source_name": "Economic Times",
  "article_url": "https://..."
}
```

### Pipeline Status (NEW)
```json
{
  "pipeline_status": "success|llm_error|validation_error",
  "validation_error_type": "llm_missing|llm_malformed|logical_contradiction|...",
  "validation_warnings": ["CONTRADICTION: Negative sentiment + bullish...", "..."],
  "validation_contradiction_details": {...}
}
```

### Market Analysis (IMPROVED)
```json
{
  "event_summary_impact": "Clear 1-line description",
  "short_term_outlook": "Directional bias + reasoning",
  "medium_term_outlook": "1-3 week outlook",
  "long_term_outlook": "Structural impact",
  "risk_assessment": "Key downside scenarios"
}
```

### Trade Decision (ENHANCED)
```json
{
  "trade_decision": "buy|watch|avoid|no_call",
  "entry_plan": "Rule-based (no prices unless in article)",
  "stop_loss_plan": "Rule-based",
  "exit_plan": "Rule-based",
  "recommendation_reasoning": "4-5 sentences with context"
}
```

### Confidence (ADJUSTED)
```json
{
  "confidence_score": 75,         // Original from LLM
  "confidence_score_adjusted": 45 // After validation penalties
}
```

### User Review
```json
{
  "review_status": "pending",
  "review_valid": null,           // ← You fill this (true/false)
  "review_notes": ""              // ← You add notes here
}
```

---

## 🔄 Validator Rules (Detailed)

### Rule 1: LLM Failure Detection
```python
ERROR if: summary contains "[LLM FAILURE" OR india_impact_reason == "[llm_failed]"
ACTION: Mark pipeline_status="llm_error", confidence=0, skip validation
```

### Rule 2: Contradiction Detection
```python
WARN if: negative_sentiment AND trade_decision IN ["buy", "watch"]
PENALTY: -40 confidence points
DETAIL: sentiment_trade_mismatch field

WARN if: positive_sentiment AND trade_decision == "avoid"
PENALTY: -25 confidence points
```

### Rule 3: Trade Rule Validation
```python
WARN if: trade_decision == "buy" AND (NOT entry_plan OR NOT exit_plan OR NOT sl_plan)
PENALTY: -35 confidence points
```

### Rule 4: Certainty Alignment
```python
WARN if: event_certainty == "rumor" AND confidence_score > 60
PENALTY: -25 confidence points
LOGIC: Rumors shouldn't justify high confidence
```

### Rule 5: India Impact Consistency
```python
WARN if: india_market_impact == "none" AND trade_decision IN ["buy", "watch"]
PENALTY: -30 confidence points
LOGIC: Can't be bullish if it has no India impact
```

### Rule 6: Confidence-Reasoning Integrity
```python
WARN if: confidence_score > 70 AND reasoning_length < 20 characters
PENALTY: -20 confidence points
LOGIC: High confidence requires detailed reasoning
```

---

## 🌐 Web Search Enrichment (Bonus: Not Yet Integrated)

### File: `scripts/web_search_enrichment.py`

**Purpose:** Find similar articles for better context

**Features:**
- Extracts entity keywords (company names, sectors)
- Searches for similar articles from different sources
- Provides corroboration of news across outlets
- Uses SerpAPI (requires paid key) or fallback to local search

**Example:**
```python
enriched = await enricher.enrich_item(
    {
        "headline": "Reliance Jio IPO announcement",
        "content_snippet": "...",
    },
    search_companies=["RELIANCE", "RIL"]
)
# Returns:
# {
#   "similar_articles": [
#     { "title": "Jio IPO 10x oversubscribed", "source": "Reuters", ... },
#     { "title": "Reliance plans record offering", "source": "FE", ... }
#   ],
#   "entity_keywords": ["Reliance", "IPO", "Jio"]
# }
```

**To Integrate:** Add SerpAPI key to `.env`:
```
SERPAPI_KEY=your_key_here
```

---

## 📊 Before vs After Comparison

| Aspect | Before | After |
|--------|--------|-------|
| **Failure Detection** | None; defaults to "no_call" | Explicit pipeline_status field |
| **Contradictions** | Silently passes nonsense | Validator catches & flags |
| **Confidence** | Single score | Score + adjusted_score (post-validation) |
| **Reasoning Depth** | Single sentence | 5-layer structured breakdown |
| **Trade Rules** | Optional | Validated (entry/exit/SL required for "buy") |
| **Output Fields** | ~20 | ~50+ (includes validation metadata) |
| **Data Quality** | Unknown | Transparent via warnings |

---

## 🚦 How to Use in Your Workflow

### Step 1: Run Pipeline with Validator
```bash
python scripts/llm_batch_summarize_news.py \
  --input scraped_events_multi_page.json \
  --output news_llm_review_v2.json
```

### Step 2: Filter by Pipeline Status
```sql
-- Items with LLM errors (skip these for now)
SELECT * FROM review WHERE pipeline_status = 'llm_error'

-- Items with validation warnings (review carefully)
SELECT * FROM review WHERE validation_warnings != '[]'

-- Clean items with India impact (focus here)
SELECT * FROM review 
WHERE pipeline_status = 'success' 
  AND india_filter_pass = true 
  AND validation_warnings = '[]'
```

### Step 3: Manual Review
For each row with `review_status='pending'`:
1. Read the structured reasoning (short/medium/long-term outlook)
2. Check the adjusted confidence score
3. Review validation warnings
4. Decide: `review_valid=true/false` based on:
   - Does the logic make sense?
   - Is the confidence score reasonable?
   - Are trade rules sound?
5. Add notes to `review_notes`

### Step 4: Next Stages
Once validated:
- **Symbol Validation:** Cross-check against NSE/BSE live data
- **Fundamentals Check:** Verify against latest quarterly reports
- **Backtesting:** Test entry/exit/SL rules against historical data

---

## 💡 Example: Corrected Contradiction

### Original (BROKEN):
```json
{
  "summary": "Oil companies are bleeding with crude prices at $30",
  "trade_decision": "buy",
  "recommendation_reasoning": "Good time to buy for recovery",
  "confidence_score": 80
}
```

### After Validator (FIXED):
```json
{
  "summary": "Oil companies are bleeding with crude prices at $30",
  "trade_decision": "buy",
  "recommendation_reasoning": "Good time to buy for recovery",
  "confidence_score": 80,
  "confidence_score_adjusted": 5,        // ← Penalized heavily
  "validation_warnings": [
    "CONTRADICTION: Negative sentiment + bullish trade decision",
    "BUY signal but missing entry/exit/SL plan"
  ],
  "validation_contradiction_details": {
    "sentiment_trade_mismatch": {
      "sentiment": "negative",
      "decision": "buy"
    }
  },
  "pipeline_status": "validation_error"
}
```

→ **You now see the problem clearly!** The validator prevents this from silently passing.

---

## ⚙️ Configuration

All scripts support these options:

```bash
python scripts/llm_batch_summarize_news.py \
  --input scraped_events_multi_page.json    # Input file
  --output news_llm_review_v2.json          # Output file
  --model llama-3.1-8b-instant              # LLM model
  --batch-size 10                           # Items per API call
  --sleep-sec 0.8                           # Delay between batches
  --max-items 0                             # 0 = all; e.g. 10 for testing
```

---

## 🔗 Files Reference

1. **`scripts/llm_batch_summarize_news.py`** (UPDATED)
   - Main pipeline with improved prompt + validator integration
   - Returns tuple (rows, validation_stats)

2. **`scripts/llm_validator.py`** (NEW)
   - Validation logic
   - Contradiction detection
   - Confidence adjustment

3. **`scripts/web_search_enrichment.py`** (NEW)
   - Web search for similar articles
   - Entity extraction
   - Ready to integrate (requires API key)

---

## 📈 Next Phase

Once you approve this architecture:

1. **Integration with Live Data**
   - NSE/BSE symbol validation
   - Live price + volume checks
   - Real-time fundamentals from filing APIs

2. **Backtesting Framework**
   - Test entry/exit/SL rules against historical 1H/1D candles
   - Measure win% and risk/reward

3. **Portfolio-Level Decisions**
   - Combine single-trade signals into portfolio rules
   - Position sizing based on confidence
   - Correlation/diversification checks

4. **Multi-Agent System** (Advanced)
   - Separate agents for: news ingestion, technical analysis, fundamental analysis
   - Consensus scoring
   - Conflict resolution

---

## 🎯 Key Takeaway

> **Before:** "AI pipeline that sometimes works and sometimes silently lies"  
> **After:** "AI pipeline that either gives high-quality output OR clearly says it failed"

Your trading decisions are now built on **transparent, validated intelligence** instead of wishful thinking.

---

*Last Updated: March 28, 2026*  
*Pipeline Version: v2.0-validator*
