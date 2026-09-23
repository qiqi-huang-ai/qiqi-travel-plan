#!/usr/bin/env python3
"""Render a trip bundle into matching Markdown and offline HTML views."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

from route_matrix import build_matrix
from trip_support import atomic_json, atomic_text, budget_limit, budget_verdict, canonical_hash, checkpoint, configure_console, inside, preflight

RENDERER_VERSION = "3.1.0"
HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
ATTRIBUTION_PATH = SKILL_ROOT / "assets" / "templates" / "attribution.json"
WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
STATUS = {"draft": "探索草案", "conditional": "条件式方案", "executable_as_of_check": "截至核查时可执行"}
BOOKING = {"not_required": "无需预约", "not_open": "尚未放票", "pending_user": "待你预约", "user_claimed": "已声称预约（待凭证）", "confirmed": "已确认预约", "failed": "预约失败", "unknown": "预约情况未知"}
VERIFY = {"verified": "已核实", "conditional": "有条件", "estimate": "估算", "unknown": "未知", "blocked": "阻断"}
FACT = {"verified": "已核实", "experience": "经验参考", "estimate": "估算", "unknown": "未知", "conflict": "存在冲突"}
KIND = {"visit": "游览", "meal": "用餐", "transit": "交通", "rest": "休息", "checkin": "入住", "checkout": "退房", "transport_major": "大交通", "free": "自由活动", "buffer": "机动"}
INTENSITY = {"light": "轻松", "moderate": "适中", "heavy": "偏累"}
PRIORITY = {"must": "必须", "should": "建议", "nice": "可选"}
WEATHER = {"forecast": "真实预报", "seasonal": "季节参考（不是预报）", "unavailable": "未获取到天气"}
CAPABILITY = {"map_route": "地图/路线", "weather": "天气", "tikhub": "TikHub 小红书", "web_search": "联网搜索", "web_fetch": "网页读取", "file_read": "文件读取", "file_write": "文件写入", "script_exec": "脚本执行", "image_fetch": "图片获取", "image_gen": "生图"}


def esc(value) -> str: return html.escape("" if value is None else str(value), quote=True)
def fmt_dt(value, with_date=False) -> str:
    if not value: return "—"
    try: parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError: return str(value)
    return parsed.strftime("%m-%d %H:%M" if with_date else "%H:%M")
def fmt_date(value) -> str:
    if not value: return "—"
    try: parsed = date.fromisoformat(value); return f"{parsed.month}月{parsed.day}日（{WEEKDAYS[parsed.weekday()]}）"
    except ValueError: return str(value)
def money(value, currency="") -> str:
    if value is None: return "未知"
    if isinstance(value, float) and value.is_integer(): value = int(value)
    return f"{value:g} {currency}".strip() if isinstance(value, (int, float)) else str(value)
def money_range(low, high, currency="") -> str:
    if low is None and high is None: return "未知"
    if low is None: return f"≤ {money(high, currency)}"
    if high is None: return f"≥ {money(low, currency)}"
    return money(low, currency) if low == high else f"{money(low)}–{money(high, currency)}"
def safe_url(value) -> str | None:
    return value if isinstance(value, str) and value.lower().startswith(("http://", "https://")) else None
def load_attribution() -> str:
    try: return json.loads(ATTRIBUTION_PATH.read_text(encoding="utf-8"))["text"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError): return "由 Travel Plan Pro 生成"


class Ctx:
    def __init__(self, trip: dict):
        self.trip = trip; self.req = trip["request"]; self.it = trip["itinerary"]
        self.places = {x["place_id"]: x for x in trip["places"]}; self.legs = {x["leg_id"]: x for x in trip["legs"]}
        self.facts = {x["fact_id"]: x for x in trip["facts"]}; self.sources = {x["source_id"]: x for x in trip["sources"]}
        self.alts = {x["alt_id"]: x for x in self.it["alternatives"]}; self.items = {i["item_id"]: i for d in self.it["days"] for i in d["items"]}
        self.attribution = load_attribution(); self.hash = canonical_hash(trip); self.cur = self.it["budget"]["currency"]

    def place_name(self, place_id): return self.places.get(place_id, {}).get("name", place_id or "—")
    def party_brief(self):
        party = self.req["party"]; result = [f"成人 {party.get('adults', 0)}"]
        if party.get("children_ages"): result.append("儿童 " + "/".join(f"{age}岁" for age in party["children_ages"]))
        if party.get("seniors"): result.append(f"长者 {party['seniors']}")
        return "，".join(result)
    def date_range(self):
        dates = self.req["dates"]; return f"{dates.get('start') or '未定'} 至 {dates.get('end') or '未定'}"
    def budget_brief(self):
        budget = self.req["budget"]; mode = {"total": "总预算", "per_person": "人均", "unknown": "预算"}.get(budget.get("mode"), "预算")
        count = f"（预算人数 {budget['budget_persons']} 人）" if budget.get("mode") == "per_person" and budget.get("budget_persons") else ""
        return f"{mode} {money_range(budget.get('amount_min'), budget.get('amount_max'), budget.get('currency', ''))}{count}"
    def budget_totals(self):
        paid = low = high = 0.0; unknown = []
        for category in self.it["budget"]["categories"]:
            paid += category.get("paid") or 0
            if category.get("status") == "unknown" or category.get("min") is None or category.get("max") is None: unknown.append(category["name"]); continue
            low += category["min"]; high += category["max"]
        return low, high, paid, unknown
    def required_strings(self) -> list[str]:
        """Visible-content contract. It intentionally contains no embedded JSON."""
        result = [self.it["summary"]["title"], self.it["summary"]["destination"], STATUS.get(self.trip["status"], self.trip["status"]), self.attribution, f"v{self.trip['plan_version']}", self.trip["trip_id"], self.date_range(), self.party_brief(), self.budget_brief()]
        result += [self.req["origin"].get("name"), *self.req.get("special_needs", []), *self.req["interests"].get("like", []), *self.req["interests"].get("avoid", []), *(x["label"] for x in self.req["interests"].get("must", []))]
        result += [v for v in self.req.get("pace", {}).values() if v]
        result += [self.req["transport"].get("luggage"), self.req["transport"].get("notes"), self.req["party"].get("mobility_notes")]
        result += [commitment.get("description") for commitment in self.req.get("fixed_commitments", [])]
        result += self.it.get("assumptions", []) + self.it.get("tradeoffs", []) + self.it.get("unmet", []) + self.it.get("risks", []) + self.it.get("weather", {}).get("prep", [])
        for day in self.it["days"]:
            result += [fmt_date(day.get("date")), day.get("theme"), day.get("region"), day.get("lodging_base"), *day.get("notes", [])]
            for item in day["items"]:
                result += [item.get("title"), item.get("description"), fmt_dt(item.get("planned_start")), fmt_dt(item.get("planned_end")), VERIFY.get(item.get("verification_status"), item.get("verification_status")), *item.get("tips", []), BOOKING.get(item.get("booking_status"), item.get("booking_status"))]
                admission_id = item.get("admission_item_id")
                if admission_id:
                    parent = self.items.get(admission_id)
                    result.append(f"场所入场预约：{BOOKING.get(parent.get('booking_status'), parent.get('booking_status')) if parent else '引用项缺失'}")
                cost = item.get("cost", {}); result.append(money_range(cost.get("min"), cost.get("max"), cost.get("currency", self.cur)))
        for variant in self.it.get("plan_variants", []): result += [variant.get("title"), variant.get("objective"), *variant.get("tradeoffs", [])]
        for alternative in self.it.get("alternatives", []): result += [alternative.get("trigger"), alternative.get("description"), alternative.get("cost_delta"), alternative.get("time_delta")]
        for category in self.it["budget"]["categories"]: result += [category.get("name"), money_range(category.get("min"), category.get("max")), category.get("note")]
        result += [self.it["budget"].get("note"), self.it.get("sources_note"), self.it["weather"].get("note")]
        result += [entry.get("summary") for entry in self.it["weather"].get("entries", [])]
        result += [check.get("action") for check in self.it["checklist"]]
        result += [region.get("name") for region in self.it["lodging"]["regions"]] + [self.it["lodging"].get("strategy")]
        result += [f.get("claim") for f in self.trip["facts"]] + [s.get("title") for s in self.trip["sources"]]
        result += [cap.get("test_result") for cap in self.trip.get("capabilities", [])]
        result += [queue.get("target_id") for queue in self.trip.get("recheck_queue", [])] + [queue.get("action") for queue in self.trip.get("recheck_queue", [])]
        result += [change.get("summary") for change in self.trip.get("changelog", [])]
        result += [decision.get("user_choice") for decision in self.trip.get("decisions", [])]
        result += [decision.get("note") for decision in self.trip.get("decisions", [])]
        return list(dict.fromkeys(value for value in result if value not in (None, "")))


def _booking(item): return BOOKING.get(item.get("booking_status"), item.get("booking_status", "未知"))


def _admission_label(c, item):
    parent_id = item.get("admission_item_id")
    if not parent_id:
        return None
    parent = c.items.get(parent_id)
    if not parent:
        return "场所入场预约：引用项缺失"
    return f"场所入场预约：{_booking(parent)}（沿用「{parent.get('title') or parent_id}」）"


def _item_line(c, item):
    place = c.place_name(item.get("place_id")); cost = money_range((item.get("cost") or {}).get("min"), (item.get("cost") or {}).get("max"), (item.get("cost") or {}).get("currency", c.cur))
    lines = [f"**{fmt_dt(item.get('planned_start'))}–{fmt_dt(item.get('planned_end'))} · {item.get('title', '未命名')}** · {KIND.get(item.get('kind'), item.get('kind'))} · {VERIFY.get(item.get('verification_status'), item.get('verification_status'))}", "", item.get("description") or "—", "", f"- 地点：{place}", f"- 活动预约：{_booking(item)}"]
    if _admission_label(c, item): lines.append(f"- {_admission_label(c, item)}")
    lines.append(f"- 费用：{cost}")
    if item.get("tips"): lines.append("- 提醒：" + "；".join(item["tips"]))
    return lines


def render_md(c: Ctx) -> str:
    trip, req, it = c.trip, c.req, c.it; out = [f"# {it['summary']['title']}", "", f"> {it['summary'].get('tagline') or '把确定的安排、未知项和下一步放在同一份离线计划里。'}", "", f"**{STATUS.get(trip['status'], trip['status'])}** · 版本 v{trip['plan_version']} · {trip['trip_id']}", "", "## 1. 总览", "", f"- 目的地：{it['summary']['destination']}", f"- 日期：{c.date_range()}（{len(it['days'])} 天）", f"- 出发地：{req['origin'].get('name') or '未提供'}", f"- 同行：{c.party_brief()}", f"- 预算：{c.budget_brief()}；包含 {('、'.join(req['budget'].get('includes') or []) or '未说明')}", f"- 核查：{trip.get('checked_at') or '未核查'}", "", "## 2. 约束、假设与取舍", "", "### 必须满足", "", "- 必去：" + ("、".join(x["label"] for x in req["interests"]["must"]) or "无"), "- 喜欢：" + ("、".join(req["interests"]["like"]) or "未说明"), "- 不去：" + ("、".join(req["interests"]["avoid"]) or "无"), "", "### 假设", ""]
    out += [f"- {x}" for x in (it.get("assumptions") or ["无"])] + ["", "### 重要取舍", ""] + [f"- {x}" for x in (it.get("tradeoffs") or ["无"])] + ["", "## 3. 接口与资料能力", ""]
    out += [f"- {CAPABILITY.get(cap.get('capability'), cap.get('capability'))}：{cap.get('availability') or 'unknown'}；权限 {cap.get('permission') or 'unknown'}；{cap.get('test_result') or cap.get('fallback') or '—'}" for cap in trip.get("capabilities", [])]
    out += [f"- 用户选择：{decision.get('user_choice')}；{decision.get('note') or '—'}" for decision in trip.get("decisions", [])]
    out += ["", "## 4. 每日行程", "", "| 天 | 日期 | 主题 | 片区 | 强度 |", "| --- | --- | --- | --- | --- |"]
    out += [f"| D{n} | {fmt_date(day.get('date'))} | {day.get('theme')} | {day.get('region') or '—'} | {INTENSITY.get(day.get('intensity'), day.get('intensity'))} |" for n, day in enumerate(it["days"], 1)] + ["", "时间为当地时间；估算与未知不会被渲染成确定事实。", ""]
    for n, day in enumerate(it["days"], 1):
        out += [f"### D{n} · {fmt_date(day.get('date'))} · {day.get('theme')}", "", f"片区：{day.get('region') or '—'} · 住宿基点：{day.get('lodging_base') or '—'}"] + [f"- {note}" for note in day.get("notes", [])] + [""]
        for item in day["items"]: out += _item_line(c, item) + [""]
    if it.get("plan_variants"):
        out += ["## 方案变体", ""]
        for variant in it["plan_variants"]:
            selected = "（当前采用）" if variant.get("selected") else ""
            out += [f"- **{variant['title']}{selected}**：{variant['objective']}；取舍：{'；'.join(variant.get('tradeoffs') or []) or '无'}"]
        out += [""]
    out += ["## 5. 住宿、交通与预算", "", f"- 住宿策略：{it['lodging'].get('strategy') or '未知'}"] + [f"- 住宿区域：{region.get('name')}：{region.get('pros') or '—'}" for region in it["lodging"].get("regions", [])] + ["", "### 预算分类", "", "| 类别 | 已付 | 预计未付 | 状态 |", "| --- | --- | --- | --- |"]
    out += [f"| {cat['name']} | {money(cat.get('paid') or 0, c.cur)} | {money_range(cat.get('min'), cat.get('max'), c.cur)} | {cat.get('status')} |" for cat in it["budget"]["categories"]]
    low, high, paid, unknown = c.budget_totals(); limit = budget_limit(trip); out += ["", f"预算摘要：已付 {money(paid, c.cur)}；总成本 {money_range(paid + low, paid + high, c.cur)}；未知 {('、'.join(unknown) or '无')}；判断 {budget_verdict(low, high, paid, bool(unknown), limit)}", "", "## 6. 预约、天气与替代", ""]
    out += [f"- 天气：{WEATHER.get(it['weather'].get('kind'), it['weather'].get('kind'))}；{it['weather'].get('note') or '—'}"] + [f"- 备选：{alt['trigger']} → {alt['description']}（{alt.get('cost_delta') or '费用无变化'}；{alt.get('time_delta') or '时间无变化'}）" for alt in it["alternatives"]] + ["", "## 7. 出发前清单", ""] + [f"- [{ 'x' if check['status'] == 'done' else ' ' }] {PRIORITY.get(check['priority'], check['priority'])}：{check['action']}；截止 {check.get('deadline') or '—'}；未完成：{check.get('if_unresolved') or '—'}" for check in it["checklist"]]
    out += ["", "## 8. 证据与复查", ""] + [f"- {fact['claim']} · {FACT.get(fact['status'], fact['status'])}" for fact in trip["facts"]] + [f"- 待重查 {queue['target_id']}：{queue['action']}" for queue in trip.get("recheck_queue", [])] + ["", f"来源：{len(trip['sources'])} 条；{c.attribution}；内容指纹 {c.hash[:12]}"]
    missing = coverage("\n".join(out), c.required_strings())["missing"]
    if missing:
        out += ["", "## 9. 补充记录", ""] + [f"- {value}" for value in missing]
    return "\n".join(out) + "\n"


def _html_section(title, body, cls="section", section_id=None):
    marker = f' id="{esc(section_id)}"' if section_id else ""
    return f'<section{marker} class="{esc(cls)}"><h2>{esc(title)}</h2>{body}</section>'


def render_html(c: Ctx) -> str:
    trip, req, it = c.trip, c.req, c.it
    days = []
    for index, day in enumerate(it["days"], 1):
        cards = []
        for item in day["items"]:
            admission = _admission_label(c, item)
            booking_text = f"活动预约：{_booking(item)}" + (f" · {admission}" if admission else "")
            cards.append(f"<article class='item'><div class='time'>{esc(fmt_dt(item.get('planned_start')))}<span>—</span>{esc(fmt_dt(item.get('planned_end')))}</div><div><h3>{esc(item.get('title'))}</h3><p class='muted'>{esc(KIND.get(item.get('kind'), item.get('kind')))} · {esc(VERIFY.get(item.get('verification_status'), item.get('verification_status')))} · {esc(booking_text)}</p><p>{esc(item.get('description') or '—')}</p><p class='meta'>地点：{esc(c.place_name(item.get('place_id')))} · 费用：{esc(money_range((item.get('cost') or {}).get('min'), (item.get('cost') or {}).get('max'), (item.get('cost') or {}).get('currency', c.cur)))}</p>{('<ul>' + ''.join(f'<li>{esc(tip)}</li>' for tip in item.get('tips', [])) + '</ul>') if item.get('tips') else ''}</div></article>")
        days.append(f"<article class=\"day\" id=\"day-{index}\" data-day=\"{index}\"><div class=\"day-head\"><span>D{index}</span><div><h2>{esc(fmt_date(day.get('date')))} · {esc(day.get('theme'))}</h2><p>{esc(day.get('region') or '—')} · {esc(INTENSITY.get(day.get('intensity'), day.get('intensity')))}</p></div></div>{''.join(f'<p class=\"note\">{esc(note)}</p>' for note in day.get('notes', []))}{''.join(cards)}</article>")
    overview = f"<div class='facts'><div><small>目的地</small><strong>{esc(it['summary']['destination'])}</strong></div><div><small>日期</small><strong>{esc(c.date_range())}</strong></div><div><small>同行</small><strong>{esc(c.party_brief())}</strong></div><div><small>预算</small><strong>{esc(c.budget_brief())}</strong></div></div><p>{esc(it['summary'].get('tagline') or '离线可读，事实、估算与下一步分层呈现。')}</p>"
    rail_nodes = "".join(f"<span class=\"rail-node\"><b>D{n}</b><small>{esc(fmt_date(day.get('date')))}</small></span>" for n, day in enumerate(it["days"], 1))
    route_rail = f"<div class=\"journey-rail\" aria-label=\"旅程骨架\"><div class=\"rail-line\"></div>{rail_nodes}</div>"
    constraint = "<div class='columns'><div><h3>必去</h3><p>" + esc("、".join(x["label"] for x in req["interests"]["must"]) or "无") + "</p></div><div><h3>喜欢</h3><p>" + esc("、".join(req["interests"]["like"]) or "未说明") + "</p></div><div><h3>不去</h3><p>" + esc("、".join(req["interests"]["avoid"]) or "无") + "</p></div></div>"
    provider_rows = []
    for capability in trip.get("capabilities", []):
        label = CAPABILITY.get(capability.get("capability"), capability.get("capability"))
        provider_rows.append(f"<p><b>{esc(label)}</b><span>{esc(capability.get('availability') or 'unknown')} · {esc(capability.get('permission') or 'unknown')}</span><small>{esc(capability.get('test_result') or capability.get('fallback') or '—')}</small></p>")
    for decision in trip.get("decisions", []):
        provider_rows.append(f"<p><b>用户选择：{esc(decision.get('user_choice'))}</b><span>{esc(decision.get('topic'))}</span><small>{esc(decision.get('note') or '—')}</small></p>")
    provider_body = "<div class='evidence-list'>" + "".join(provider_rows or ["<p>本次没有记录接口选择。</p>"]) + "</div>"
    low, high, paid, unknown = c.budget_totals(); limit = budget_limit(trip)
    budget_body = f"<div class='budget-grid'><div><small>已付</small><strong>{esc(money(paid, c.cur))}</strong></div><div><small>预计总额</small><strong>{esc(money_range(paid + low, paid + high, c.cur))}</strong></div><div><small>预算判断</small><strong>{esc(budget_verdict(low, high, paid, bool(unknown), limit))}</strong></div></div><table><tr><th>类别</th><th>已付</th><th>预计未付</th><th>状态</th></tr>{''.join(f"<tr><td>{esc(cat['name'])}</td><td>{esc(money(cat.get('paid') or 0, c.cur))}</td><td>{esc(money_range(cat.get('min'), cat.get('max'), c.cur))}</td><td>{esc(cat.get('status'))}</td></tr>" for cat in it['budget']['categories'])}</table>"
    check_body = f"<div class=\"progress-wrap\"><div><b>出发准备度</b><span id=\"check-progress\">0/{len(it['checklist'])} 已完成</span></div><div class=\"progress\"><i id=\"check-progress-bar\"></i></div></div><div class=\"checklist\">" + "".join(f"<label><input type=\"checkbox\" {'checked' if check['status'] == 'done' else ''}><span>{esc(PRIORITY.get(check['priority'], check['priority']))} · {esc(check['action'])}</span><small>截止 {esc(check.get('deadline') or '—')} · 未完成：{esc(check.get('if_unresolved') or '—')}</small></label>" for check in it["checklist"]) + "</div>"
    evidence_body = "<div class='evidence-list'>" + "".join(f"<p><b>{esc(fact['claim'])}</b><span>{esc(FACT.get(fact['status'], fact['status']))}</span></p>" for fact in trip["facts"]) + "</div>"
    queue = trip.get("recheck_queue", []); queue_body = "<ul>" + "".join(f"<li>{esc(entry['target_id'])}：{esc(entry['action'])}</li>" for entry in queue) + "</ul>" if queue else "<p>当前没有待重查事项。</p>"
    source_body = "<ul class='source-list'>" + "".join(f"<li>{esc(source['title'])} · {esc(source.get('source_type') or '未分类')}</li>" for source in trip["sources"]) + "</ul>"
    lodging_budget = f"<p>{esc(it['lodging'].get('strategy') or '住宿策略未知')}</p>{budget_body}"
    variants_body = "".join(f"<article><h3>{esc(variant['title'])}{'（当前采用）' if variant.get('selected') else ''}</h3><p>{esc(variant['objective'])}</p><p class='muted'>取舍：{esc('；'.join(variant.get('tradeoffs') or []) or '无')}</p></article>" for variant in it.get("plan_variants", [])) or "<p>没有额外方案变体。</p>"
    alternative_rows = "".join("<li>" + esc(alt["trigger"]) + " → " + esc(alt["description"]) + "</li>" for alt in it["alternatives"])
    weather_alternatives = f"<p><b>{esc(WEATHER.get(it['weather'].get('kind'), it['weather'].get('kind')))}</b> · {esc(it['weather'].get('note') or '—')}</p><ul>{alternative_rows}</ul>"
    evidence_section = evidence_body + f"<h3>待重查队列（{len(queue)}）</h3>" + queue_body + "<h3>来源</h3>" + source_body
    day_switcher = '<nav id="day-switcher" class="day-switcher" aria-label="按天筛选"><button type="button" data-day-filter="all" aria-pressed="true">全部</button>' + "".join(f'<button type="button" data-day-filter="{n}" aria-pressed="false">D{n}</button>' for n in range(1, len(it["days"]) + 1)) + "</nav>"
    page_sections = "".join((
        _html_section("总览", overview + route_rail, section_id="overview"), _html_section("约束与取舍", constraint, section_id="constraints"), _html_section("接口与资料能力", provider_body, section_id="providers"), _html_section("每日行程", day_switcher + "".join(days), section_id="days"),
        _html_section("方案变体", variants_body, section_id="variants"), _html_section("住宿、交通与预算", lodging_budget, section_id="stay"), _html_section("预约、天气与替代", weather_alternatives, section_id="alternatives"),
        _html_section("出发前清单", check_body, section_id="checklist"), _html_section("证据与复查", evidence_section, section_id="evidence"),
    ))
    html_missing = coverage(html_to_text(page_sections), c.required_strings())["missing"]
    if html_missing:
        page_sections += _html_section("补充记录", "<details><summary>展开可检索的计划字段</summary><ul>" + "".join(f"<li>{esc(value)}</li>" for value in html_missing) + "</ul></details>", section_id="appendix")
    deadline = next((check.get("deadline") for check in it["checklist"] if check.get("deadline")), "")
    snapshot = json.dumps({"trip_id": trip["trip_id"], "plan_version": trip["plan_version"], "content_hash": c.hash}, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{esc(it['summary']['title'])}</title><style>
:root{{--ink:#174f4e;--deep:#103d3c;--coral:#c97564;--paper:#fffaf1;--wash:#edf5f1;--line:#c9d9d2;--muted:#667772;--shadow:0 20px 60px #164d3d1c}}*{{box-sizing:border-box}}body{{margin:0;background:#e6f1ec;color:var(--deep);font:15px/1.7 -apple-system,BlinkMacSystemFont,'PingFang SC','Noto Sans SC',sans-serif}}body:before{{content:'';position:fixed;inset:0;pointer-events:none;opacity:.3;background:repeating-linear-gradient(0deg,transparent 0 18px,#bad9ce 19px 20px),radial-gradient(ellipse at 15% 10%,#fff 0 18%,transparent 52%)}}main{{max-width:1180px;margin:26px auto;padding:0 22px}}.page{{background:var(--paper);border:1px solid #d8e4dd;border-radius:28px;box-shadow:var(--shadow);overflow:hidden}}header{{padding:58px 62px 42px;position:relative;background:linear-gradient(130deg,#fffdf7,#eff8f1)}}header:after{{content:'潮汐纸纹 · OFFLINE';position:absolute;right:42px;top:42px;color:#c5dcd2;letter-spacing:.18em;font-size:11px}}.eyebrow{{color:var(--coral);letter-spacing:.16em;font-weight:700}}h1{{font-family:Georgia,'Songti SC',serif;font-size:clamp(42px,6vw,82px);line-height:1.05;margin:16px 0;color:var(--ink);letter-spacing:-.04em}}h2{{font:700 28px/1.2 Georgia,'Songti SC',serif;color:var(--ink);margin:0 0 18px}}h3{{margin:0 0 6px;color:var(--ink)}}.muted,small,.meta{{color:var(--muted)}}.status{{display:inline-block;border:1px solid #d9b0a4;color:#a65e51;padding:4px 12px;border-radius:999px;background:#fff4ee}}button{{border:1px solid var(--line);background:#fff;padding:6px 10px;border-radius:999px;color:var(--ink)}}.facts,.budget-grid,.columns{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}.facts>div,.budget-grid>div,.columns>div{{background:var(--wash);border:1px solid var(--line);border-radius:16px;padding:16px}}small{{display:block;font-size:12px;letter-spacing:.08em}}strong{{display:block;color:var(--ink);font-size:19px;margin-top:3px}}.content{{padding:26px 62px 72px}}.section{{padding:30px 0;border-top:1px solid var(--line)}}.section:first-child{{border-top:0}}.day{{background:#fffdf8;border:1px solid var(--line);border-radius:20px;padding:22px;margin:18px 0}}.day-head{{display:flex;gap:16px;align-items:flex-start;border-bottom:1px dashed var(--line);padding-bottom:14px}}.day-head>span{{background:var(--ink);color:white;border-radius:50%;width:42px;height:42px;display:grid;place-items:center;font-weight:700}}.item{{display:grid;grid-template-columns:120px 1fr;gap:20px;padding:18px 0;border-bottom:1px solid #e5eee9}}.item:last-child{{border-bottom:0}}.time{{font-weight:700;color:var(--coral);font-variant-numeric:tabular-nums}}.time span{{color:#b5c8bf;padding:0 4px}}.item p{{margin:4px 0}}.meta{{font-size:13px}}.note{{color:var(--muted);margin:8px 0}}table{{width:100%;border-collapse:collapse;margin-top:18px;background:#fffdf8}}th,td{{text-align:left;padding:10px;border-bottom:1px solid var(--line)}}th{{color:var(--ink);background:var(--wash)}}.checklist{{display:grid;gap:10px}}.checklist label{{display:grid;grid-template-columns:28px 1fr;gap:8px;border:1px solid var(--line);border-radius:13px;padding:11px 14px;background:#fffdf8}}.checklist label>span{{color:var(--coral);font-size:20px}}.checklist small{{grid-column:2}}.evidence-list p{{display:flex;justify-content:space-between;gap:16px;border-bottom:1px solid #e5eee9;padding:8px 0}}.evidence-list span{{color:var(--coral);white-space:nowrap}}.source-list{{columns:2}}footer{{padding:24px 62px;background:var(--deep);color:#eaf5f0;font-size:13px}}footer .muted{{color:#b8d4c9}}@media(prefers-color-scheme:dark){{body{{background:#112c2b}}.page{{filter:brightness(.92)}}}}@media(max-width:760px){{header,.content,footer{{padding-left:22px;padding-right:22px}}.facts,.budget-grid,.columns{{grid-template-columns:1fr 1fr}}.item{{grid-template-columns:1fr;gap:4px}}.source-list{{columns:1}}}}@media(max-width:430px){{.facts,.budget-grid,.columns{{grid-template-columns:1fr}}h1{{font-size:48px}}}}
</style></head><body><main><div class='page'><header><div class='eyebrow'>A SLOW ISLAND DAY · TRAVEL PLAN PRO · {esc(trip['trip_id'])} · v{trip['plan_version']}</div><h1>{esc(it['summary']['title'])}</h1><p>{esc(it['summary'].get('tagline') or '把确定的安排、未知项和下一步放在同一份离线计划里。')}</p><p><span class='status'>{esc(STATUS.get(trip['status'], trip['status']))}</span> · {esc(c.attribution)} · <button id="departure-mode" type="button">出发模式</button> <span class="countdown" data-deadline="{esc(deadline)}"></span></p></header><div class='content'>{page_sections}</div><footer>{esc(c.attribution)}<br><span class='muted'>编号 {esc(trip['trip_id'])} · 版本 v{trip['plan_version']} · 生成于 {esc(trip['generated_at'])} · 内容指纹 {c.hash[:12]} · 渲染器 {RENDERER_VERSION}</span></footer></div></main><script type='application/json' id='trip-meta'>{snapshot}</script><script>document.getElementById('departure-mode').addEventListener('click',function(){{document.body.classList.toggle('departure')}});document.querySelectorAll('.countdown').forEach(function(el){{var d=new Date(el.dataset.deadline);if(!isNaN(d))el.textContent=' · 截止 '+d.toLocaleString()}});</script></body></html>"""


_BASE_RENDER_HTML = render_html


def render_html(c: Ctx) -> str:
    """Add the navigation shell after the content renderer has built the page.

    Keeping this shell as a post-processing layer makes the business renderer
    and the offline reading experience independently testable.
    """
    page = _BASE_RENDER_HTML(c)
    nav_items = (("overview", "总览"), ("constraints", "约束取舍"), ("providers", "接口能力"), ("days", "每日行程"),
                 ("variants", "方案变体"), ("stay", "住宿预算"), ("alternatives", "天气替代"),
                 ("checklist", "出发清单"), ("evidence", "证据复查"))
    nav = "".join(f'<a href="#{key}" data-section="{key}">{label}</a>' for key, label in nav_items)
    sidebar = (f'<aside class="sidebar"><div class="sidebar-title">PLAN INDEX</div><nav id="section-nav">{nav}</nav>'
               '<div class="side-note">点击分区快速定位；每日行程可按天筛选。打开后无需联网。</div></aside>')
    page = page.replace("<div class='content'>", f'<div class="layout">{sidebar}<div class="content">', 1)
    page = page.replace("</div><footer>", "</div></div><footer>", 1)
    css = """
.layout{display:grid;grid-template-columns:214px minmax(0,1fr);gap:26px;align-items:start;padding:26px 62px 72px}.sidebar{position:sticky;top:18px;border:1px solid var(--line);border-radius:18px;background:#f4faf6;padding:16px}.sidebar-title{font-size:12px;letter-spacing:.14em;color:var(--coral);font-weight:700;margin-bottom:10px}.sidebar nav{display:grid;gap:3px}.sidebar a{color:var(--deep);text-decoration:none;padding:8px 10px;border-radius:10px}.sidebar a:hover,.sidebar a.active{background:var(--ink);color:#fff}.sidebar .side-note{border-top:1px solid var(--line);margin-top:14px;padding-top:12px;color:var(--muted);font-size:12px}.section{scroll-margin-top:20px}.journey-rail{display:flex;align-items:center;justify-content:space-between;gap:0;margin:30px 4px 8px;position:relative}.rail-line{position:absolute;left:7%;right:7%;height:3px;background:var(--coral);opacity:.55;top:17px}.rail-node{position:relative;z-index:1;display:grid;justify-items:center;gap:3px;background:var(--paper);padding:0 8px;color:var(--ink)}.rail-node b{display:grid;place-items:center;border:3px solid var(--ink);border-radius:50%;width:36px;height:36px;background:var(--paper)}.rail-node small{font-size:11px;white-space:nowrap}.day-switcher{display:flex;gap:8px;position:sticky;top:12px;z-index:2;background:var(--paper);padding:4px 0 12px}.day-switcher button[aria-pressed="true"]{background:var(--ink);color:#fff}.day.is-hidden{display:none}.progress-wrap{margin:4px 0 16px;color:var(--ink)}.progress-wrap>div:first-child{display:flex;justify-content:space-between;font-size:13px}.progress{height:7px;background:#dcebe3;border-radius:999px;overflow:hidden;margin-top:6px}.progress i{display:block;height:100%;width:0;background:var(--coral);transition:width .2s}.checklist input{accent-color:var(--coral)}.totop{position:fixed;right:24px;bottom:24px;opacity:0;pointer-events:none;transition:opacity .2s}.totop.visible{opacity:1;pointer-events:auto}body.departure .day{border-color:var(--coral)}@media print{body:before,.sidebar,.day-switcher,.totop,#departure-mode{display:none!important}.layout{display:block;padding:0 40px}.page{box-shadow:none;border:0}}@media(max-width:760px){.layout{display:block;padding:18px 22px 50px}.sidebar{position:static;margin-bottom:18px;overflow:auto;padding:10px}.sidebar nav{display:flex;min-width:max-content}.sidebar .side-note{display:none}.rail-node{padding:0 3px}.rail-node small{font-size:9px}}
"""
    page = page.replace("</style>", css + "</style>", 1)
    script = """
<script>(function(){var links=[].slice.call(document.querySelectorAll('#section-nav a')),sections=links.map(function(a){return document.getElementById(a.dataset.section)});function mark(id){links.forEach(function(a){a.classList.toggle('active',a.dataset.section===id)})}if(window.IntersectionObserver){var observer=new IntersectionObserver(function(entries){entries.forEach(function(entry){if(entry.isIntersecting)mark(entry.target.id)})},{rootMargin:'-18% 0px -70% 0px'});sections.forEach(function(section){if(section)observer.observe(section)})}mark('overview');var filters=[].slice.call(document.querySelectorAll('[data-day-filter]')),cards=[].slice.call(document.querySelectorAll('.day'));filters.forEach(function(button){button.addEventListener('click',function(){filters.forEach(function(b){b.setAttribute('aria-pressed',String(b===button))});cards.forEach(function(card){card.classList.toggle('is-hidden',button.dataset.dayFilter!=='all'&&card.dataset.day!==button.dataset.dayFilter)})})});var checks=[].slice.call(document.querySelectorAll('.checklist input')),progress=document.getElementById('check-progress'),bar=document.getElementById('check-progress-bar'),key='travel-plan-pro:{trip}:{version}';try{var saved=JSON.parse(localStorage.getItem(key)||'[]');checks.forEach(function(box,index){box.checked=saved[index]===true})}catch(e){}function update(){var done=checks.filter(function(box){return box.checked}).length,total=checks.length;if(progress)progress.textContent=done+'/'+total+' 已完成';if(bar)bar.style.width=(total?done/total*100:0)+'%';try{localStorage.setItem(key,JSON.stringify(checks.map(function(box){return box.checked})))}catch(e){}}checks.forEach(function(box){box.addEventListener('change',update)});update();var top=document.querySelector('.totop');window.addEventListener('scroll',function(){if(top)top.classList.toggle('visible',window.scrollY>500)});if(top)top.addEventListener('click',function(){window.scrollTo({top:0,behavior:'smooth'})})})()</script>
""".replace("{trip}", c.trip["trip_id"]).replace("{version}", str(c.trip["plan_version"]))
    page = page.replace("</body>", '<button class="totop" type="button" aria-label="回到顶部">↑ 顶部</button>' + script + "</body>", 1)
    return page


def html_to_text(value: str) -> str:
    value = re.sub(r"<script.*?</script>|<style.*?</style>", " ", value, flags=re.S | re.I); return html.unescape(re.sub(r"<[^>]+>", " ", value))
def coverage(text: str, required: list[str]) -> dict:
    normalized = re.sub(r"\s+", " ", text).strip(); missing = [value for value in required if re.sub(r"\s+", " ", str(value)).strip() not in normalized]
    return {"required": len(required), "present": len(required) - len(missing), "missing": missing}


def main(argv=None) -> int:
    configure_console(); parser = argparse.ArgumentParser(description="travel-plan-pro 同源渲染 MD + HTML")
    parser.add_argument("trip"); parser.add_argument("--out-dir"); parser.add_argument("--manifest"); parser.add_argument("--force", action="store_true"); args = parser.parse_args(argv)
    try: trip = json.loads(Path(args.trip).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error: print(f"✖ 无法读取 trip.json：{error}", file=sys.stderr); return 2
    trip_dir = os.path.dirname(os.path.realpath(os.path.abspath(args.trip)))
    try:
        preflight(trip, semantic=True); output_dir = inside(trip_dir, args.out_dir or os.path.join(trip_dir, "outputs"))
        manifest_path = inside(trip_dir, args.manifest or os.path.join(trip_dir, f"manifest.v{trip['plan_version']}.json"))
        c = Ctx(trip); base = f"{trip['trip_id']}_v{trip['plan_version']}"; md_path = inside(trip_dir, os.path.join(output_dir, base + ".md")); html_path = inside(trip_dir, os.path.join(output_dir, base + ".html"))
        if any(os.path.exists(path) for path in (md_path, html_path)) and not args.force: raise ValueError("同版本产物已存在；请升级版本或使用 --force")
        md, page, required = render_md(c), render_html(c), c.required_strings(); cov_md, cov_html = coverage(md, required), coverage(html_to_text(page), required)
        if cov_md["missing"] or cov_html["missing"]: raise ValueError("可见业务内容覆盖不足")
        checkpoint(trip_dir, trip); Path(output_dir).mkdir(parents=True, exist_ok=True); atomic_text(md_path, md); atomic_text(html_path, page)
        files = []
        for path, kind in ((md_path, "md"), (html_path, "html")):
            raw = Path(path).read_bytes(); files.append({"path": os.path.relpath(path, trip_dir).replace(os.sep, "/"), "kind": kind, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)})
        manifest = {"trip_id": trip["trip_id"], "plan_version": trip["plan_version"], "content_hash": c.hash, "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "renderer_version": RENDERER_VERSION, "files": files, "coverage": {"md": cov_md, "html": cov_html}, "attribution": {"text": c.attribution, "md": c.attribution in md, "html": c.attribution in page}}
        atomic_json(manifest_path, manifest); print(f"travel-plan-pro render · {trip['trip_id']} v{trip['plan_version']}"); print(f"  MD   {md_path} ({files[0]['bytes']} B) 覆盖 {cov_md['present']}/{cov_md['required']}"); print(f"  HTML {html_path} ({files[1]['bytes']} B) 覆盖 {cov_html['present']}/{cov_html['required']}"); print(f"  署名 md={manifest['attribution']['md']} html={manifest['attribution']['html']} · manifest {manifest_path}"); return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"✖ 无法渲染：{error}", file=sys.stderr); return 1


if __name__ == "__main__": sys.exit(main())
