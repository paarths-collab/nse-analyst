from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Iterable

import psycopg


@dataclass(frozen=True)
class EventRecord:
    event_id: str
    dedup_key: str
    event_type: str
    source_id: str
    source_url: str
    headline: str
    article_url: str
    published_at: str
    observed_at: str
    payload_json: dict


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None

    # First, try ISO timestamps.
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        pass

    # Then RSS-style dates like 'Wed, 27 Mar 2026 14:03:00 +0530'.
    try:
        return parsedate_to_datetime(value)
    except Exception:
        return None


def insert_events(database_url: str, events: Iterable[EventRecord]) -> tuple[int, int]:
    rows = list(events)
    if not rows:
        return 0, 0

    sql = """
    INSERT INTO events (
        event_id,
        dedup_key,
        event_type,
        symbol,
        source_id,
        source_url,
        headline,
        body,
        published_at,
        observed_at,
        payload_json
    ) VALUES (
        %(event_id)s,
        %(dedup_key)s,
        %(event_type)s,
        %(symbol)s,
        %(source_id)s,
        %(source_url)s,
        %(headline)s,
        %(body)s,
        %(published_at)s,
        %(observed_at)s,
        %(payload_json)s::jsonb
    )
    ON CONFLICT (dedup_key) DO NOTHING
    """

    inserted = 0
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            for event in rows:
                payload = {
                    "event_id": event.event_id,
                    "dedup_key": event.dedup_key,
                    "event_type": event.event_type,
                    "source_id": event.source_id,
                    "source_url": event.source_url,
                    "headline": event.headline,
                    "body": "",
                    "symbol": None,
                    "published_at": _parse_timestamp(event.published_at),
                    "observed_at": _parse_timestamp(event.observed_at) or datetime.utcnow(),
                    "payload_json": json.dumps(event.payload_json),
                }
                cur.execute(sql, payload)
                inserted += cur.rowcount
        conn.commit()

    skipped = len(rows) - inserted
    return inserted, skipped
