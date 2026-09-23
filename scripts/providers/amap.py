#!/usr/bin/env python3
"""Budgeted Amap adapter that emits normalized, credential-free JSON."""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from trip_support import atomic_json

BASE = "https://restapi.amap.com"
ALLOWED = "restapi.amap.com"


class BudgetExceeded(RuntimeError):
    pass


class RequestBudget:
    """Fail-closed ledger shared by all Amap commands for one trip."""

    def __init__(self, limit: int, path: str):
        if limit < 0:
            raise ValueError("请求上限不能为负数")
        self.path = os.path.abspath(path)
        self.lock_path = self.path + ".lock"
        self.limit, self.used, self.lock_fd = limit, 0, None
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        try:
            self.lock_fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise RuntimeError("高德请求账本正在使用或存在中断锁") from None
        try:
            if os.path.exists(self.path):
                with open(self.path, encoding="utf-8") as handle:
                    saved = json.load(handle)
                if (not isinstance(saved, dict) or type(saved.get("requests")) is not int
                        or type(saved.get("limit")) is not int or saved["requests"] < 0
                        or saved["requests"] > saved["limit"] or limit > saved["limit"]):
                    raise ValueError("高德请求账本损坏或未授权提高上限")
                self.limit, self.used = min(limit, saved["limit"]), saved["requests"]
            self.persist()
        except Exception:
            self.close()
            raise

    def reserve(self):
        if self.used + 1 > self.limit:
            raise BudgetExceeded(f"高德请求预算已用 {self.used}/{self.limit}")
        self.used += 1
        self.persist()

    def persist(self):
        atomic_json(self.path, self.record())

    def record(self):
        return {"requests": self.used, "limit": self.limit}

    def close(self):
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None
            try:
                os.unlink(self.lock_path)
            except FileNotFoundError:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def result(capability, availability, items=None, warnings=None, usage=None, **extra):
    data = {"provider":"amap", "capability":capability, "availability":availability,
            "retrieved_at":now(), "items":items if items is not None else [],
            "warnings":warnings or [], "usage":usage or {"requests":0}}
    data.update(extra)
    return data


def _number(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _minutes(seconds):
    value = _number(seconds)
    return math.ceil(value / 60) if value is not None else None


def _location(value):
    try:
        lng, lat = str(value).split(",", 1)
        return {"lng":float(lng), "lat":float(lat)}
    except (TypeError, ValueError):
        return None


def normalize_payload(capability: str, payload: dict, mode: str | None = None) -> list[dict]:
    """Keep stable planning fields and never persist raw provider bodies."""
    if capability == "geocode":
        return [{"name":x.get("formatted_address"), "address":x.get("formatted_address"),
                 "city":x.get("city") or x.get("province"), "district":x.get("district"),
                 "location":_location(x.get("location")), "level":x.get("level")}
                for x in payload.get("geocodes", [])]
    if capability == "place_search":
        return [{"provider_place_id":x.get("id"), "name":x.get("name"),
                 "address":x.get("address") if isinstance(x.get("address"), str) else None,
                 "city":x.get("cityname"), "district":x.get("adname"),
                 "location":_location(x.get("location")), "type":x.get("type")}
                for x in payload.get("pois", [])]
    route = payload.get("route") if isinstance(payload.get("route"), dict) else {}
    paths = route.get("transits") if mode == "transit" else route.get("paths")
    items = []
    for path in paths or []:
        walking_distance = _number(path.get("walking_distance"), 0) or 0
        segments = path.get("segments") if isinstance(path.get("segments"), list) else []
        busline_count = sum(len((segment.get("bus") or {}).get("buslines") or []) for segment in segments)
        items.append({"mode":mode, "duration_min":_minutes(path.get("duration")),
                      "distance_m":int(_number(path.get("distance"), 0) or 0),
                      "walking_min":math.ceil(walking_distance / 80) if walking_distance else 0,
                      "transfers":max(0, busline_count - 1) if mode == "transit" else 0,
                      "fare_cny":_number(path.get("cost") or path.get("tolls"))})
    return items


def call(path, params, timeout=15, budget: RequestBudget | None = None):
    capability = "route" if "/direction/" in path else ("geocode" if path.endswith("/geo") else "place_search")
    key = os.environ.get("AMAP_API_KEY")
    if not key:
        return result(capability, "unavailable", warnings=["未配置 AMAP_API_KEY；未发起请求"])
    query = {k:v for k, v in params.items() if v is not None and v != ""}
    mode = query.pop("_mode", None)
    public_query = dict(query)
    query.update({"key":key, "output":"json"})
    url = BASE + path + "?" + urllib.parse.urlencode(query)
    request = urllib.request.Request(url, headers={"Accept":"application/json", "User-Agent":"travel-plan-pro/1.1"})

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise urllib.error.HTTPError(req.full_url, code, "redirect rejected", headers, fp)

    try:
        if budget:
            budget.reserve()
        with urllib.request.build_opener(NoRedirect).open(request, timeout=timeout) as response:
            if urllib.parse.urlparse(response.geturl()).hostname != ALLOWED:
                raise RuntimeError("Amap 响应域名不在允许列表")
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except BudgetExceeded as exc:
        return result(capability, "error", warnings=[str(exc)], usage=budget.record())
    except urllib.error.HTTPError as exc:
        return result(capability, "error", warnings=[f"HTTP {exc.code}（未输出响应正文）"], usage=budget.record() if budget else {"requests":1})
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, RuntimeError):
        return result(capability, "error", warnings=["请求失败，未输出密钥或响应正文"], usage=budget.record() if budget else {"requests":1})
    if not isinstance(payload, dict) or payload.get("status") != "1":
        return result(capability, "error", warnings=["Amap 业务响应失败"], usage=budget.record() if budget else {"requests":1})
    items = normalize_payload(capability, payload, mode)
    if not items:
        return result(capability, "error", warnings=["Amap 响应无可用规范化数据"], usage=budget.record() if budget else {"requests":1})
    return result(capability, "available", items=items,
                  usage=budget.record() if budget else {"requests":1},
                  query={**public_query, "mode":mode} if mode else public_query)


def main(argv=None):
    parser = argparse.ArgumentParser(description="高德 Web Service 安全适配器")
    parser.add_argument("--budget", type=int, default=20, help="用户已同意的本行程请求上限")
    parser.add_argument("--usage-file", help="本行程共享的高德请求账本")
    live = parser.add_mutually_exclusive_group(required=True)
    live.add_argument("--live", action="store_true", help="明确读取密钥并发起真实请求")
    live.add_argument("--dry-run", action="store_true", help="仅显示请求计划，不读密钥、不联网")
    sub = parser.add_subparsers(dest="cmd", required=True)
    geocode = sub.add_parser("geocode"); geocode.add_argument("address"); geocode.add_argument("--city")
    search = sub.add_parser("search"); search.add_argument("keywords"); search.add_argument("--city")
    route = sub.add_parser("route"); route.add_argument("origin"); route.add_argument("destination"); route.add_argument("--mode", choices=["driving","walking","transit"], default="driving"); route.add_argument("--city")
    args = parser.parse_args(argv)
    if args.live and not args.usage_file:
        parser.error("真实调用必须指定同一行程共享的 --usage-file")
    if args.cmd == "geocode":
        path, params = "/v3/geocode/geo", {"address":args.address, "city":args.city}
    elif args.cmd == "search":
        path, params = "/v3/place/text", {"keywords":args.keywords, "city":args.city, "citylimit":"true" if args.city else None}
    else:
        path = {"driving":"/v3/direction/driving", "walking":"/v3/direction/walking", "transit":"/v3/direction/transit/integrated"}[args.mode]
        params = {"origin":args.origin, "destination":args.destination, "city":args.city,
                  "strategy":"0" if args.mode == "driving" else None, "_mode":args.mode}
    if args.dry_run:
        capability = "route" if args.cmd == "route" else ("geocode" if args.cmd == "geocode" else "place_search")
        output = result(capability, "dry_run", request={"path":path, "params":params})
    else:
        try:
            with RequestBudget(args.budget, args.usage_file) as budget:
                output = call(path, params, budget=budget)
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            output = result("route" if args.cmd == "route" else args.cmd, "error", warnings=[str(exc)])
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output.get("availability") in ("available", "unavailable", "dry_run") else 1


if __name__ == "__main__":
    sys.exit(main())
