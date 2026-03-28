"""Concurrent source scraper for shard-based news ingestion.

Usage:
    python scripts/scrape_sources.py --shard 1 --live-only --max-items 5
    python scripts/scrape_sources.py --shard 1 --live-only --push-stream
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree

import httpx

# Allow direct script execution.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fillings

from infra.config import load_config, load_env_file
from infra.event_store import EventRecord, insert_events
from infra.source_registry import SourceConfig, filter_sources, load_sources
from infra.streams import get_redis_client


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class ScrapedEvent:
    event_id: str
    dedup_key: str
    event_type: str
    source_id: str
    source_name: str
    source_url: str
    headline: str
    article_url: str
    published_at: str
    observed_at: str
    payload_json: dict


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dedup_key(source_id: str, article_url: str, published_at: str, headline: str) -> str:
    raw = f"{source_id}|{article_url}|{published_at[:16]}|{headline.strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _extract_html_links(html: str, base_url: str, max_items: int) -> list[tuple[str, str]]:
    pattern = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    for href, inner in pattern.findall(html):
        text = _clean_text(re.sub(r"<[^>]+>", " ", inner))
        if not text or len(text) < 25:
            continue

        if href.startswith("/"):
            # Basic absolute URL join for same-origin paths.
            match = re.match(r"https?://[^/]+", base_url)
            if not match:
                continue
            href = match.group(0) + href

        if not href.startswith("http"):
            continue

        key = f"{href}|{text[:120]}"
        if key in seen:
            continue
        seen.add(key)
        out.append((text[:240], href))

        if len(out) >= max_items:
            break

    return out


def _parse_rss_items(xml_text: str, max_items: int) -> list[tuple[str, str, str]]:
    items: list[tuple[str, str, str]] = []
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return items

    for node in root.findall(".//item")[:max_items]:
        title = _clean_text(node.findtext("title", default=""))
        link = _clean_text(node.findtext("link", default=""))
        pub_date = _clean_text(node.findtext("pubDate", default=""))
        if title and link:
            items.append((title[:240], link, pub_date))

    if items:
        return items

    # Atom fallback
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for node in root.findall(".//atom:entry", ns)[:max_items]:
        title = _clean_text(node.findtext("atom:title", default="", namespaces=ns))
        link_node = node.find("atom:link", ns)
        link = _clean_text(link_node.get("href", "") if link_node is not None else "")
        pub_date = _clean_text(node.findtext("atom:updated", default="", namespaces=ns))
        if title and link:
            items.append((title[:240], link, pub_date))

    return items


def _build_filing_events(hours: int, max_items: int) -> list[ScrapedEvent]:
    observed_at = _now_iso()
    try:
        fillings.refresh_session()
        filings_rows = fillings.fetch_data(hours=hours)
    except Exception as exc:
        return [
            ScrapedEvent(
                event_id=str(uuid.uuid4()),
                dedup_key=_dedup_key("nse_filings", "https://www.nseindia.com", observed_at, "FILING_FETCH_FAILED"),
                event_type="source_status",
                source_id="nse_filings",
                source_name="NSE Filings",
                source_url="https://www.nseindia.com/companies-listing/corporate-filings-announcements",
                headline=f"Filings fetch failed: {exc}",
                article_url="https://www.nseindia.com/",
                published_at=observed_at,
                observed_at=observed_at,
                payload_json={"status": "failed", "error": str(exc)},
            )
        ]

    events: list[ScrapedEvent] = []
    for row in filings_rows[: max(1, max_items)]:
        symbol = _clean_text(str(row.get("symbol", "")))
        subject = _clean_text(str(row.get("subject", "")))
        desc = _clean_text(str(row.get("attchmntText") or row.get("desc") or ""))
        pdf_url = _clean_text(str(row.get("attchmntFile", "")))
        an_dt = _clean_text(str(row.get("an_dt", "")))

        headline = " | ".join(x for x in [symbol, subject or desc[:140]] if x)
        if not headline:
            headline = "NSE Filing"

        link = pdf_url or "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
        dedup = _dedup_key("nse_filings", link, an_dt or observed_at, headline)

        events.append(
            ScrapedEvent(
                event_id=str(uuid.uuid4()),
                dedup_key=dedup,
                event_type="news_event",
                source_id="nse_filings",
                source_name="NSE Filings",
                source_url="https://www.nseindia.com/companies-listing/corporate-filings-announcements",
                headline=headline[:240],
                article_url=link,
                published_at=an_dt or observed_at,
                observed_at=observed_at,
                payload_json={
                    "source_mode": "api",
                    "trust_tier": "official",
                    "shard": 0,
                    "asset_kind": "filing",
                    "symbol": symbol,
                    "subject": subject,
                    "description": desc[:1000],
                    "pdf_url": pdf_url,
                },
            )
        )

    return events


async def _fetch_with_playwright(url: str) -> str:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(user_agent=USER_AGENT)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=12000)
            content = await page.content()
            return content
        finally:
            await page.close()
            await browser.close()


async def scrape_source(
    client: httpx.AsyncClient,
    source: SourceConfig,
    max_items: int,
    use_playwright_fallback: bool,
) -> list[ScrapedEvent]:
    observed_at = _now_iso()

    try:
        response = await client.get(source.url, timeout=12.0)
        response.raise_for_status()
        body = response.text
    except Exception as exc:
        if source.mode == "html" and use_playwright_fallback:
            try:
                body = await _fetch_with_playwright(source.url)
            except Exception as pw_exc:
                return [
                    ScrapedEvent(
                        event_id=str(uuid.uuid4()),
                        dedup_key=_dedup_key(source.source_id, source.url, observed_at, "SOURCE_FETCH_FAILED"),
                        event_type="source_status",
                        source_id=source.source_id,
                        source_name=source.name,
                        source_url=source.url,
                        headline=f"Source fetch failed: {pw_exc}",
                        article_url=source.url,
                        published_at=observed_at,
                        observed_at=observed_at,
                        payload_json={"status": "failed", "error": str(pw_exc), "fallback": "playwright"},
                    )
                ]
        else:
            return [
                ScrapedEvent(
                    event_id=str(uuid.uuid4()),
                    dedup_key=_dedup_key(source.source_id, source.url, observed_at, "SOURCE_FETCH_FAILED"),
                    event_type="source_status",
                    source_id=source.source_id,
                    source_name=source.name,
                    source_url=source.url,
                    headline=f"Source fetch failed: {exc}",
                    article_url=source.url,
                    published_at=observed_at,
                    observed_at=observed_at,
                    payload_json={"status": "failed", "error": str(exc)},
                )
            ]

    events: list[ScrapedEvent] = []

    if source.mode == "rss":
        items = _parse_rss_items(body, max_items=max_items)
        for title, link, pub_date in items:
            dedup = _dedup_key(source.source_id, link, pub_date or observed_at, title)
            events.append(
                ScrapedEvent(
                    event_id=str(uuid.uuid4()),
                    dedup_key=dedup,
                    event_type="news_event",
                    source_id=source.source_id,
                    source_name=source.name,
                    source_url=source.url,
                    headline=title,
                    article_url=link,
                    published_at=pub_date or observed_at,
                    observed_at=observed_at,
                    payload_json={
                        "source_mode": source.mode,
                        "trust_tier": source.trust_tier,
                        "shard": source.shard,
                    },
                )
            )
        return events

    links = _extract_html_links(body, source.url, max_items=max_items)
    for title, link in links:
        dedup = _dedup_key(source.source_id, link, observed_at, title)
        events.append(
            ScrapedEvent(
                event_id=str(uuid.uuid4()),
                dedup_key=dedup,
                event_type="news_event",
                source_id=source.source_id,
                source_name=source.name,
                source_url=source.url,
                headline=title,
                article_url=link,
                published_at=observed_at,
                observed_at=observed_at,
                payload_json={
                    "source_mode": source.mode,
                    "trust_tier": source.trust_tier,
                    "shard": source.shard,
                },
            )
        )
    return events


async def run_scrape(
    sources: Iterable[SourceConfig],
    max_items: int,
    concurrency: int,
    use_playwright_fallback: bool,
) -> list[ScrapedEvent]:
    sem = asyncio.Semaphore(concurrency)
    headers = {"User-Agent": USER_AGENT}

    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        async def bound(source: SourceConfig) -> list[ScrapedEvent]:
            async with sem:
                return await scrape_source(
                    client,
                    source,
                    max_items=max_items,
                    use_playwright_fallback=use_playwright_fallback,
                )

        results = await asyncio.gather(*(bound(s) for s in sources))

    events: list[ScrapedEvent] = []
    for chunk in results:
        events.extend(chunk)
    return events


def maybe_push_stream(events: list[ScrapedEvent]) -> tuple[int, str]:
    try:
        cfg = load_config()
    except RuntimeError as exc:
        return 0, f"Stream publish skipped ({exc})"

    redis_client = get_redis_client(cfg.redis_url)
    pushed = 0
    for event in events:
        if event.event_type != "news_event":
            continue
        redis_client.xadd(
            cfg.redis_stream_events,
            {
                "event_id": event.event_id,
                "dedup_key": event.dedup_key,
                "event_type": event.event_type,
                "source_id": event.source_id,
                "source_url": event.source_url,
                "headline": event.headline,
                "article_url": event.article_url,
                "published_at": event.published_at,
                "observed_at": event.observed_at,
                "payload_json": json.dumps(event.payload_json),
            },
        )
        pushed += 1
    return pushed, f"Pushed {pushed} events to stream"


def maybe_persist_db(events: list[ScrapedEvent]) -> tuple[int, str]:
    load_env_file()
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        return 0, "DB persist skipped (missing DATABASE_URL)"

    news_events = [
        EventRecord(
            event_id=e.event_id,
            dedup_key=e.dedup_key,
            event_type=e.event_type,
            source_id=e.source_id,
            source_url=e.source_url,
            headline=e.headline,
            article_url=e.article_url,
            published_at=e.published_at,
            observed_at=e.observed_at,
            payload_json={**e.payload_json, "article_url": e.article_url, "source_name": e.source_name},
        )
        for e in events
        if e.event_type == "news_event"
    ]
    inserted, skipped = insert_events(database_url, news_events)
    return inserted, f"Persisted {inserted} events to DB ({skipped} duplicates skipped)"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Shard-based concurrent source scraper")
    parser.add_argument("--registry", default="sources/sources_registry.json", help="Path to source registry JSON")
    parser.add_argument("--shard", type=int, default=1, help="Shard number to scrape")
    parser.add_argument("--live-only", action="store_true", help="Scrape only live-enabled sources")
    parser.add_argument("--max-items", type=int, default=5, help="Max items per source")
    parser.add_argument("--concurrency", type=int, default=5, help="Concurrent source fetch limit")
    parser.add_argument("--push-stream", action="store_true", help="Push normalized events to Redis stream")
    parser.add_argument("--persist-db", action="store_true", help="Persist normalized news events into Postgres")
    parser.add_argument("--use-playwright-fallback", action="store_true", help="Use Playwright fallback for HTML failures")
    parser.add_argument("--include-filings", action="store_true", help="Include NSE filings as additional events")
    parser.add_argument("--filings-hours", type=int, default=24, help="Time window (hours) for filing fetch when --include-filings is set")
    parser.add_argument("--filings-max-items", type=int, default=25, help="Max filing events to append when --include-filings is set")
    parser.add_argument("--output", default="scraped_events_latest.json", help="Output JSON file path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    sources = load_sources(args.registry)
    sources = filter_sources(sources, shard=args.shard, live_only=args.live_only)
    if not sources:
        print("No sources selected for scraping")
        return 0

    events = asyncio.run(
        run_scrape(
            sources=sources,
            max_items=args.max_items,
            concurrency=max(args.concurrency, 1),
            use_playwright_fallback=args.use_playwright_fallback,
        )
    )

    filings_added = 0
    if args.include_filings:
        filing_events = _build_filing_events(hours=max(1, args.filings_hours), max_items=max(1, args.filings_max_items))
        events.extend(filing_events)
        filings_added = sum(1 for e in filing_events if e.event_type == "news_event")

    out_path = Path(args.output)
    out_path.write_text(json.dumps([asdict(e) for e in events], indent=2), encoding="utf-8")

    print(f"Scraped {len(events)} records from {len(sources)} sources")
    if args.include_filings:
        print(f"Included NSE filing events: {filings_added}")
    print(f"Saved output to {out_path}")

    if args.push_stream:
        pushed, message = maybe_push_stream(events)
        print(message)
        if pushed == 0:
            return 2

    if args.persist_db:
        persisted, message = maybe_persist_db(events)
        print(message)
        if persisted == 0:
            return 3

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
