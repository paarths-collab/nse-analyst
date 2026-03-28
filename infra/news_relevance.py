#!/usr/bin/env python3
"""
Enhanced validation: Detect direct & indirect stock-related news.
Indirect relevance = policy changes, macro events, sector trends, regulations, etc.
"""

import re
from typing import List, Dict, Optional, Tuple
from enum import Enum
from dataclasses import dataclass

class NewsRelevance(Enum):
    """News relevance classification."""
    DIRECT_STOCK = 1  # Mentions specific company/ticker
    SECTOR_RELEVANT = 2  # Affects entire sector
    MACRO_ECONOMIC = 3  # GDP, inflation, interest rates, employment
    REGULATORY = 4  # Policy, regulations, compliance
    GEOPOLITICAL = 5  # Global events affecting markets
    COMMODITY = 6  # Oil, gold, metals, agricultural
    CURRENCY = 7  # Forex, currency movements
    CRYPTO = 8  # Cryptocurrency markets
    NOT_RELEVANT = 0  # Not stock-related

@dataclass
class ValidationResult:
    """Comprehensive validation result."""
    is_valid: bool  # Passes basic quality checks
    is_market_relevant: bool  # Related to stocks/trading
    relevance_type: NewsRelevance
    confidence: float  # 0.0-1.0
    tags: List[str]  # ['urgent', 'sector_tech', 'macro_inflation', etc.]
    reasoning: str  # Why this classification
    risk_level: str  # 'high', 'medium', 'low', 'unknown'

class IndirectRelevanceDetector:
    """Detect indirect market relevance in news."""
    
    # MACRO-ECONOMIC KEYWORDS
    MACRO_KEYWORDS = {
        'gdp': 'macro_economic',
        'inflation': 'macro_economic',
        'deflation': 'macro_economic',
        'interest rate': 'macro_economic',
        'fed': 'macro_economic',
        'central bank': 'macro_economic',
        'employment': 'macro_economic',
        'unemployment': 'macro_economic',
        'earnings': 'macro_economic',
        'recession': 'macro_economic',
        'growth': 'macro_economic',
        'stimulus': 'macro_economic',
        'quantitative easing': 'macro_economic',
        'yield': 'macro_economic',
        'bond': 'macro_economic',
        'treasury': 'macro_economic',
        'federal reserve': 'macro_economic',
    }
    
    # REGULATORY KEYWORDS
    REGULATORY_KEYWORDS = {
        'sec': 'regulatory',
        'securities': 'regulatory',
        'regulation': 'regulatory',
        'compliance': 'regulatory',
        'audit': 'regulatory',
        'investigation': 'regulatory',
        'subpoena': 'regulatory',
        'antitrust': 'regulatory',
        'merger': 'regulatory',
        'acquisition': 'regulatory',
        'ipo': 'regulatory',
        'offering': 'regulatory',
        'fda': 'regulatory',
        'approval': 'regulatory',
        'license': 'regulatory',
        'filing': 'regulatory',
        'disclosure': 'regulatory',
    }
    
    # GEOPOLITICAL KEYWORDS
    GEOPOLITICAL_KEYWORDS = {
        'war': 'geopolitical',
        'conflict': 'geopolitical',
        'sanctions': 'geopolitical',
        'trade': 'geopolitical',
        'tariff': 'geopolitical',
        'embargo': 'geopolitical',
        'election': 'geopolitical',
        'government': 'geopolitical',
        'political': 'geopolitical',
        'president': 'geopolitical',
        'minister': 'geopolitical',
        'congress': 'geopolitical',
        'parliament': 'geopolitical',
        'treaty': 'geopolitical',
        'diplomat': 'geopolitical',
    }
    
    # SECTOR KEYWORDS
    SECTOR_KEYWORDS = {
        'tech|technology|software|hardware|semiconductor|chip': 'sector_tech',
        'bank|banking|finance|fintech|payment': 'sector_finance',
        'manufacturing|industrial|factory': 'sector_industrial',
        'retail|consumer|ecommerce': 'sector_retail',
        'healthcare|pharma|drug|medical|hospital': 'sector_healthcare',
        'energy|oil|gas|coal|nuclear': 'sector_energy',
        'auto|automotive|electric vehicle|ev|tesla': 'sector_auto',
        'real estate|property|housing|construction': 'sector_realestate',
        'agriculture|farm|crop': 'sector_agriculture',
        'telecom|communication|wireless': 'sector_telecom',
        'airline|aviation|travel': 'sector_travel',
        'media|entertainment|streaming': 'sector_media',
    }
    
    # COMMODITY/CURRENCY KEYWORDS
    COMMODITY_KEYWORDS = {
        'oil': 'commodity_oil',
        'gold': 'commodity_gold',
        'silver': 'commodity_metal',
        'copper': 'commodity_metal',
        'lithium': 'commodity_battery',
        'rare earth': 'commodity_reareearth',
        'wheat': 'commodity_agriculture',
        'corn': 'commodity_agriculture',
        'bitcoin': 'crypto',
        'ethereum': 'crypto',
        'cryptocurrency': 'crypto',
        'blockchain': 'crypto',
        'rupee': 'currency',
        'dollar': 'currency',
        'euro': 'currency',
        'forex': 'currency',
        'exchange rate': 'currency',
    }
    
    # POSITIVE CATALYST KEYWORDS
    POSITIVE_KEYWORDS = {
        'surge': 1.2,
        'surge': 1.2,
        'jump': 1.15,
        'rally': 1.15,
        'gain': 1.1,
        'up': 1.05,
        'rise': 1.1,
        'growth': 1.1,
        'record': 1.15,
        'profit': 1.1,
        'success': 1.1,
        'breakthrough': 1.2,
        'approval': 1.2,
        'launch': 1.15,
        'beat': 1.2,
    }
    
    # NEGATIVE CATALYST KEYWORDS
    NEGATIVE_KEYWORDS = {
        'crash': -1.3,
        'plunge': -1.2,
        'tumble': -1.2,
        'fall': -1.1,
        'down': -1.05,
        'decline': -1.1,
        'loss': -1.1,
        'miss': -1.1,
        'delays': -1.15,
        'recall': -1.2,
        'bankruptcy': -1.3,
        'liquidation': -1.3,
        'fraud': -1.3,
        'scandal': -1.25,
        'warning': -1.15,
        'concern': -1.1,
    }
    
    @classmethod
    def detect_relevance(cls, headline: str, content: str = "") -> ValidationResult:
        """Detect if news is market-relevant (direct or indirect)."""
        
        text = (headline + " " + content).lower()
        tags = []
        confidence = 0.0
        relevance_type = NewsRelevance.NOT_RELEVANT
        risk_level = 'unknown'
        reasoning = ""
        
        # Check for company/ticker mentions (DIRECT)
        if cls._has_direct_stock_mention(text):
            return ValidationResult(
                is_valid=True,
                is_market_relevant=True,
                relevance_type=NewsRelevance.DIRECT_STOCK,
                confidence=0.95,
                tags=['direct_stock_mention'],
                reasoning="Contains direct company/ticker reference",
                risk_level='medium'
            )
        
        # Check MACRO-ECONOMIC
        macro_matches = cls._match_keywords(text, cls.MACRO_KEYWORDS)
        if macro_matches:
            tags.extend(macro_matches)
            confidence = max(confidence, 0.8)
            relevance_type = NewsRelevance.MACRO_ECONOMIC
            reasoning = f"Macro-economic event: {', '.join(macro_matches)}"
            risk_level = cls._assess_risk('macro', headline)
        
        # Check REGULATORY
        regulatory_matches = cls._match_keywords(text, cls.REGULATORY_KEYWORDS)
        if regulatory_matches:
            tags.extend(regulatory_matches)
            confidence = max(confidence, 0.85)
            relevance_type = NewsRelevance.REGULATORY
            reasoning = f"Regulatory event: {', '.join(regulatory_matches)}"
            risk_level = cls._assess_risk('regulatory', headline)
        
        # Check GEOPOLITICAL
        geopolitical_matches = cls._match_keywords(text, cls.GEOPOLITICAL_KEYWORDS)
        if geopolitical_matches:
            tags.extend(geopolitical_matches)
            confidence = max(confidence, 0.75)
            relevance_type = NewsRelevance.GEOPOLITICAL
            reasoning = f"Geopolitical event: {', '.join(geopolitical_matches)}"
            risk_level = cls._assess_risk('geopolitical', headline)
        
        # Check SECTOR-SPECIFIC
        sector_matches = cls._match_regex_keywords(text, cls.SECTOR_KEYWORDS)
        if sector_matches:
            tags.extend(sector_matches)
            confidence = max(confidence, 0.85)
            relevance_type = NewsRelevance.SECTOR_RELEVANT
            reasoning = f"Sector impact: {', '.join(sector_matches)}"
            risk_level = cls._assess_risk('sector', headline)
        
        # Check COMMODITIES/CURRENCY
        commodity_matches = cls._match_keywords(text, cls.COMMODITY_KEYWORDS)
        if commodity_matches:
            tags.extend(commodity_matches)
            confidence = max(confidence, 0.8)
            if 'crypto' in commodity_matches:
                relevance_type = NewsRelevance.CRYPTO
            elif 'currency' in commodity_matches:
                relevance_type = NewsRelevance.CURRENCY
            else:
                relevance_type = NewsRelevance.COMMODITY
            reasoning = f"Commodity/currency impact: {', '.join(commodity_matches)}"
            risk_level = cls._assess_risk('commodity', headline)
        
        # Adjust confidence based on sentiment intensity
        sentiment_boost = cls._detect_sentiment_intensity(headline)
        if sentiment_boost != 0:
            confidence = min(1.0, confidence + abs(sentiment_boost) * 0.15)
            if sentiment_boost > 0:
                tags.append('positive_catalyst')
                risk_level = 'high'
            else:
                tags.append('negative_catalyst')
                risk_level = 'high'
        
        is_market_relevant = confidence >= 0.7
        is_valid = len(tags) > 0 or confidence >= 0.7
        
        return ValidationResult(
            is_valid=is_valid,
            is_market_relevant=is_market_relevant,
            relevance_type=relevance_type,
            confidence=min(1.0, confidence),
            tags=list(set(tags)),  # De-duplicate
            reasoning=reasoning or "No market relevance detected",
            risk_level=risk_level
        )
    
    @classmethod
    def _has_direct_stock_mention(cls, text: str) -> bool:
        """Check if text mentions specific companies or tickers."""
        # Common Indian company patterns
        direct_patterns = [
            r'\bindia\s+ltd\b',
            r'\binc\.\b',
            r'\bcorp\.\b',
            r'\bltd\.\b',
            r'\bplc\b',
            r'\bltd\b',
            r'\binc\b',
            r'\bcorp\b',
            # Ticker-like patterns (MSFT, AAPL, RELIANCE, INFY, etc.)
            r'\b[A-Z]{1,5}\b',  # Tickers are usually 1-5 capital letters
        ]
        
        # Known companies
        known_companies = [
            'reliance', 'tcs', 'infosys', 'wipro', 'bajaj', 'maruti', 'hdfc', 
            'icici', 'axis', 'kotak', 'sbi', 'apple', 'microsoft', 'google',
            'amazon', 'meta', 'tesla', 'nvidia', 'intel', 'samsung', 'sony'
        ]
        
        for company in known_companies:
            if company in text:
                return True
        
        return False
    
    @classmethod
    def _match_keywords(cls, text: str, keywords: Dict[str, str]) -> List[str]:
        """Match simple keywords."""
        matches = []
        for keyword, category in keywords.items():
            if keyword in text:
                matches.append(category)
        return matches
    
    @classmethod
    def _match_regex_keywords(cls, text: str, keywords: Dict[str, str]) -> List[str]:
        """Match regex patterns in keywords."""
        matches = []
        for pattern, category in keywords.items():
            if re.search(pattern, text, re.IGNORECASE):
                matches.append(category)
        return matches
    
    @classmethod
    def _detect_sentiment_intensity(cls, headline: str) -> float:
        """Detect sentiment intensity (positive or negative)."""
        text = headline.lower()
        
        positive_score = sum(
            multiplier for keyword, multiplier in cls.POSITIVE_KEYWORDS.items()
            if keyword in text
        )
        
        negative_score = sum(
            multiplier for keyword, multiplier in cls.NEGATIVE_KEYWORDS.items()
            if keyword in text
        )
        
        return positive_score + negative_score
    
    @classmethod
    def _assess_risk(cls, event_type: str, headline: str) -> str:
        """Assess market risk level."""
        text = headline.lower()
        
        # High risk indicators
        high_risk_words = ['crash', 'collapse', 'bankruptcy', 'scandal', 'fraud', 'war', 'emergency']
        if any(word in text for word in high_risk_words):
            return 'high'
        
        # Medium risk
        medium_risk_words = ['warning', 'concern', 'decline', 'delay', 'miss', 'investigation']
        if any(word in text for word in medium_risk_words):
            return 'medium'
        
        # Default based on event type
        if event_type in ['macro', 'regulatory']:
            return 'medium'
        elif event_type == 'geopolitical':
            return 'high'
        else:
            return 'low'

if __name__ == '__main__':
    # Test the detector
    test_cases = [
        ("Apple launches new iPhone 15 Pro Max", "Direct company mention"),
        ("RBI raises interest rates to 7.5% amid inflation concerns", "Macro-economic"),
        ("US imposes new tariffs on Chinese semiconductors", "Geopolitical/sector"),
        ("Tech sector rallies on AI breakthrough from OpenAI", "Sector indirect"),
        ("Oil prices surge 5% amid Middle East tensions", "Commodity/geo"),
        ("FDA approves new drug treatment for cancer", "Regulatory/sector"),
        ("Bitcoin crashes 15% after crypto exchange scandal", "Crypto negative"),
        ("India election results: Market-friendly party wins", "Macro/geo"),
        ("Weather report: Sunny tomorrow in NYC", "Not relevant"),
    ]
    
    detector = IndirectRelevanceDetector()
    print("\n" + "="*80)
    print("NEWS RELEVANCE DETECTION TEST")
    print("="*80)
    
    for headline, description in test_cases:
        result = detector.detect_relevance(headline)
        print(f"\n📰 {headline}")
        print(f"   Category: {description}")
        print(f"   ├─ Market Relevant: {result.is_market_relevant} ({result.confidence:.0%})")
        print(f"   ├─ Type: {result.relevance_type.name}")
        print(f"   ├─ Risk: {result.risk_level.upper()}")
        print(f"   ├─ Tags: {', '.join(result.tags)}")
        print(f"   └─ Reason: {result.reasoning}")
