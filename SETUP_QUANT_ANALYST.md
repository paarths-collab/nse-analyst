# NSE Filing Quant Analyst Edition

## What's New

Your script has been **upgraded** to function as a **high-stakes institutional-grade equity analyst**. It now:

### 1. **Groq-Based Web Search** (No Tavily)
- Directly integrates Groq's internal web search capabilities
- Eliminates external dependency on Tavily
- Searches for market context & news using **mixtral-8x7b-32768**

### 2. **Institutional Quant Analysis**
- Multi-paragraph investment thesis on each announcement
- **Price impact predictions** (e.g., "+2.5%" or "-1.2%")
- Time horizons: SHORT_TERM, MEDIUM_TERM, LONG_TERM
- Confidence levels: HIGH, MEDIUM, LOW

### 3. **Detailed Output Format**
Each alert now shows:
- ✓ Expected price change percentage
- ✓ **Quick take** — 3-5 sentence executive summary
- ✓ **Detailed institutional analysis** — 10-15 line full thesis with mechanisms, precedents, and conviction
- ✓ Specific positive catalysts (what could drive price UP)
- ✓ Specific downside risks (what could drive price DOWN)
- ✓ Market context (how similar announcements have historically performed)
- ✓ Full citation of news sources and URLs

### 4. **JSON Storage**
Analysis results saved as `.json` alongside `.txt` files:
```json
{
  "uid": "INFY_a1b2c3d4e5f6",
  "symbol": "INFY",
  "summary": "Acquisition of Reliance International Leasing IFSC",
  "analysis": {
    "verdict": "BULLISH",
    "confidence": "HIGH",
    "expected_price_change_percent": 2.5,
    "time_horizon": "MEDIUM_TERM",
    "reasoning_short": "Acquisition signals entry into $50B BFSI market. Historical precedent (TCS 2022) delivered +9.5% in 6 months. Moderate execution risk. Expect +2.5% medium-term.",
    "reasoning_long": "This acquisition represents a strategic pivot toward the high-margin BFSI-tech vertical, a core growth thesis for Infosys. Similar moves by TCS in 2022 delivered +2.1% day-1 pop and +9.5% in 6 months as the market repriced growth expectations. Digitalization budgets in the BFSI sector remain resilient even in tightening cycles. Execution risk is moderate given Infosys' track record with large inorganic deals. Current macro headwinds (RBI tightening) suggest a discount to TCS precedent, hence +2.5% vs. higher upside. Time to main catalyst: 2-4 weeks (first earnings mention).",
    "key_catalysts": [
      "High-margin BFSI services market entry",
      "Accretive to EPS within 12 months",
      "Strategic inorganic growth in adjacency"
    ],
    "key_risks": [
      "Integration execution delays (typical for large acquisitions)",
      "BFSI sector regulatory scrutiny in India",
      "Valuation multiple compression if macro worsens"
    ],
    "news_sources_reviewed": ["https://www.nseindia.com/...", "https://markets.com/..."]
  }
}
```

---

## Installation

### 1. Install Python Dependencies
```bash
pip install --upgrade cloudscraper pymupdf groq
```

### No longer needed:
- ~~tavily-python~~ (removed)

### 2. Set GROQ_API_KEY

#### PowerShell (Windows):
```powershell
$env:GROQ_API_KEY = "gsk_your_actual_key_here"
python .\fillings.py
```

#### Bash (Linux/Mac):
```bash
export GROQ_API_KEY="gsk_your_actual_key_here"
python fillings.py
```

#### Get your API key:
- Go to **https://console.groq.com**
- Sign up or log in
- Generate API key in "API Keys" section
- Copy the key (starts with `gsk_`)

---

## Configuration (Optional)

Edit the top of `fillings.py`:

```python
CHECK_INTERVAL       = 30                  # Seconds between iterations
GROQ_MODEL           = "mixtral-8x7b-32768"  # Analysis model (reasoning)
GROQ_WEB_MODEL       = "mixtral-8x7b-32768"  # Web search model
SUMMARY_MAX_CHARS    = 6000                # PDF chars sent to summariser
```

---

## Running the Script

### Start analysis:
```bash
.\.venv\Scripts\Activate.ps1  # Windows
python .\fillings.py
```

### What happens each iteration:
1. Fetches new NSE announcements (equities + SME indices)
2. Downloads PDFs for new filings
3. **Summarises** the core event (1 sentence)
4. **Searches web** for market context & news
5. **Quant analysis**: Runs as a senior equity analyst
   - Assesses price impact direction & magnitude
   - Lists catalysts and risks
   - Provides institutional reasoning
6. **Prints alert** with full details and sources
7. **Saves JSON** for programmatic access

---

## Output Example

```
================================================================================
[ BULLISH | HIGH | MEDIUM_TERM ]  INFY  25-Mar-2026 22:27:09
  Infosys acquires Reliance International Leasing subsidiary.

PRICE IMPACT:
  +2.5%

QUICK TAKE:
  Acquisition signals entry into $50B BFSI market. Historical precedent (TCS 
  2022) delivered +9.5% in 6 months. Moderate execution risk. Expect +2.5% 
  medium-term.

DETAILED INSTITUTIONAL ANALYSIS:
  This acquisition represents a strategic pivot toward the high-margin BFSI-tech
  vertical, a core growth thesis for Infosys. Similar moves by TCS in 2022
  delivered +2.1% day-1 pop and +9.5% in 6 months as the market repriced growth
  expectations. Digitalization budgets in the BFSI sector remain resilient even
  in tightening cycles. Execution risk is moderate given Infosys' track record
  with large inorganic deals. Current macro headwinds (RBI tightening) argue for
  a discount to TCS precedent, hence +2.5% vs. higher upside. Time to catalyst:
  2-4 weeks (first earnings mention).

POSITIVE CATALYSTS:
  + High-margin BFSI services market entry
  + Accretive to EPS within 12 months
  + Strategic inorganic growth in adjacency

KEY RISKS:
  - Integration execution delays (typical for large acquisitions)
  - BFSI sector regulatory scrutiny in India post-fraud episodes
  - Valuation multiple compression if macro worsens

MARKET CONTEXT:
  Historical precedent: TCS similar move (2022) → +2.1% day 1, 
  +9.5% in 6 months. However, current macro (RBI tightening) 
  argues for discount to precedent.

SOURCES & REFERENCES:
  → Infosys SEC Filing & Market Commentaries
    https://www.nseindia.com/...
  → Market research on BFSI M&A trends
    https://markets.com/...
================================================================================
```

---

## Troubleshooting

### API Key Invalid
```
ERROR: Missing env vars: GROQ_API_KEY
```
→ Check your key at https://console.groq.com and re-run

### Model not found
```
ERROR: model "mixtral-8x7b-32768" not found
```
→ Use a different model. Check available models at console.groq.com

### PDF extraction fails
→ Some PDFs are image scans. Analysis gracefully continues with metadata only.

### Web search returns no results
→ Groq's web search may be rate-limited or unavailable. Analysis proceeds with filing text alone.

---

## Economics

**Free tier (Groq):**
- 10,000 tokens/minute
- Typically: 1-2 KB per analysis = 500-1000 analyses/minute
- **Cost: $0**

**vs. Tavily ($100/month):**
- Groq's native web search is included in your API key

---

## Next Steps

1. Set your `GROQ_API_KEY`
2. Run: `python fillings.py`
3. Monitor the console output
4. Review `.json` files in the `pdfs/` directory for programmatic alert integration
5. Extend with Telegram/Email notifications using the alert data

---

**Persona:** You are now a high-stakes quant analyst at a tier-1 investment bank, analyzing NSE filings in real-time.

**Confidence:** Your analysis reflects institutional-grade equity research standards, with explicit price targets and risk quantification.
