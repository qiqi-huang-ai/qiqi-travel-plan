#!/usr/bin/env python3
"""Keyless Open-Meteo forecast adapter with explicit coverage metadata."""
from __future__ import annotations
import argparse, json, sys, urllib.error, urllib.parse, urllib.request
from datetime import date, datetime

def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")

def result(availability, items=None, warnings=None, usage=None, **extra):
    data = {
        "provider": "open-meteo",
        "capability": "weather",
        "availability": availability,
        "retrieved_at": now(),
        "items": items or [],
        "warnings": warnings or [],
        "usage": usage or {"requests": 0},
    }
    data.update(extra)
    return data

def fetch(latitude, longitude, start, end=None, timezone="auto"):
    end = end or start
    q=urllib.parse.urlencode({"latitude":latitude,"longitude":longitude,"daily":"weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max","start_date":start,"end_date":end,"timezone":timezone})
    url="https://api.open-meteo.com/v1/forecast?" + q
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise urllib.error.HTTPError(req.full_url, code, "redirect rejected", headers, fp)
    try:
        with urllib.request.build_opener(NoRedirect).open(url, timeout=15) as r:
            if urllib.parse.urlparse(r.geturl()).hostname != "api.open-meteo.com":
                raise RuntimeError("天气响应域名不在允许列表")
            payload=json.loads(r.read().decode("utf-8","replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError,
            ValueError, RuntimeError, json.JSONDecodeError):
        return result("error", warnings=["天气请求失败"], usage={"requests": 1})
    daily=payload.get("daily") if isinstance(payload,dict) else None
    if not isinstance(daily,dict) or not isinstance(daily.get("time"),list) or not daily["time"]:
        return result("error", warnings=["天气响应结构不符或没有目标日期数据"], usage={"requests": 1})
    items=[]
    for i,d in enumerate(daily["time"]):
        def at(name):
            values = daily.get(name) or []
            return values[i] if i < len(values) else None
        items.append({"date":d,"weather_code":at("weather_code"),"temp_max":at("temperature_2m_max"),"temp_min":at("temperature_2m_min"),"precipitation_probability_max":at("precipitation_probability_max")})
    return result("available", items=items, usage={"requests": 1}, coverage={"from":items[0]["date"],"to":items[-1]["date"]}, timezone=payload.get("timezone"), source_url=url)

def main(argv=None):
    p=argparse.ArgumentParser()
    live=p.add_mutually_exclusive_group(required=True)
    live.add_argument("--live",action="store_true",help="明确发起真实天气请求")
    live.add_argument("--dry-run",action="store_true",help="仅显示请求计划，不联网")
    p.add_argument("latitude",type=float); p.add_argument("longitude",type=float)
    p.add_argument("--start",default=date.today().isoformat()); p.add_argument("--end"); p.add_argument("--timezone",default="auto"); a=p.parse_args(argv)
    if a.dry_run:
        output = result("dry_run", request={"latitude":a.latitude,"longitude":a.longitude,
                        "start":a.start,"end":a.end or a.start,"timezone":a.timezone})
    else:
        output = fetch(a.latitude, a.longitude, a.start, a.end, a.timezone)
    print(json.dumps(output,ensure_ascii=False,indent=2))
    return 0 if output["availability"] in ("available", "dry_run") else 1
if __name__=="__main__": sys.exit(main())
