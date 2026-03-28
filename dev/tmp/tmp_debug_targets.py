import json
from datetime import datetime, timezone

from scripts.verify_research_pipeline import (
    select_rows_for_report,
    build_price_targets,
    try_fetch_price_history,
    parse_date_any,
)

rows = json.load(open("news_llm_review.json", "r", encoding="utf-8"))
sel = select_rows_for_report(rows, 10, 0)

for i, r in enumerate(sel, 1):
    targets, kind = build_price_targets(r)
    dt = parse_date_any(str(r.get("published_at", ""))) or datetime.now(timezone.utc)
    hit = ""
    for t in targets[:6]:
        p = try_fetch_price_history(t, dt)
        if p:
            hit = t
            break
    print(i, kind, targets[:6], "HIT=", hit, "|", str(r.get("raw_headline", ""))[:70])
