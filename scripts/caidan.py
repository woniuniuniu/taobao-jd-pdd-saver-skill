#!/usr/bin/env python3
"""蜗牛最爽省钱 Skill · 彩蛋：省钱战报、成就、等级，值得庆祝时放一场烟花。

每次比价（或盯价）后运行一次，参数和 huizong.py 一样。它会：
  1. 算这次省了多少：选最低价，比同款最贵的那个平台便宜多少（只有一个平台时，比划线价便宜多少）；
  2. 记一笔账（~/WorkBuddy/.chaoji-caidan/，每次新建文件，不覆盖），累计一共帮用户省了多少；
  3. 检查成就有没有新解锁，算等级；
  4. 输出一段 Markdown 战报，原样贴在比价表后面；
  5. 值得庆祝时在 Mac 上庆祝（--quiet 或用户关了彩蛋时都不弹）：
     - 放烟花：盯价到价、等级提升、解锁隐藏成就、单次省 ¥1,000 以上；除了到价，24 小时内最多放一次；
     - 只弹通知：解锁普通成就。

只依赖 Python 标准库；弹通知用 osascript，打开页面用 open。

用法：
  python3 caidan.py mirror-1.json mirror-2.json bijia.json [--ref ref.json] [--drop ...]   # 比价后
  python3 caidan.py ... --event alert        # 盯价到价时（dingjia.py 会自动调用）
  python3 caidan.py --show                   # 看成就墙和累计战绩
  python3 caidan.py --off / --on             # 关掉 / 打开烟花和通知（文字战报照常）
  python3 caidan.py --fireworks              # 用户说「放个烟花」时：直接放一场
  python3 caidan.py --intro                  # 第一次使用时的自我介绍（只输出一次；--force 每次都输出）
"""
import argparse
import glob
import html
import re
import importlib.util
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOME_DIR = os.environ.get("CHAOJI_CAIDAN_DIR") or os.path.expanduser("~/WorkBuddy/.chaoji-caidan")  # 环境变量只给测试用


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hz = _load("huizong")

# 等级：按累计省下的钱
LEVELS = [(0, "🌱 省钱学徒"), (500, "🛒 精打细算"), (2000, "💰 省钱达人"), (5000, "💎 省钱大师"), (20000, "👑 省钱之神")]

# 成就：(id, 图标, 名字, 说明, 是否隐藏)。隐藏成就解锁前在成就墙上只显示「？？？」
ACHIEVEMENTS = [
    ("first", "🐣", "初来乍到", "第一次用蜗牛最爽省钱 Skill比价", False),
    ("radar", "🔭", "全网雷达", "一次比价看了 4 个以上平台的价格", False),
    ("phone", "📱", "手机侦探", "最低价是在你自己手机 App 里挖出来的", False),
    ("eagle", "🦅", "火眼金睛", "累计 3 次揪出低得离谱、可能不是正品的店", False),
    ("customs", "🛂", "海关缉私", "累计 3 次拦下海外版 / 全球购", False),
    ("big", "🎇", "一顿火锅钱", "一次比价省下 ¥300 以上", False),
    ("huge", "🚀", "省出一部手机", "一次比价省下 ¥1,000 以上", False),
    ("three", "🧐", "货比三家", "同一件商品比价 3 次以上", False),
    ("wait", "🎯", "守株待兔", "盯价等到了目标价", False),
    ("streak", "🔁", "风雨无阻", "同一件商品盯价 7 次以上", False),
    ("night", "🌙", "深夜剁手党", "在 23 点到 5 点之间比价", True),
    ("lucky", "🧧", "吉利价", "最低价里有 666、888 或 999", True),
    ("double11", "🛍️", "双十一战士", "在 11 月 1 日到 11 日之间比价", True),
    ("same", "🤝", "三家同价", "三个平台给出了一模一样的最低价", True),
]
ACH = {a[0]: a for a in ACHIEVEMENTS}


# 省下的钱能换成什么（挑最接近的一个，逗个乐）
FUN_UNITS = [(15, "🥤", "杯奶茶"), (40, "🎬", "张电影票"), (120, "🍲", "顿火锅"), (300, "💆", "次按摩"),
             (700, "🎧", "副入门耳机"), (2000, "✈️", "张特价机票")]

INTRO = """👋 我是「蜗牛最爽省钱 Skill」，你的省钱小助手。我能帮你：

1. 🔍 **全网比价**：说「帮我比一下 iPhone 18 Pro 256GB」或「三只松鼠每日坚果哪里便宜」，我在你手机的京东、淘宝、拼多多 App 里实时查价，再参考最新资料补抖音等平台的参考价，给你一张表和结论。
2. 📸 **拍图比价**：发一张商品图片，我认出型号再比价。
3. ⏰ **盯价提醒**：说「帮我盯 iPhone 18 Pro，降到 9000 提醒我」，我每天定时查，到价就在桌面提醒器和 Mac 上提醒你，还会记一本价格日记。
4. 📅 **抢购日历**：把双十一等大促的关键时间排进你的日历。
5. 🏆 **省钱成就**：每次比价都有省钱战报，攒成就、升等级，偶尔还会放烟花。说「看看我的成就」看看你的成就墙。

用之前把 iPhone 锁屏放在 Mac 旁边就行：iPhone 镜像没开我会帮你打开，连不上会告诉你怎么连。"""


def fun_unit(saved):
    unit = min(FUN_UNITS, key=lambda u: abs(saved / u[0] - 3))  # 换算出来大约 3 份左右最有画面感
    n = saved / unit[0]
    return f"{unit[1]} 约等于 {n:.0f} {unit[2]}" if n >= 1 else ""


def money(v):
    return hz.money(v)


def ensure_dir():
    os.makedirs(HOME_DIR, exist_ok=True)


def new_file(prefix, ext="json"):
    stamp = "%s-%06d" % (time.strftime("%Y%m%d-%H%M%S"), int(time.time() * 1e6) % 1000000)
    return os.path.join(HOME_DIR, f"{prefix}-{stamp}.{ext}")


def load_events():
    out = []
    for p in sorted(glob.glob(os.path.join(HOME_DIR, "event-*.json"))):
        try:
            with open(p, encoding="utf-8") as f:
                out.append(json.load(f))
        except (OSError, ValueError):
            pass
    return out


def fun_enabled():
    """最近一次 --on/--off 的设置（每次新建文件，取最新的）。默认打开。"""
    files = sorted(glob.glob(os.path.join(HOME_DIR, "config-*.json")))
    if not files:
        return True
    try:
        with open(files[-1], encoding="utf-8") as f:
            return bool(json.load(f).get("fun", True))
    except (OSError, ValueError):
        return True


def rejected_reasons(paths):
    """手机查价文件里被排除的商品原因（用来解锁「火眼金睛」「海关缉私」）。"""
    whys = []
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        whys += [r.get("why") or "" for r in d.get("rejected") or []]
    return whys


def summarize_run(a):
    data = hz.load_many(a.files, a.keyword, a.spec)
    refs = hz.load_refs(a.ref)
    best, plain, skipped, dropped = hz.summarize(data, a.drop)
    if not best:
        return None
    low = hz.pick(best.values())  # 和小票一样：有自营 / 旗舰店就先用它们的价
    high = max(best.values(), key=lambda i: i["price"])
    if len(best) >= 2 and high["price"] > low["price"]:
        saved, vs = high["price"] - low["price"], f"比最贵的{high['platform']}（¥{money(high['price'])}）"
    elif low["original"] > low["price"]:
        saved, vs = low["original"] - low["price"], f"比划线价（¥{money(low['original'])}）"
    else:
        saved, vs = 0.0, ""
    whys = rejected_reasons(a.files)
    lows = sorted(i["price"] for i in best.values())
    return {
        "time": time.strftime("%Y-%m-%d %H:%M"), "hour": time.localtime().tm_hour,
        "month_day": time.strftime("%m-%d"), "event": a.event,
        "keyword": data["keyword"], "spec": data["spec"],
        "lowest": {k: low[k] for k in ("platform", "price", "shop", "source")},
        "saved": round(saved, 2), "vs": vs,
        "platforms": len(set(best) | set(refs)),
        "realtime": len(best),
        "eagle": any("可能不是正品" in w for w in whys),
        "customs": any(w.startswith("非国行") or w.startswith("海外/全球购") for w in whys),
        "same3": any(lows.count(p) >= 3 for p in lows),
    }


def unlocked(events):
    """根据全部记录算出已解锁的成就 → {id: 第一次解锁的时间}。"""
    got = {}

    def give(k, ev):
        got.setdefault(k, ev["time"])

    per_item = {}
    n_eagle = n_customs = 0
    for ev in events:
        item = (hz._norm(ev["keyword"]), ev.get("spec") or "")
        per_item.setdefault(item, {"compare": 0, "watch": 0})
        per_item[item]["watch" if ev["event"] in ("watch", "alert") else "compare"] += 1
        if ev["event"] == "alert":
            give("wait", ev)
        if per_item[item]["watch"] >= 7:
            give("streak", ev)
        if ev["event"] == "watch":
            continue  # 定时盯价的例行检查只算盯价次数，不解锁比价类成就
        if ev["event"] == "compare":
            give("first", ev)
        if ev["platforms"] >= 4:
            give("radar", ev)
        if (ev.get("lowest") or {}).get("source") == "手机App":
            give("phone", ev)
        n_eagle += bool(ev.get("eagle"))
        n_customs += bool(ev.get("customs"))
        if n_eagle >= 3:
            give("eagle", ev)
        if n_customs >= 3:
            give("customs", ev)
        if ev["saved"] >= 300:
            give("big", ev)
        if ev["saved"] >= 1000:
            give("huge", ev)
        if per_item[item]["compare"] >= 3:
            give("three", ev)
        if ev["hour"] >= 23 or ev["hour"] < 5:
            give("night", ev)
        price = str((ev.get("lowest") or {}).get("price") or "")
        if any(x in price for x in ("666", "888", "999")):
            give("lucky", ev)
        if "11-01" <= ev["month_day"] <= "11-11":
            give("double11", ev)
        if ev.get("same3"):
            give("same", ev)
    return got


def total_saved(events):
    """一共帮用户省了多少：每件商品只算一次（取省得最多的那次），定时盯价的例行检查不算。"""
    per = {}
    for e in events:
        if e["event"] == "watch":
            continue
        item = (hz._norm(e["keyword"]), e.get("spec") or "")
        per[item] = max(per.get(item, 0), e["saved"])
    return sum(per.values())


def level(total):
    cur, nxt = LEVELS[0], None
    for i, lv in enumerate(LEVELS):
        if total >= lv[0]:
            cur, nxt = lv, (LEVELS[i + 1] if i + 1 < len(LEVELS) else None)
    return cur, nxt


def notify(title, text):
    script = f'display notification {json.dumps(text, ensure_ascii=False)} with title {json.dumps(title, ensure_ascii=False)} sound name "Glass"'
    subprocess.run(["osascript", "-e", script], capture_output=True)


def last_fireworks():
    files = sorted(glob.glob(os.path.join(HOME_DIR, "fireworks-*.html")))
    return os.path.getmtime(files[-1]) if files else 0


def fireworks(headline, sub, badges):
    """写一个自包含的烟花页面（不联网）并用默认浏览器打开。返回文件路径。"""
    ensure_dir()
    path = new_file("fireworks", "html")
    cards = "".join(f'<div class="badge" style="animation-delay:{1.2 + i * 0.45:.2f}s"><span>{html.escape(b[0])}</span>'
                    f'<b>{html.escape(b[1])}</b><i>{html.escape(b[2])}</i></div>' for i, b in enumerate(badges))
    page = FIREWORKS_HTML.replace("{{HEADLINE}}", html.escape(headline)).replace("{{SUB}}", html.escape(sub)).replace("{{BADGES}}", cards)
    with open(path, "x", encoding="utf-8") as f:
        f.write(page)
    subprocess.run(["open", path], capture_output=True)
    return path


def plain(v):
    """去掉等级、成就名前面的表情，文字报告里只留字。"""
    return re.sub(r"^[^\w\u4e00-\u9fff]+", "", v).strip()


def report(ev, new_ids, events):
    """战报：一个小标题，下面几行短句，用中点分隔；表情只留在成就墙和通知里。"""
    total = total_saved(events)
    cur, nxt = level(total)
    lines = ["**省钱战报**"]
    if ev["event"] == "alert":
        lines.append(f"到价了：{ev['keyword']} {ev['spec']} 已经降到目标价以下。")
    if ev["saved"] > 0:
        fun = fun_unit(ev["saved"])
        lines.append(f"选{ev['lowest']['platform']} ¥{money(ev['lowest']['price'])}，{ev['vs']}省 **¥{money(ev['saved'])}**"
                     + (f" · {plain(fun)}" if fun else ""))
    elif ev.get("realtime", 2) < 2:
        lines.append(f"这次只有{ev['lowest']['platform']}有实时价，没有差价可算；多查几个平台才知道能省多少。")
    else:
        lines.append("几个平台价格一样，这次没有差价可省——至少确认了没被宰。")
    item = (hz._norm(ev["keyword"]), ev.get("spec") or "")
    prev = [e for e in events[:-1] if (hz._norm(e["keyword"]), e.get("spec") or "") == item and e.get("lowest")]
    if prev and ev["event"] == "compare":
        p = prev[-1]["lowest"]["price"]
        d = ev["lowest"]["price"] - p
        when = prev[-1]["time"][5:10]
        lines.append(f"和 {when} 比（¥{money(p)}）：" +
                     ("持平" if abs(d) < 0.01 else f"便宜了 ¥{money(-d)}" if d < 0 else f"贵了 ¥{money(d)}，不急可以说「帮我盯价」"))
    for k in new_ids[:3]:  # 一次最多展示 3 个，不刷屏
        _, icon, name, desc, hidden = ACH[k]
        lines.append(f"解锁成就 {icon} **{name}**" + ("（隐藏）" if hidden else "") + f" — {desc}")
    if len(new_ids) > 3:
        lines.append(f"另有 {len(new_ids) - 3} 个成就解锁，说「看看我的成就」去成就墙看看。")
    n_compare = sum(1 for e in events if e["event"] == "compare")
    n_watch = sum(1 for e in events if e["event"] != "compare")
    up = f"，再省 ¥{money(nxt[0] - total)} 升「{plain(nxt[1])}」" if nxt else "（最高级）"
    got = unlocked(events)
    lines.append(f"累计 比价 {n_compare} 次 · 盯价 {n_watch} 次 · 省下 ¥{money(total)} · 「{plain(cur[1])}」{up} · 成就 {len(got)}/{len(ACHIEVEMENTS)}")
    return "\n---\n\n" + "  \n".join(lines)


def show():
    events = load_events()
    got = unlocked(events)
    total = total_saved(events)
    cur, nxt = level(total)
    out = [f"## 🏅 蜗牛最爽省钱 Skill · 成就墙（{len(got)}/{len(ACHIEVEMENTS)}）", "",
           f"累计比价 {sum(1 for e in events if e['event'] == 'compare')} 次，盯价 {sum(1 for e in events if e['event'] != 'compare')} 次，"
           f"一共帮你省了 **¥{money(total)}**，等级 **{cur[1]}**" + (f"（再省 ¥{money(nxt[0] - total)} 升级「{nxt[1]}」）" if nxt else ""), "",
           "| | 成就 | 说明 | 解锁时间 |", "|---|---|---|---|"]
    for k, icon, name, desc, hidden in ACHIEVEMENTS:
        if k in got:
            out.append(f"| {icon} | **{name}** | {desc} | {got[k]} |")
        else:
            out.append(f"| 🔒 | {'？？？' if hidden else name} | {'隐藏成就，自己发现吧' if hidden else desc} | — |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="蜗牛最爽省钱 Skill · 彩蛋（战报、成就、烟花）")
    ap.add_argument("files", nargs="*")
    ap.add_argument("--ref")
    ap.add_argument("--keyword")
    ap.add_argument("--spec")
    ap.add_argument("--drop", action="append", default=[], type=hz.parse_drop)
    ap.add_argument("--event", choices=["compare", "watch", "alert"], default="compare")
    ap.add_argument("--quiet", action="store_true", help="只输出文字战报，不弹通知、不放烟花")
    ap.add_argument("--show", action="store_true", help="看成就墙")
    ap.add_argument("--off", action="store_true", help="关掉烟花和通知")
    ap.add_argument("--on", action="store_true", help="打开烟花和通知")
    ap.add_argument("--fireworks", action="store_true", help="直接放一场烟花")
    ap.add_argument("--intro", action="store_true", help="第一次使用时的自我介绍（只输出一次）")
    ap.add_argument("--force", action="store_true", help="和 --intro 一起用：不管是不是第一次都输出")
    a = ap.parse_args()
    ensure_dir()

    if a.off or a.on:
        with open(new_file("config"), "x", encoding="utf-8") as f:
            json.dump({"fun": bool(a.on)}, f)
        print("🎆 烟花和通知已打开。" if a.on else "🔕 烟花和通知已关闭，之后只有文字战报。说「打开彩蛋」可以再打开。")
        return 0
    if a.intro:
        seen = glob.glob(os.path.join(HOME_DIR, "intro-*.json"))
        if seen and not a.force:
            return 0  # 已经介绍过了，什么都不输出
        if not seen:
            with open(new_file("intro"), "x", encoding="utf-8") as f:
                json.dump({"time": time.strftime("%Y-%m-%d %H:%M")}, f)
        print(INTRO)
        return 0
    if a.show:
        print(show())
        return 0
    if a.fireworks:
        events = load_events()
        total = total_saved(events)
        fireworks("🎆 砰！", f"蜗牛最爽省钱 Skill已经帮你省了 ¥{money(total)}", [])
        print("🎆 烟花已经在你的 Mac 上放起来了！")
        return 0
    if not a.files:
        ap.error("需要比价结果文件（和 huizong.py 的参数一样）")

    ev = summarize_run(a)
    if ev is None:
        print("\n---\n\n**省钱战报**  \n这次没读到实时价，下次再算。")
        return 0
    before = unlocked(load_events())
    with open(new_file("event"), "x", encoding="utf-8") as f:
        json.dump(ev, f, ensure_ascii=False)
    events = load_events()
    after = unlocked(events)
    new_ids = [k for k, _, _, _, _ in ACHIEVEMENTS if k in after and k not in before]
    print(report(ev, new_ids, events))

    before_level = level(total_saved(events[:-1]))[0]
    after_level = level(total_saved(events))[0]
    level_up = after_level != before_level
    hidden_new = [k for k in new_ids if ACH[k][4]]
    big_party = ev["event"] == "alert" or level_up or hidden_new or ev["saved"] >= 1000
    if level_up:
        print(f"🎉 **升级啦**：你现在是「{after_level[1]}」！")
    quiet = a.quiet or os.environ.get("CHAOJI_CAIDAN_QUIET") or not fun_enabled()
    cooled = ev["event"] == "alert" or time.time() - last_fireworks() > 24 * 3600
    if quiet:
        return 0
    if (new_ids or level_up) and not (big_party and cooled):
        notify("蜗牛最爽省钱 Skill", "、".join([f"升级「{after_level[1]}」"] * level_up + ["成就解锁：" + "、".join(ACH[k][1] + ACH[k][2] for k in new_ids)] * bool(new_ids)))
    if big_party and cooled:
        badges = [(ACH[k][1], ACH[k][2], ACH[k][3]) for k in new_ids]
        if level_up:
            badges.insert(0, (after_level[1].split()[0], "升级", after_level[1].split()[1]))
        if ev["event"] == "alert":
            head, sub = "🎯 到价啦！", f"{ev['keyword']} {ev['spec']} · {ev['lowest']['platform']} ¥{money(ev['lowest']['price'])}"
        elif ev["saved"] > 0:
            head, sub = f"省 ¥{money(ev['saved'])}！", f"{ev['keyword']} {ev['spec']} · 选{ev['lowest']['platform']} ¥{money(ev['lowest']['price'])}"
        else:
            head, sub = "🏆 成就解锁！", f"{ev['keyword']} {ev['spec']}"
        fireworks(head, sub, badges)
        notify("蜗牛最爽省钱 Skill", (f"成就解锁：{'、'.join(ACH[k][2] for k in new_ids)}　" if new_ids else "") + f"{head} {sub}")
    return 0


FIREWORKS_HTML = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>蜗牛最爽省钱 Skill · 烟花</title>
<style>
  :root { --gold:#ffd166; --pink:#ff5c8a; --ink:#0b0d1a; }
  html,body { margin:0; height:100%; background:radial-gradient(ellipse at bottom,#1b1f3b 0%,var(--ink) 70%); overflow:hidden;
    font-family:-apple-system,"PingFang SC","Helvetica Neue",sans-serif; color:#fff; cursor:crosshair; }
  canvas { position:fixed; inset:0; width:100%; height:100%; display:block; }
  .stage { position:fixed; inset:0; display:flex; flex-direction:column; align-items:center; justify-content:center; pointer-events:none; text-align:center; padding:16px; }
  h1 { margin:0; font-size:clamp(48px,11vw,128px); font-weight:900; letter-spacing:2px;
    background:linear-gradient(90deg,var(--gold),#fff,var(--pink),var(--gold)); background-size:300% 100%;
    -webkit-background-clip:text; background-clip:text; color:transparent;
    animation:pop .9s cubic-bezier(.2,1.6,.4,1) both, shine 3s linear infinite; text-shadow:0 0 40px rgba(255,209,102,.25); }
  p.sub { margin:12px 0 0; font-size:clamp(16px,2.6vw,26px); opacity:0; animation:fade .6s .6s forwards; }
  .badges { display:flex; flex-wrap:wrap; gap:14px; justify-content:center; margin-top:34px; max-width:900px; }
  .badge { display:flex; flex-direction:column; align-items:center; gap:4px; min-width:150px; padding:14px 18px; border-radius:16px;
    background:rgba(255,255,255,.08); border:1px solid rgba(255,209,102,.5); backdrop-filter:blur(6px);
    box-shadow:0 0 24px rgba(255,209,102,.25); opacity:0; transform:translateY(40px) scale(.6); animation:fly .7s cubic-bezier(.2,1.5,.4,1) forwards; }
  .badge span { font-size:38px; } .badge b { color:var(--gold); font-size:18px; } .badge i { font-style:normal; font-size:13px; opacity:.8; max-width:180px; }
  .hint { position:fixed; bottom:14px; width:100%; text-align:center; font-size:13px; opacity:.45; }
  @keyframes pop { from { transform:scale(.2); opacity:0 } to { transform:scale(1); opacity:1 } }
  @keyframes shine { to { background-position:300% 0 } }
  @keyframes fade { to { opacity:.9 } }
  @keyframes fly { to { opacity:1; transform:none } }
  @media (prefers-reduced-motion: reduce) { h1,.badge,p.sub { animation:none; opacity:1; transform:none } }
</style></head><body>
<canvas id="c"></canvas>
<div class="stage"><h1>{{HEADLINE}}</h1><p class="sub">{{SUB}}</p><div class="badges">{{BADGES}}</div></div>
<div class="hint">点哪里，哪里就放烟花 · 偷偷敲 6 6 6 试试</div>
<script>
const c=document.getElementById('c'),x=c.getContext('2d');let W,H;const P=[],R=[];
function size(){W=c.width=innerWidth*devicePixelRatio;H=c.height=innerHeight*devicePixelRatio}size();addEventListener('resize',size);
const COLORS=['#ffd166','#ff5c8a','#7bdff2','#b2f7ef','#f7aef8','#fff','#9b5de5','#00f5d4'];
function burst(px,py,n,col,gold){for(let i=0;i<n;i++){const a=Math.random()*Math.PI*2,s=(Math.random()*6+2)*devicePixelRatio;
 P.push({x:px,y:py,vx:Math.cos(a)*s,vy:Math.sin(a)*s,life:60+Math.random()*40,c:gold?'#ffd166':(col||COLORS[i%COLORS.length]),r:(gold?3:2)*devicePixelRatio,coin:gold})}}
function rocket(tx){R.push({x:tx??Math.random()*W,y:H,vy:-(10+Math.random()*5)*devicePixelRatio,ty:H*(.15+Math.random()*.35),c:COLORS[(Math.random()*COLORS.length)|0]})}
function rain(){for(let i=0;i<160;i++)P.push({x:Math.random()*W,y:-Math.random()*H*.5,vx:0,vy:(2+Math.random()*3)*devicePixelRatio,life:240,c:'#ffd166',r:4*devicePixelRatio,coin:true,fall:true})}
let t=0;function loop(){x.globalCompositeOperation='destination-out';x.fillStyle='rgba(0,0,0,.18)';x.fillRect(0,0,W,H);x.globalCompositeOperation='lighter';
 if(t<420&&t%22===0)rocket();if(t>420&&t%70===0)rocket();t++;
 for(let i=R.length-1;i>=0;i--){const r=R[i];r.y+=r.vy;x.fillStyle=r.c;x.fillRect(r.x,r.y,2*devicePixelRatio,8*devicePixelRatio);if(r.y<=r.ty){burst(r.x,r.y,90,r.c);R.splice(i,1)}}
 for(let i=P.length-1;i>=0;i--){const p=P[i];p.x+=p.vx;p.y+=p.vy;if(!p.fall){p.vx*=.985;p.vy=p.vy*.985+.06*devicePixelRatio}p.life--;
  x.globalAlpha=Math.max(p.life/100,0);x.fillStyle=p.c;x.beginPath();
  if(p.coin){x.ellipse(p.x,p.y,p.r*1.6,p.r*Math.abs(Math.sin(t/6+i)),0,0,7)}else{x.arc(p.x,p.y,p.r,0,7)}x.fill();if(p.life<=0||p.y>H+20)P.splice(i,1)}
 x.globalAlpha=1;requestAnimationFrame(loop)}loop();
for(let i=0;i<4;i++)setTimeout(()=>rocket(W*(.2+.2*i)),i*180);
addEventListener('click',e=>burst(e.clientX*devicePixelRatio,e.clientY*devicePixelRatio,120));
let k='';addEventListener('keydown',e=>{k=(k+e.key).slice(-3);if(k==='666'||k==='888'){rain();for(let i=0;i<6;i++)setTimeout(rocket,i*120)}});
</script></body></html>
"""

if __name__ == "__main__":
    sys.exit(main())
