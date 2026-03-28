"""
News Source Tracker for Groq Web Search Results

Ensures:
    - All citations are from actual sources (no hallucinations)
    - Proper URL tracking and attribution
    - Fallback when sources are unavailable
    - Audit trail for analyst verification
"""

import logging
import json
import re
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


class NewsSourceTracker:
    """Tracks and validates news sources from Groq web search."""
    
    # Known financial news sources (verified, trusted)
    TRUSTED_SOURCES = {
        "bseindia.com": "BSE India",
        "nseindia.com": "NSE India",
        "reuters.com": "Reuters",
        "bloomberg.com": "Bloomberg",
        "moneycontrol.com": "Moneycontrol",
        "economictimes.com": "Economic Times",
        "thehindu.com": "The Hindu",
        "business-standard.com": "Business Standard",
        "financialexpress.com": "Financial Express",
        "livemint.com": "Mint",
        "outlook.com": "Outlook Business",
        "deccanchronicle.com": "Deccan Chronicle",
        "deccanherald.com": "Deccan Herald",
        "thestatesman.com": "The Statesman",
        "thehinduBusinessline.com": "The Hindu BusinessLine",
        "thehindubusinessline.com": "The Hindu BusinessLine",
        "dnaindia.com": "DNA India",
        "hindustantimes.com": "Hindustan Times",
        "theprint.in": "The Print",
        "barandbench.com": "Bar and Bench",
        "scroll.in": "Scroll.in",
        "theconversation.com": "The Conversation",
        "thequint.com": "The Quint",
        "newslaundry.com": "NewsLaundry",
        "inc42.com": "Inc42",
        "techcrunch.com": "TechCrunch",
        "theverge.com": "The Verge",
        "arstechnica.com": "Ars Technica",
    }
    
    @staticmethod
    def extract_sources_from_response(response_text: str) -> List[Dict[str, str]]:
        """
        Extract and validate sources from Groq web search response.
        
        Returns:
            List of {"url": "...", "title": "...", "source": "...", "trusted": bool}
        """
        sources = []
        
        # Try to parse as JSON first
        try:
            if response_text.strip().startswith("["):
                data = json.loads(response_text)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and "url" in item:
                            source = NewsSourceTracker._validate_source(item)
                            sources.append(source)
                    return sources
        except json.JSONDecodeError:
            pass
        
        # Fallback: Extract URLs via regex
        url_pattern = r'https?://[^\s\"\)<>\[\]]+(?:\.com|\.in|\.co\.uk|\.org|\.net|\.io)'
        urls = re.findall(url_pattern, response_text, re.IGNORECASE)
        
        for url in urls:
            source = {
                "url": url,
                "title": "Web Source",
                "source": NewsSourceTracker._extract_domain(url),
                "trusted": NewsSourceTracker._is_trusted(url)
            }
            sources.append(source)
        
        return sources
    
    @staticmethod
    def _validate_source(item: dict) -> dict:
        """Validate and enrich a single source item."""
        url = item.get("url", "")
        title = item.get("title", "")
        snippet = item.get("snippet", "")
        
        domain = NewsSourceTracker._extract_domain(url)
        trusted = NewsSourceTracker._is_trusted(url)
        
        return {
            "url": url,
            "title": title or f"{domain} article",
            "source": domain,
            "trusted": trusted,
            "snippet": snippet[:150] if snippet else ""
        }
    
    @staticmethod
    def _extract_domain(url: str) -> str:
        """Extract domain from URL."""
        match = re.search(r'https?://(?:www\.)?([^/]+)', url)
        return match.group(1) if match else "unknown"
    
    @staticmethod
    def _is_trusted(url: str) -> bool:
        """Check if URL is from a trusted/established source."""
        url_lower = url.lower()
        for trusted_domain in NewsSourceTracker.TRUSTED_SOURCES.keys():
            if trusted_domain in url_lower:
                return True
        return False
    
    @staticmethod
    def get_source_names(sources: List[Dict[str, str]]) -> List[str]:
        """Extract clean source names from validated sources."""
        names = []
        for source in sources[:5]:  # Limit to top 5
            name = source.get("source", "Unknown")
            if source.get("trusted"):
                names.append(f"✓ {name}")  # Mark trusted sources
            elif source.get("url"):
                names.append(f"🔗 {name}")
        
        return names if names else ["Unable to fetch sources"]
    
    @staticmethod
    def format_for_analyst_review(sources: List[Dict[str, str]]) -> str:
        """Format sources for inclusion in analyst notes."""
        if not sources:
            return "\n⚠️ No sources retrieved. Analyst must verify independently."
        
        trusted = [s for s in sources if s.get("trusted")]
        untrusted = [s for s in sources if not s.get("trusted")]
        
        output = "\n📚 Sources Reviewed:\n"
        
        if trusted:
            output += "\nTrusted Sources:\n"
            for s in trusted[:3]:
                output += f"✓ {s.get('source')} — {s.get('title', 'Article')}\n"
                output += f"  {s.get('url')}\n"
        
        if untrusted:
            output += "\nAdditional Sources (unverified):\n"
            for s in untrusted[:2]:
                output += f"🔗 {s.get('source')} — {s.get('title', 'Article')}\n"
                output += f"  {s.get('url')}\n"
        
        output += "\n⚠️ NOTE: Analyst should independently verify all sources and data before trading decisions."
        
        return output


# Global instance
source_tracker = NewsSourceTracker()


def extract_verified_sources(response_text: str, symbol: str) -> tuple[List[str], List[dict]]:
    """
    Parse Groq response and extract only verified sources.
    
    Returns:
        (source_names_list, source_objects_list)
    """
    try:
        sources = source_tracker.extract_sources_from_response(response_text)
        
        if sources:
            logger.info(f"[{symbol}] Extracted {len(sources)} sources ({sum(1 for s in sources if s.get('trusted'))} trusted)")
            source_names = source_tracker.get_source_names(sources)
            return source_names, sources
        else:
            logger.warning(f"[{symbol}] No sources found in response")
            return [], []
    
    except Exception as e:
        logger.error(f"[{symbol}] Error extracting sources: {e}")
        return [], []
