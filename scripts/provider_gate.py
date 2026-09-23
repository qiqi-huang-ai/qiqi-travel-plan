#!/usr/bin/env python3
"""Record the user's provider choice before a trip enters research."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime

from trip_support import atomic_json, preflight


CHOICES = {
    "connect": {
        "label": "接入已有安全配置",
        "availability": "unknown",
        "permission": "granted",
        "test_result": "已选择接入；等待本机安全配置检查",
        "note": "不会在聊天中索要或保存 API Key；只有安全配置实际可用后才发起请求。",
    },
    "configure": {
        "label": "接入，但需要配置指导",
        "availability": "unavailable",
        "permission": "granted",
        "test_result": "已选择接入；等待本机 TIKHUB_API_KEY 配置",
        "note": "先完成本机安全配置，再 dry-run，再由用户确认 live 请求和预算。",
    },
    "decline": {
        "label": "本次不接入",
        "availability": "unavailable",
        "permission": "denied",
        "test_result": "用户明确选择不接入社媒资料",
        "note": "继续普通资料规划；排队、亲子体验和近期变化线索覆盖会更少。",
    },
}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def choose(trip: dict, choice: str, budget: int | None) -> dict:
    if choice not in CHOICES:
        raise ValueError(f"不支持的 provider choice: {choice}")
    if choice != "decline" and budget is None:
        raise ValueError("接入 TikHub 时必须明确本次请求次数上限")
    if budget is not None and (budget < 1 or budget > 1000):
        raise ValueError("TikHub 请求上限必须在 1–1000 之间")
    preflight(trip)
    out = copy.deepcopy(trip)
    spec = CHOICES[choice]
    decisions = [d for d in out.get("decisions", []) if d.get("topic") != "provider_onboarding:tikhub"]
    decisions.append({
        "decision_id": "decision-tikhub-onboarding",
        "topic": "provider_onboarding:tikhub",
        "options": [item["label"] for item in CHOICES.values()],
        "user_choice": spec["label"],
        "time": _now(),
        "affected_ids": ["capability:tikhub", "research:social-experience"],
        "note": (f"请求预算上限 {budget} 次。" if budget is not None else "本次不设置 TikHub 请求预算，不会发起请求。") + spec["note"],
    })
    out["decisions"] = decisions
    capabilities = [cap for cap in out.get("capabilities", []) if cap.get("capability") != "tikhub"]
    capabilities.append({
        "capability": "tikhub",
        "availability": spec["availability"],
        "permission": spec["permission"],
        "tool_id": "tikhub_xhs",
        "verified_at": None,
        "test_result": spec["test_result"],
        "fallback": "官方与公开网页；社媒体验线索保持 unknown",
    })
    out["capabilities"] = capabilities
    preflight(out)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="在旅行规划前记录 TikHub 接入选择")
    parser.add_argument("trip")
    parser.add_argument("--choice", choices=sorted(CHOICES), required=True,
                        help="connect=已有安全配置；configure=需要配置指导；decline=本次不接入")
    parser.add_argument("--budget", type=int, default=None, help="接入时必填：本次 TikHub 请求硬上限（1–1000）")
    parser.add_argument("--out", required=True, help="候选 trip.json；不得覆盖源文件")
    args = parser.parse_args(argv)
    try:
        if args.trip == args.out:
            raise ValueError("--out 不得覆盖源文件")
        with open(args.trip, encoding="utf-8") as handle:
            trip = json.load(handle)
        atomic_json(args.out, choose(trip, args.choice, args.budget))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"✖ provider 选择未写入：{error}", file=sys.stderr)
        return 1
    print(f"✔ 已记录 TikHub 选择 {args.choice}，候选行程：{args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
