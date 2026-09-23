#!/usr/bin/env python3
"""Group comparable route candidates and expose explainable recommendations."""
from __future__ import annotations

import argparse
import json
import sys

from trip_support import atomic_json, preflight


def _score(leg, objective):
    unknown = leg.get("evidence_level") == "unknown" or leg.get("time_max") is None
    if objective == "fast":
        return (unknown, leg.get("time_max") if leg.get("time_max") is not None else 10**9, leg.get("walking_min") or 0)
    if objective == "low_walk":
        return (unknown, leg.get("walking_min") if leg.get("walking_min") is not None else 10**9, leg.get("time_max") or 10**9)
    return (unknown, leg.get("transfers") if leg.get("transfers") is not None else 10**9, leg.get("time_max") or 10**9)


def build_matrix(legs: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for leg in legs:
        group_id = leg.get("route_group_id")
        if not group_id:
            continue
        groups.setdefault(group_id, []).append(leg)
    matrix = []
    for group_id, candidates in sorted(groups.items()):
        recommended = {}
        for objective in ("fast", "low_walk", "low_transfer"):
            recommended[objective] = min(candidates, key=lambda leg: _score(leg, objective))["leg_id"]
        matrix.append({
            "route_group_id": group_id,
            "from_id": candidates[0]["from_id"],
            "to_id": candidates[0]["to_id"],
            "candidates": candidates,
            "recommended": recommended,
        })
    return matrix


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成同起终点路线候选矩阵")
    parser.add_argument("trip")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    try:
        with open(args.trip, encoding="utf-8") as handle:
            trip = json.load(handle)
        preflight(trip)
        payload = {"trip_id": trip["trip_id"], "plan_version": trip["plan_version"], "route_matrix": build_matrix(trip.get("legs", []))}
        if args.out:
            atomic_json(args.out, payload)
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        print("✖ 无法生成路线矩阵", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
