#!/usr/bin/env python3
"""Evaluate the twelve product-level acceptance contracts for a trip bundle."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from trip_support import secret_hits
from validate_trip import Audit, check_outputs, semantic_checks


def _check(identifier, title, ok=None, detail=""):
    status = "manual" if ok is None else ("pass" if ok else "fail")
    return {"id": identifier, "title": title, "status": status, "detail": detail}


def evaluate(trip: dict, trip_path: str | None = None, manifest_path: str | None = None,
             behavior_results: dict[str, bool] | None = None) -> list[dict]:
    days = trip.get("itinerary", {}).get("days", [])
    items = [item for day in days for item in day.get("items", [])]
    places = {p["place_id"]: p for p in trip.get("places", [])}
    legs = {l["leg_id"]: l for l in trip.get("legs", [])}
    facts = {f["fact_id"]: f for f in trip.get("facts", [])}
    sources = {s["source_id"]: s for s in trip.get("sources", [])}

    a01 = all(
        i.get("planned_start") and i.get("planned_end")
        and (i.get("place_id") in places or i["kind"] in ("transit", "rest", "free", "buffer", "checkout", "transport_major"))
        and i.get("verification_status") in ("verified", "planned", "conditional", "estimate", "unknown", "blocked")
        for i in items
    )
    a02 = all(not i.get("incoming_leg_id") or i["incoming_leg_id"] in legs for i in items)
    unknown_budget = [c for c in trip.get("itinerary", {}).get("budget", {}).get("categories", []) if c.get("status") == "unknown"]
    a03 = all(c.get("min") is None and c.get("max") is None for c in unknown_budget)
    semantic_audit = Audit(trip)
    semantic_checks(trip, semantic_audit)
    semantic_blockers = [issue for issue in semantic_audit.issues if issue["severity"] == "blocking"]
    a04 = not semantic_blockers
    a05 = None
    a05_detail = "未提供 trip 文件路径，不能核对 manifest 与实际产物"
    if trip_path:
        output_audit = Audit(trip)
        check_outputs(trip, trip_path, manifest_path, output_audit)
        output_check = next((c for c in output_audit.checks if c["check_id"] in ("outputs", "outputs-check")), None)
        a05 = bool(output_check and output_check["result"] == "pass")
        a05_detail = output_check["detail"] if output_check else "产物一致性检查未执行"
    behavior_results = behavior_results or {}
    a06 = behavior_results.get("A06")
    a07 = behavior_results.get("A07")
    a08 = all(c.get("availability") != "unavailable" or c.get("fallback") for c in trip.get("capabilities", []))
    social_verified = []
    for fact in facts.values():
        fact_sources = [sources[s] for s in fact.get("source_ids", []) if s in sources]
        if fact.get("status") == "verified" and fact_sources and all(s.get("source_type") == "social" for s in fact_sources):
            social_verified.append(fact["fact_id"])
    a09 = not social_verified
    a11 = not secret_hits(trip)
    map_weather_unavailable = any(c.get("capability") in ("map_route", "weather") and c.get("availability") != "available" for c in trip.get("capabilities", []))
    a12 = bool(days) if map_weather_unavailable else True

    return [
        _check("A01", "主行程项地点、时间与证据状态完整", a01),
        _check("A02", "跨地点移动有可解析路段或未知说明", a02),
        _check("A03", "未知预算未被当作零", a03),
        _check("A04", "预约失败、闭馆、超预算与返程冲突会阻断", a04,
               "；".join(x["evidence"] for x in semantic_blockers[:3])),
        _check("A05", "Markdown 与 H5 来自同一业务数据", a05, a05_detail),
        _check("A06", "修改后可生成重查队列", a06,
               "由依赖失效行为测试判定" if a06 is not None else "未运行产品行为测试"),
        _check("A07", "历史版本可恢复且版本记录唯一", a07,
               "由不可变快照与恢复行为测试判定" if a07 is not None else "未运行产品行为测试"),
        _check("A08", "Provider 不可用时有明确降级", a08),
        _check("A09", "社媒体验不能单独成为 verified 规则", a09, ",".join(social_verified)),
        _check("A10", "macOS、Windows、Linux 完整测试", None, "由 CI 三平台矩阵判定"),
        _check("A11", "业务数据无疑似凭据", a11),
        _check("A12", "无地图或天气时仍可交付基础方案", a12),
    ]


def behavior_contracts() -> dict[str, bool]:
    tests = os.path.join(os.path.dirname(__file__), "tests")
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = os.path.dirname(__file__) + os.pathsep + env.get("PYTHONPATH", "")
    cases = {
        "A06": [
            "test_maturity.MaturityTests.test_place_change_invalidates_dependent_fact_leg_and_item",
            "test_maturity.MaturityTests.test_recheck_can_create_candidate_trip_without_mutating_source",
        ],
        "A07": [
            "test_core_contracts.CoreContractTests.test_checkpoint_rejects_changed_data_with_the_same_version",
            "test_core_contracts.CoreContractTests.test_restore_creates_a_new_version_and_invalidates_review_state",
        ],
    }
    return {
        identifier: subprocess.run(
            [sys.executable, "-m", "unittest", "-q", *names], cwd=tests, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        ).returncode == 0
        for identifier, names in cases.items()
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="检查 Travel Plan Pro 十二项验收合同")
    parser.add_argument("trip")
    parser.add_argument("--manifest", help="非默认位置的 manifest；默认读取 trip 同目录的当前版本 manifest")
    args = parser.parse_args(argv)
    try:
        with open(args.trip, encoding="utf-8") as handle:
            report = evaluate(json.load(handle), os.path.abspath(args.trip), args.manifest,
                              behavior_results=behavior_contracts())
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        print("✖ 无法生成验收报告", file=sys.stderr)
        return 2
    print(json.dumps({"checks": report}, ensure_ascii=False, indent=2))
    return 1 if any(x["status"] == "fail" for x in report) else 0


if __name__ == "__main__":
    sys.exit(main())
