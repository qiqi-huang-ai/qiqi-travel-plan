#!/usr/bin/env python3
"""Create privacy-scoped trip projections without changing the private source."""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys

from trip_support import atomic_json, preflight

PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
CN_ID = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
BOOKING_CODE = re.compile(r"(?i)(?P<label>\b(?:order|booking)\s*(?:id|no)?\.?|(?:订单|预订|票号|确认)(?:号|编号|码)?)\s*[:：#-]?\s*[A-Z0-9-]{6,}")


def _scrub_text(value, redact_terms=()):
    if not isinstance(value, str):
        return value
    value = EMAIL.sub("[已隐藏邮箱]", PHONE.sub("[已隐藏手机号]", value))
    value = CN_ID.sub("[已隐藏证件号]", value)
    value = BOOKING_CODE.sub(lambda m: f"{m.group('label')}:[已隐藏编号]", value)
    for term in redact_terms:
        if term:
            value = value.replace(term, "[已隐藏指定信息]")
    return value


def _walk(node, redact_terms=()):
    if isinstance(node, dict):
        return {k: _walk(v, redact_terms) for k, v in node.items()}
    if isinstance(node, list):
        return [_walk(v, redact_terms) for v in node]
    return _scrub_text(node, redact_terms)


def project(trip: dict, scope: str, redact_terms=None) -> dict:
    if scope not in ("delivery", "public"):
        raise ValueError("scope 只能是 delivery 或 public")
    redact_terms = tuple(x for x in (redact_terms or []) if isinstance(x, str) and x)
    out = _walk(copy.deepcopy(trip), redact_terms)
    out["privacy"] = {"classification": scope, "redacted_fields": []}
    if scope == "public":
        out["provider_results"] = []
        out["request"]["user_materials"] = []
        out["decisions"] = []
        for commitment in out["request"]["fixed_commitments"]:
            commitment["description"] = f"已锁定{commitment['kind']}安排"
        booked = out["itinerary"]["lodging"].get("booked")
        if booked:
            booked["name"] = "已预订住宿"
            booked["address"] = None
        for source in out["sources"]:
            if source["source_type"] == "user_material":
                source["url"] = None
                source["note"] = "私人资料已隐藏"
        out["privacy"]["redacted_fields"] = [
            "provider_results", "request.user_materials", "request.fixed_commitments.description",
            "itinerary.lodging.booked.name", "itinerary.lodging.booked.address", "sources.user_material",
            "decisions", "phone", "email", "identity_number", "booking_code",
        ]
        if redact_terms:
            out["privacy"]["redacted_fields"].append("exact_redactions")
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="导出隐私分层旅行数据")
    parser.add_argument("trip")
    parser.add_argument("--scope", choices=["delivery", "public"], required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--redact", action="append", default=[],
                        help="公开版需全局删除的精确文本（可重复，如旅客姓名）")
    args = parser.parse_args(argv)
    if args.trip == args.out:
        parser.error("导出文件不得覆盖私有源文件")
    try:
        with open(args.trip, encoding="utf-8") as handle:
            output = project(json.load(handle), args.scope, args.redact)
        preflight(output)
        atomic_json(args.out, output)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        print("✖ 隐私导出失败；源文件未修改", file=sys.stderr)
        return 1
    print(f"✔ 已生成 {args.scope} 投影：{args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
