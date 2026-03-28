-- Day 1 foundation schema for stream-driven pipeline.

CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    source_class TEXT NOT NULL,
    fetch_mode TEXT NOT NULL,
    cadence_s INTEGER NOT NULL,
    timeout_s INTEGER NOT NULL,
    trust_tier INTEGER NOT NULL,
    shard INTEGER NOT NULL,
    health_state TEXT NOT NULL DEFAULT 'healthy',
    is_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    dedup_key TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    symbol TEXT,
    source_id TEXT,
    source_url TEXT,
    headline TEXT,
    body TEXT,
    published_at TIMESTAMPTZ,
    observed_at TIMESTAMPTZ NOT NULL,
    payload_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    timeframe_bucket TEXT NOT NULL,
    price_change_pct DOUBLE PRECISION,
    relative_volume DOUBLE PRECISION,
    filing_exists_6h BOOLEAN NOT NULL,
    state TEXT NOT NULL,
    reason_codes JSONB NOT NULL,
    confidence_tier TEXT NOT NULL,
    decided_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS notifications (
    notification_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
    dispatch_key TEXT NOT NULL UNIQUE,
    channel TEXT NOT NULL,
    destination TEXT NOT NULL,
    status TEXT NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    sent_at TIMESTAMPTZ,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS symbol_state (
    symbol TEXT PRIMARY KEY,
    active_state TEXT,
    last_event_id TEXT,
    last_decision_id TEXT,
    watch_expires_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS subscriptions (
    subscription_id TEXT PRIMARY KEY,
    user_chat_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_chat_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_events_symbol_observed_at ON events(symbol, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_decisions_symbol_decided_at ON decisions(symbol, decided_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_status_created_at ON notifications(status, created_at DESC);
