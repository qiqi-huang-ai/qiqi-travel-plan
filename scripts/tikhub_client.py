#!/usr/bin/env python3
"""Opt-in TikHub Xiaohongshu App V2 adapter.

It is an experience-research adapter, never a booking or official-rules
provider. Live calls require both an environment token and an explicit budget;
dry-run is deliberately token-free and network-free.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from trip_support import atomic_json, configure_console

API_ORIGIN = "https://api.tikhub.io"
API_HOST = "api.tikhub.io"
ENDPOINTS = {
    "search": "/api/v1/xiaohongshu/app_v2/search_notes",
    "image_note": "/api/v1/xiaohongshu/app_v2/get_image_note_detail",
    "video_note": "/api/v1/xiaohongshu/app_v2/get_video_note_detail",
    "comments": "/api/v1/xiaohongshu/app_v2/get_note_comments",
    "sub_comments": "/api/v1/xiaohongshu/app_v2/get_note_sub_comments",
}
RETRYABLE = {429, 500, 502, 503, 504}


class TikHubError(RuntimeError): pass
class AuthError(TikHubError): pass
class BudgetExceeded(TikHubError): pass
class StructureError(TikHubError): pass


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def first_present(*values):
    return next((value for value in values if value is not None), None)


def parse_json_string(value):
    if isinstance(value, str) and value.lstrip().startswith("{"):
        try: return json.loads(value)
        except json.JSONDecodeError: pass
    return value


def redact(token: str | None) -> str:
    return "(未设置)" if not token else "****" + token[-4:]


class Budget:
    """Per-run request meter. A retry consumes one request slot."""
    def __init__(self, limit: int):
        self.limit = int(limit)
        if self.limit < 0: raise ValueError("请求上限不能为负数")
        self.used = self.billable_estimate = self.unknown_cost = 0
        self.notes: list[str] = []; self.stopped_reason: str | None = None

    def reserve(self, amount: int = 1) -> None:
        if amount < 1: raise ValueError("预留次数必须为正数")
        if self.stopped_reason: raise TikHubError("本次 TikHub 调研已停止")
        if self.used + amount > self.limit: raise BudgetExceeded(f"请求预算已用 {self.used}/{self.limit}，不再发起请求")
        self.used += amount; self.persist()

    def record(self) -> dict:
        return {"requests": self.used, "limit": self.limit, "billable_estimate": self.billable_estimate,
                "unknown_cost": self.unknown_cost, "notes": self.notes, "stopped_reason": self.stopped_reason}

    def persist(self) -> None: pass
    def close(self) -> None: pass


class PersistentBudget(Budget):
    """Small lock-protected ledger shared by deliberate live commands."""
    def __init__(self, limit: int, path: str):
        super().__init__(limit); self.path = Path(path).resolve(); self.lock = Path(str(self.path) + ".lock"); self.handle = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try: self.handle = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as error: raise TikHubError("消费账本正在使用或遗留锁；未发起请求") from error
        try:
            if self.path.exists(): self._restore()
            self.persist()
        except Exception:
            self.close(); raise

    def _restore(self) -> None:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        fields = ("requests", "limit", "billable_estimate", "unknown_cost")
        if not isinstance(data, dict) or any(type(data.get(field)) is not int or data[field] < 0 for field in fields):
            raise ValueError("消费账本结构损坏")
        if data["requests"] > data["limit"] or data["billable_estimate"] > data["requests"] or self.limit > data["limit"]:
            raise ValueError("消费账本计数或上限不一致")
        self.limit, self.used = min(self.limit, data["limit"]), data["requests"]
        if self.used > self.limit: raise BudgetExceeded("已用请求数超过新上限")
        self.billable_estimate, self.unknown_cost = data["billable_estimate"], max(data["unknown_cost"], self.used - data["billable_estimate"])
        self.stopped_reason = data.get("stopped_reason")

    def persist(self) -> None: atomic_json(self.path, self.record())
    def close(self) -> None:
        if self.handle is not None:
            os.close(self.handle); self.handle = None
            try: self.lock.unlink()
            except FileNotFoundError: pass


class Client:
    def __init__(self, token: str | None, budget: Budget, transport=None, timeout: int = 20, dry_run: bool = False, log=print):
        self.token, self.budget, self.timeout, self.dry_run, self.log = token, budget, timeout, dry_run, log
        self.transport = transport or self._network_get
        self.response_mode = "mock" if transport else "live"; self.stopped_reason = None

    def _network_get(self, url: str, headers: dict) -> tuple[int, str]:
        class RejectRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args): raise TikHubError("服务返回重定向，已中止")
        request = urllib.request.Request(url, headers=headers, method="GET")
        opener = urllib.request.build_opener(RejectRedirect)
        try:
            with opener.open(request, timeout=self.timeout) as response:
                return response.status, response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8", errors="replace")

    def _url(self, operation: str, params: dict) -> str:
        if operation not in ENDPOINTS: raise TikHubError("不支持的 TikHub 操作")
        query = urllib.parse.urlencode({key: value for key, value in params.items() if value not in (None, "")})
        url = API_ORIGIN + ENDPOINTS[operation] + ("?" + query if query else "")
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != API_HOST or parsed.port not in (None, 443):
            raise TikHubError("目标域名不在允许列表")
        return url

    def request(self, operation: str, params: dict) -> dict:
        try:
            return self._request(operation, params)
        except AuthError:
            self.budget.stopped_reason = self.stopped_reason or "认证失败"; raise
        finally: self.budget.persist()

    def _request(self, operation: str, params: dict) -> dict:
        if self.stopped_reason or self.budget.stopped_reason: raise TikHubError("适配器已停止")
        url = self._url(operation, params)
        if self.dry_run:
            self.log(f"[dry-run] GET {url}  (未发送请求)")
            return {"_dry_run": True}
        if not self.token: raise AuthError("未配置 TIKHUB_API_KEY；本次跳过小红书调研")
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json", "User-Agent": "travel-plan-pro/2"}
        for attempt in range(3):
            self.budget.reserve()
            try: status, body = self.transport(url, headers)
            except (TimeoutError, urllib.error.URLError, OSError) as error:
                if isinstance(error, TimeoutError) or "timed out" in str(error).lower():
                    self.budget.unknown_cost += 1; self.budget.notes.append(f"{operation} 超时，费用状态未知")
                    raise TikHubError(f"{operation} 请求超时") from error
                if attempt < 2: time.sleep((attempt + 1) * 0.5); continue
                raise TikHubError(f"{operation} 网络错误") from error
            if status in (401, 403):
                self.stopped_reason = f"认证失败或余额不足（HTTP {status}）"; raise AuthError(self.stopped_reason)
            if status in RETRYABLE and attempt < 2:
                time.sleep((attempt + 1) * 0.5); continue
            if status != 200: raise TikHubError(f"{operation} HTTP {status}，未采用响应内容")
            try: payload = json.loads(body)
            except json.JSONDecodeError as error: raise StructureError(f"{operation} 返回不是 JSON") from error
            return self._validate_envelope(operation, payload)
        raise TikHubError(f"{operation} 重试后未得到有效响应")

    def _validate_envelope(self, operation: str, payload: object) -> dict:
        if not isinstance(payload, dict): raise StructureError("响应外层不是对象")
        self.budget.billable_estimate += 1
        outer = payload.get("data")
        messages = " ".join(str(payload.get(key) or "") for key in ("message", "detail", "msg"))
        if isinstance(outer, dict): messages += " " + " ".join(str(outer.get(key) or "") for key in ("message", "detail", "msg"))
        code = first_present(payload.get("code"), outer.get("code") if isinstance(outer, dict) else None)
        lowered = messages.lower()
        if code in (401, 402, 403, "401", "402", "403") or any(word in lowered for word in ("unauthorized", "invalid api key", "insufficient balance", "余额不足")):
            self.stopped_reason = "认证失败或余额不足（业务状态）"; raise AuthError(self.stopped_reason)
        if code not in (None, 0, 200, "0", "200") or any(word in lowered for word in ("service error", "服务异常")):
            raise TikHubError(f"{operation} 业务状态异常（可能计费）")
        if not isinstance(outer, dict): raise StructureError("响应缺少 data 对象")
        return payload

    @staticmethod
    def _business(payload: dict):
        outer = payload["data"]
        return outer.get("data", outer)

    @staticmethod
    def _list(payload: dict, fields: tuple[str, ...]) -> list[dict]:
        data = Client._business(payload)
        if isinstance(data, list): return [item for item in data if isinstance(item, dict)]
        if not isinstance(data, dict): raise StructureError("业务数据不是对象或列表")
        for field in fields:
            if field in data:
                value = data[field]
                if isinstance(value, list) and all(isinstance(item, dict) for item in value): return value
                raise StructureError("列表字段类型不正确")
        raise StructureError("响应没有支持的列表字段")

    @staticmethod
    def _note_id(note: dict) -> str | None:
        card = note.get("note") if isinstance(note.get("note"), dict) else note.get("note_card") if isinstance(note.get("note_card"), dict) else note
        return str(first_present(card.get("id"), card.get("note_id"))) if first_present(card.get("id"), card.get("note_id")) else None

    @staticmethod
    def _time(value):
        try:
            epoch = int(value); epoch = epoch // 1000 if epoch > 10**12 else epoch
            return datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds")
        except (TypeError, ValueError, OSError, OverflowError): return value

    @staticmethod
    def _note(note: dict, note_id: str, full: bool = False) -> dict:
        card = note.get("note") if isinstance(note.get("note"), dict) else note.get("note_card") if isinstance(note.get("note_card"), dict) else note
        engagement = card.get("interact_info") if isinstance(card.get("interact_info"), dict) else {}
        excerpt = card.get("desc") or card.get("content") or ""
        return {"note_id": note_id, "title": card.get("title") or card.get("display_title") or "", "type": card.get("type") or card.get("note_type") or "",
                "excerpt": excerpt if full else excerpt[:200], "published_at": Client._time(first_present(card.get("timestamp"), card.get("publish_time"), card.get("time"))),
                "liked_count": first_present(engagement.get("liked_count"), card.get("liked_count"), card.get("likes")),
                "comment_count": first_present(engagement.get("comment_count"), card.get("comments_count"), card.get("comment_count")),
                "collected_count": first_present(engagement.get("collected_count"), card.get("collected_count")), "url": f"https://www.xiaohongshu.com/explore/{note_id}",
                "image_count": len(card.get("image_list") or card.get("images_list") or []), "_license_note": "未保存图片 URL、作者昵称或用户 ID；图片地址不等于转载许可"}

    @staticmethod
    def _comment(comment: dict, comment_id: str) -> dict:
        return {"comment_id": comment_id, "content": (comment.get("content") or "")[:300],
                "published_at": Client._time(first_present(comment.get("create_time"), comment.get("time"))),
                "liked_count": first_present(comment.get("like_count"), comment.get("liked_count")), "sub_comment_count": first_present(comment.get("sub_comment_count"), comment.get("reply_count")), "ip_location": comment.get("ip_location")}

    def _result(self, items, next_token, meta, warnings):
        source = dict(meta, retrieved_at=now_iso(), series="app_v2", response_mode="dry_run" if self.dry_run else self.response_mode)
        usage = self.budget.record()
        return {
            "provider": "tikhub",
            "capability": "social_experience",
            "availability": "available" if items and not self.dry_run else "unavailable",
            "retrieved_at": source["retrieved_at"],
            "coverage": {"series": "xiaohongshu_app_v2", "sample_count": len(items)},
            "source_url": API_ORIGIN + ENDPOINTS.get(meta.get("endpoint"), ""),
            "items": items,
            "next_page_token": next_token,
            "source_meta": source,
            "warnings": warnings,
            "usage": usage,
            "usage_record": usage,
        }

    def search(self, keyword: str, pages: int = 1, sort_type: str = "general", time_filter: str = "不限", note_type: str = "不限") -> dict:
        items, seen, warnings, session = [], set(), [], {}
        for page in range(1, pages + 1):
            params = {"keyword": keyword, "page": page, "sort_type": sort_type, "time_filter": time_filter, "note_type": note_type, **session}
            response = self.request("search", params)
            if response.get("_dry_run"): continue
            raw = self._list(response, ("items", "notes", "note_list")); new = 0
            for note in raw:
                if note.get("model_type") not in (None, "note"): continue
                note_id = self._note_id(note)
                if note_id and note_id not in seen: seen.add(note_id); items.append(self._note(note, note_id)); new += 1
            outer = response["data"]; business = self._business(response)
            session = {key: first_present(outer.get(key), business.get(key) if isinstance(business, dict) else None) for key in ("search_id", "search_session_id")}
            if not new or outer.get("has_more") is False:
                if not new: warnings.append(f"第 {page} 页没有新笔记，停止翻页")
                break
        token = {"page": page + 1, **session} if session and page < pages else None
        return self._result(items, token, {"endpoint": "search", "keyword": keyword, "sort_type": sort_type, "time_filter": time_filter}, warnings)

    def note_detail(self, note_id: str, video: bool = False) -> dict:
        operation = "video_note" if video else "image_note"; response = self.request(operation, {"note_id": note_id})
        if response.get("_dry_run"): return self._result([], None, {"endpoint": operation, "note_id": note_id}, [])
        business = self._business(response); candidate = None
        if isinstance(business, list) and business and isinstance(business[0], dict): candidate = first_present((business[0].get("note_list") or [None])[0], business[0].get("note"))
        elif isinstance(business, dict): candidate = first_present(business.get("note"), business.get("note_card"), (business.get("note_list") or [None])[0])
        if not isinstance(candidate, dict) or self._note_id(candidate) != str(note_id): raise StructureError("详情响应中没有匹配的笔记")
        item = self._note(candidate, note_id, full=True); item["hashtags"] = [tag.get("name") for tag in candidate.get("hash_tag", []) if isinstance(tag, dict) and tag.get("name")]
        warnings = ["视频笔记只保留文字/可读字段，未观看视频内容"] if video else []
        return self._result([item], None, {"endpoint": operation, "note_id": note_id}, warnings)

    def _comments(self, operation: str, params: dict, pages: int) -> dict:
        items, seen, warnings, cursor, last = [], set(), [], params.get("cursor", ""), None
        for _ in range(pages):
            response = self.request(operation, {**params, "cursor": cursor})
            if response.get("_dry_run"): continue
            raw = self._list(response, ("comments", "items")); new = 0
            for comment in raw:
                comment_id = str(first_present(comment.get("id"), comment.get("comment_id")) or "")
                if comment_id and comment_id not in seen: seen.add(comment_id); items.append(self._comment(comment, comment_id)); new += 1
            business = self._business(response); next_cursor = business.get("cursor", "") if isinstance(business, dict) else ""
            decoded = parse_json_string(next_cursor); cursor = decoded.get("cursor", next_cursor) if isinstance(decoded, dict) else next_cursor
            if not new or not cursor or cursor == last:
                if cursor == last: warnings.append("游标未前进，停止翻页")
                cursor = None; break
            last = cursor
        return self._result(items, {"cursor": cursor} if cursor else None, {"endpoint": operation, **{key: value for key, value in params.items() if key != "cursor"}}, warnings)

    def comments(self, note_id: str, pages: int = 1, sort_strategy: str = "latest_v2") -> dict:
        return self._comments("comments", {"note_id": note_id, "index": 0, "pageArea": "UNFOLDED", "sort_strategy": sort_strategy}, pages)

    def sub_comments(self, note_id: str, comment_id: str, pages: int = 1, start_cursor: str = "", start_index: int = 1) -> dict:
        return self._comments("sub_comments", {"note_id": note_id, "comment_id": comment_id, "cursor": start_cursor, "index": start_index}, pages)


def _emit(result: dict, out: str | None) -> None:
    if out: atomic_json(out, result)
    else: print(json.dumps(result, ensure_ascii=False, indent=2))


def _error_result(endpoint: str, warning: str, usage: dict) -> dict:
    return {
        "provider": "tikhub",
        "capability": "social_experience",
        "availability": "error",
        "retrieved_at": now_iso(),
        "coverage": None,
        "source_url": API_ORIGIN + ENDPOINTS.get(endpoint, ""),
        "items": [],
        "next_page_token": None,
        "source_meta": {"endpoint": endpoint, "response_mode": "live"},
        "warnings": [warning],
        "usage": usage,
        "usage_record": usage,
    }


def main(argv=None) -> int:
    configure_console(); parser = argparse.ArgumentParser(description="TikHub 小红书体验线索适配器")
    parser.add_argument("cmd", choices=("search", "note", "video", "comments", "replies")); parser.add_argument("args", nargs="*")
    parser.add_argument("--pages", type=int, default=1); parser.add_argument("--budget", type=int, default=None); parser.add_argument("--usage-file")
    parser.add_argument("--sort"); parser.add_argument("--time-filter", default="不限"); parser.add_argument("--cursor", default=""); parser.add_argument("--index", type=int, default=1); parser.add_argument("--token-env", default="TIKHUB_API_KEY"); parser.add_argument("--out")
    mode = parser.add_mutually_exclusive_group(required=True); mode.add_argument("--live", action="store_true"); mode.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv); needed = 2 if args.cmd == "replies" else 1
    if len(args.args) < needed or args.pages < 1: parser.error("参数不足或页数无效")
    if args.budget is None:
        if args.live: parser.error("真实调用必须显式指定 --budget；不会默认为付费请求设置预算")
        args.budget = 40
    if args.budget < 0: parser.error("预算不能为负数")
    if args.live and not args.usage_file: parser.error("真实调用必须指定 --usage-file")
    token = os.environ.get(args.token_env) if args.live else None
    try: budget = Budget(args.budget) if args.dry_run else PersistentBudget(args.budget, args.usage_file)
    except (ValueError, OSError, TikHubError): print("✖ 消费账本不可用；未发起请求", file=sys.stderr); return 4
    client = Client(token, budget, dry_run=args.dry_run, log=lambda message: print(message, file=sys.stderr))
    try:
        if args.cmd == "search": result = client.search(" ".join(args.args), args.pages, args.sort or "general", args.time_filter)
        elif args.cmd in ("note", "video"): result = client.note_detail(args.args[0], video=args.cmd == "video")
        elif args.cmd == "comments": result = client.comments(args.args[0], args.pages, args.sort or "latest_v2")
        else: result = client.sub_comments(args.args[0], args.args[1], args.pages, args.cursor, args.index)
    except AuthError as error:
        result = _error_result(args.cmd, str(error), budget.record()); _emit(result, args.out); return 3
    except BudgetExceeded as error:
        _emit(_error_result(args.cmd, str(error), budget.record()), args.out); return 4
    except TikHubError as error:
        result = _error_result(args.cmd, str(error), budget.record()); _emit(result, args.out); return 5
    finally: budget.close()
    _emit(result, args.out); print(f"✔ {args.cmd}: {len(result['items'])} 条 · 请求 {budget.used}/{budget.limit}", file=sys.stderr); return 0


if __name__ == "__main__": sys.exit(main())
