"""Runtime dependency health check for PostgreSQL and Redis.

Usage:
    python scripts/health_check.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from infra.config import load_config
from infra.db import ping_database
from infra.streams import get_redis_client, ping_redis


def main() -> None:
    cfg = load_config()

    ping_database(cfg.database_url)
    redis_client = get_redis_client(cfg.redis_url)
    ping_redis(redis_client)

    print("HEALTHY: database and redis are reachable")


if __name__ == "__main__":
    main()
