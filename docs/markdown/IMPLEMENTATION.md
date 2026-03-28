# IMPLEMENTATION

## 4-Day Execution Plan

### Day 1 - Foundations
- Create PostgreSQL schemas:
  - sources
  - events
  - decisions
  - notifications
  - symbol_state
  - subscriptions
- Define Redis Streams and consumer groups.
- Add unique idempotency constraints for event and notification dedup.
- Configure Render services and environment secrets.
- Deliverable: all services boot in staging and health checks pass.

### Day 2 - Ingest and Parse
- Build async ingest worker for active sources (Shard 1).
- Add rate limiting (per-domain + global).
- Build parser worker (headline/body extraction + symbol mapping).
- Add source health states: healthy, degraded, blocked, cooldown.
- Add fallback queue for JS-heavy pages.
- Deliverable: stable ingestion and normalized events persisted.

### Day 3 - Decision and Notify
- Implement timeframe selection by event age.
- Fetch price and volume reaction metrics.
- Add filing existence check for last 6 hours.
- Implement EARLY/IN_PLAY/LATE assignment with reason codes.
- Build Telegram notifier worker with dedup + retry + backoff.
- Enforce proactive routing: EARLY/IN_PLAY only.
- Deliverable: end-to-end staging flow from source event to Telegram.

### Day 4 - Hardening and Pilot Go-Live
- Add metrics:
  - p50/p95/p99 latency
  - queue lag
  - parser success rate
  - notification success/failure rates
- Add dead-letter queue and replay script.
- Run load and failure tests.
- Launch pilot for 10 users on Shard 1.
- Deliverable: production pilot running with monitoring.

## Acceptance Checklist
- Pipeline runs continuously without crashes.
- p95 <= 60s for active shard sources.
- Duplicate alert count = 0 in retry simulations.
- State transition tests pass at threshold boundaries.
- Only EARLY/IN_PLAY proactive alerts are delivered.

## Out of Scope (Current Sprint)
- Live alerting for all 80 sources.
- Frontend dashboard build.
- Advanced multi-model reasoning enhancements.

## Immediate Next Actions
1. Create migration scripts and apply in staging.
2. Wire Redis streams and start ingest worker.
3. Implement parser contract and decision contract tests.
4. Run first dry run with one source, then scale to full Shard 1.

## Flow and Expected Output
1. Flow: ingest -> parse -> enrich -> filing-check -> decision -> notify.
2. Expected data output: rows in `events`, `decisions`, `notifications`, and updates in `symbol_state`.
3. Expected user output: Telegram alerts for EARLY/IN_PLAY only, each including symbol, timeframe, price change, relative volume, and filing status.
4. Expected ops output: latency and reliability metrics showing p95 <= 60s for active sources and duplicate-send count of 0.
