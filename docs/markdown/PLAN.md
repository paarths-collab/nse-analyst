# PLAN

## Objective
Build a deployable, scalable Telegram-only news and filing pipeline that can:
- Ingest market/news signals quickly.
- Select a reaction timeframe based on event age.
- Evaluate price and volume reaction.
- Check filing existence in recent hours.
- Classify state as EARLY, IN_PLAY, or LATE.
- Deliver actionable Telegram alerts with a p95 target of 60 seconds for active sources.

## Scope (Pilot)
- Users: 10
- Sources: 80 registered total
- Live alerts: Shard 1 only
- Shadow mode: Shards 2 to 8
- Delivery channel: Telegram only
- Deployment target: Render backend services

## Core Architecture
- Event transport: Redis Streams
- Durable store: PostgreSQL
- Services:
  - Ingest worker
  - Parse worker
  - Enrichment/market-check worker
  - Decision worker
  - Notification worker
  - API/health service

## Minimal Decision Logic
1. Receive news event.
2. Compute event age.
3. Select timeframe bucket:
   - 0 to 5 min -> 1m/5m
   - 5 to 30 min -> 5m
   - 30 to 120 min -> 15m
   - 2h to 1d -> 15m/1h
   - Over 1d -> non-proactive
4. Compute price_change_pct and relative_volume.
5. Check filing existence in last 6 hours.
6. Assign state:
   - EARLY: no filing + low reaction
   - IN_PLAY: no filing + reaction starting
   - LATE: filing exists or move extended
7. Send proactive Telegram alerts for EARLY and IN_PLAY only.

## End-to-End Flow
1. Source ingest worker fetches new items from active shard sources.
2. Parser worker normalizes headline/body, extracts symbol candidates, and writes a normalized event.
3. Enrichment worker computes event_age, selects timeframe bucket, and fetches price/volume reaction.
4. Filing-check step verifies if a filing exists in the last 6 hours for the same symbol.
5. Decision worker assigns EARLY/IN_PLAY/LATE with reason codes and stores audit fields.
6. Notification worker sends Telegram alert only if state is EARLY or IN_PLAY.
7. Metrics and health service records latency, queue lag, and delivery outcomes.

## Expected Output
### Stage Outputs
- Ingest output: event pushed to stream with `event_id`, `source`, `published_at`, and raw payload.
- Parse output: normalized event with `symbol`, `headline`, `dedup_key`, and parse confidence.
- Enrichment output: `timeframe_bucket`, `price_change_pct`, `relative_volume`, `event_age_s`.
- Filing-check output: `filing_exists_6h` boolean and matched filing reference if present.
- Decision output: `state`, `reason_codes`, `confidence_tier`, and decision timestamp.
- Notification output: `sent`, `failed`, or `suppressed` with idempotent `dispatch_key`.

### Telegram Alert Output (for EARLY/IN_PLAY)
- Symbol and source.
- State (EARLY/IN_PLAY).
- Timeframe bucket used for reaction check.
- Price change percent and relative volume.
- Filing existence status in last 6h.
- Short reason summary.

### Operational Output
- p50/p95/p99 end-to-end latency.
- Queue lag per worker group.
- Parser success rate and source health status.
- Notification success rate and duplicate-send count.

## Non-Negotiable Requirements
- p95 end-to-end latency <= 60s for active shard sources.
- Zero duplicate Telegram alerts under retry conditions.
- Deterministic state assignment with audit trail.
- LATE suppression for proactive alerting.

## Data Contracts (Minimum)
- sources table: source metadata, shard, health state, fetch mode.
- events table: normalized event payloads, timestamps, dedup key.
- decisions table: timeframe, price/volume evidence, filing flag, state, reason codes.
- notifications table: dispatch idempotency key, status, retries.
- symbol_state table: latest active state and watch lifecycle.

## Rollout Strategy
1. Register all 80 sources.
2. Activate only Shard 1 for live alerts.
3. Keep other shards in shadow mode.
4. Promote shards only when SLO, reliability, and quality gates pass.

## Success Gates
- 5 consecutive market sessions meeting latency and reliability targets.
- No unresolved critical incidents.
- Stable false-positive trend versus baseline.
- Shard 2 promotion checklist approved.
