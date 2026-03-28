from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class SourceConfig:
    source_id: str
    name: str
    mode: str  # rss | html
    url: str
    shard: int
    is_live: bool
    trust_tier: int


def load_sources(path: str | Path) -> list[SourceConfig]:
    source_path = Path(path)
    if not source_path.exists():
        raise FileNotFoundError(f"Source registry not found: {source_path}")

    raw = json.loads(source_path.read_text(encoding="utf-8"))
    out: list[SourceConfig] = []
    for item in raw:
        out.append(
            SourceConfig(
                source_id=item["source_id"],
                name=item["name"],
                mode=item["mode"],
                url=item["url"],
                shard=int(item["shard"]),
                is_live=bool(item.get("is_live", False)),
                trust_tier=int(item.get("trust_tier", 2)),
            )
        )
    return out


def filter_sources(
    sources: Iterable[SourceConfig],
    shard: int | None = None,
    live_only: bool = False,
) -> list[SourceConfig]:
    filtered = list(sources)
    if shard is not None:
        filtered = [s for s in filtered if s.shard == shard]
    if live_only:
        filtered = [s for s in filtered if s.is_live]
    return filtered
