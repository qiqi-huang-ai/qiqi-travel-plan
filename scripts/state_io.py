#!/usr/bin/env python3
"""Versioned trip state operations.

The current JSON is mutable; the ``versions/`` directory is append-only. Any
edit that changes a place, route, date, or fact creates a new version and a
recheck queue instead of silently carrying old evidence forward.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from trip_support import atomic_json, canonical_hash, checkpoint, configure_console, inside, preflight, secret_hits

TRIP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def atomic_write_json(path: str, data: dict) -> None:
    atomic_json(path, data)


def load_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def ensure_inside(base: str, path: str) -> str:
    return inside(base, path)


def skeleton(trip_id: str, title: str | None, destination: str | None) -> dict:
    """Create a schema-complete draft, with every unknown left explicit."""
    timestamp = now_iso()
    return {
        "schema_version": "1.1", "trip_id": trip_id, "plan_version": 1, "status": "draft",
        "user_confirmed": False, "research_mode": "standard", "generated_at": timestamp, "checked_at": None,
        "request": {
            "origin": {"name": None, "region": None},
            "dates": {"start": None, "end": None, "flexible": True, "nights": None, "return_by": None},
            "timezone": "Asia/Shanghai", "party": {"adults": 1, "children_ages": [], "seniors": None, "mobility_notes": None},
            "budget": {"mode": "unknown", "currency": "CNY", "amount_min": None, "amount_max": None, "includes": [], "paid_amount": None, "flexibility": None},
            "pace": {"early_start": None, "daily_hours": None, "walking": None, "rest_needs": None},
            "interests": {"like": [], "must": [], "optional": [], "avoid": []}, "fixed_commitments": [],
            "transport": {"mode": "unknown", "luggage": None, "notes": None}, "special_needs": [], "user_materials": [],
            "field_status": {field: "unknown" for field in ("origin", "dates", "party", "budget", "pace", "interests", "fixed_commitments", "transport")},
        },
        "capabilities": [], "provider_results": [], "recheck_queue": [], "privacy": {"classification": "private", "redacted_fields": []},
        "sources": [], "facts": [], "places": [], "legs": [],
        "itinerary": {
            "summary": {"title": title or f"{destination or '待定目的地'} 行程（草案）", "destination": destination or "待定", "tagline": None},
            "assumptions": [], "tradeoffs": [], "unmet": [], "plan_variants": [], "days": [],
            "lodging": {"strategy": "待定", "regions": [], "change_lodging": False, "booked": None}, "transport_major": [],
            "budget": {"currency": "CNY", "hard_limit": None, "categories": [], "note": None}, "alternatives": [], "checklist": [],
            "weather": {"kind": "unavailable", "entries": [], "note": None, "prep": []}, "risks": [], "sources_note": None,
        },
        "decisions": [], "changelog": [{"version": 1, "at": timestamp, "summary": "创建骨架", "affected_ids": []}],
    }


def cmd_init(args) -> int:
    if not TRIP_ID_RE.fullmatch(args.trip_id):
        print("✖ trip_id 只允许小写字母、数字、连字符，长度 3–64", file=sys.stderr)
        return 2
    data = skeleton(args.trip_id, args.title, args.destination)
    preflight(data)
    root = Path(os.path.abspath(args.dir))
    folder = Path(inside(root, root / args.trip_id))
    target = folder / "trip.json"
    if target.exists():
        print(f"✖ 已存在 {target}，不覆盖。", file=sys.stderr)
        return 1
    for name in ("versions", "outputs"):
        Path(inside(root, folder / name)).mkdir(parents=True, exist_ok=True)
    atomic_json(target, data)
    print(f"✔ 已创建 {target}（status=draft）")
    return 0


def cmd_checkpoint(args) -> int:
    source = Path(os.path.abspath(args.trip))
    target = checkpoint(str(source.parent), load_json(str(source)))
    print(f"✔ 已保留不可覆盖的快照：{target}")
    return 0


def invalidate_review(trip: dict, all_facts: bool = False) -> None:
    trip["checked_at"], trip["user_confirmed"], trip["status"] = None, False, "draft"
    if not all_facts:
        return
    for fact in trip.get("facts", []):
        if fact.get("status") == "verified":
            fact["status"] = "unknown"
            fact.setdefault("conflicts", []).append("版本恢复后待重查")
    for day in trip.get("itinerary", {}).get("days", []):
        day["weekday_check"] = False
        for item in day["items"]:
            if item.get("verification_status") == "verified":
                item["verification_status"] = "unknown"


def invalidate_changed_dependencies(original: dict, new: dict) -> set[str]:
    """Propagate edits through place → leg/fact → itinerary dependencies."""
    old_places = {x["place_id"]: x for x in original.get("places", [])}
    new_places = {x["place_id"]: x for x in new.get("places", [])}
    changed_places = {key for key in old_places.keys() | new_places.keys() if old_places.get(key) != new_places.get(key)}
    changed_legs = set()
    for leg in new.get("legs", []):
        if leg.get("from_id") in changed_places or leg.get("to_id") in changed_places:
            changed_legs.add(leg["leg_id"])
            leg.update({"evidence_level": "unknown", "time_min": None, "time_max": None, "retrieved_at": None, "source_ids": []})
            leg["note"] = (leg.get("note") + "；" if leg.get("note") else "") + "依赖地点已修改，路线待重查"
    changed_facts = set()
    for fact in new.get("facts", []):
        if fact.get("subject_id") in changed_places | changed_legs:
            changed_facts.add(fact["fact_id"])
            fact["status"] = "unknown"
            fact["stale_after"] = None
            fact.setdefault("conflicts", []).append("依赖对象已修改，待重查")
    for day in new.get("itinerary", {}).get("days", []):
        for item in day.get("items", []):
            if item.get("place_id") in changed_places or item.get("incoming_leg_id") in changed_legs or changed_facts.intersection(item.get("fact_ids", [])):
                item["verification_status"] = "unknown"
    affected = changed_places | changed_legs | changed_facts
    if affected:
        from recheck_trip import build_queue
        new["recheck_queue"] = build_queue(new)
    return affected


def list_versions(trip_dir: str) -> list[int]:
    directory = Path(ensure_inside(trip_dir, Path(trip_dir) / "versions"))
    if not directory.is_dir():
        return []
    return sorted(int(match.group(1)) for path in directory.iterdir() if (match := re.fullmatch(r"trip\.v(\d+)\.json", path.name)))


def cmd_save(args) -> int:
    path = Path(os.path.abspath(args.trip)); folder = path.parent
    current = load_json(str(path)); preflight(current)
    current_version = current["plan_version"]
    snapshot = folder / "versions" / f"trip.v{current_version}.json"
    if args.from_file:
        candidate_path = Path(ensure_inside(folder, os.path.abspath(args.from_file)))
        if candidate_path.resolve() == path.resolve():
            raise ValueError("--from 必须是独立修改稿")
        original, candidate = current, load_json(str(candidate_path))
        if snapshot.exists() and canonical_hash(load_json(str(snapshot))) != canonical_hash(current):
            raise ValueError("当前数据已被原地修改，不能冒充旧版")
    else:
        if not snapshot.exists():
            raise ValueError("找不到修改前快照，请先 checkpoint")
        original, candidate = load_json(str(snapshot)), copy.deepcopy(current)
    preflight(original); preflight(candidate)
    if (candidate.get("trip_id"), candidate.get("plan_version")) != (original.get("trip_id"), current_version):
        raise ValueError("修改稿的行程 ID 或基础版本不一致")
    candidate["plan_version"] = max([current_version, *list_versions(str(folder))]) + 1
    candidate["generated_at"] = now_iso()
    if original["request"]["dates"] != candidate["request"]["dates"]:
        invalidate_review(candidate, all_facts=True)
    affected = invalidate_changed_dependencies(original, candidate)
    supplied = {value.strip() for value in (args.affected or "").split(",") if value.strip()}
    candidate.setdefault("changelog", []).append({"version": candidate["plan_version"], "at": candidate["generated_at"], "summary": args.summary, "affected_ids": sorted(affected | supplied)})
    preflight(candidate)
    checkpoint(str(folder), original)
    atomic_json(path, candidate)
    print(f"✔ 原 v{current_version} 已保留；当前升级为 v{candidate['plan_version']}（待重新核查）")
    return 0


def flatten(value, prefix="$", out=None):
    out = {} if out is None else out
    if isinstance(value, dict):
        for key, child in value.items():
            flatten(child, f"{prefix}.{key}", out)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            marker = next((child[key] for key in ("item_id", "day_id", "place_id", "leg_id", "fact_id", "source_id", "alt_id", "check_id", "decision_id", "id") if isinstance(child, dict) and key in child), index)
            flatten(child, f"{prefix}[{marker}]", out)
    else:
        out[prefix] = value
    return out


def _load_version(folder: str, version: str):
    if version in ("cur", "current"):
        return load_json(ensure_inside(folder, Path(folder) / "trip.json"))
    candidate = Path(ensure_inside(folder, Path(folder) / "versions" / f"trip.v{int(version)}.json"))
    if candidate.exists():
        return load_json(str(candidate))
    current = load_json(ensure_inside(folder, Path(folder) / "trip.json"))
    if current.get("plan_version") == int(version):
        return current
    raise ValueError(f"没有版本 v{version}")


def cmd_versions(args) -> int:
    folder = os.path.abspath(args.trip_dir)
    current = load_json(ensure_inside(folder, Path(folder) / "trip.json")); preflight(current)
    print(f"当前：v{current['plan_version']} · {current.get('status')}")
    for version in list_versions(folder):
        entry = load_json(ensure_inside(folder, Path(folder) / "versions" / f"trip.v{version}.json")); preflight(entry)
        summary = next((x.get("summary", "") for x in entry.get("changelog", []) if x.get("version") == version), "")
        print(f"  v{version} · {entry.get('status')} · {entry.get('generated_at')} · {summary}")
    return 0


def cmd_diff(args) -> int:
    folder = os.path.abspath(args.trip_dir)
    left, right = flatten(_load_version(folder, args.va)), flatten(_load_version(folder, args.vb))
    added = sorted(set(right) - set(left)); removed = sorted(set(left) - set(right)); changed = sorted(key for key in left.keys() & right.keys() if left[key] != right[key] and not key.endswith((".generated_at", ".plan_version")))
    print(f"v{args.va} → v{args.vb}：新增 {len(added)}，删除 {len(removed)}，修改 {len(changed)}")
    for key in changed[:args.limit]: print(f"  ~ {key}: {left[key]!r} → {right[key]!r}")
    for key in added[:args.limit]: print(f"  + {key}: {right[key]!r}")
    for key in removed[:args.limit]: print(f"  - {key}: {left[key]!r}")
    return 0


def cmd_restore(args) -> int:
    folder = os.path.abspath(args.trip_dir); source = Path(ensure_inside(folder, Path(folder) / "versions" / f"trip.v{int(args.v)}.json"))
    if not source.exists():
        print(f"✖ 没有版本 v{args.v}", file=sys.stderr); return 1
    current_path = Path(ensure_inside(folder, Path(folder) / "trip.json")); current, old = load_json(str(current_path)), load_json(str(source))
    preflight(current); preflight(old)
    if old.get("trip_id") != current.get("trip_id") or old.get("plan_version") != int(args.v):
        raise ValueError("快照 ID/版本与恢复目标不一致")
    current_version = current["plan_version"]; current_snapshot = Path(ensure_inside(folder, Path(folder) / "versions" / f"trip.v{current_version}.json"))
    if current_snapshot.exists() and canonical_hash(load_json(str(current_snapshot))) != canonical_hash(current):
        raise ValueError("当前存在未保存修改，请先 save 后恢复")
    old["plan_version"] = max([current_version, *list_versions(folder)]) + 1
    old["generated_at"] = now_iso(); invalidate_review(old, all_facts=True)
    old["changelog"] = copy.deepcopy(current.get("changelog", []))
    old["changelog"].append({"version": old["plan_version"], "at": old["generated_at"], "summary": f"从 v{args.v} 恢复", "affected_ids": []})
    preflight(old); checkpoint(folder, current); atomic_json(current_path, old)
    print(f"✔ 已把 v{args.v} 恢复为当前 v{old['plan_version']}；旧核查状态已失效")
    return 0


def cmd_scan(args) -> int:
    folder = Path(os.path.abspath(args.trip_dir)); found = []
    for path in folder.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".json", ".md", ".html", ".txt", ".log"}:
            continue
        try: text = path.read_text(encoding="utf-8", errors="replace")
        except OSError: continue
        text = re.sub(r"\b[0-9a-f]{64}\b", "", text)
        if any(pattern.search(text) for pattern, _ in __import__("validate_trip").SECRET_PATTERNS):
            found.append(str(path.relative_to(folder)))
    if found:
        print("✖ 发现疑似凭据：\n  " + "\n  ".join(found)); return 1
    print("✔ 未发现疑似凭据。"); return 0


def main(argv=None) -> int:
    configure_console(); parser = argparse.ArgumentParser(description="travel-plan-pro 版本与依赖管理")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init"); p.add_argument("trip_id"); p.add_argument("--dir", default="travel-plan"); p.add_argument("--title"); p.add_argument("--destination"); p.set_defaults(fn=cmd_init)
    p = sub.add_parser("checkpoint"); p.add_argument("trip"); p.set_defaults(fn=cmd_checkpoint)
    p = sub.add_parser("save"); p.add_argument("trip"); p.add_argument("--summary", required=True); p.add_argument("--affected"); p.add_argument("--from", dest="from_file"); p.set_defaults(fn=cmd_save)
    p = sub.add_parser("versions"); p.add_argument("trip_dir"); p.set_defaults(fn=cmd_versions)
    p = sub.add_parser("diff"); p.add_argument("trip_dir"); p.add_argument("va"); p.add_argument("vb"); p.add_argument("--limit", type=int, default=40); p.set_defaults(fn=cmd_diff)
    p = sub.add_parser("restore"); p.add_argument("trip_dir"); p.add_argument("v"); p.set_defaults(fn=cmd_restore)
    p = sub.add_parser("scan"); p.add_argument("trip_dir"); p.set_defaults(fn=cmd_scan)
    args = parser.parse_args(argv)
    try: return args.fn(args)
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError):
        print("✖ 操作未完成：数据、快照或路径检查未通过；未覆盖历史快照。", file=sys.stderr); return 1


if __name__ == "__main__": sys.exit(main())
