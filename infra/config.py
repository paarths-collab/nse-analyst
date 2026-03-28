import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PipelineConfig:
    """Runtime configuration for stream and database services."""

    database_url: str
    redis_url: str
    redis_stream_events: str
    redis_stream_notify: str
    redis_group_parse: str
    redis_group_decide: str
    redis_group_notify: str


def load_config() -> PipelineConfig:
    load_env_file()

    database_url = os.environ.get("DATABASE_URL", "").strip()
    redis_url = os.environ.get("REDIS_URL", "").strip()

    if not database_url:
        raise RuntimeError("Missing required env var DATABASE_URL")
    if not redis_url:
        raise RuntimeError("Missing required env var REDIS_URL")

    return PipelineConfig(
        database_url=database_url,
        redis_url=redis_url,
        redis_stream_events=os.environ.get("REDIS_STREAM_EVENTS", "pipeline:events"),
        redis_stream_notify=os.environ.get("REDIS_STREAM_NOTIFY", "pipeline:notify"),
        redis_group_parse=os.environ.get("REDIS_GROUP_PARSE", "parse-workers"),
        redis_group_decide=os.environ.get("REDIS_GROUP_DECIDE", "decision-workers"),
        redis_group_notify=os.environ.get("REDIS_GROUP_NOTIFY", "notify-workers"),
    )


def load_env_file(env_name: str = ".env") -> None:
    """Load simple KEY=VALUE pairs from a local .env file if present."""
    env_path = Path(__file__).resolve().parents[1] / env_name
    if not env_path.exists():
        return

    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
