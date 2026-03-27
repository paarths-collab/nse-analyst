"""
Content Filter for NSE Filings

Filters out low-impact announcements to:
    - Reduce token usage
    - Avoid rate limiting
    - Focus on material events only
    - Improve signal-to-noise ratio
"""

import logging
import re

logger = logging.getLogger(__name__)


# Keywords indicating routine/low-value filings
ROUTINE_KEYWORDS = [
    "compliance statement digitally signed",
    "annual general meeting",
    "board meeting",
    "AGM",
    "audit",
    "statutory",
    "routine",
    "administrative",
    "compliance",
    "regulatory filing",
    "submission of document",
    "filing of form",
    "update of status",
    "change of address",
    "change of director",
    "ordinary resolution",
    "email id",
    "telephone",
]

# Keywords indicating routine dividend/distribution
ROUTINE_DIVIDEND_KEYWORDS = [
    "dividend",
    "interim dividend",
    "final dividend",
    "distribution",
]

# Keywords indicating routine corporate actions
ROUTINE_CORPORATE_KEYWORDS = [
    "stock split",
    "bonus shares",
    "trading window",
    "trading halt",
    "timetable for",
]

# Keywords indicating market-moving events - ALWAYS PROCESS
MATERIAL_KEYWORDS = [
    "acquisition",
    "merger",
    "ipo",
    "rights issue",
    "open offer",
    "delisting",
    "regulatory action",
    "penalty",
    "fraud",
    "investigation",
    "seizure",
    "suspension",
    "default",
    "bankruptcy",
    "scheme",
    "restructuring",
    "going concern",
    "material loss",
    "impairment",
    "provision",
    "related party",
    "insider trading",
    "conflict of interest",
]

# Keywords indicating value threshold checks
VALUE_KEYWORDS = {
    "crore": 10_000_000,
    "lakh": 100_000,
    "rupees": 1,
    "rs": 1,
}


def extract_value(text: str) -> float:
    """
    Try to extract numerical value from text.
    Returns the largest value found (in rupees equivalent).
    """
    if not text:
        return 0.0
    
    text_lower = text.lower()
    
    # Look for "₹ X crore", "Rs. X crore", etc.
    patterns = [
        r'[\₹rs\.]+\s*([0-9,\.]+)\s*crore',
        r'([0-9,\.]+)\s*crore',
        r'[\₹rs\.]+\s*([0-9,\.]+)\s*lakh',
        r'([0-9,\.]+)\s*lakh',
    ]
    
    max_value = 0.0
    for pattern in patterns:
        matches = re.findall(pattern, text_lower)
        for match in matches:
            try:
                num = float(match.replace(',', ''))
                if "crore" in pattern:
                    value = num * 10_000_000
                else:
                    value = num * 100_000
                max_value = max(max_value, value)
            except ValueError:
                pass
    
    return max_value


def should_process(symbol: str, subject: str, description: str) -> dict:
    """
    Decide if a filing should be processed by the LLM pipeline.
    
    Returns:
        {
            "should_process": bool,
            "reason": str,
            "confidence": "HIGH" | "MEDIUM" | "LOW",
            "estimated_impact": "HIGH" | "MEDIUM" | "LOW" | "ROUTINE"
        }
    """
    
    combined_text = f"{symbol} {subject} {description}".lower()
    
    # 1. CHECK: Is this definitely material?
    for keyword in MATERIAL_KEYWORDS:
        if keyword in combined_text:
            logger.info(f"[FILTER] {symbol}: MATERIAL keyword '{keyword}' → PROCESS (HIGH impact)")
            return {
                "should_process": True,
                "reason": f"Material event: contains '{keyword}'",
                "confidence": "HIGH",
                "estimated_impact": "HIGH"
            }
    
    # 2. CHECK: Is this definitely routine?
    routine_matches = [kw for kw in ROUTINE_KEYWORDS if kw in combined_text]
    if routine_matches:
        logger.info(f"[FILTER] {symbol}: Routine keywords {routine_matches} → SKIP")
        return {
            "should_process": False,
            "reason": f"Routine filing: contains {routine_matches[0]}",
            "confidence": "HIGH",
            "estimated_impact": "ROUTINE"
        }
    
    # 3. CHECK: Is this a routine dividend but large?
    dividend_match = any(kw in combined_text for kw in ROUTINE_DIVIDEND_KEYWORDS)
    if dividend_match:
        estimated_value = extract_value(combined_text)
        
        # If dividend is large (>1 crore), might be material
        if estimated_value > 100_000_000:  # >1 crore
            logger.info(f"[FILTER] {symbol}: Large dividend (₹{estimated_value/10_000_000:.1f} crore) → PROCESS")
            return {
                "should_process": True,
                "reason": f"Large dividend: ₹{estimated_value/10_000_000:.1f} crore",
                "confidence": "MEDIUM",
                "estimated_impact": "MEDIUM"
            }
        else:
            logger.info(f"[FILTER] {symbol}: Routine dividend → SKIP")
            return {
                "should_process": False,
                "reason": "Routine dividend payment",
                "confidence": "HIGH",
                "estimated_impact": "ROUTINE"
            }
    
    # 4. CHECK: Is this a routine corporate action?
    corporate_match = any(kw in combined_text for kw in ROUTINE_CORPORATE_KEYWORDS)
    if corporate_match:
        # Stock splits, bonus shares may be material depending on context
        if "split" in combined_text or "bonus" in combined_text:
            logger.info(f"[FILTER] {symbol}: Stock action (split/bonus) → PROCESS (MEDIUM impact)")
            return {
                "should_process": True,
                "reason": "Stock action (split/bonus) may signal confidence",
                "confidence": "MEDIUM",
                "estimated_impact": "MEDIUM"
            }
        else:
            logger.info(f"[FILTER] {symbol}: Trading window/halt → SKIP")
            return {
                "should_process": False,
                "reason": "Routine corporate action",
                "confidence": "HIGH",
                "estimated_impact": "ROUTINE"
            }
    
    # 5. DEFAULT: Uncertain — process with caution
    logger.info(f"[FILTER] {symbol}: No clear classification → PROCESS (LOW confidence)")
    return {
        "should_process": True,
        "reason": "Unclear classification, processing with caution",
        "confidence": "LOW",
        "estimated_impact": "MEDIUM"
    }


def score_filing(symbol: str, subject: str, description: str) -> float:
    """
    Score filing for priority (0.0 = lowest, 1.0 = highest).
    Used to rank queue order for batch processing.
    """
    combined_text = f"{symbol} {subject} {description}".lower()
    score = 0.5  # Default middle score
    
    # Material events score higher
    material_count = sum(1 for kw in MATERIAL_KEYWORDS if kw in combined_text)
    score += min(material_count * 0.1, 0.3)
    
    # Routine events score lower
    routine_count = sum(1 for kw in ROUTINE_KEYWORDS if kw in combined_text)
    score -= min(routine_count * 0.05, 0.2)
    
    # Value-based scoring
    value = extract_value(combined_text)
    if value > 500_000_000:  # >5 crore
        score += 0.2
    elif value > 100_000_000:  # >1 crore
        score += 0.1
    
    return max(0.0, min(1.0, score))
