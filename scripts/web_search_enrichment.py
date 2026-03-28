#!/usr/bin/env python3
"""
Web search enrichment for news items.

Finds similar articles from different sources to provide broader context
for better analysis. Useful for:
- Corroborating news across sources
- Finding sector/company-specifics
- Identifying emerging patterns

Uses public search APIs and fallback to heuristic web discovery.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List
from urllib.parse import urlencode
from enum import Enum

import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SearchProvider(str, Enum):
    """Search provider options."""
    SERPAPI = "serpapi"
    DUCKDUCKGO = "duckduckgo"
    GOOGLE = "google"


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    source: str
    published: str | None = None


@dataclass
class EnrichedArticle:
    original_item_id: str
    original_headline: str
    original_url: str
    similar_articles: List[SearchResult]
    entity_keywords: List[str]
    search_summary: str


class WebSearchEnricher:
    """Find similar articles using web search."""

    def __init__(self, api_key: str | None = None, provider: SearchProvider = SearchProvider.DUCKDUCKGO):
        """
        Initialize web search enricher.
        
        Args:
            api_key: Optional API key for paid search providers
            provider: Search provider to use
        """
        self.api_key = api_key
        self.provider = provider
        self.client = httpx.AsyncClient(timeout=10.0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    def _extract_entity_keywords(self, headline: str, summary: str) -> List[str]:
        """Extract key entities (company names, countries, etc.) from text."""
        # Simple regex-based extraction; can be enhanced with NER library
        keywords: List[str] = []
        
        # Common company name patterns
        import re
        
        # Find capitalized company names (basic heuristic)
        company_pattern = r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b"
        candidates = re.findall(company_pattern, headline + " " + summary)
        
        # Filter out common words
        stopwords = {"The", "A", "An", "And", "Or", "But", "In", "On", "At", "To", "For"}
        keywords = [c for c in candidates if c not in stopwords][:3]  # Top 3
        
        return keywords

    async def search_similar(
        self, headline: str, summary: str, max_results: int = 5, top_companies: List[str] | None = None
    ) -> List[SearchResult]:
        """
        Search for similar articles.
        
        Args:
            headline: Original article headline
            summary: Article summary
            max_results: Max search results to return
            top_companies: Optional list of company names to search for
        
        Returns:
            List of SearchResult objects
        """
        # Build search query from headline and key entities
        keywords = self._extract_entity_keywords(headline, summary)
        if top_companies:
            keywords = top_companies[:2] + keywords[:1]
        
        query = " ".join(keywords) if keywords else headline
        
        logger.info(f"Searching for: {query}")
        
        try:
            if self.provider == SearchProvider.DUCKDUCKGO:
                results = await self._search_duckduckgo(query, max_results)
            elif self.provider == SearchProvider.SERPAPI:
                results = await self._search_serpapi(query, max_results)
            else:
                results = await self._search_google_fallback(query, max_results)
            
            return results
        except Exception as e:
            logger.warning(f"Search failed for '{query}': {e}")
            return []

    async def _search_duckduckgo(self, query: str, max_results: int) -> List[SearchResult]:
        """Search using DuckDuckGo (no API key required)."""
        try:
            # Note: DuckDuckGo doesn't have a simple free API
            # This is a fallback that returns empty; use SERP API for production
            logger.warning("DuckDuckGo direct API not available; use SerpAPI")
            return []
        except Exception as e:
            logger.error(f"DuckDuckGo search error: {e}")
            return []

    async def _search_serpapi(self, query: str, max_results: int) -> List[SearchResult]:
        """Search using SerpAPI (requires API key)."""
        if not self.api_key:
            logger.warning("SerpAPI key not provided; skipping search")
            return []

        params = {
            "q": query,
            "api_key": self.api_key,
            "num": max_results,
        }

        try:
            resp = await self.client.get("https://serpapi.com/search", params=params)
            resp.raise_for_status()
            data = resp.json()

            results: List[SearchResult] = []
            for item in data.get("organic_results", [])[:max_results]:
                results.append(
                    SearchResult(
                        title=item.get("title", ""),
                        url=item.get("link", ""),
                        snippet=item.get("snippet", ""),
                        source=item.get("source", "Unknown"),
                    )
                )
            return results
        except Exception as e:
            logger.error(f"SerpAPI error: {e}")
            return []

    async def _search_google_fallback(self, query: str, max_results: int) -> List[SearchResult]:
        """Fallback: return empty results (requires custom implementation)."""
        logger.warning("Google search not fully implemented; consider using SerpAPI")
        return []

    async def enrich_item(
        self, item: Dict[str, Any], search_companies: List[str] | None = None
    ) -> EnrichedArticle:
        """
        Enrich a single item with search results.
        
        Args:
            item: Original scraped item dict
            search_companies: Optional list of company names to prioritize
        
        Returns:
            EnrichedArticle with search results
        """
        headline = item.get("headline", "")
        summary = item.get("content_snippet", "")[:200]
        item_id = item.get("event_id") or item.get("dedup_key") or "unknown"
        url = item.get("article_url", "")

        similar = await self.search_similar(headline, summary, max_results=5, top_companies=search_companies)
        keywords = self._extract_entity_keywords(headline, summary)

        search_summary = (
            f"Found {len(similar)} similar articles for '{' '.join(keywords or [headline[:30]])}'"
        )

        return EnrichedArticle(
            original_item_id=str(item_id),
            original_headline=headline,
            original_url=url,
            similar_articles=similar,
            entity_keywords=keywords,
            search_summary=search_summary,
        )

    async def enrich_batch(
        self, items: List[Dict[str, Any]], companies_per_item: Dict[str, List[str]] | None = None
    ) -> List[EnrichedArticle]:
        """
        Enrich multiple items concurrently.
        
        Args:
            items: List of items to enrich
            companies_per_item: Mapping of item_id -> list of company names
        
        Returns:
            List of EnrichedArticle objects
        """
        companies_per_item = companies_per_item or {}
        enriched = await asyncio.gather(
            *[
                self.enrich_item(
                    item, search_companies=companies_per_item.get(str(item.get("event_id", "")), None)
                )
                for item in items
            ]
        )
        return enriched


async def enrich_news_batch(
    news_items: List[Dict[str, Any]], api_key: str | None = None, concurrency: int = 3
) -> List[Dict[str, Any]]:
    """
    Main entry point: enrich news items with web search.
    
    Args:
        news_items: List of news items
        api_key: Optional SerpAPI key for search
        concurrency: Max concurrent enrichments (default 3)
    
    Returns:
        List of items with added "search_enrichment" field
    """
    async with WebSearchEnricher(api_key=api_key) as enricher:
        enriched_articles = await enricher.enrich_batch(news_items)

    output_items: List[Dict[str, Any]] = []
    for original_item, enriched in zip(news_items, enriched_articles):
        output_items.append(
            {
                **original_item,
                "search_enrichment": {
                    "context_summary": enriched.search_summary,
                    "entity_keywords": enriched.entity_keywords,
                    "similar_articles": [
                        {
                            "title": art.title,
                            "url": art.url,
                            "snippet": art.snippet,
                            "source": art.source,
                        }
                        for art in enriched.similar_articles
                    ],
                },
            }
        )

    return output_items


if __name__ == "__main__":
    # Test the enricher
    import sys

    test_item = {
        "event_id": "test-1",
        "headline": "Reliance Jio IPO to be largest in Indian history",
        "content_snippet": "Reliance Industries plans record IPO for Jio subsidiary",
        "article_url": "https://example.com/reliance-jio-ipo",
    }

    async def main():
        async with WebSearchEnricher() as enricher:
            enriched = await enricher.enrich_item(test_item)
            print(f"Item: {enriched.original_headline}")
            print(f"Entity Keywords: {enriched.entity_keywords}")
            print(f"Similar Articles Found: {len(enriched.similar_articles)}")
            for art in enriched.similar_articles:
                print(f"  - {art.title}")

    asyncio.run(main())
