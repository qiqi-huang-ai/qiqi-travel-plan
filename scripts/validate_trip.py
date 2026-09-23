#!/usr/bin/env python3
"""Deterministic contract checker for a travel-plan-pro bundle.

The checker deliberately reports evidence and a repair action for every
problem. It does not pretend that a populated field is proof of a fact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from trip_support import atomic_json, budget_limit, canonical_hash, configure_console, inside, secret_hits

SCRIPT_VERSION = "2.0.0"
HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
SCHEMA_PATH = str(SKILL_ROOT / "assets" / "schemas" / "trip.schema.json")
ATTRIBUTION_PATH = str(SKILL_ROOT / "assets" / "templates" / "attribution.json")
WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

SECRET_PATTERNS = [
    (re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|secret|password|passwd|token)\b\s*[:=]\s*['\"]?[A-Za-z0-9_.-]{12,}"), "疑似键值形式的凭据"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9_.=-]{16,}", re.I), "疑似 Bearer 令牌"),
    (re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"), "疑似 sk- 开头的密钥"),
    (re.compile(r"\b[A-Fa-f0-9]{40,}\b"), "疑似长十六进制令牌"),
    (re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "疑似 18 位证件号码"),
    (re.compile(r"(?<!\d)\d{16,19}(?!\d)"), "疑似银行卡号"),
]
BAD_SCHEME = re.compile(r"(?i)^\s*(?:javascript|data|file|vbscript):")


class MiniSchema:
    """A dependency-free subset of JSON Schema used by the bundled contract."""

    def __init__(self, schema: dict):
        self.root = schema
        self.errors: list[str] = []

    def _resolve(self, ref: str) -> dict:
        node = self.root
        for part in ref.removeprefix("#/").split("/"):
            node = node[part]
        return node

    @staticmethod
    def _valid_type(value, expected: str) -> bool:
        return {
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value),
            "boolean": isinstance(value, bool),
            "null": value is None,
        }.get(expected, True)

    def check(self, value, schema: dict, path: str = "$") -> bool:
        if "$ref" in schema:
            return self.check(value, self._resolve(schema["$ref"]), path)
        ok = True
        if "const" in schema and value != schema["const"]:
            self.errors.append(f"{path}: 应为常量 {schema['const']!r}，实际 {value!r}")
            ok = False
        if "enum" in schema and value not in schema["enum"]:
            self.errors.append(f"{path}: 值不在允许范围内")
            ok = False
        if "type" in schema:
            types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
            if not any(self._valid_type(value, kind) for kind in types):
                self.errors.append(f"{path}: 类型不匹配")
                return False
        if "anyOf" in schema:
            alternatives = []
            for branch in schema["anyOf"]:
                previous = self.errors
                self.errors = []
                alternatives.append(self.check(value, branch, path))
                self.errors = previous
            if not any(alternatives):
                self.errors.append(f"{path}: 不满足任一候选结构")
                ok = False
        if isinstance(value, str) and schema.get("pattern") and not re.search(schema["pattern"], value):
            self.errors.append(f"{path}: 字符串格式不符合要求")
            ok = False
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                self.errors.append(f"{path}: 小于允许的最小值")
                ok = False
            if "maximum" in schema and value > schema["maximum"]:
                self.errors.append(f"{path}: 大于允许的最大值")
                ok = False
        if isinstance(value, list):
            if len(value) < schema.get("minItems", 0):
                self.errors.append(f"{path}: 项目数量不足")
                ok = False
            item_schema = schema.get("items")
            if item_schema:
                for index, child in enumerate(value):
                    ok = self.check(child, item_schema, f"{path}[{index}]") and ok
        if isinstance(value, dict):
            for required in schema.get("required", []):
                if required not in value:
                    self.errors.append(f"{path}: 缺少字段 {required!r}")
                    ok = False
            properties = schema.get("properties", {})
            for key, child in value.items():
                if key in properties:
                    ok = self.check(child, properties[key], f"{path}.{key}") and ok
                elif schema.get("additionalProperties") is False:
                    self.errors.append(f"{path}: 不允许的字段 {key!r}")
                    ok = False
                elif isinstance(schema.get("additionalProperties"), dict):
                    ok = self.check(child, schema["additionalProperties"], f"{path}.{key}") and ok
        return ok


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def hhmm_on(day: date, value: str, tzinfo) -> datetime:
    hour, minute = (int(piece) for piece in value.split(":", 1))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tzinfo)


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def walk_strings(node, path="$"):
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from walk_strings(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk_strings(value, f"{path}[{index}]")


class Audit:
    def __init__(self, trip: dict):
        self.trip = trip
        self.checks: list[dict] = []
        self.issues: list[dict] = []

    def check(self, check_id: str, dimension: str, result: str, detail: str, method: str = "script") -> None:
        self.checks.append({"check_id": check_id, "dimension": dimension, "method": method, "result": result, "detail": detail})

    def issue(self, severity: str, affected: list[str], evidence: str, fix: str) -> None:
        self.issues.append({
            "issue_id": f"issue-{len(self.issues) + 1:03d}",
            "severity": severity,
            "affected_ids": affected,
            "evidence": evidence,
            "fix_action": fix,
            "resolved": False,
        })

    def summary(self) -> dict:
        result = {"blocking": 0, "conditional": 0, "info": 0}
        for issue in self.issues:
            result[issue["severity"]] = result.get(issue["severity"], 0) + 1
        return result

    def recommended_status(self) -> str:
        totals = self.summary()
        dates = self.trip.get("request", {}).get("dates", {})
        if not dates.get("start") or not (self.trip.get("request", {}).get("origin") or {}).get("name"):
            return "draft"
        if totals["blocking"]:
            return "draft"
        return "conditional" if totals["conditional"] else "executable_as_of_check"

    def to_json(self) -> dict:
        return {"trip_id": self.trip.get("trip_id"), "plan_version": self.trip.get("plan_version"),
                "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "script_version": SCRIPT_VERSION, "checks": self.checks, "issues": self.issues,
                "summary": self.summary(), "recommended_status": self.recommended_status()}


def _index(trip: dict):
    itinerary = trip["itinerary"]
    places = {x["place_id"]: x for x in trip["places"]}
    facts = {x["fact_id"]: x for x in trip["facts"]}
    sources = {x["source_id"]: x for x in trip["sources"]}
    legs = {x["leg_id"]: x for x in trip["legs"]}
    alternatives = {x["alt_id"]: x for x in itinerary["alternatives"]}
    days = {x["day_id"]: x for x in itinerary["days"]}
    items = {item["item_id"]: item for day in itinerary["days"] for item in day["items"]}
    known = set(places) | set(facts) | set(sources) | set(legs) | set(alternatives) | set(days) | set(items)
    return places, facts, sources, legs, alternatives, days, items, known


def _duplicates(values, key):
    seen, duplicate = set(), []
    for value in values:
        current = value.get(key)
        if current in seen:
            duplicate.append(current)
        seen.add(current)
    return duplicate


def semantic_checks(trip: dict, audit: Audit) -> None:
    request, itinerary = trip["request"], trip["itinerary"]
    places, facts, sources, legs, alternatives, days, items, known = _index(trip)
    if not itinerary["days"]:
        audit.issue("blocking" if trip.get("status") != "draft" else "conditional", [], "行程没有任何日期", "补充需求后再生成每日计划")
    if not trip.get("checked_at"):
        audit.issue("conditional", [], "事实核查时间为空", "完成来源逐项核查后记录 checked_at")

    for collection, key in ((trip["places"], "place_id"), (trip["facts"], "fact_id"), (trip["sources"], "source_id"),
                            (trip["legs"], "leg_id"), (itinerary["days"], "day_id"), (list(items.values()), "item_id")):
        duplicates = _duplicates(collection, key)
        if duplicates:
            audit.issue("blocking", duplicates, f"{key} 出现重复值", "为每个对象分配唯一 ID")

    selected = [v for v in itinerary.get("plan_variants", []) if v.get("selected")]
    if len(selected) > 1:
        audit.issue("blocking", [v["variant_id"] for v in selected], "方案变体只能选中一个", "只保留一个 selected=true")
    elif itinerary.get("plan_variants") and not selected:
        audit.issue("conditional", [v["variant_id"] for v in itinerary["plan_variants"]], "存在方案变体但没有主方案", "和用户确认后选择一个主方案")

    dangling = []
    for place in places.values():
        dangling.extend(f"{place['place_id']} → {fact}" for fact in place.get("fact_ids", []) if fact not in facts)
    for fact in facts.values():
        dangling.extend(f"{fact['fact_id']} → {source}" for source in fact.get("source_ids", []) if source not in sources)
        if fact.get("subject_id") not in known:
            audit.issue("info", [fact["fact_id"]], "事实 subject_id 无法对应对象", "改为已存在的地点、路段或行程 ID")
        if fact.get("status") == "verified" and not fact.get("source_ids"):
            audit.issue("blocking", [fact["fact_id"]], "verified 事实没有来源", "补来源或降级为 unknown")
        stale = parse_dt(fact.get("stale_after"))
        if stale and stale <= datetime.now(stale.tzinfo):
            audit.issue("conditional", [fact["fact_id"]], "事实已过期，超过 stale_after", "重新核查后更新有效期")
    for leg in legs.values():
        for endpoint in (leg["from_id"], leg["to_id"]):
            if endpoint not in places:
                dangling.append(f"{leg['leg_id']} → place {endpoint}")
        dangling.extend(f"{leg['leg_id']} → source {source}" for source in leg.get("source_ids", []) if source not in sources)
        if leg.get("evidence_level") == "unknown" and (leg.get("time_min") is not None or leg.get("time_max") is not None):
            audit.issue("blocking", [leg["leg_id"]], "未知路段不能携带看似精确的时间", "删除时间或补充路线依据")
        if leg.get("evidence_level") != "unknown":
            if leg.get("time_min") is None or leg.get("time_max") is None:
                audit.issue("blocking", [leg["leg_id"]], "有证据等级的路段缺时间范围", "补充上下界或改为 unknown")
            elif leg["time_min"] > leg["time_max"]:
                audit.issue("blocking", [leg["leg_id"]], "路段时间下界大于上界", "修正 time_min/time_max")
    for item in items.values():
        if item.get("place_id") and item["place_id"] not in places:
            dangling.append(f"{item['item_id']} → place {item['place_id']}")
        dangling.extend(f"{item['item_id']} → fact {fact}" for fact in item.get("fact_ids", []) if fact not in facts)
        if item.get("incoming_leg_id") and item["incoming_leg_id"] not in legs:
            dangling.append(f"{item['item_id']} → leg {item['incoming_leg_id']}")
        if item.get("alternative_id") and item["alternative_id"] not in alternatives:
            dangling.append(f"{item['item_id']} → alternative {item['alternative_id']}")
    if dangling:
        audit.issue("blocking", [], "存在悬空引用：" + "; ".join(dangling[:8]), "补齐对象或改正 ID")
        audit.check("references", "ID 引用", "fail", f"发现 {len(dangling)} 处悬空引用")
    else:
        audit.check("references", "ID 引用", "pass", "所有主要引用均可解析")

    start, end = parse_date(request["dates"].get("start")), parse_date(request["dates"].get("end"))
    last_dates = []
    for day in itinerary["days"]:
        day_date = parse_date(day.get("date"))
        if not day_date:
            audit.issue("blocking", [day["day_id"]], "日程日期无法解析", "使用 YYYY-MM-DD")
            continue
        last_dates.append(day_date)
        if start and day_date < start or end and day_date > end:
            audit.issue("conditional", [day["day_id"]], "日程日期超出旅行范围", "调整日期或更新需求")
        if not day.get("weekday_check"):
            audit.issue("conditional", [day["day_id"]], f"尚未确认 {day_date} 是 {WEEKDAYS[day_date.weekday()]}", "核对日期和开放规则")
        parsed = []
        for item in day["items"]:
            begin, finish = parse_dt(item.get("planned_start")), parse_dt(item.get("planned_end"))
            parsed.append((begin, finish))
            if not begin or not finish:
                audit.issue("blocking", [item["item_id"]], "时间缺失或不是带时区的 ISO 8601", "补完整时间")
            elif finish <= begin:
                audit.issue("blocking", [item["item_id"]], "结束时间不晚于开始时间", "修正时间区间")
        for index in range(1, len(day["items"])):
            previous, current = parsed[index - 1], parsed[index]
            if previous[1] and current[0] and current[0] < previous[1]:
                audit.issue("blocking", [day["items"][index - 1]["item_id"], day["items"][index]["item_id"]], "同日项目时间重叠", "调整顺序或增加时长")
            item = day["items"][index]
            leg = legs.get(item.get("incoming_leg_id"))
            if leg and leg.get("evidence_level") == "unknown":
                if item.get("verification_status") == "verified":
                    audit.issue("blocking", [item["item_id"], leg["leg_id"]], "已核实项目依赖未知路段", "降级项目或补路线证据")
                else:
                    audit.issue("conditional", [item["item_id"], leg["leg_id"]], "到达路段没有时间依据", "补路线资料或保留复查动作")
            elif leg and current[0] and previous[1]:
                required = timedelta(minutes=leg.get("time_max") or 0)
                same_transit = day["items"][index - 1].get("kind") == "transit" and day["items"][index - 1].get("incoming_leg_id") == leg["leg_id"]
                if same_transit:
                    pass
                elif item.get("kind") == "transit" and current[1] and current[1] - current[0] < required:
                    audit.issue("blocking", [item["item_id"], leg["leg_id"]], "交通项时长短于路线时间上界", "按上界留时")
                elif item.get("kind") != "transit" and current[0] < previous[1] + required:
                    audit.issue("blocking", [item["item_id"], leg["leg_id"]], "活动开始前未留足路段时间", "推迟活动或加入交通项")
        _check_day_rules(day, parsed, places, facts, alternatives, audit)

    _check_commitments(request, items, audit)
    _check_must_go(request, items, itinerary, audit)
    _check_budget(request, itinerary, items, audit)
    _check_weather(itinerary, audit)
    todo = [x for x in itinerary["checklist"] if x.get("priority") == "must" and x.get("status") == "todo"]
    for entry in todo:
        if not entry.get("deadline") or not entry.get("if_unresolved"):
            audit.issue("conditional", [entry["check_id"]], "出发前必做项缺截止时间或失败动作", "补齐 deadline 与 if_unresolved")
    if todo:
        audit.issue("conditional", [x["check_id"] for x in todo], "仍有出发前必做项未完成", "完成或在交付中明确截止时间")

    if trip.get("status") == "executable_as_of_check" and (audit.summary()["blocking"] or audit.summary()["conditional"]):
        audit.issue("blocking", [], "方案标为可执行但仍有问题", "改为 conditional/draft 或先修复问题")
    if trip.get("user_confirmed") and any(x.get("booking_status") in ("pending_user", "not_open") for x in items.values()):
        audit.check("confirmation", "状态一致性", "pass", "用户确认与预约状态分别保留，没有自动冒充已预约")

    bad_values = []
    for path, text in walk_strings(trip):
        if any(pattern.search(text) for pattern, _ in SECRET_PATTERNS) or BAD_SCHEME.match(text):
            bad_values.append(path)
        if path.endswith((".url", ".nav_link")) and text and not text.lower().startswith(("http://", "https://")):
            bad_values.append(path)
    if bad_values:
        audit.issue("blocking", [], f"发现 {len(bad_values)} 个敏感信息或危险链接字段", "移除凭据/证件号，仅保留安全 http(s) 链接")
        audit.check("safety", "安全扫描", "fail", "存在需要清理的字段")
    else:
        audit.check("safety", "安全扫描", "pass", "未发现凭据或危险协议")
    for check_id, dimension, detail in (
        ("model-evidence", "证据", "来源是否真的支持事实且覆盖目标日期"),
        ("model-route", "路线", "路线是否顺路，首末日衔接是否合理"),
        ("model-copy", "输出", "活动说明是否解释安排理由和执行方式"),
        ("model-social", "风险", "社媒样本是否被过度概括"),
        ("model-alternatives", "备选", "替代项是否也满足时间、开放、费用与移动条件"),
    ):
        audit.check(check_id, dimension, "skip", detail, method="model")


def _check_day_rules(day, parsed, places, facts, alternatives, audit):
    for index, item in enumerate(day["items"]):
        if item.get("kind") in ("transit", "transport_major"):
            if item.get("admission_item_id"):
                audit.issue("blocking", [item["item_id"]], "交通项不能复用场所入场资格", "移除 admission_item_id")
            continue
        place = places.get(item.get("place_id"))
        if item.get("kind") in ("visit", "meal", "checkin") and not place:
            audit.issue("blocking", [item["item_id"]], "活动缺少实际发生地点", "补 place_id")
            continue
        if not place:
            continue
        if item.get("kind") == "visit" and not item.get("fact_ids"):
            audit.issue("conditional", [item["item_id"]], "游览项没有挂接事实依据", "补开放/入场 FactRecord")
        booking = (place.get("booking") or {}).get("required")
        status = item.get("booking_status")
        if booking is True and status == "not_required":
            audit.issue("blocking", [item["item_id"]], "地点需要预约但行程写成无需预约", "核对地点或预约状态")
        if booking is True and status in ("pending_user", "not_open", "unknown", "user_claimed"):
            audit.issue("conditional", [item["item_id"]], "地点预约尚未确认", "写明渠道、截止时间、失败替代")
            if item.get("verification_status") == "verified":
                audit.issue("blocking", [item["item_id"]], "预约未确认却标记 verified", "降级为 conditional")
        if status == "failed":
            audit.issue("blocking", [item["item_id"]], "预约失败仍在主行程", "切换到经过核查的替代")
        if item.get("verification_status") == "verified":
            missing = [fact for fact in item.get("fact_ids", []) if facts.get(fact, {}).get("status") != "verified"]
            if missing:
                audit.issue("blocking", [item["item_id"], *missing], "行程已核实但依赖事实未核实", "补来源或降级")
        windows = place.get("opening_windows") or []
        if not windows:
            if item.get("kind") == "visit" and item.get("verification_status") == "verified":
                audit.issue("blocking", [item["item_id"]], "已核实游览项没有开放窗口", "补窗口或降级")
            else:
                audit.issue("info", [item["item_id"]], "开放窗口未知", "查到后补 opening_windows")
            continue
        begin, finish = parsed[index]
        if not begin or not finish:
            continue
        applicable = [window for window in windows if (window.get("dates") and day["date"] in window["dates"]) or (window.get("days") and begin.isoweekday() in window["days"]) or (not window.get("dates") and not window.get("days"))]
        if not applicable:
            audit.issue("blocking", [item["item_id"]], "当天没有适用开放窗口", "换日、换点或补日期特别开放依据")
            continue
        if not any(begin >= hhmm_on(parse_date(day["date"]), w["open"], begin.tzinfo) and finish <= hhmm_on(parse_date(day["date"]), w["close"], begin.tzinfo) for w in applicable):
            audit.issue("blocking", [item["item_id"]], "计划时段不落在开放窗口内", "调整到完整可用时段")


def _check_commitments(request, items, audit):
    for commitment in request.get("fixed_commitments", []):
        if commitment.get("status") != "confirmed":
            continue
        begin = parse_dt(commitment.get("start"))
        matches = [item for item in items.values() if item.get("locked") and parse_dt(item.get("planned_start")) == begin]
        if not matches:
            audit.issue("blocking", [commitment["id"]], "已确认锁定安排没有对应 locked 行程项", "放回主行程且不得擅自修改")


def _check_must_go(request, items, itinerary, audit):
    present_places = {item.get("place_id") for item in items.values()}
    titles = " ".join(item.get("title", "") for item in items.values())
    for must in request.get("interests", {}).get("must", []):
        present = (must.get("place_id") in present_places) if must.get("place_id") else must.get("label", "") in titles
        if not present:
            if any(must.get("label", "") in text for text in itinerary.get("unmet", [])):
                audit.issue("conditional", [must.get("place_id") or must.get("label", "")], "必去项未排入但已记录 unmet", "与用户确认取舍")
            else:
                audit.issue("blocking", [must.get("place_id") or must.get("label", "")], "必去项未排入也未解释", "排入或记录冲突")


def _check_budget(request, itinerary, items, audit):
    budget = itinerary["budget"]
    paid = sum(float(category.get("paid") or 0) for category in budget.get("categories", []))
    minimum, maximum, unknown = paid, paid, []
    for category in budget.get("categories", []):
        if category.get("status") == "unknown":
            unknown.append(category.get("name", "未命名"))
            if category.get("min") is not None or category.get("max") is not None:
                audit.issue("info", [], "未知预算类别不应有数字", "改为 null")
            continue
        low, high = category.get("min"), category.get("max")
        if low is None or high is None or low > high:
            audit.issue("blocking", [], "预算类别缺少合法范围", "补 min/max 或改为 unknown")
            continue
        minimum += low
        maximum += high
    limit = budget_limit({"itinerary": itinerary, "request": request})
    if limit is None:
        audit.issue("conditional", [], "预算上限或人数口径未能确定", "明确 currency、mode、budget_persons 和 amount_max")
    elif minimum > limit:
        audit.issue("blocking", [], f"预算下界 {minimum:g} 已超过上限 {limit:g}", "删减可选项或调整预算")
    elif maximum > limit:
        audit.issue("conditional", [], f"预算上界 {maximum:g} 超过上限 {limit:g}", "列出可删减项并提示风险")
    if unknown:
        audit.issue("conditional", [], "预算含未知类别：" + ", ".join(unknown), "交付中单独列出未知费用")
    audit.check("budget", "预算", "pass", f"已付 {paid:g}，预计总额 {minimum:g}–{maximum:g} {budget.get('currency', '')}")


def _check_weather(itinerary, audit):
    weather = itinerary.get("weather", {})
    if weather.get("kind") == "forecast" and any(not entry.get("source_id") for entry in weather.get("entries", [])):
        audit.issue("conditional", [], "天气预报条目缺来源", "补 provider 来源或降级 seasonal")
    if weather.get("kind") == "seasonal" and any(re.search(r"\d+\s*[°℃]", entry.get("summary", "")) for entry in weather.get("entries", [])):
        audit.issue("conditional", [], "季节参考含精确温度，容易被误读为预报", "改为区间与准备建议")


def check_outputs(trip: dict, trip_path: str, manifest_path: str | None, audit: Audit) -> None:
    from render_outputs import Ctx, coverage, html_to_text, load_attribution
    base = os.path.dirname(os.path.abspath(trip_path))
    try:
        manifest = manifest_path or os.path.join(base, f"manifest.v{trip['plan_version']}.json")
        manifest = inside(base, manifest)
        data = json.loads(Path(manifest).read_text(encoding="utf-8"))
        schema = json.loads(Path(SKILL_ROOT / "assets" / "schemas" / "manifest.schema.json").read_text(encoding="utf-8"))
        checker = MiniSchema(schema)
        if not checker.check(data, schema):
            raise ValueError("manifest 结构错误")
        if (data.get("trip_id"), data.get("plan_version")) != (trip["trip_id"], trip["plan_version"]):
            raise ValueError("manifest 与当前数据版本不一致")
        if data.get("content_hash") != canonical_hash(trip):
            raise ValueError("产物不是当前 trip.json 的渲染结果")
        context = Ctx(trip)
        kinds = [entry.get("kind") for entry in data.get("files", [])]
        if kinds.count("md") != 1 or kinds.count("html") != 1:
            raise ValueError("manifest 必须包含一个 md 与一个 html")
        for entry in data["files"]:
            artifact = inside(base, os.path.join(base, entry["path"]))
            if sha256_file(artifact) != entry["sha256"] or os.path.getsize(artifact) != entry["bytes"]:
                raise ValueError("产物指纹或文件大小不匹配")
            text = Path(artifact).read_text(encoding="utf-8")
            visible = html_to_text(text) if entry["kind"] == "html" else text
            actual = coverage(visible, context.required_strings())
            if actual.get("missing") or data.get("coverage", {}).get(entry["kind"]) != actual:
                raise ValueError(f"{entry['kind']} 业务内容覆盖不完整")
            if load_attribution() not in visible:
                raise ValueError("产物缺少署名")
            if secret_hits(text.replace(data["content_hash"], "")):
                raise ValueError("产物含疑似凭据")
            if entry["kind"] == "html" and re.search(r"<script[^>]+src=|<link[^>]+href=[\"']https?://|\b(?:fetch|XMLHttpRequest|sendBeacon)\b", text, re.I):
                raise ValueError("HTML 含远程依赖或运行时网络请求")
        audit.check("outputs", "输出", "pass", "manifest、指纹、覆盖率、署名与离线约束均通过")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        audit.issue("blocking", [], str(error) if isinstance(error, ValueError) else "产物或 manifest 缺失/不可读", "重新从当前 trip.json 渲染并复核")
        audit.check("outputs", "输出", "fail", "产物检查未通过")


def main(argv=None) -> int:
    configure_console()
    parser = argparse.ArgumentParser(description="travel-plan-pro 数据与交付校验")
    parser.add_argument("trip")
    parser.add_argument("--out")
    parser.add_argument("--check-outputs", action="store_true")
    parser.add_argument("--manifest")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    try:
        trip = json.loads(Path(args.trip).read_text(encoding="utf-8"))
    except (OSError, TypeError, json.JSONDecodeError) as error:
        print(f"✖ 无法读取 trip.json：{error}", file=sys.stderr)
        return 2
    report = Audit(trip)
    try:
        schema = json.loads(Path(SCHEMA_PATH).read_text(encoding="utf-8"))
        checker = MiniSchema(schema)
        if not checker.check(trip, schema):
            report.issue("blocking", [], f"schema 不通过（{len(checker.errors)} 处）", "修复数据字段后重试")
        else:
            semantic_checks(trip, report)
            if args.check_outputs:
                check_outputs(trip, args.trip, args.manifest, report)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        report.issue("blocking", [], "校验器无法完成：" + str(error), "修复数据结构或运行环境")
    output = report.to_json()
    destination = args.out or os.path.join(os.path.dirname(os.path.abspath(args.trip)), f"audit.v{trip.get('plan_version', 1)}.json")
    try:
        atomic_json(destination, output)
    except (OSError, ValueError, TypeError):
        print("✖ 无法写入 audit 报告", file=sys.stderr)
        return 2
    if not args.quiet:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if output["summary"]["blocking"] else 0


if __name__ == "__main__":
    sys.exit(main())
