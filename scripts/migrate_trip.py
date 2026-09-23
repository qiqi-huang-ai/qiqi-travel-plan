#!/usr/bin/env python3
"""Migrate saved trip bundles without silently discarding older data."""
from __future__ import annotations

import argparse
import copy
import json
import sys

from trip_support import atomic_json, preflight

LATEST_SCHEMA_VERSION = "1.1"


def migrate(trip: dict) -> dict:
    out = copy.deepcopy(trip)
    version = out.get("schema_version")
    if version not in ("1.0", LATEST_SCHEMA_VERSION):
        raise ValueError(f"不支持从 schema_version={version!r} 迁移")
    if version == "1.0":
        out["schema_version"] = LATEST_SCHEMA_VERSION
        out.setdefault("provider_results", [])
        out.setdefault("recheck_queue", [])
        out.setdefault("privacy", {"classification": "private", "redacted_fields": []})
        out.setdefault("itinerary", {}).setdefault("plan_variants", [])
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="travel-plan-pro Schema 迁移")
    parser.add_argument("trip")
    parser.add_argument("--out", required=True, help="迁移后的新文件；不得覆盖原文件")
    args = parser.parse_args(argv)
    if args.trip == args.out:
        parser.error("--out 不得覆盖原 trip.json")
    try:
        with open(args.trip, encoding="utf-8") as handle:
            migrated = migrate(json.load(handle))
        preflight(migrated)
        atomic_json(args.out, migrated)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        print("✖ 迁移失败；原文件未修改", file=sys.stderr)
        return 1
    print(f"✔ 已迁移到 schema {LATEST_SCHEMA_VERSION}：{args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
