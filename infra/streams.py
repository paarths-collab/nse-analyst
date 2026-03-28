from __future__ import annotations

from dataclasses import dataclass

from redis import Redis
from redis.exceptions import ResponseError


@dataclass(frozen=True)
class StreamContract:
    events_stream: str
    notify_stream: str
    parse_group: str
    decision_group: str
    notify_group: str


def get_redis_client(redis_url: str) -> Redis:
    return Redis.from_url(redis_url, decode_responses=True)


def ensure_consumer_group(redis_client: Redis, stream: str, group: str) -> None:
    try:
        redis_client.xgroup_create(name=stream, groupname=group, id="0", mkstream=True)
    except ResponseError as exc:
        # BUSYGROUP means the consumer group already exists.
        if "BUSYGROUP" not in str(exc):
            raise


def ensure_stream_contract(redis_client: Redis, contract: StreamContract) -> None:
    ensure_consumer_group(redis_client, contract.events_stream, contract.parse_group)
    ensure_consumer_group(redis_client, contract.events_stream, contract.decision_group)
    ensure_consumer_group(redis_client, contract.notify_stream, contract.notify_group)


def ping_redis(redis_client: Redis) -> None:
    if not redis_client.ping():
        raise RuntimeError("Redis ping failed")
