"""Bootstrap database schema and Redis stream consumer groups.

Usage:
    python scripts/bootstrap_pipeline.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from infra.config import load_config
from infra.db import ensure_schema, ping_database
from infra.streams import StreamContract, ensure_stream_contract, get_redis_client, ping_redis


def main() -> None:
    cfg = load_config()

    print("Checking database connectivity...")
    ping_database(cfg.database_url)
    print("Database OK")

    migration_file = str(Path(__file__).resolve().parents[1] / "migrations" / "001_init_pipeline.sql")
    print(f"Applying migration: {migration_file}")
    ensure_schema(cfg.database_url, [migration_file])
    print("Schema migration applied")

    print("Checking Redis connectivity...")
    redis_client = get_redis_client(cfg.redis_url)
    ping_redis(redis_client)
    print("Redis OK")

    contract = StreamContract(
        events_stream=cfg.redis_stream_events,
        notify_stream=cfg.redis_stream_notify,
        parse_group=cfg.redis_group_parse,
        decision_group=cfg.redis_group_decide,
        notify_group=cfg.redis_group_notify,
    )

    ensure_stream_contract(redis_client, contract)
    print("Redis stream contract is ready")
    print("Bootstrap complete")


if __name__ == "__main__":
    main()
