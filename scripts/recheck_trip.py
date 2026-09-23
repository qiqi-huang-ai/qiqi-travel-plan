#!/usr/bin/env python3
"""Build a deterministic queue of evidence that must be rechecked."""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from datetime import datetime

from trip_support import atomic_json, preflight


def _parse(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except ValueError:
        return None


def build_queue(trip: dict, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now().astimezone()
    must_places = {m.get("place_id") for m in trip["request"]["interests"]["must"] if m.get("place_id")}
    locked_items = [i for day in trip["itinerary"]["days"] for i in day["items"] if i.get("locked")]
    locked_subjects = {i["item_id"] for i in locked_items} | {i.get("place_id") for i in locked_items if i.get("place_id")}
    locked_facts = {fid for i in locked_items for fid in i.get("fact_ids", [])}
    queue = []
    for fact in trip["facts"]:
        reasons = []
        stale = _parse(fact.get("stale_after"))
        if stale and stale <= now:
            reasons.append("stale")
        if fact["status"] == "conflict":
            reasons.append("conflict")
        elif fact["status"] == "unknown":
            reasons.append("unknown")
        if fact["subject_id"] in must_places:
            reasons.append("must_go")
        if fact["subject_id"] in locked_subjects or fact["fact_id"] in locked_facts:
            reasons.append("locked")
        if reasons:
            queue.append({
                "target_id": fact["fact_id"],
                "target_type": "fact",
                "reasons": list(dict.fromkeys(reasons)),
                "action": "重新读取适用于目标日期的一手来源并更新状态与时间",
            })
    for leg in trip.get("legs", []):
        if leg["evidence_level"] == "unknown":
            queue.append({"target_id": leg["leg_id"], "target_type": "leg", "reasons": ["unknown"], "action": "重新查询路线或保留宽松结构"})
    return queue


def with_queue(trip: dict, now: datetime | None = None) -> dict:
    """Return a candidate trip containing the queue, without mutating the source."""
    candidate = copy.deepcopy(trip)
    candidate["recheck_queue"] = build_queue(candidate, now)
    return candidate


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成出发前重查队列")
    parser.add_argument("trip")
    parser.add_argument("--out")
    parser.add_argument("--trip-out", help="写入带 recheck_queue 的候选 trip；不得覆盖源文件")
    args = parser.parse_args(argv)
    source = os.path.realpath(args.trip)
    targets = [os.path.realpath(p) for p in (args.out, args.trip_out) if p]
    if source in targets:
        parser.error("输出不得覆盖源 trip.json")
    if len(targets) != len(set(targets)):
        parser.error("--out 与 --trip-out 必须是不同文件")
    try:
        with open(args.trip, encoding="utf-8") as handle:
            trip = json.load(handle)
        queue = build_queue(trip)
        payload = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "queue": queue}
        if args.out:
            atomic_json(args.out, payload)
        if args.trip_out:
            candidate = copy.deepcopy(trip)
            candidate["recheck_queue"] = queue
            preflight(candidate)
            atomic_json(args.trip_out, candidate)
        if not args.out and not args.trip_out:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        print("✖ 无法生成重查队列", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
