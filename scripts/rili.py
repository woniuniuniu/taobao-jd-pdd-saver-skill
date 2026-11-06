#!/usr/bin/env python3
"""蜗牛最爽省钱 Skill · 抢购日历：把大促时间点写成 .ics 日历文件，提前提醒。

双击生成的 .ics 就能导入苹果日历 / Outlook / 企业微信日历。
只依赖 Python 标准库。

用法：
  python3 rili.py events.json -o 双十一抢购日历.ics
  echo '[...]' | python3 rili.py - -o 双十一抢购日历.ics

events.json 是列表，每项：
  {"title": "京东 iPhone 18 Pro 0点开抢", "start": "2026-11-11 00:00",
   "minutes": 30, "remind": [60, 10], "note": "券-300 已领", "url": "https://item.jd.com/..."}
  minutes 默认 30；remind 是提前几分钟提醒，默认 [30]。时间按北京时间。
"""
import argparse
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone

CST = timedelta(hours=8)


def _esc(s):
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _utc(dt_cst):
    """北京时间 → UTC 写法，所有日历软件都能正确识别，不依赖时区定义。"""
    return (dt_cst - CST).strftime("%Y%m%dT%H%M%SZ")


def build_ics(events):
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//chaoji-shengshengsheng//rili//CN",
             "CALSCALE:GREGORIAN", "X-WR-CALNAME:蜗牛最爽省钱 Skill 抢购日历", "X-WR-TIMEZONE:Asia/Shanghai"]
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for e in events:
        start = datetime.strptime(e["start"], "%Y-%m-%d %H:%M")
        end = start + timedelta(minutes=int(e.get("minutes", 30)))
        desc = "\n".join(x for x in [e.get("note"), e.get("url")] if x)
        lines += ["BEGIN:VEVENT", f"UID:{uuid.uuid4()}@chaoji-shengshengsheng", f"DTSTAMP:{now}",
                  f"DTSTART:{_utc(start)}", f"DTEND:{_utc(end)}",
                  f"SUMMARY:{_esc(e['title'])}"]
        if desc:
            lines.append(f"DESCRIPTION:{_esc(desc)}")
        if e.get("url"):
            lines.append(f"URL:{e['url']}")
        for m in e.get("remind", [30]):
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_esc(e['title'])}",
                      f"TRIGGER:-PT{int(m)}M", "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def main():
    ap = argparse.ArgumentParser(description="生成抢购日历 .ics")
    ap.add_argument("events", help="事件 JSON 文件路径，- 表示从标准输入读")
    ap.add_argument("-o", "--out", default="抢购日历.ics")
    a = ap.parse_args()
    raw = sys.stdin.read() if a.events == "-" else open(a.events, encoding="utf-8").read()
    events = json.loads(raw)
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        f.write(build_ics(events))
    print(f"已生成 {a.out}，共 {len(events)} 个时间点")


if __name__ == "__main__":
    main()
