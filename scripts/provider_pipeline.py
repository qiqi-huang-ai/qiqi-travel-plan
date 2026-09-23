#!/usr/bin/env python3
"""Apply one normalized provider result to a new candidate trip file."""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys

from trip_support import atomic_json, preflight


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-") or "provider"


def _source(trip: dict, result: dict, target_id: str | None) -> str:
    base = f"src-{_slug(result['provider'])}-{_slug(result['capability'])}-{_slug(target_id or 'trip')}"
    existing = {x["source_id"] for x in trip["sources"]}
    source_id = base
    counter = 2
    while source_id in existing:
        source_id = f"{base}-{counter}"
        counter += 1
    trip["sources"].append({
        "source_id": source_id,
        "url": result.get("source_url"),
        "title": f"{result['provider']} {result['capability']} 查询结果",
        "publisher": result["provider"],
        "published_at": None,
        "retrieved_at": result.get("retrieved_at"),
        "source_type": "social" if result["capability"] in ("social_experience", "tikhub") else ("web" if result["capability"] == "weather" else "map_tool"),
        "note": "由 Provider 适配器返回；仅适用于本次查询语境",
    })
    return source_id


def _weather_summary(item: dict) -> str:
    parts = []
    if item.get("temp_min") is not None and item.get("temp_max") is not None:
        parts.append(f"{item['temp_min']}–{item['temp_max']}℃")
    if item.get("precipitation_probability_max") is not None:
        parts.append(f"最高降水概率 {item['precipitation_probability_max']}%")
    if item.get("weather_code") is not None:
        parts.append(f"天气代码 {item['weather_code']}")
    return "；".join(parts) or "Provider 返回预报，详情未解析"


def apply_result(trip: dict, result: dict, target_id: str | None = None) -> dict:
    preflight(trip)
    if result.get("availability") != "available" or not result.get("items"):
        raise ValueError("Provider 结果不可用或没有数据，不得写入已验证字段")
    out = copy.deepcopy(trip)
    out.setdefault("provider_results", []).append(copy.deepcopy(result))
    source_id = _source(out, result, target_id)
    capability = result["capability"]
    item = result["items"][0]

    if capability in ("geocode", "place_search"):
        if not target_id:
            raise ValueError("地理结果需要 --target-id 指向 PlaceRecord")
        target = next((p for p in out["places"] if p["place_id"] == target_id), None)
        if not target:
            raise ValueError("找不到目标地点")
        location = item.get("location")
        if location:
            target["coords"] = {"lat": location["lat"], "lng": location["lng"]}
        if item.get("address"):
            target["address"] = item["address"]
    elif capability == "route":
        if not target_id:
            raise ValueError("路线结果需要 --target-id 指向 TravelLeg")
        target = next((leg for leg in out["legs"] if leg["leg_id"] == target_id), None)
        if not target:
            raise ValueError("找不到目标路段")
        minutes = item.get("duration_min")
        if minutes is None:
            raise ValueError("路线结果缺少 duration_min")
        mode = {"transit":"公交", "driving":"驾车", "walking":"步行"}.get(
            item.get("mode"), item.get("mode") or target["mode"])
        target.update({
            "mode": mode,
            "provider": result["provider"],
            "time_min": minutes,
            "time_max": minutes,
            "transfers": item.get("transfers"),
            "walking_min": item.get("walking_min"),
            "evidence_level": "tool_route",
            "retrieved_at": result.get("retrieved_at"),
            "source_ids": [source_id],
        })
    elif capability == "weather":
        entries = [{"date": x["date"], "summary": _weather_summary(x), "source_id": source_id}
                   for x in result["items"]]
        out["itinerary"]["weather"] = {
            "kind": "forecast", "entries": entries,
            "note": f"{result['provider']} 预报，查询于 {result.get('retrieved_at') or '未记录'}",
            "prep": out["itinerary"]["weather"].get("prep", []),
        }
    elif capability in ("social_experience", "tikhub"):
        subject_id = target_id or trip["trip_id"]
        existing_fact_ids = {fact["fact_id"] for fact in out["facts"]}
        for index, item in enumerate(result["items"], 1):
            title = item.get("title") or "未命名笔记"
            excerpt = (item.get("excerpt") or "").strip()
            claim = f"小红书体验线索：{title}"
            if excerpt:
                claim += f"；{excerpt}"
            fact_id = f"fact-tikhub-{_slug(item.get('note_id') or str(index))}"
            suffix = 2
            while fact_id in existing_fact_ids:
                fact_id = f"fact-tikhub-{_slug(item.get('note_id') or str(index))}-{suffix}"
                suffix += 1
            existing_fact_ids.add(fact_id)
            out["facts"].append({
                "fact_id": fact_id,
                "subject_id": subject_id,
                "subject_field": "social_experience",
                "claim": claim[:500],
                "valid_for": None,
                "status": "experience",
                "source_ids": [source_id],
                "excerpt": excerpt[:300] or None,
                "published_at": item.get("published_at"),
                "retrieved_at": result.get("retrieved_at"),
                "stale_after": None,
                "conflicts": [],
                "method": "tool",
            })
    else:
        raise ValueError(f"尚不支持自动应用 capability={capability}")
    preflight(out)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="将标准化 Provider 结果写入新的候选行程")
    parser.add_argument("trip")
    parser.add_argument("result")
    parser.add_argument("--target-id")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if os.path.realpath(args.trip) == os.path.realpath(args.out):
        parser.error("--out 不得覆盖私有 trip.json")
    try:
        with open(args.trip, encoding="utf-8") as handle:
            trip = json.load(handle)
        with open(args.result, encoding="utf-8") as handle:
            result = json.load(handle)
        candidate = apply_result(trip, result, args.target_id)
        atomic_json(args.out, candidate)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        print("✖ Provider 结果应用失败；源文件未修改", file=sys.stderr)
        return 1
    print(f"✔ 已生成 Provider 候选行程：{args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
