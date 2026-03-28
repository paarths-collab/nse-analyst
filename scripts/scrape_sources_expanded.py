#!/usr/bin/env python3
"""
Enhanced scraper: Handle 80 sources simultaneously with data validation.
Validates article quality, freshness, and data integrity.
"""

import asyncio
import json
import sys
import os
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from urllib.parse import urljoin, urlparse
import re

# Add parent path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import httpx
from bs4 import BeautifulSoup
import feedparser
from email.utils import parsedate_to_datetime

from infra.config import load_config
from infra.event_store import EventRecord, insert_events
from infra.streams import get_redis_client
from infra.news_relevance import IndirectRelevanceDetector

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_source_dicts(path: str) -> List[Dict[str, Any]]:
    """Load source registry as plain dicts to preserve optional fields."""
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    out: List[Dict[str, Any]] = []
    for item in raw:
        # Keep required fields normalized and preserve optional fields.
        src = dict(item)
        src["source_id"] = str(item["source_id"])
        src["name"] = str(item["name"])
        src["mode"] = str(item["mode"])
        src["url"] = str(item["url"])
        src["shard"] = int(item["shard"])
        src["is_live"] = bool(item.get("is_live", False))
        src["trust_tier"] = int(item.get("trust_tier", 2))
        out.append(src)
    return out


def _is_candidate_listing_page(base_domain: str, page_url: str, anchor_text: str) -> bool:
    """Heuristic to identify extra listing pages worth crawling."""
    parsed = urlparse(page_url)
    if parsed.netloc != base_domain:
        return False

    lower_url = page_url.lower()
    text = (anchor_text or "").strip().lower()

    # Strong pagination signals.
    if any(k in lower_url for k in ["page=", "/page/", "?p=", "start="]):
        return True
    if text in {"next", "older", "more", "load more", "view more"}:
        return True

    # Listing/category paths that often contain additional headlines.
    listing_keywords = [
        "/news",
        "/market",
        "/markets",
        "/business",
        "/latest",
        "/archive",
        "/economy",
    ]
    if any(k in lower_url for k in listing_keywords):
        return True

    return False

@dataclass
class ValidationError:
    """Data validation error."""
    field: str
    issue: str
    value: Any = None

@dataclass
class ScrapedEvent:
    """Normalized scraped article."""
    event_id: str
    dedup_key: str
    headline: str
    article_url: str
    published_at: str
    source_id: str
    source_name: str
    content_snippet: Optional[str] = None
    sector: Optional[str] = None
    tags: List[str] = None
    validation_errors: List[ValidationError] = None
    # Market relevance fields
    is_market_relevant: bool = False
    relevance_type: str = "NOT_RELEVANT"
    relevance_confidence: float = 0.0
    market_risk_level: str = "unknown"
    relevance_tags: List[str] = None
    
    def __post_init__(self):
        if self.tags is None:
            self.tags = []
        if self.validation_errors is None:
            self.validation_errors = []
        if self.relevance_tags is None:
            self.relevance_tags = []
    
    def is_valid(self) -> bool:
        """Check if event has no critical validation errors AND is market relevant."""
        critical_errors = [e for e in self.validation_errors if self._is_critical(e.field)]
        # Valid if no critical errors AND either directly or indirectly market relevant
        return len(critical_errors) == 0 and self.is_market_relevant
    
    def is_high_value(self) -> bool:
        """Check if event is high-value (direct mention or high-risk indirect)."""
        return self.relevance_type in ['DIRECT_STOCK', 'MACRO_ECONOMIC', 'REGULATORY'] or \
               self.market_risk_level == 'high'
    
    @staticmethod
    def _is_critical(field: str) -> bool:
        """Critical fields that must be valid."""
        return field in ['headline', 'article_url', 'published_at']

def _validate_headline(headline: str) -> Optional[ValidationError]:
    """Validate headline quality."""
    if not headline:
        return ValidationError('headline', 'empty or null')
    if len(headline.strip()) < 5:
        return ValidationError('headline', f'too short ({len(headline)} chars)', headline)
    if len(headline) > 500:
        return ValidationError('headline', f'too long ({len(headline)} chars)', headline)
    if headline.lower().startswith('[removed]') or '[deleted]' in headline.lower():
        return ValidationError('headline', 'deleted/removed content', headline)
    # Check for excessive special characters
    special_count = sum(1 for c in headline if c in '!@#$%^&*')
    if special_count > len(headline) * 0.2:
        return ValidationError('headline', 'excessive special chars', headline)
    return None

def _validate_url(url: str, source_url: str) -> Optional[ValidationError]:
    """Validate article URL."""
    if not url:
        return ValidationError('article_url', 'empty or null')
    if len(url) > 2048:
        return ValidationError('article_url', 'URL too long', url[:100])
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return ValidationError('article_url', 'invalid URL structure', url)
    except Exception as e:
        return ValidationError('article_url', f'parse error: {str(e)}', url)
    return None

def _validate_timestamp(timestamp_str: str) -> Optional[ValidationError]:
    """Validate published_at timestamp."""
    if not timestamp_str:
        return ValidationError('published_at', 'empty or null')
    try:
        # Normalize odd timezone suffixes like '+053' -> '+0530'.
        normalized_ts = timestamp_str.strip()
        normalized_ts = re.sub(r'([+-]\d{3})$', r'\g<1>0', normalized_ts)

        dt: Optional[datetime] = None

        # 1) ISO timestamps (handles microseconds and timezone offsets).
        try:
            dt = datetime.fromisoformat(normalized_ts.replace('Z', '+00:00'))
        except ValueError:
            dt = None

        # 2) RSS-style dates, e.g., 'Wed, 27 Mar 2026 14:03:00 +0530'.
        if dt is None:
            try:
                dt = parsedate_to_datetime(normalized_ts)
            except Exception:
                dt = None

        # 3) Common fallback patterns.
        if dt is None:
            for fmt in [
                '%Y-%m-%d %H:%M:%S',
                '%Y-%m-%d',
                '%d-%m-%Y %H:%M:%S',
                '%d %b %Y %H:%M:%S',
            ]:
                try:
                    dt = datetime.strptime(normalized_ts, fmt)
                    break
                except ValueError:
                    continue
        
        if not dt:
            return ValidationError('published_at', 'unparsable format', timestamp_str)
        
        # Check if timestamp is within reasonable range (not in future, not > 365 days old)
        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        
        if dt > now:
            return ValidationError('published_at', 'future date', timestamp_str)
        if (now - dt) > timedelta(days=365):
            return ValidationError('published_at', 'older than 365 days', timestamp_str)
    except Exception as e:
        return ValidationError('published_at', f'validation error: {str(e)}', timestamp_str)
    return None

def _apply_market_relevance(event: ScrapedEvent) -> None:
    """Apply market relevance detection to event."""
    detector = IndirectRelevanceDetector()
    result = detector.detect_relevance(event.headline, event.content_snippet or "")
    
    event.is_market_relevant = result.is_market_relevant
    event.relevance_type = result.relevance_type.name
    event.relevance_confidence = result.confidence
    event.market_risk_level = result.risk_level
    event.relevance_tags = result.tags

def _validate_event(event: ScrapedEvent) -> List[ValidationError]:
    """Run all validation checks on an event."""
    errors = []
    
    err = _validate_headline(event.headline)
    if err:
        errors.append(err)
    
    err = _validate_url(event.article_url, "")
    if err:
        errors.append(err)
    
    err = _validate_timestamp(event.published_at)
    if err:
        errors.append(err)
    
    # Apply market relevance detection
    _apply_market_relevance(event)
    
    return errors

def _dedup_key(event: ScrapedEvent) -> str:
    """Generate deterministic dedup key."""
    key_parts = [
        event.source_id,
        event.article_url.lower(),
        event.published_at[:10],  # Date part only
        event.headline.lower()[:50]  # First 50 chars of headline
    ]
    key_str = '|'.join(key_parts)
    return hashlib.sha256(key_str.encode()).hexdigest()[:32]

async def _parse_rss_feed(url: str, source_id: str, source_name: str, client: httpx.AsyncClient, timeout: int = 15) -> List[ScrapedEvent]:
    """Parse RSS/Atom feed."""
    events = []
    try:
        response = await client.get(url, timeout=timeout, follow_redirects=True)
        response.raise_for_status()
        
        feed = feedparser.parse(response.content)
        if feed.bozo:
            logger.warning(f"{source_id}: Feed parse warning: {feed.bozo_exception}")
        
        for idx, entry in enumerate(feed.entries[:20]):  # Limit to 20 items per source
            headline = entry.get('title', '').strip()
            article_url = entry.get('link', '').strip()
            
            # Try to get published date
            published_at = None
            for date_field in ['published', 'updated', 'created']:
                if hasattr(entry, date_field):
                    try:
                        published_at = entry[date_field]
                        break
                    except (KeyError, TypeError):
                        pass
            
            if not published_at:
                published_at = datetime.now(timezone.utc).isoformat()
            
            if not headline or not article_url:
                continue
            
            # Clean up published_at if needed
            if isinstance(published_at, tuple):
                published_at = datetime(*published_at[:6]).isoformat()
            
            event = ScrapedEvent(
                event_id=f"{source_id}_{idx}_{hashlib.md5(headline.encode()).hexdigest()[:8]}",
                dedup_key="",  # Will be set later
                headline=headline,
                article_url=article_url,
                published_at=str(published_at)[:30],
                source_id=source_id,
                source_name=source_name,
                content_snippet=entry.get('summary', '')[:200].strip()
            )
            
            # Validate event
            event.validation_errors = _validate_event(event)
            event.dedup_key = _dedup_key(event)
            events.append(event)
    
    except asyncio.TimeoutError:
        logger.warning(f"{source_id}: RSS fetch timeout after {timeout}s")
    except Exception as e:
        logger.warning(f"{source_id}: RSS parse error: {str(e)}")
    
    return events

async def _extract_html_links(url: str, source_id: str, source_name: str, client: httpx.AsyncClient, 
                              timeout: int = 15, max_items: int = 15) -> List[ScrapedEvent]:
    """Extract article links from HTML page."""
    events = []
    try:
        response = await client.get(url, timeout=timeout, follow_redirects=True)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        links = soup.find_all('a', href=True)
        
        seen_urls = set()
        item_count = 0
        
        for link in links:
            if item_count >= max_items:
                break
            
            text = link.get_text().strip()
            href = link.get('href', '').strip()
            
            if not text or len(text) < 10 or len(text) > 300:
                continue
            
            # Normalize URL
            if href:
                href = urljoin(url, href)
            
            if href in seen_urls or not href.startswith('http'):
                continue
            
            seen_urls.add(href)
            item_count += 1
            
            event = ScrapedEvent(
                event_id=f"{source_id}_{item_count}_{hashlib.md5(text.encode()).hexdigest()[:8]}",
                dedup_key="",
                headline=text,
                article_url=href,
                published_at=datetime.now(timezone.utc).isoformat()[:30],
                source_id=source_id,
                source_name=source_name
            )
            
            # Validate event
            event.validation_errors = _validate_event(event)
            event.dedup_key = _dedup_key(event)
            events.append(event)
    
    except asyncio.TimeoutError:
        logger.warning(f"{source_id}: HTML fetch timeout after {timeout}s")
    except Exception as e:
        logger.warning(f"{source_id}: HTML parse error: {str(e)}")
    
    return events


async def _extract_html_links_and_pages(
    url: str,
    source_id: str,
    source_name: str,
    client: httpx.AsyncClient,
    timeout: int = 15,
    max_items: int = 15,
) -> tuple[List[ScrapedEvent], List[str]]:
    """Extract article links and crawlable listing pages from HTML page."""
    events: List[ScrapedEvent] = []
    discovered_pages: List[str] = []

    try:
        response = await client.get(url, timeout=timeout, follow_redirects=True)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, "html.parser")
        links = soup.find_all("a", href=True)

        parsed_base = urlparse(url)
        base_domain = parsed_base.netloc
        seen_urls = set()
        item_count = 0

        for link in links:
            text = link.get_text().strip()
            href = link.get("href", "").strip()
            if not href:
                continue

            absolute = urljoin(url, href)
            if not absolute.startswith("http"):
                continue

            if _is_candidate_listing_page(base_domain, absolute, text):
                discovered_pages.append(absolute)

            if item_count >= max_items:
                continue

            if not text or len(text) < 10 or len(text) > 300:
                continue

            if absolute in seen_urls:
                continue

            seen_urls.add(absolute)
            item_count += 1

            event = ScrapedEvent(
                event_id=f"{source_id}_{item_count}_{hashlib.md5(text.encode()).hexdigest()[:8]}",
                dedup_key="",
                headline=text,
                article_url=absolute,
                published_at=datetime.now(timezone.utc).isoformat()[:30],
                source_id=source_id,
                source_name=source_name,
            )

            event.validation_errors = _validate_event(event)
            event.dedup_key = _dedup_key(event)
            events.append(event)

    except asyncio.TimeoutError:
        logger.warning(f"{source_id}: HTML fetch timeout after {timeout}s for {url}")
    except Exception as e:
        logger.warning(f"{source_id}: HTML parse error on {url}: {str(e)}")

    return events, discovered_pages

async def scrape_source(source: Dict, client: httpx.AsyncClient, use_playwright: bool = False) -> tuple[List[ScrapedEvent], Dict[str, int]]:
    """Scrape a single source."""
    source_id = source['source_id']
    source_name = source['name']
    mode = source['mode']
    url = source['url']
    
    stats = {
        'scraped': 0,
        'valid': 0,
        'invalid': 0,
        'errors': 0,
        'pages_crawled': 0,
    }
    
    try:
        max_items_per_page = int(source.get("max_items_per_page", 15))
        max_pages = int(source.get("max_pages", 3))

        if mode == 'rss':
            events = await _parse_rss_feed(url, source_id, source_name, client)
            stats['pages_crawled'] = 1
        else:  # html
            crawl_queue: List[str] = [url]
            for extra in source.get("seed_urls", []):
                crawl_queue.append(urljoin(url, str(extra)))

            visited_pages = set()
            all_events: List[ScrapedEvent] = []
            seen_article_urls = set()

            while crawl_queue and len(visited_pages) < max_pages:
                page_url = crawl_queue.pop(0)
                if page_url in visited_pages:
                    continue
                visited_pages.add(page_url)

                page_events, discovered_pages = await _extract_html_links_and_pages(
                    page_url,
                    source_id,
                    source_name,
                    client,
                    max_items=max_items_per_page,
                )

                for evt in page_events:
                    if evt.article_url in seen_article_urls:
                        continue
                    seen_article_urls.add(evt.article_url)
                    all_events.append(evt)

                for next_page in discovered_pages:
                    if next_page not in visited_pages and next_page not in crawl_queue:
                        crawl_queue.append(next_page)

            stats['pages_crawled'] = len(visited_pages)
            events = all_events
        
        stats['scraped'] = len(events)
        
        for event in events:
            if event.is_valid():
                stats['valid'] += 1
            else:
                stats['invalid'] += 1
        
        return events, stats
    
    except Exception as e:
        logger.error(f"{source_id}: {str(e)}")
        stats['errors'] = 1
        return [], stats

async def run_scrape_expanded(all_sources: List[Dict], concurrency: int = 15, 
                              max_items_per_source: int = 5) -> tuple[List[ScrapedEvent], Dict]:
    """Scrape all sources with controlled concurrency."""
    
    logger.info(f"Starting expanded scrape of {len(all_sources)} sources (concurrency={concurrency})")
    
    events = []
    stats = {
        'total_sources': len(all_sources),
        'sources_ok': 0,
        'sources_error': 0,
        'total_scraped': 0,
        'total_valid': 0,
        'total_invalid': 0,
        'total_pages_crawled': 0,
        'by_shard': {}
    }
    
    async with httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=concurrency)) as client:
        semaphore = asyncio.Semaphore(concurrency)
        
        async def bounded_scrape(source):
            async with semaphore:
                scraped, source_stats = await scrape_source(source, client)
                return source, scraped, source_stats
        
        tasks = [bounded_scrape(src) for src in all_sources]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        
        for result in results:
            if isinstance(result, Exception):
                stats['sources_error'] += 1
                continue
            
            source, scraped, source_stats = result
            shard = source['shard']
            
            if shard not in stats['by_shard']:
                stats['by_shard'][shard] = {'count': 0, 'valid': 0, 'invalid': 0}
            
            stats['by_shard'][shard]['count'] += source_stats['valid']
            stats['by_shard'][shard]['valid'] += source_stats['valid']
            stats['by_shard'][shard]['invalid'] += source_stats['invalid']
            
            if source_stats['errors'] == 0:
                stats['sources_ok'] += 1
            else:
                stats['sources_error'] += 1
            
            stats['total_scraped'] += source_stats['scraped']
            stats['total_valid'] += source_stats['valid']
            stats['total_invalid'] += source_stats['invalid']
            stats['total_pages_crawled'] += source_stats.get('pages_crawled', 0)
            
            events.extend(scraped)
    
    return events, stats

def maybe_push_stream(events: List[ScrapedEvent], redis_url: str, push: bool = False) -> int:
    """Push events to Redis stream."""
    if not push or not events:
        return 0
    
    try:
        redis_client = get_redis_client(redis_url)
        pushed_count = 0
        
        for event in events:
            if event.is_valid():
                msg = {
                    'event_id': event.event_id,
                    'dedup_key': event.dedup_key,
                    'headline': event.headline,
                    'article_url': event.article_url,
                    'published_at': event.published_at,
                    'source_id': event.source_id,
                    'source_name': event.source_name,
                }
                redis_client.xadd('pipeline:events', msg)
                pushed_count += 1
        
        return pushed_count
    except Exception as e:
        logger.error(f"Stream push error: {str(e)}")
        return 0

def maybe_persist_db(events: List[ScrapedEvent], database_url: str, persist: bool = False) -> tuple[int, int]:
    """Persist valid events to database."""
    if not persist or not events:
        return 0, 0
    
    try:
        valid_events = [e for e in events if e.is_valid()]
        if not valid_events:
            return 0, 0
        
        records = [
            EventRecord(
                event_id=e.event_id,
                dedup_key=e.dedup_key,
                event_type='news_article',
                source_id=e.source_id,
                source_url=e.article_url,
                headline=e.headline,
                article_url=e.article_url,
                published_at=e.published_at,
                observed_at=datetime.now(timezone.utc).isoformat()[:30],
                payload_json={
                    'tags': e.tags,
                    'snippet': e.content_snippet,
                    'sector': e.sector,
                    'source_name': e.source_name
                }
            )
            for e in valid_events
        ]
        
        inserted, skipped = insert_events(database_url, records)
        logger.info(f"DB persist: {inserted} inserted, {skipped} duplicates skipped")
        return inserted, skipped
    except Exception as e:
        logger.error(f"DB persist error: {str(e)}")
        return 0, 0

def generate_validation_report(events: List[ScrapedEvent], stats: Dict) -> Dict:
    """Generate comprehensive validation report."""
    
    invalid_events = [e for e in events if not e.is_valid()]
    market_relevant = [e for e in events if e.is_market_relevant]
    high_value = [e for e in events if e.is_high_value()]
    
    error_summary = {}
    relevance_distribution = {}
    risk_distribution = {'high': 0, 'medium': 0, 'low': 0, 'unknown': 0}
    
    for event in invalid_events:
        for error in event.validation_errors:
            key = f"{error.field}_{error.issue}"
            error_summary[key] = error_summary.get(key, 0) + 1
    
    # Analyze relevance distribution
    for event in market_relevant:
        rel_type = event.relevance_type
        relevance_distribution[rel_type] = relevance_distribution.get(rel_type, 0) + 1
        
        risk = event.market_risk_level
        if risk in risk_distribution:
            risk_distribution[risk] += 1
    
    report = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'summary': {
            'total_sources_targeted': stats['total_sources'],
            'sources_successful': stats['sources_ok'],
            'sources_failed': stats['sources_error'],
            'success_rate': f"{(stats['sources_ok'] / stats['total_sources'] * 100):.1f}%",
        },
        'articles': {
            'total_scraped': stats['total_scraped'],
            'valid_format': stats['total_valid'],
            'invalid_format': stats['total_invalid'],
            'valid_rate': f"{(stats['total_valid'] / max(1, stats['total_scraped']) * 100):.1f}%",
        },
        'market_relevance': {
            'market_relevant': len(market_relevant),
            'high_value': len(high_value),
            'relevance_distribution': relevance_distribution,
            'risk_distribution': risk_distribution,
            'market_relevant_rate': f"{(len(market_relevant) / max(1, stats['total_scraped']) * 100):.1f}%",
            'high_value_rate': f"{(len(high_value) / max(1, stats['total_scraped']) * 100):.1f}%",
        },
        'by_shard': stats['by_shard'],
        'validation_issues': error_summary,
        'high_value_events': [
            {
                'source': e.source_id,
                'headline': e.headline[:100],
                'type': e.relevance_type,
                'risk': e.market_risk_level.upper(),
                'confidence': f"{e.relevance_confidence:.0%}",
                'tags': e.relevance_tags[:5]
            }
            for e in sorted(high_value, key=lambda x: x.relevance_confidence, reverse=True)[:10]
        ],
        'sample_invalid_events': [
            {
                'source': e.source_id,
                'headline': e.headline[:80],
                'errors': [{'field': err.field, 'issue': err.issue} for err in e.validation_errors]
            }
            for e in invalid_events[:5]
        ]
    }
    
    return report

async def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Scrape all 80 news sources with centralized validation')
    parser.add_argument('--concurrency', type=int, default=15, help='Concurrent scraping threads')
    parser.add_argument('--max-items', type=int, default=5, help='Max articles per source')
    parser.add_argument('--max-pages', type=int, default=3, help='Max listing pages to crawl per HTML source')
    parser.add_argument('--sources-file', default='sources/sources_registry_cleaned.json', help='Path to source registry JSON')
    parser.add_argument('--push-stream', action='store_true', help='Publish to Redis stream')
    parser.add_argument('--persist-db', action='store_true', help='Persist to database')
    parser.add_argument('--output', default='scraped_events_expanded.json', help='Output JSON file')
    parser.add_argument('--report', action='store_true', help='Generate validation report')
    
    args = parser.parse_args()
    
    try:
        # Load configuration
        config = load_config()
        
        # Load source registry with optional crawl fields.
        all_sources = load_source_dicts(args.sources_file)
        all_sources = [src for src in all_sources if src.get("is_live", False)]
        for src in all_sources:
            if src.get("mode") == "html":
                src["max_items_per_page"] = int(src.get("max_items_per_page", args.max_items))
                src["max_pages"] = int(src.get("max_pages", args.max_pages))
        logger.info(f"Loaded {len(all_sources)} live sources from {args.sources_file}")
        
        # Run scrape
        start_time = datetime.now()
        events, stats = await run_scrape_expanded(all_sources, concurrency=args.concurrency, max_items_per_source=args.max_items)
        elapsed = (datetime.now() - start_time).total_seconds()
        
        logger.info(
            f"Scrape completed in {elapsed:.1f}s: {len(events)} events "
            f"({stats['total_valid']} valid) across {stats['total_pages_crawled']} pages"
        )
        
        # Push to stream if requested
        if args.push_stream:
            pushed = maybe_push_stream(events, config.redis_url, push=True)
            logger.info(f"Pushed {pushed} valid events to Redis stream")
        
        # Persist to DB if requested
        if args.persist_db:
            inserted, skipped = maybe_persist_db(events, config.database_url, persist=True)
            logger.info(f"Persisted {inserted} events ({skipped} duplicates)")
        
        # Generate report
        report = generate_validation_report(events, stats)
        
        # Save events
        with open(args.output, 'w') as f:
            json.dump([
                {
                    'event_id': e.event_id,
                    'dedup_key': e.dedup_key,
                    'headline': e.headline,
                    'article_url': e.article_url,
                    'published_at': e.published_at,
                    'source_id': e.source_id,
                    'source_name': e.source_name,
                    'valid': e.is_valid(),
                    'market_relevant': e.is_market_relevant,
                    'relevance_type': e.relevance_type,
                    'relevance_confidence': e.relevance_confidence,
                    'market_risk_level': e.market_risk_level,
                    'relevance_tags': e.relevance_tags,
                    'errors': [{'field': err.field, 'issue': err.issue} for err in e.validation_errors]
                }
                for e in events
            ], f, indent=2)
        
        
        # Save report
        report_file = args.output.replace('.json', '_report.json')
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2)
        
        # Print summary
        print("\n" + "="*100)
        print("COMPREHENSIVE NEWS SCRAPE REPORT - ALL 80 SOURCES")
        print("="*100)
        print(f"Time: {report['timestamp']}")
        
        print(f"\n📊 SOURCE COVERAGE:")
        print(f"   {report['summary']['sources_successful']}/{report['summary']['total_sources_targeted']} sources successful ({report['summary']['success_rate']})")
        
        print(f"\n📰 ARTICLE METRICS:")
        print(f"   Total Scraped: {report['articles']['total_scraped']}")
        print(f"   Valid Format: {report['articles']['valid_format']} ({report['articles']['valid_rate']})")
        print(f"   Invalid Format: {report['articles']['invalid_format']}")
        print(f"   Listing Pages Crawled: {stats['total_pages_crawled']}")
        
        print(f"\n💰 MARKET RELEVANCE ANALYSIS:")
        print(f"   Market Relevant: {report['market_relevance']['market_relevant']} ({report['market_relevance']['market_relevant_rate']})")
        print(f"   High Value: {report['market_relevance']['high_value']} ({report['market_relevance']['high_value_rate']})")
        
        print(f"\n📈 RELEVANCE BREAKDOWN:")
        for rel_type, count in sorted(report['market_relevance']['relevance_distribution'].items(), key=lambda x: x[1], reverse=True):
            print(f"   • {rel_type}: {count}")
        
        print(f"\n⚠️  RISK DISTRIBUTION:")
        for risk in ['high', 'medium', 'low', 'unknown']:
            count = report['market_relevance']['risk_distribution'].get(risk, 0)
            if count > 0:
                print(f"   • {risk.upper()}: {count}")
        
        print(f"\n🔥 TOP 10 HIGH-VALUE OPPORTUNITIES:")
        if report['high_value_events']:
            for i, event in enumerate(report['high_value_events'][:10], 1):
                tags_str = ", ".join(event['tags']) if event['tags'] else "none"
                print(f"   {i}. [{event['risk']}] {event['headline']}")
                print(f"      Source: {event['source']} | Type: {event['type']} | Confidence: {event['confidence']}")
                print(f"      Tags: {tags_str}\n")
        else:
            print("   No high-value events detected")
        
        print(f"\n📍 BY SHARD:")
        for shard in sorted(report['by_shard'].keys()):
            shard_stats = report['by_shard'][shard]
            print(f"   Shard {shard}: {shard_stats['valid']} valid, {shard_stats['invalid']} invalid")
        
        print(f"\n💾 FILES SAVED:")
        print(f"   • Events: {args.output}")
        print(f"   • Report: {report_file}")
        
        print("="*100 + "\n")
        
        return 0
    
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}", exc_info=True)
        return 1

if __name__ == '__main__':
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
