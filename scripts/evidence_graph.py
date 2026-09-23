#!/usr/bin/env python3
"""Export field-level evidence relationships without copying source bodies."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from trip_support import atomic_json, preflight


def _time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def build_graph(trip: dict, now: datetime | None = None) -> dict:
    preflight(trip)
    now = now or datetime.now().astimezone()
    item_uses: dict[str, list[str]] = {}
    place_uses: dict[str, list[str]] = {}
    for day in trip["itinerary"]["days"]:
        for item in day["items"]:
            for fact_id in item.get("fact_ids", []):
                item_uses.setdefault(fact_id, []).append(item["item_id"])
            if item.get("place_id"):
                place_uses.setdefault(item["place_id"], []).append(item["item_id"])
    facts = []
    for fact in trip["facts"]:
        stale_at = _time(fact.get("stale_after"))
        facts.append({
            "fact_id": fact["fact_id"],
            "subject_id": fact["subject_id"],
            "subject_field": fact.get("subject_field"),
            "status": fact["status"],
            "is_stale": bool(stale_at and stale_at <= now),
            "source_ids": fact.get("source_ids", []),
            "conflicts": fact.get("conflicts", []),
            "used_by_item_ids": sorted(set(item_uses.get(fact["fact_id"], []))),
            "related_item_ids": sorted(set(place_uses.get(fact["subject_id"], []))),
        })
    return {
        "trip_id": trip["trip_id"],
        "plan_version": trip["plan_version"],
        "generated_at": now.isoformat(timespec="seconds"),
        "facts": facts,
        "sources": [{"source_id": s["source_id"], "source_type": s["source_type"], "title": s["title"]} for s in trip["sources"]],
        "summary": {
            "facts": len(facts),
            "stale": sum(1 for f in facts if f["is_stale"]),
            "conflicts": sum(1 for f in facts if f["status"] == "conflict" or f["conflicts"]),
            "unlinked_to_items": sum(1 for f in facts if not f["used_by_item_ids"] and not f["related_item_ids"]),
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="导出旅行事实、字段、来源与行程项的证据图谱")
    parser.add_argument("trip")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    try:
        with open(args.trip, encoding="utf-8") as handle:
            graph = build_graph(json.load(handle))
        if args.out:
            atomic_json(args.out, graph)
        else:
            print(json.dumps(graph, ensure_ascii=False, indent=2))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        print("✖ 无法导出证据图谱", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
