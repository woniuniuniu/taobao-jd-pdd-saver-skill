#!/usr/bin/env python3
"""蜗牛最爽省钱 Skill · 小票：把一次比价的结果做成一张「比价小票」网页，打开就是一张小票，所有信息都印在上面。

和 huizong.py 用同一套口径（直接调用它的函数），价格、结论和比价表完全一致；只是换成网页呈现。
页面是单个自包含的 HTML 文件：不联网、不用外部字体和脚本，WorkBuddy 自带的网页预览、任何浏览器都能打开。
--name 写上称呼，印在小票抬头那一行，文件名也带上名字。

样子：橙色背景（一圈网点 + 两条斜着走的跑马灯，跑马灯里只放这单的真实数字），中间一张白色小票，无衬线字体。
小票从上到下（脚本不运行时内容照样完整显示，只是没有动效和互动）：
  - 抬头：比价小票、小票号、时间、称呼；商品和规格；
  - 平台明细：每个平台一行（单色平台图标，最低那家红色、价格划一道黄色荧光笔；参考价灰色），
    点开看这家怎么算出来的（优惠前 → 各项优惠 → 到手）、详情页优惠、去下单/搜同款、参考价出处；
  - 最低到手：大字价格（落定时滚一下）、店铺（可复制店名）、一张「刮刮乐」红色贴纸（刮开是省了多少），
    黄色大按钮「去 X 下单 / 搜同款」，下面黄底提醒（身份价、第三方店、参考价更低、标题没写容量）；
  - 价格鉴定：脚本按固定规则给结论（入手好时机 / 价格合理 / 可以再等等），三段表盘的表针甩到结论那一段，
    下面一张对比表：这单比其他平台实时价、你查到过的最低、网上参考价最低、划线价，各便宜/贵多少；
  - 大促倒计时：双 11 / 双 12 / 618 前 45 天内，一条黑底黄字的倒计时；
  - 买家口碑：WebSearch 查到的评分、夸和吐槽的点、要点，每条都能点开出处（--kb，没有就写「没查到」）；
  - 撕下来：沿虚线往下拉，存根（条形码、小票号、累计帮你省多少、小字说明）撕断掉下去，小票文字同时复制好。

用法（参数和 huizong.py 一样，多一个 --kb / --name / --out）：
  python3 xiaopiao.py mirror-1.json mirror-2.json --ref ref.json --kb kb.json --name 小林
  python3 xiaopiao.py ... --drop 京东:9294 --out ~/Desktop/小票.html
输出一行 JSON：{"ok": true, "html": "<文件路径>", "next": "<下一步：用 present_files 展示>"}
"""
import argparse
import datetime
import hashlib
import html
import importlib.util
import itertools
import json
import os
import re
import string
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from huizong import (PLATFORMS, _norm, calc_line, clean_promos, promo_rules, conditions, identity_price, load_many, load_refs,  # noqa: E402
                     low_label, money, official, panel_breakdown, parse_drop, pick, safe_url, stamp_time, summarize, tidy,
                     unconfirmed_lines, unverified)

# 倒计时只算这几个大促的零点，45 天以内才显示
FESTIVALS = [(11, 11, "双 11"), (12, 12, "双 12"), (6, 18, "618")]

# 平台图标：统一画成单色的 App 图标（黑色方块 + 白色字形），本单最低那家换成红色，参考价灰色。
# 不用各家的官方彩色 logo，只取大家认得的那个字或形：淘、天猫的猫头、JD、拼、音符、V、鱼
_GLYPH = '<text x="16" y="{y}" text-anchor="middle" font-size="{size}" font-weight="700" font-family="{font}">{text}</text>'
_HEI = "PingFang SC, Hiragino Sans GB, Microsoft YaHei, sans-serif"
_LATIN = "Helvetica Neue, Arial, sans-serif"
PLATFORM_ICON = {
    "淘宝": _GLYPH.format(y=21.6, size=16, font=_HEI, text="淘"),
    "天猫": '<path d="M8 11.5V6.2l4.6 3.3h6.8L24 6.2v5.3c0 7-3.2 12-8 12s-8-5-8-12z"/>'
            '<circle cx="12.6" cy="14.6" r="1.5" class="pi-hole"/><circle cx="19.4" cy="14.6" r="1.5" class="pi-hole"/>',
    "京东": _GLYPH.format(y=20.8, size=13.5, font=_LATIN, text="JD"),
    "拼多多": _GLYPH.format(y=21.6, size=16, font=_HEI, text="拼"),
    "抖音商城": '<path d="M17.6 7v12.3a3.7 3.7 0 1 1-3.1-3.65" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/>'
               '<path d="M17.6 7.2c.7 2.6 2.7 4.3 5.3 4.6" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/>',
    "唯品会": _GLYPH.format(y=22, size=17, font=_LATIN, text="V"),
    "闲鱼": '<path d="M5.5 16c3-4.6 7-6.6 11-6.6 3.2 0 5.6 1.5 7 3.2l3-2.6v12l-3-2.6c-1.4 1.7-3.8 3.2-7 3.2-4 0-8-2-11-6.6z"/>'
            '<circle cx="11.2" cy="15.2" r="1.35" class="pi-hole"/>',
}



def esc(v):
    return html.escape(str(v), quote=True)


def stamp(time_text):
    """「2026-10-29 14:21 至 14:27」→ ("2026.10.29", "14:21", "20261029-1421")。"""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})", time_text or "")
    # 只有参考价时没有检索时刻：用开小票的时间
    y, mo, d, h, mi = m.groups() if m else datetime.datetime.now().strftime("%Y %m %d %H %M").split()
    return f"{y}.{mo}.{d}", f"{h}:{mi}", f"{y}{mo}{d}-{h}{mi}"


def barcode(seed, width=232, height=40):
    """装饰用条形码：由小票号算出来，同一张小票每次画得一样。"""
    bits = "".join(f"{b:08b}" for b in hashlib.sha256(seed.encode()).digest())
    bars, x, i = [], 0, 0
    while x < width - 4 and i + 3 <= len(bits):
        w = 1 + int(bits[i:i + 2], 2) % 3
        if bits[i + 2] == "1":
            bars.append(f'<rect x="{x}" y="0" width="{w}" height="{height}"/>')
        x += w + 1
        i += 3
    return (f'<svg class="barcode" viewBox="0 0 {x} {height}" preserveAspectRatio="none" aria-hidden="true">'
            + "".join(bars) + "</svg>")


def short(platform):
    return platform.split("/")[0]


def ref_amount(r):
    return r["text"].replace("约 ¥", "")



def search_title(item):
    """到 App 里搜的词：这件商品的真实标题，去掉【】里的宣传语和开头的徽标，太长就截短。"""
    t = re.sub(r"[【\[［(（][^】\]］)）]*[】\]］)）]", " ", item.get("title") or "")
    t = re.sub(r"^\s*(天猫|百亿补贴|双11|品牌|自营)\s*", "", t)
    return " ".join(t.split())[:40].strip()


def go_link(platform, item, q):
    """去下单：有商品链接就打开商品页；App 里常拿不到链接，就打开这个平台的同款搜索。"""
    if item and item.get("url"):
        return item["url"], f"去{short(platform)}下单", True
    return PLATFORMS[platform].format(q=q), f"去{short(platform)}网页搜同款", False


def saving_of(best, low):
    """省了多少：口径同 caidan.py（比最贵的平台；只有一个平台时比划线价）。"""
    if len(best) >= 2:
        hi = max(best.values(), key=lambda i: i["price"])
        if hi["price"] > low["price"]:
            return hi["price"] - low["price"], f"比最贵的{hi['platform']}（¥{money(hi['price'])}）"
    if low["original"] > low["price"]:
        return low["original"] - low["price"], f"比划线价（¥{money(low['original'])}）"
    return 0.0, ""


def breakdown(i):
    """详情页的优惠明细能对上账时，拆成「优惠前 → 各项优惠 → 到手」；对不上就不拆，返回空列表。
    只在几项优惠加起来正好等于「优惠前 − 到手价」时才用，免得把没算进去的券也画上去。"""
    start, cands = 0.0, []
    for raw in (i.get("detail") or {}).get("promos") or []:
        for p in re.split(r"[|｜]", str(raw)):
            p = p.strip()
            m = re.fullmatch(r"(优惠前|京东价|原价)\s*[¥￥]\s*(\d+(?:\.\d+)?)", p)
            if m:
                start = start or float(m.group(2))
                continue
            m = re.fullmatch(r"([一-鿿A-Za-z]{2,8})\s*[¥￥]\s*(\d+(?:\.\d+)?)", p)
            if m and not re.search(r"后|前|到手|满|可用|预估|价", m.group(1)) and (m.group(1), float(m.group(2))) not in cands:
                cands.append((m.group(1), float(m.group(2))))
    start = start or (i["original"] if i["original"] > i["price"] else 0) or (i["list_price"] if i["list_price"] > i["price"] else 0)
    if start <= i["price"] or not cands:
        return start, []
    gap = start - i["price"]
    cands = cands[:8]
    for n in range(len(cands), 0, -1):
        for combo in itertools.combinations(cands, n):
            if abs(sum(v for _, v in combo) - gap) < 0.02:
                return start, list(combo)
    return start, []


def load_kb(path):
    """WebSearch 查到的口碑（SKILL.md 规定的格式）。每条要点必须带出处链接；关键词要有出处才显示。查不到、格式不对就当没有。"""
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    points = [{"text": str(p.get("text") or "").strip()[:80], "url": safe_url(p.get("url"))}
              for p in d.get("points") or [] if isinstance(p, dict)]
    points = [p for p in points if p["text"] and p["url"]][:3]
    sources = [u for u in (safe_url(u) for u in d.get("sources") or []) if u] + [p["url"] for p in points]
    rating = d.get("rating") if isinstance(d.get("rating"), dict) else {}
    rating = {"value": str(rating.get("value") or "").strip()[:12], "label": str(rating.get("label") or "").strip()[:12],
              "url": safe_url(rating.get("url"))}
    if not (rating["value"] and rating["url"]):
        rating = None
    else:
        sources.append(rating["url"])

    def tags(key):
        out = []
        for t in d.get(key) or []:
            t = str(t).strip()[:10]
            if t and t not in out:
                out.append(t)
        return out[:5] if sources else []
    kb = {"rating": rating, "pros": tags("pros"), "cons": tags("cons"), "points": points,
          "sources": list(dict.fromkeys(sources)), "date": str(d.get("date") or "").strip()[:12]}
    return kb if (kb["rating"] or kb["pros"] or kb["cons"] or kb["points"]) else None


def caidan():
    """彩蛋脚本：用它的账本算这件商品的历史价格、累计战绩和成就。读不到就当没有，报告照常出。"""
    try:
        spec = importlib.util.spec_from_file_location("caidan", os.path.join(HERE, "caidan.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def load_stats(keyword, spec, low_price):
    cd = caidan()
    if not cd:
        return None
    try:
        events = cd.load_events()
    except Exception:
        return None
    key = (_norm(keyword), spec or "")
    mine = [n for n, e in enumerate(events)
            if (_norm(e.get("keyword") or ""), e.get("spec") or "") == key and e.get("lowest")]
    # 这次比价：彩蛋脚本刚记的那一笔（同一件商品、同一个最低价、一小时以内）
    cur = None
    if mine and low_price is not None:
        e = events[mine[-1]]
        try:
            age = time.time() - time.mktime(time.strptime(e["time"], "%Y-%m-%d %H:%M"))
        except (KeyError, ValueError):
            age = 1e9
        if e.get("event") == "compare" and abs(e["lowest"]["price"] - low_price) < 0.01 and age < 3600:
            cur = mine[-1]
    history = [{"time": events[n]["time"], "price": events[n]["lowest"]["price"]} for n in mine]
    got = cd.unlocked(events)
    new = []
    if cur is not None:
        before = cd.unlocked(events[:cur])
        after = cd.unlocked(events[:cur + 1])
        new = [a[0] for a in cd.ACHIEVEMENTS if a[0] in after and a[0] not in before]
    total = cd.total_saved(events)
    lv, nxt = cd.level(total)
    return {"history": history, "before": [h for n, h in zip(mine, history) if n != cur],
            "achievements": cd.ACHIEVEMENTS, "got": got, "new": new, "total": total,
            "level": (cd.plain(lv[1]), lv[0]), "next": (cd.plain(nxt[1]), nxt[0]) if nxt else None,
            "compare": sum(1 for e in events if e.get("event") == "compare"),
            "watch": sum(1 for e in events if e.get("event") != "compare"),
            "fun_units": cd.FUN_UNITS}


def fun_of(saved, units):
    """省下的钱换成奶茶、电影票……挑换算出来大约 3 份的那个（同 caidan.fun_unit）。"""
    if saved <= 0 or not units:
        return None
    unit = min(units, key=lambda u: abs(saved / u[0] - 3))
    n = round(saved / unit[0])
    return (unit[1], n, unit[2]) if n >= 1 else None


def verdict(low, refs, before):
    """这个价划不划算：脚本按固定规则打分，不是 AI 现编的。返回 (等级, 标题, 副标题, [(好/坏/平, 依据)])。"""
    price, reasons, score, signals = low["price"], [], 0, 0
    if before:
        h = min(before, key=lambda r: r["price"])
        when, signals = h["time"][5:10].replace("-", "."), signals + 1
        if price < h["price"] - 0.01:
            score += 2
            reasons.append(("good", f"比你 {when} 查到的最低价 ¥{money(h['price'])} 还便宜 ¥{money(h['price'] - price)}"))
        elif price <= h["price"] + 0.01:
            score += 1
            reasons.append(("good", f"和你查到过的最低价持平（{when} ¥{money(h['price'])}）"))
        elif price > h["price"] * 1.02:
            score -= 1
            reasons.append(("bad", f"比你 {when} 查到的 ¥{money(h['price'])} 贵 ¥{money(price - h['price'])}"))
        else:
            reasons.append(("same", f"和你查到过的最低价 ¥{money(h['price'])}（{when}）差不多"))
    near = [r for p, r in refs.items() if p != "闲鱼"]  # 闲鱼是二手价，不拿来比
    if near:
        r, signals = min(near, key=lambda r: r["low"]), signals + 1
        if price <= r["low"] + 0.01:
            score += 1
            reasons.append(("good", f"不高于网上各平台的参考价（最低 ¥{money(r['low'])}）"))
        elif price > r["low"] * 1.03:
            score -= 1
            reasons.append(("bad", f"{r['platform']}的网上参考价 ¥{money(r['low'])} 更低，但不是实时价，下单前要去核实"))
        else:
            reasons.append(("same", f"和网上参考价（最低 ¥{money(r['low'])}）差不多"))
    base = low["original"] if low["original"] > price else 0
    if base:
        off, signals = (base - price) / base, signals + 1
        if off >= 0.1:
            score += 1
            reasons.append(("good", f"比划线价 ¥{money(base)} 低 {off:.0%}"))
        else:
            reasons.append(("same", f"比划线价 ¥{money(base)} 低 {off:.0%}，折扣不大"))
    if not signals:
        return "unknown", "数据还不够", "只有这一个价格，没有可比的；盯几次价就能看出高低。", reasons
    if score >= 2:
        return "good", "入手好时机", "几项对比都站在你这边。", reasons
    # 一条不利、别的都还行时只算「合理」；不利的线索占多数才建议等
    if score <= -2 or (score < 0 and signals == 1):
        return "wait", "可以再等等", "有更低的价格线索，不急的话可以先盯价。", reasons
    return "ok", "价格合理", "不算最低，也不算贵。", reasons


def next_festival(now):
    found = None
    for y in (now.year, now.year + 1):
        for m, d, name in FESTIVALS:
            day = datetime.date(y, m, d)
            left = (day - now.date()).days
            if 0 <= left <= 45 and (found is None or left < found[0]):
                found = (left, name, day)
    return found


# ———————————————————————— 小票上的各段 ————————————————————————

def ic(platform):
    """平台图标：统一的单色 App 图标（最低那家变红、参考价变灰靠 CSS）；淘宝/天猫是「淘」+ 压在角上的小猫头。"""
    names = ["淘宝", "天猫"] if platform == "淘宝/天猫" else [platform]
    tiles = "".join(f'<span><svg viewBox="0 0 32 32">{PLATFORM_ICON[n]}</svg></span>' for n in names if n in PLATFORM_ICON)
    return f'<span class="ic" aria-hidden="true">{tiles or "<span></span>"}</span>'


def item_html(e, low, q):
    """一个平台一行：<details> 不靠脚本也能点开；点开是这家怎么算出来的、详情页优惠和链接。"""
    platform, kind, i, r = e["platform"], e["kind"], e.get("item"), e.get("ref")
    cls = {"best": ' class="best"', "live": "", "ref": ' class="ref"', "miss": ' class="ref"'}[kind]
    url, label, _ = go_link(platform, i, q) if platform in PLATFORMS else ("", "", False)
    more, links = [], []
    if kind in ("best", "live"):
        sub = i["shop"] or "手机 App 实时价"
        # 按自营 / 旗舰店优先选中的那行不一定最便宜：角标写 tag（「自营/旗舰」），比它便宜的那行写便宜多少
        diff = i["price"] - low["price"]
        price = f'<b class="num">¥{esc(money(i["price"]))}</b>' + \
            (f'<small>{esc(e.get("tag") or "最低")}</small>' if kind == "best" else
             f'<small class="num">−¥{esc(money(-diff))}</small>' if diff < -0.005 else
             f'<small class="up num">+¥{esc(money(diff))}</small>')
        facts = [("官方认证店（旗舰店 / 自营 / 直营）" if official(i) else ""), ("要新人/会员等身份" if identity_price(i) else ""),
                 tidy(conditions(i)), f"列表价 ¥{money(i['list_price'])}" if i["list_price"] else "",
                 "这是列表价，没能核实详情页优惠" if unverified(i) else ""]
        facts = " · ".join(f for f in facts if f)
        if facts:
            more.append(esc(facts))
        c = i.get("calc") or {}
        steps = c.get("steps") or []
        if not (steps and c.get("base")):
            # 2.15 的算法没算出来时，照 2.14 原来的样子：优惠明细能对上账就拆开（含用划线价 / 列表价当起点）
            start, old_steps = breakdown(i)
            if old_steps:
                c = dict(c, base=start, base_from="优惠前", steps=old_steps, final=i["price"], mode="panel", app=0)
                steps = old_steps
        if steps and c.get("base"):
            # 2.15：怎么算出来的——从「优惠前」（或列表价）开始，一条条减掉页面上的立减、满减、券、国补
            more.append('<p class="pm-h">优惠怎么算 · 券只看没领</p><div class="calc num">'
                        + f'<p><span>{esc(c.get("base_from") or "优惠前")}</span><span>¥{esc(money(c["base"]))}</span></p>'
                        + "".join(f'<p class="m"><span>{esc(t)}</span><span>−{esc(money(a))}</span></p>' for t, a in steps)
                        + f'<p class="t"><span>{"到手" if c.get("mode") == "panel" else "算下来到手"}</span>'
                          f'<span>¥{esc(money(c["final"]))}</span></p></div>')
            if c.get("app") and abs(c["app"] - c["final"]) > 0.5:
                more.append(f'<p class="pm-h">App 详情页标的到手价 ¥{esc(money(c["app"]))}'
                            + ("；页面上还有没算进去的优惠，这单按算出来的价" if abs(i["price"] - c["final"]) < 0.01 else
                               "；这单按 App 标的价（算出来的起算价可能已经含了部分优惠）") + "</p>")
        elif i["original"] > i["price"]:
            more.append('<div class="calc num">' + f'<p><span>划线价</span><span>¥{esc(money(i["original"]))}</span></p>'
                        + f'<p class="t"><span>到手</span><span>¥{esc(money(i["price"]))}</span></p></div>')
        if c.get("coupons"):
            more.append('<p class="pm-h">页面上的券 · 只看没领</p><ul class="pm">'
                        + "".join(f"<li>{esc(t)}</li>" for t in c["coupons"][:6]) + "</ul>")
        if c.get("cond"):
            more.append('<p class="pm-h">要满足条件才有，没算进去</p><ul class="pm">'
                        + "".join(f"<li>{esc(t)}</li>" for t in c["cond"]) + "</ul>")
        promos = clean_promos((i.get("detail") or {}).get("promos"))
        if steps:  # 已经写进「怎么算出来的」的几项（和优惠前、国补后这类总价）不再重复列
            used = {t for t, _ in steps}

            def dup(p):
                m = re.fullmatch(r"([一-鿿A-Za-z]{2,8})\s*[¥￥]\s*\d+(?:\.\d+)?", p)
                if m and (m.group(1) in used or re.search(r"前|后|到手", m.group(1))):
                    return True
                # 2.15：上面「优惠怎么算」里已经用上的规则（满减、立减、国补…）不再在「其他优惠」里重复列
                rules = promo_rules([p])[0]
                def done(r):
                    core = re.search(r"满\d+(?:\.\d+)?减\d+(?:\.\d+)?", r["text"])
                    return (r["text"] in used or (core and any(core.group() in t for t in used))
                            or (r["kind"] == "gov" and any(t.startswith("国补") for t in used)))
                return bool(rules) and all(done(r) for r in rules)
            promos = [p for p in promos if not dup(p)]
        if promos:
            more.append(f'<p class="pm-h">{"其他优惠" if steps else "详情页优惠"} · 只看未领</p><ul class="pm">'
                        + "".join(f"<li>{esc(p)}</li>" for p in promos) + "</ul>")
        if url:
            links.append(f'<a href="{esc(url)}" target="_blank" rel="noopener">{esc(label)} →</a>')
        if i["couponUrl"]:
            links.append(f'<a href="{esc(i["couponUrl"])}" target="_blank" rel="noopener">领券 →</a>')
        if r and r.get("url"):
            links.append(f'<a class="src" href="{esc(r["url"])}" target="_blank" rel="noopener">网上参考价 ≈ ¥{esc(ref_amount(r))} 出处 →</a>')
    elif kind == "ref":
        sub = "网上参考价 · 不是实时价"
        price = f'<b class="num">≈¥{esc(ref_amount(r))}</b><small>参考</small>'
        more.append("手机上没读到实时价，这是公开网页上的价格，下单前到 App 核实。")
        if r.get("url"):
            links.append(f'<a class="src" href="{esc(r["url"])}" target="_blank" rel="noopener">看出处 →</a>')
        if url:
            links.append(f'<a href="{esc(url)}" target="_blank" rel="noopener">{esc(label)} →</a>')
    else:
        sub = "手机上搜过，没看到同款"
        price = '<b>—</b>'
        more.append(esc(e.get("reason") or "原因不明"))
        if url:
            links.append(f'<a href="{esc(url)}" target="_blank" rel="noopener">{esc(label)} →</a>')
    if links:
        more.append(f'<p class="lk">{"".join(links)}</p>')
    return f'''
          <li><details{cls}>
            <summary>
              {ic(platform)}
              <span class="nm"><b>{esc(e.get("label") or platform)}</b><small>{esc(sub)}</small></span>
              <span class="pr">{price}</span>
            </summary>
            <div class="more">{"".join(more)}</div>
          </details></li>'''


def total_html(best, low, refs, q, saved, vs, fun, notes):
    warn = f'<div class="warn">{"".join(f"<p>{esc(n)}</p>" for n in notes)}</div>' if notes else ""
    if low:
        url, label, direct = go_link(low["platform"], low, q)
        shop = (f' · <b>{esc(low["shop"])}</b><button type="button" data-copy="{esc(low["shop"])}" '
                f'data-done="店名已复制，到 App 里搜它">复制店名</button>') if low["shop"] else ""
        sticker = ""
        if saved > 0:
            who = vs.split("（")[0]  # 「比最贵的京东」「比划线价」
            who = who.replace("比最贵的", "比")
            sticker = (f'<div class="sticker" id="sticker"><div><small>{esc(who)}省</small><b class="num">¥{esc(money(round(saved, 2)))}</b></div>'
                       f'<canvas aria-label="刮开看省了多少"></canvas></div><span class="scratch-tip">刮开看看</span>')
        arrow = '<svg viewBox="0 0 24 24"><path d="M5 12h14M13 6l6 6-6 6"/></svg>'
        how = calc_line(low)
        formula = f'\n          <p class="formula num"><small>优惠怎么算</small>{esc(how)}</p>' if how else ""
        if direct:
            go, tip = f'<a class="go" href="{esc(url)}" target="_blank" rel="noopener">{esc(label)}{arrow}</a>', ""
        else:
            # 这个价是手机 App 里读到的，网页上看不到（同一件网页常贵不少）：主按钮复制商品名，到手机 App 里搜
            app = short(low["platform"])
            name = search_title(low) or urllib.parse.unquote(q)
            go = (f'<button type="button" class="go" data-copy="{esc(name)}" data-done="商品名已复制，到手机{esc(app)}里粘贴搜索">'
                  f'复制商品名，去手机{esc(app)}搜{arrow}</button>')
            tip = (f'<p class="tip">这个价只在手机 App 里，网页上的价格可能不同' + ("，认准这家店" if low["shop"] else "") + "。"
                   f'<a href="{esc(url)}" target="_blank" rel="noopener">{esc(label)}</a></p>')
        return f'''
        <div class="total">
          <p class="lab">{esc(low_label(best, low))}到手</p>
          <p class="big num"><i>¥</i><span data-count="{low['price']}">{esc(money(low['price']))}</span></p>
          <p class="where">{esc(low['platform'])}{shop}</p>{sticker}{formula}
          {go}{tip}{warn}
        </div>'''
    if refs:
        r = min(refs.values(), key=lambda r: r["low"])
        url, label, _ = go_link(r["platform"], None, q)
        return f'''
        <div class="total">
          <p class="lab">参考价最低（不是实时价）</p>
          <p class="big num is-ref"><i>≈¥</i>{esc(ref_amount(r))}</p>
          <p class="where">{esc(r['platform'])} · 公开网页上的价格</p>
          <a class="go" href="{esc(url)}" target="_blank" rel="noopener">{esc(label)}<svg viewBox="0 0 24 24"><path d="M5 12h14M13 6l6 6-6 6"/></svg></a>{warn}
        </div>'''
    return f'''
        <div class="total">
          <p class="lab">最低到手</p>
          <p class="where">这次手机 App 里没有读到对得上型号的实时价格，可以在上面的平台里点「搜同款」自己看看。</p>{warn}
        </div>'''


# 表盘三段：入手（左）/ 合理（中）/ 再等等（右），表针停在结论那一段
GAUGE = '''
        <div class="gauge" style="--deg: {deg}deg">
          <svg viewBox="0 0 240 140" aria-hidden="true">
            <path class="g-out" d="M30 125 A90 90 0 0 1 210 125"/>
            <path class="g-seg g1" d="M30 125 A90 90 0 0 1 75 47.06"/>
            <path class="g-seg g2" d="M75 47.06 A90 90 0 0 1 165 47.06"/>
            <path class="g-seg g3" d="M165 47.06 A90 90 0 0 1 210 125"/>
            <line class="g-sep" x1="81.5" y1="58.3" x2="68.5" y2="35.8"/>
            <line class="g-sep" x1="158.5" y1="58.3" x2="171.5" y2="35.8"/>
            <text class="g-lab{on1}" x="18" y="66">{l1}</text>
            <text class="g-lab{on2}" x="120" y="12">{l2}</text>
            <text class="g-lab{on3}" x="224" y="66">{l3}</text>
            <g class="needle"><polygon points="115.5,125 120,46 124.5,125"/><circle cx="120" cy="125" r="11"/><circle class="hub" cx="120" cy="125" r="4.5"/></g>
          </svg>
        </div>'''


def delta(price, other):
    """这单比 other 便宜还是贵；差 1% 以内算持平。"""
    d = price - other
    if abs(d) <= max(other * 0.01, 0.01):
        return "same", "≈ 持平"
    return ("good", f"便宜 ¥{money(-d)}") if d < 0 else ("bad", f"贵 ¥{money(d)}")


def judge_html(low, best, refs, before):
    if not low:
        return '''
        <p class="vd">无从判断</p>
        <p class="vs">这次只有参考价，没有实时价，没法判断这个价贵不贵</p>'''
    level, title, sub, _ = verdict(low, refs, before)
    price, rows = low["price"], []
    for p, i in sorted(best.items(), key=lambda kv: -kv[1]["price"]):
        if i is not low:
            rows.append((f"{p}实时价", "", i["price"]))
    if before:
        h = min(before, key=lambda r: r["price"])
        rows.append(("你查到过的最低", h["time"][5:].replace("-", "."), h["price"]))
    near = [r for p, r in refs.items() if p != "闲鱼"]
    if near:
        r = min(near, key=lambda r: r["low"])
        rows.append((f"{r['platform']}参考价", "不是实时价，要去核实", r["low"]))
    if low["original"] > price:
        rows.append(("划线价", "", low["original"]))
    cmp = ""
    if rows:
        body = "".join(f'<p><span>{esc(t)}{f"<small>{esc(s)}</small>" if s else ""}</span><b>¥{esc(money(v))}</b>'
                       f'<span class="d {delta(price, v)[0]}">{esc(delta(price, v)[1])}</span></p>' for t, s, v in rows[:4])
        cmp = f'''
        <div class="cmp num">
          <p class="cmp-h"><span>这单 ¥{esc(money(price))} 比</span><span></span><span>差多少</span></p>{body}
        </div>'''
    if level == "unknown":
        return f'''
        <p class="vd">{esc(title)}</p>
        <p class="vs">{esc(sub.rstrip("。"))}</p>{cmp}'''
    deg = {"good": -60, "ok": 6, "wait": 60}[level]
    gauge = GAUGE.format(deg=deg, on1=" on" if level == "good" else "", on2=" on" if level == "ok" else "",
                         on3=" on" if level == "wait" else "", l1="入手", l2="合理", l3="再等等")
    return f'''{gauge}
        <p class="vd">{esc(title)}</p>
        <p class="vs">{esc(sub.rstrip("。"))}</p>{cmp}'''


def kb_html(kb):
    if not kb:
        return '<p class="none">这次没查到口碑</p>'
    order = [p["url"] for p in kb["points"]] + ([kb["rating"]["url"]] if kb["rating"] else []) + kb["sources"]
    srcs = {u: n for n, u in enumerate(dict.fromkeys(order), 1)}
    out = ""
    if kb["rating"]:
        out += (f'<div class="kb"><a class="num" href="{esc(kb["rating"]["url"])}" target="_blank" rel="noopener">'
                f'{esc(kb["rating"]["value"])}</a><span>{esc(kb["rating"]["label"] or "评分")}</span></div>')
    if kb["pros"] or kb["cons"]:
        out += ('<div class="tags">' + "".join(f"<span>{esc(t)}</span>" for t in kb["pros"])
                + "".join(f'<span class="con">{esc(t)}</span>' for t in kb["cons"]) + "</div>")
    if kb["points"]:
        out += ('<ol class="pts">' + "".join(f'<li>{esc(p["text"])}<a href="{esc(p["url"])}" target="_blank" rel="noopener">'
                                             f'[{srcs[p["url"]]}]</a></li>' for p in kb["points"]) + "</ol>")
    return out


def build(data, best, plain, refs, skipped, name, kb=None, stats=None, now=None):
    now = now or datetime.datetime.now()
    keyword, spec = data["keyword"], data.get("spec") or ""
    q = urllib.parse.quote(f"{keyword} {spec}".strip())
    day, clock, no = stamp(data.get("time"))
    low = pick(best.values())  # 有自营 / 旗舰店就先用它们的价
    tag = low_label(best, low) if low else ""
    text = [f"比价小票 No.{no}", f"{keyword} {spec}".strip()]
    if low:
        text.append(f"{tag} ¥{money(low['price'])}　{low['platform']}" + (f" · {low['shop']}" if low["shop"] else ""))
        if calc_line(low):
            text.append(f"优惠怎么算：{calc_line(low)}")
    text.append("—")

    # 一个平台一行：实时价在前（按价格），参考价在后，搜过没同款的放最后
    entries = []
    for platform, i in sorted(best.items(), key=lambda kv: kv[1]["price"]):
        entries.append({"platform": platform, "kind": "best" if i is low else "live", "item": i, "ref": refs.get(platform),
                        "tag": "自营/旗舰" if i is low and tag != "最低" else ""})
        text.append(f"{platform}　¥{money(i['price'])}" + (f"　{i['shop']}" if i["shop"] else "")
                    + (f"（参考价 ≈ ¥{ref_amount(refs[platform])}）" if platform in refs else ""))
    for platform, r in sorted(refs.items(), key=lambda kv: kv[1]["low"]):
        if platform not in best:
            entries.append({"platform": platform, "kind": "ref", "ref": r})
            text.append(f"{platform}　≈ ¥{ref_amount(r)}（参考价）")
    for platform, reason in skipped.items():
        if platform not in best and platform not in refs:
            entries.append({"platform": platform, "kind": "miss", "reason": reason})
            text.append(f"{platform}　未见同款")

    # 最低价下面的提醒：口径同 huizong.render_md
    notes = []
    saved, vs = saving_of(best, low) if low else (0.0, "")
    if low:
        if identity_price(low):
            if plain:
                p = pick(plain.values())
                notes.append(f"这个价要新人/会员等身份；不限身份的最低是 ¥{money(p['price'])}，在{p['platform']}"
                             + (f" · {p['shop']}" if p["shop"] else "") + "。")
            else:
                notes.append("这个价要新人/会员等身份；这次没读到不限身份的价格。")
        lc = low.get("calc") or {}
        if lc.get("steps"):
            notes.append("到手价算上了页面上的券、满减、立减和国补（券只看没领，下单前要先在 App 里领）。")
        if lc.get("cond"):
            notes.append("会员、以旧换新、指定支付等要满足条件的优惠没算进去：" + "；".join(lc["cond"][:3]) + "。")
        if unverified(low):
            notes.append("这是列表价，没能点进详情页核实优惠，实际可能更低。")
        cheaper = [r for p, r in refs.items() if p != "闲鱼" and r["low"] < low["price"]]
        if cheaper:
            r = min(cheaper, key=lambda r: r["low"])
            notes.append(f"{r['platform']}的参考价更低（{r['text']}），但不是实时价，下单前去 App 核实。")
    elif refs:
        notes.append("这次手机 App 里没有读到对得上型号的实时价格，上面是网上的参考价。")
    notes += unconfirmed_lines(data, best)

    fine = []
    if best:
        fine.append(f"实时价是你手机 App 里看到的（{stamp_time(data.get('time'))[5:] or '检索时刻'}）")
        fine.append("只比官方旗舰店 / 自营 / 直营店（拼多多只比品牌官方旗舰店和拼多多自营），代购、第三方和个人小店不算")
    if refs:
        fine.append("参考价来自公开网页")
    fine.append("下单以 App 为准")
    before = (stats or {}).get("before") or []
    level_title = verdict(low, refs, before)[1] if low else "无从判断"
    text += ["—", f"价格鉴定：{level_title}", "下单以 App 页面为准"]

    # 背景上两条跑马灯：只放这单的真实数字
    fest = next_festival(now)
    r1 = ([f"{tag} ¥{money(low['price'])}"] if low else
          [f"参考价最低 ≈ ¥{ref_amount(min(refs.values(), key=lambda r: r['low']))}"] if refs else ["这次没有实时价"])
    if saved > 0:
        r1.append(f"{vs.split('（')[0].replace('比最贵的', '比')}省 ¥{money(round(saved, 2))}")
    r1 += [f"{keyword} {spec}".strip(), f"查了 {len(entries)} 个平台"]
    r2 = ([f"离{fest[1]} 不到 {fest[0]} 天" if fest[0] else f"今天就是{fest[1]}"] if fest else []) + \
        ([f"{low['platform']} {tag}"] if low else []) + [level_title, "下单以 App 为准"]

    def ribbon(words):
        words = words * (2 if len(words) > 3 else 3)
        return "".join(f"{esc(w)}<i>/</i>" for w in words)

    count = ""
    if fest:
        left, fname, fday = fest
        if left:
            count = f'''
        <div class="count">
          <small>距{esc(fname)} 零点</small>
          <b class="num" data-deadline="{fday:%Y-%m-%d}T00:00:00+08:00"><span data-u="d">{left}</span><em>天</em><span data-u="h">00</span>:<span data-u="m">00</span>:<span data-u="s">00</span></b>
        </div>'''
        else:
            count = f'\n        <div class="count"><small>大促</small><b>今天就是{esc(fname)}</b></div>'
    total = (stats or {}).get("total") or 0
    fun = fun_of(saved, (stats or {}).get("fun_units"))
    pop = ""
    if saved > 0:
        pop = f"{vs.split('（')[0].replace('比最贵的', '比')}省 ¥{money(round(saved, 2))}" + (f"，约等于 {fun[1]} {fun[2]}" if fun else "")
    meta = json.dumps({"text": "\n".join(text), "pop": pop}, ensure_ascii=False).replace("</", "<\\/")
    who = f" · {esc(name)}" if name else ""
    return string.Template(PAGE).safe_substitute(
        title=esc(f"{keyword} {spec}".strip()), r1=ribbon(r1), r2=ribbon(r2),
        head=f"No.{esc(no)} · {esc(day[5:])} {esc(clock)}{who}", keyword=esc(keyword),
        spec=f"<span>{esc(spec)}</span>" if spec else "", count_n=len(entries),
        items="".join(item_html(e, low, q) for e in entries) or '<li class="none">这次没有任何平台的价格</li>',
        total=total_html(best, low, refs, q, saved, vs, fun, notes), judge=judge_html(low, best, refs, before),
        countdown=count, kb=kb_html(kb), barcode=barcode(no + keyword + spec, 220, 40), no=esc(no),
        acc=f'<p class="acc num">累计帮你省 ¥{esc(money(round(total, 2)))}</p>' if total > 0 else "",
        fine=esc(" · ".join(fine)), meta=meta)


def main():
    ap = argparse.ArgumentParser(description="把比价结果做成一张奢侈品小票风格的网页")
    ap.add_argument("files", nargs="*", help="zidong.py 的 mirror-*.json，可以给多个")
    ap.add_argument("--ref", help="WebSearch 参考价文件")
    ap.add_argument("--kb", help="WebSearch 口碑文件（SKILL.md Step 3 的 kb-*.json）")
    ap.add_argument("--keyword", help="只有参考价时用：型号")
    ap.add_argument("--spec", help="只有参考价时用：规格")
    ap.add_argument("--drop", action="append", default=[], type=parse_drop, help="同 huizong.py")
    ap.add_argument("--name", default="", help="用户的称呼，印在小票「贵宾」一栏；不知道就不写")
    ap.add_argument("--out", help="网页保存到哪；默认和第一个结果文件放在同一个目录")
    a = ap.parse_args()
    if not a.files and not a.keyword:
        ap.error("需要 zidong.py 的结果文件（mirror-*.json）；只有参考价时用 --keyword")

    data = load_many(a.files, a.keyword, a.spec)
    refs = load_refs(a.ref)
    best, plain, skipped, _ = summarize(data, a.drop)
    low = (pick(best.values()) or {}).get("price")
    page = build(data, best, plain, refs, skipped, a.name.strip(), load_kb(a.kb),
                 load_stats(data["keyword"], data.get("spec") or "", low))

    out = a.out
    if not out:
        folder = os.path.dirname(os.path.abspath(a.files[0] if a.files else a.ref or "."))
        stem = re.sub(r"[^\w一-鿿]+", "", f"{data['keyword']}{data.get('spec') or ''}")[:24]
        who = re.sub(r"[^\w一-鿿]+", "", a.name)[:12]
        out = os.path.join(folder, f"小票-{stem}" + (f"-{who}" if who else "") + f"-{stamp(data.get('time'))[2]}.html")
    out = os.path.abspath(os.path.expanduser(out))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    # WorkBuddy 实跑时生成了网页却没展示：直接在输出里写明下一步
    print(json.dumps({"ok": True, "html": out,
                      "next": "必须马上用 present_files 展示这个 html 文件（files 填上面的 html 路径），再发总报告；"
                              "没有实时价时也要展示。"}, ensure_ascii=False))


PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>比价小票 · ${title}</title>
<style>
  :root {
    --stage: #ff5b1f;
    --paper: #fffdf8;
    --ink: #111111;
    --ink2: #333333;
    --muted: #6b6b6b;
    --line: #dcdcdc;
    --hi: #ffe14d;
    --red: #ff3b30;
    --green: #17b26a;
    --link: #2a3cff;
    --sans: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Helvetica Neue", Arial, sans-serif;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    min-height: 100vh; background: var(--stage); color: var(--ink); font: 400 15px/1.6 var(--sans);
    -webkit-font-smoothing: antialiased; overflow-x: hidden;
    display: flex; flex-direction: column; align-items: center; padding: 48px 16px 72px;
  }
  a { color: inherit; }
  button { font: inherit; color: inherit; }
  .num { font-variant-numeric: tabular-nums; }

  /* 背景：橙色上一圈网点，两条斜着走的跑马灯（只放这单的真实数字） */
  .bg { position: fixed; inset: 0; z-index: 0; overflow: hidden; pointer-events: none; }
  .bg::before {
    content: ""; position: absolute; inset: 0;
    background-image: radial-gradient(rgba(120,30,0,.22) 1.3px, transparent 1.8px); background-size: 16px 16px;
    -webkit-mask-image: radial-gradient(ellipse 70% 60% at 50% 40%, transparent 30%, #000 100%);
            mask-image: radial-gradient(ellipse 70% 60% at 50% 40%, transparent 30%, #000 100%);
  }
  .ribbon { position: absolute; left: -30%; width: 160%; overflow: hidden; white-space: nowrap; padding: 12px 0; font-size: 22px; font-weight: 900; letter-spacing: .02em; }
  .ribbon span { display: inline-block; animation: marquee 38s linear infinite; }
  .ribbon i { font-style: normal; margin: 0 22px; opacity: .55; }
  .r1 { top: 14%; background: var(--ink); color: var(--hi); transform: rotate(-7deg); }
  .r2 { top: 68%; background: var(--paper); color: var(--ink); transform: rotate(5deg); }
  .r2 span { animation-direction: reverse; animation-duration: 46s; }
  @keyframes marquee { to { transform: translateX(-50%); } }

  /* 小票 */
  .scene { position: relative; z-index: 1; width: 100%; max-width: 420px; perspective: 1200px; }
  .tilt { transform-style: preserve-3d; transition: transform .25s ease-out; }
  .receipt { position: relative; filter: drop-shadow(0 28px 34px rgba(80,20,0,.45)); }
  .zig { height: 10px; background: var(--paper); -webkit-mask: conic-gradient(from -45deg at bottom, #0000, #000 1deg 89deg, #0000 90deg) bottom / 16px 8px repeat-x, linear-gradient(#000 0 0) top / 100% calc(100% - 8px) no-repeat; mask: conic-gradient(from -45deg at bottom, #0000, #000 1deg 89deg, #0000 90deg) bottom / 16px 8px repeat-x, linear-gradient(#000 0 0) top / 100% calc(100% - 8px) no-repeat; }
  .zig.top { transform: scaleY(-1); }
  .paper { position: relative; background: var(--paper); padding: 22px 26px 0; }
  .glare { position: absolute; inset: 0; pointer-events: none; background: radial-gradient(circle at var(--gx, 50%) var(--gy, 0%), rgba(255,255,255,.6), transparent 45%); mix-blend-mode: soft-light; opacity: 0; transition: opacity .3s; }
  .scene:hover .glare { opacity: 1; }

  .hd { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: baseline; gap: 2px 12px; font-size: 13px; color: var(--muted); }
  .hd b { color: var(--ink); font-size: 15px; font-weight: 800; white-space: nowrap; }
  .prod { margin-top: 12px; font-size: 26px; line-height: 1.2; font-weight: 800; letter-spacing: -.01em; }
  .prod span { display: inline-block; margin-left: 8px; padding: 2px 8px; border-radius: 6px; background: var(--ink); color: var(--paper); font-size: 14px; font-weight: 700; vertical-align: 4px; }
  .cut { margin: 18px -26px; border: 0; border-top: 2px dashed var(--line); }
  .h { display: flex; justify-content: space-between; align-items: baseline; font-size: 13px; font-weight: 700; color: var(--muted); letter-spacing: .08em; margin-bottom: 8px; }
  .h small { font-weight: 400; letter-spacing: 0; }
  .none { font-size: 14px; color: var(--muted); }

  /* 平台行：点开看怎么算的 */
  .items { list-style: none; }
  .items details { border-radius: 10px; transition: background .2s; }
  .items details:hover { background: #f6f3ec; }
  .items summary { list-style: none; display: flex; align-items: center; gap: 12px; padding: 10px 8px; cursor: pointer; margin: 0 -8px; }
  .items summary::-webkit-details-marker { display: none; }
  .items summary:focus-visible { outline: 2px solid var(--ink); outline-offset: -2px; border-radius: 10px; }
  .ic { position: relative; width: 34px; flex: none; }
  .ic span { display: grid; place-items: center; width: 30px; height: 30px; border-radius: 8px; background: var(--ink); color: var(--paper); }
  .ic span + span { position: absolute; left: 18px; bottom: -6px; width: 17px; height: 17px; border-radius: 5px; box-shadow: 0 0 0 2px var(--paper); }
  .ic svg { width: 100%; height: 100%; fill: currentColor; }
  .ic .pi-hole { fill: var(--ink); }
  .best .ic span { background: var(--red); } .best .ic .pi-hole { fill: var(--red); }
  .ref .ic span { background: #b5b5b5; } .ref .ic .pi-hole { fill: #b5b5b5; }
  .nm { flex: 1; min-width: 0; }
  .nm b { display: block; font-size: 16px; font-weight: 700; }
  .nm small { display: block; font-size: 12.5px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .pr { text-align: right; white-space: nowrap; }
  .pr b { position: relative; z-index: 0; font-size: 20px; font-weight: 800; }
  .pr small { display: block; font-size: 12px; color: var(--muted); }
  .pr small.up { color: var(--red); font-weight: 700; }
  .ref .pr b { color: var(--muted); font-weight: 600; }
  /* 荧光笔：最低那一行划一道黄 */
  .best .pr b::before {
    content: ""; position: absolute; z-index: -1; left: -6px; right: -6px; top: 52%; height: 50%;
    background: var(--hi); transform: skewX(-12deg) scaleX(0); transform-origin: left; border-radius: 3px;
    transition: transform .55s cubic-bezier(.3,.8,.3,1) .1s;
  }
  .done .best .pr b::before { transform: skewX(-12deg) scaleX(1); }
  .more { padding: 2px 0 12px 46px; font-size: 13.5px; color: var(--ink2); }
  details[open] .more { animation: drop .25s ease both; }
  @keyframes drop { from { opacity: 0; transform: translateY(-4px); } }
  .calc { margin-top: 6px; }
  .calc p { display: flex; justify-content: space-between; padding: 2px 0; }
  .calc p.m span:last-child { color: var(--red); font-weight: 600; }
  .calc p.t { margin-top: 4px; padding-top: 6px; border-top: 1.5px solid var(--ink); font-weight: 800; }
  .formula { margin-top: 8px; font-size: 12.5px; line-height: 1.5; color: var(--ink2); }
  .formula small { display: block; font-size: 11px; font-weight: 700; color: var(--muted); }
  .pm-h { margin-top: 8px; font-size: 12px; font-weight: 700; color: var(--muted); }
  .pm { list-style: none; font-size: 13px; }
  .pm li::before { content: "· "; color: var(--red); font-weight: 800; }
  .lk { display: flex; flex-wrap: wrap; gap: 6px 14px; margin-top: 8px; }
  .lk a { font-weight: 700; text-decoration: none; border-bottom: 2px solid var(--hi); }
  .lk a.src { color: var(--link); border-color: transparent; font-weight: 600; }

  /* 合计 + 刮刮乐贴纸 */
  .total { position: relative; margin-top: 4px; }
  .total .lab { font-size: 13px; font-weight: 700; color: var(--muted); letter-spacing: .08em; }
  .big { margin-top: 2px; font-size: 62px; line-height: 1; font-weight: 900; letter-spacing: -.035em; }
  .big i { font-style: normal; font-size: 28px; font-weight: 800; margin-right: 2px; vertical-align: 24px; }
  .big.is-ref { color: var(--muted); font-size: 52px; }
  .where { margin-top: 8px; font-size: 14px; color: var(--ink2); }
  .where b { color: var(--ink); }
  .where button { margin-left: 6px; padding: 1px 7px; border: 1.5px solid var(--ink); border-radius: 6px; background: none; font-size: 12px; font-weight: 700; cursor: pointer; }
  .where button:hover { background: var(--ink); color: var(--paper); }
  .sticker {
    position: absolute; right: -40px; top: -30px; width: 112px; height: 112px; transform: rotate(10deg);
    clip-path: polygon(50% 0%, 61% 10%, 75% 6%, 79% 20%, 93% 25%, 89% 39%, 100% 50%, 89% 61%, 93% 75%, 79% 80%, 75% 94%, 61% 90%, 50% 100%, 39% 90%, 25% 94%, 21% 80%, 7% 75%, 11% 61%, 0% 50%, 11% 39%, 7% 25%, 21% 20%, 25% 6%, 39% 10%);
    background: var(--red); color: #fff; display: grid; place-items: center; text-align: center; line-height: 1.1;
    user-select: none; -webkit-user-select: none;
  }
  .sticker small { display: block; font-size: 12px; font-weight: 700; }
  .sticker b { display: block; font-size: 20px; font-weight: 900; letter-spacing: -.02em; }
  .sticker canvas { position: absolute; inset: 0; width: 100%; height: 100%; cursor: grab; touch-action: none; transition: opacity .4s; }
  .sticker.open canvas { opacity: 0; pointer-events: none; }
  .sticker.open { animation: pop .5s cubic-bezier(.2,1.8,.4,1); }
  @keyframes pop { 40% { transform: rotate(10deg) scale(1.18); } }
  .scratch-tip { position: absolute; right: 78px; top: -18px; font-size: 12px; font-weight: 800; color: var(--red); transform: rotate(-6deg); white-space: nowrap; transition: opacity .3s; }
  .scratch-tip::after { content: ""; display: inline-block; width: 18px; height: 2px; margin-left: 4px; background: currentColor; vertical-align: 4px; }
  .sticker.open + .scratch-tip { opacity: 0; }
  html:not(.js) .sticker canvas, html:not(.js) .scratch-tip { display: none; }
  .go {
    margin-top: 18px; display: flex; align-items: center; justify-content: space-between; padding: 15px 18px;
    background: var(--hi); color: var(--ink); text-decoration: none; font-size: 17px; font-weight: 800;
    border: 2.5px solid var(--ink); border-radius: 12px; box-shadow: 4px 4px 0 var(--ink); transition: transform .12s, box-shadow .12s;
  }
  .go:hover { transform: translate(-2px, -2px); box-shadow: 6px 6px 0 var(--ink); }
  .go:active { transform: translate(3px, 3px); box-shadow: 1px 1px 0 var(--ink); }
  .go svg { width: 22px; height: 22px; stroke: currentColor; fill: none; stroke-width: 2.6; stroke-linecap: round; stroke-linejoin: round; }
  button.go { width: 100%; font: inherit; font-weight: 800; color: inherit; text-align: left; cursor: pointer; }
  .tip { margin-top: 8px; font-size: 12.5px; color: var(--muted); }
  .tip a { color: var(--muted); text-decoration: underline; margin-left: 4px; white-space: nowrap; }
  .warn { margin-top: 14px; padding: 10px 12px; border-radius: 10px; background: #fff3cc; font-size: 13.5px; color: var(--ink2); display: grid; gap: 4px; }

  /* 价格鉴定：表盘 + 对比 */
  .gauge { position: relative; width: 240px; margin: 18px auto 0; }
  .gauge svg { display: block; width: 100%; overflow: visible; }
  .g-out { fill: none; stroke: var(--ink); stroke-width: 30; }
  .g-seg { fill: none; stroke-width: 24; }
  .g1 { stroke: var(--green); } .g2 { stroke: var(--hi); } .g3 { stroke: var(--red); }
  .g-sep { stroke: var(--ink); stroke-width: 3; }
  .g-lab { font: 800 13px var(--sans); fill: var(--muted); text-anchor: middle; }
  .g-lab.on { font-size: 15px; fill: var(--ink); }
  .needle { transform-box: view-box; transform-origin: 120px 125px; transform: rotate(-90deg); transition: transform 1.3s cubic-bezier(.3,1.7,.45,1) .35s; }
  .needle polygon, .needle circle { fill: var(--ink); }
  .needle .hub { fill: var(--hi); }
  .done .needle, html:not(.js) .needle { transform: rotate(var(--deg)); }
  .vd { margin-top: 4px; text-align: center; font-size: 30px; font-weight: 900; letter-spacing: .04em; }
  .vs { text-align: center; font-size: 14px; color: var(--ink2); }
  .cmp { margin-top: 14px; border: 2px solid var(--ink); border-radius: 12px; overflow: hidden; }
  .cmp p { display: grid; grid-template-columns: 1fr auto auto; align-items: center; gap: 10px; padding: 9px 12px; font-size: 14px; }
  .cmp p + p { border-top: 1.5px dashed var(--line); }
  .cmp .cmp-h { background: var(--ink); color: var(--paper); font-size: 12.5px; font-weight: 700; padding: 7px 12px; }
  .cmp b { font-weight: 800; }
  .cmp small { display: block; font-size: 11.5px; color: var(--muted); }
  .d { min-width: 92px; padding: 3px 8px; border-radius: 999px; font-size: 12.5px; font-weight: 800; text-align: center; }
  .d.good { background: var(--green); color: #fff; }
  .d.same { background: #ececec; color: var(--ink); }
  .d.bad { background: var(--red); color: #fff; }

  /* 大促倒计时：黑底黄字一条 */
  .count { margin: 18px -26px; padding: 14px 26px; background: var(--ink); color: #fff; display: flex; align-items: center; justify-content: space-between; }
  .count small { font-size: 13px; color: #bdbdbd; }
  .count b { font-size: 26px; font-weight: 900; color: var(--hi); letter-spacing: .02em; }
  .count b em { font-style: normal; font-size: 14px; color: #fff; margin: 0 6px 0 2px; font-weight: 700; }

  /* 口碑 */
  .kb { display: flex; align-items: baseline; gap: 10px; }
  .kb a { font-size: 30px; font-weight: 900; letter-spacing: -.02em; text-decoration: none; }
  .kb span { font-size: 13px; color: var(--muted); }
  .tags { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 6px; }
  .tags span { padding: 3px 10px; border-radius: 999px; border: 1.5px solid var(--ink); font-size: 13px; font-weight: 600; }
  .tags span.con { border-color: var(--red); color: var(--red); }
  .pts { margin-top: 10px; padding-left: 18px; font-size: 14px; color: var(--ink2); display: grid; gap: 4px; }
  .pts a { color: var(--link); font-size: 12px; font-weight: 700; text-decoration: none; margin-left: 4px; }

  /* 撕下来 */
  .tear { position: relative; margin: 18px -26px 0; padding: 14px 0 6px; border-top: 2px dashed var(--ink); text-align: center; touch-action: none; cursor: grab; user-select: none; -webkit-user-select: none; }
  .tear:active { cursor: grabbing; }
  .tear svg { position: absolute; left: 16px; top: -12px; width: 22px; height: 22px; padding: 2px; background: var(--paper); stroke: var(--ink); fill: none; stroke-width: 1.8; }
  .tear p { font-size: 13px; font-weight: 700; color: var(--ink2); }
  .tear p small { display: block; font-weight: 400; color: var(--muted); font-size: 12px; }
  html:not(.js) .tear { display: none; }
  .torn-part { position: relative; }
  .stub { padding: 14px 26px 18px; text-align: center; background: var(--paper); }
  .stub .barcode { display: block; width: 220px; height: 40px; margin: 0 auto; fill: var(--ink); }
  .stub .no { margin-top: 6px; font-size: 12px; letter-spacing: .2em; color: var(--muted); }
  .stub .acc { margin-top: 10px; font-size: 14px; font-weight: 700; }
  .stub .fine { margin-top: 6px; font-size: 11.5px; color: var(--muted); }
  .torn-part.fall { animation: fall 1s cubic-bezier(.5,0,.8,.4) forwards; }
  @keyframes fall { to { transform: translateY(120vh) rotate(18deg); opacity: .9; } }
  /* 撕掉存根以后，剩下这截底边变成锯齿 */
  .receipt.torn .paper { padding-bottom: 14px; -webkit-mask: conic-gradient(from -45deg at bottom, #0000, #000 1deg 89deg, #0000 90deg) bottom / 16px 8px repeat-x, linear-gradient(#000 0 0) top / 100% calc(100% - 8px) no-repeat; mask: conic-gradient(from -45deg at bottom, #0000, #000 1deg 89deg, #0000 90deg) bottom / 16px 8px repeat-x, linear-gradient(#000 0 0) top / 100% calc(100% - 8px) no-repeat; }
  .receipt.torn .tear { display: none; }

  .toast { position: fixed; z-index: 5; left: 50%; bottom: 24px; max-width: calc(100% - 32px); transform: translate(-50%, 20px); opacity: 0; padding: 10px 18px; border-radius: 999px; background: var(--ink); color: #fff; font-size: 14px; font-weight: 600; text-align: center; transition: .25s; pointer-events: none; }
  .toast.on { opacity: 1; transform: translate(-50%, 0); }
  .confetti { position: fixed; z-index: 4; width: 8px; height: 12px; border-radius: 2px; pointer-events: none; }

  @media (max-width: 440px) {
    body { padding-top: 28px; }
    .paper { padding: 20px 20px 0; }
    .cut, .count, .tear { margin-left: -20px; margin-right: -20px; }
    .count { padding-left: 20px; padding-right: 20px; }
    .big { font-size: 52px; }
    .sticker { width: 92px; height: 92px; right: -12px; top: -44px; }
    .scratch-tip { right: 84px; top: -30px; }
    .ribbon { font-size: 18px; }
  }
  @media (prefers-reduced-motion: reduce) {
    *, *::before { animation: none !important; transition: none !important; }
    .best .pr b::before { transform: skewX(-12deg); }
    .needle { transform: rotate(var(--deg)); }
  }
  @media print {
    .bg, .tear, .toast, .scratch-tip, .sticker canvas { display: none !important; }
    body { background: #fff; }
    .receipt { filter: none; }
  }
</style>
</head>
<body>
  <div class="bg" aria-hidden="true">
    <div class="ribbon r1"><span>${r1}</span></div>
    <div class="ribbon r2"><span>${r2}</span></div>
  </div>

  <div class="scene">
    <div class="tilt">
    <article class="receipt" id="receipt">
      <div class="zig top"></div>
      <div class="paper">
        <div class="glare"></div>
        <div class="hd"><b>比价小票</b><span class="num">${head}</span></div>
        <p class="prod">${keyword}${spec}</p>

        <hr class="cut">
        <p class="h">查了 ${count_n} 个平台<small>点一行看怎么算的</small></p>
        <ul class="items">${items}
        </ul>

        <hr class="cut">${total}

        <hr class="cut">
        <p class="h">价格鉴定<small>按固定规则比出来的</small></p>${judge}
${countdown}
        <p class="h">买家口碑<small>来自公开网页</small></p>
        ${kb}

        <div class="tear" id="tear">
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M8.6 7.5 20 18M8.6 16.5 20 6"/></svg>
          <p>往下拉，撕下来<small>撕下来就复制好了，直接发给朋友</small></p>
        </div>
      </div>
      <div class="torn-part" id="stub">
        <div class="stub">
          ${barcode}
          <p class="no num">${no}</p>
          ${acc}
          <p class="fine">${fine}</p>
        </div>
        <div class="zig"></div>
      </div>
    </article>
    </div>
  </div>
  <p class="toast" role="status"></p>
  <script type="application/json" id="receipt-data">${meta}</script>

<script>
(function () {
  document.documentElement.classList.add("js");
  var data = {};
  try { data = JSON.parse(document.getElementById("receipt-data").textContent); } catch (e) {}
  var reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  var receipt = document.getElementById("receipt"), toast = document.querySelector(".toast"), timer;
  function say(t) { toast.textContent = t; toast.classList.add("on"); clearTimeout(timer); timer = setTimeout(function () { toast.classList.remove("on"); }, 2400); }
  // 复制：先在点击里同步复制（内嵌浏览器常不给异步剪贴板权限），不行再试剪贴板接口
  function copy(t, done) {
    var ta = document.createElement("textarea"); ta.value = t; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select(); var ok = false; try { ok = document.execCommand("copy"); } catch (e) {} ta.remove();
    if (ok) say(done); else if (navigator.clipboard) navigator.clipboard.writeText(t).then(function () { say(done); }, function () { say("没复制成功，可以手动选中文字复制"); });
  }

  // 小票落定以后：划荧光笔、表针摆过去、价格数字滚一下
  function countUp() {
    var el = document.querySelector("[data-count]");
    if (!el || reduce) return;
    var to = parseFloat(el.getAttribute("data-count")), final = el.textContent, t0 = null;
    function step(t) { if (t0 === null) t0 = t; var k = Math.min(1, (t - t0) / 900), e = 1 - Math.pow(1 - k, 3);
      el.textContent = k < 1 ? Math.floor(to * e).toLocaleString("zh-CN") : final;
      if (k < 1) requestAnimationFrame(step); }
    requestAnimationFrame(step);
  }
  // 进场：小票从下面弹上来。动画没跑起来（后台标签页、预览缩略图）时 0.6 秒后直接到位，小票永远看得见
  var land = function () { receipt.classList.add("done"); countUp(); };
  if (reduce || !receipt.animate) land();
  else {
    var arrive = receipt.animate([{ opacity: 0, transform: "translateY(40px) rotate(-3deg)" }, { opacity: 1, transform: "none" }],
                                 { duration: 700, easing: "cubic-bezier(.2,.9,.3,1)" });
    arrive.onfinish = land;
    setTimeout(function () { if (arrive.playState === "running" && !(arrive.currentTime > 0)) arrive.finish(); }, 600);
  }

  // 跟着鼠标轻轻倾斜，带一点反光
  var scene = document.querySelector(".scene"), tilt = document.querySelector(".tilt"), glare = document.querySelector(".glare");
  if (!reduce && matchMedia("(hover: hover)").matches) {
    scene.addEventListener("pointermove", function (e) {
      var r = scene.getBoundingClientRect(), px = (e.clientX - r.left) / r.width - .5, py = (e.clientY - r.top) / r.height - .5;
      tilt.style.transform = "rotateY(" + (px * 5) + "deg) rotateX(" + (-py * 2) + "deg)";
      glare.style.setProperty("--gx", (px + .5) * 100 + "%"); glare.style.setProperty("--gy", (py + .5) * 100 + "%");
    });
    scene.addEventListener("pointerleave", function () { tilt.style.transform = ""; });
  }

  // 刮刮乐：贴纸上盖一层银色涂层，刮掉三成就整块揭开，撒一把彩纸
  var sticker = document.getElementById("sticker");
  if (sticker) {
    var cv = sticker.querySelector("canvas"), ctx = cv.getContext("2d"), scratching = false, opened = false, moves = 0;
    var coat = function () {
      var s = 2; cv.width = sticker.offsetWidth * s; cv.height = sticker.offsetHeight * s;
      var g = ctx.createLinearGradient(0, 0, cv.width, cv.height);
      g.addColorStop(0, "#d9d9d9"); g.addColorStop(.5, "#f4f4f4"); g.addColorStop(1, "#bdbdbd");
      ctx.globalCompositeOperation = "source-over"; ctx.fillStyle = g; ctx.fillRect(0, 0, cv.width, cv.height);
      ctx.fillStyle = "#8a8a8a"; ctx.font = "800 " + 13 * s + "px -apple-system, PingFang SC, sans-serif"; ctx.textAlign = "center";
      ctx.fillText("刮我", cv.width / 2, cv.height / 2 + 5 * s);
    };
    var scratch = function (e) {
      var k = cv.width / cv.clientWidth;
      ctx.globalCompositeOperation = "destination-out"; ctx.beginPath();
      ctx.arc(e.offsetX * k, e.offsetY * k, 16 * k, 0, Math.PI * 2); ctx.fill();
    };
    var cleared = function () {
      var d = ctx.getImageData(0, 0, cv.width, cv.height).data, n = 0, all = 0;
      for (var i = 3; i < d.length; i += 32) { all++; if (d[i] === 0) n++; }
      return n / all;
    };
    var reveal = function () {
      if (opened) return; opened = true; sticker.classList.add("open");
      if (data.pop) say(data.pop);
      if (reduce || !document.body.animate) return;
      var r = sticker.getBoundingClientRect(), colors = ["#ffe14d", "#ff3b30", "#111", "#17b26a", "#2a3cff"];
      for (var i = 0; i < 26; i++) {
        var c = document.createElement("i"), a = Math.random() * Math.PI * 2, d = 50 + Math.random() * 110;
        c.className = "confetti"; c.style.left = r.left + r.width / 2 + "px"; c.style.top = r.top + r.height / 2 + "px"; c.style.background = colors[i % colors.length];
        document.body.appendChild(c);
        c.animate([{ transform: "translate(0,0) rotate(0)", opacity: 1 },
                   { transform: "translate(" + Math.cos(a) * d + "px," + (Math.sin(a) * d - 40) + "px) rotate(" + (Math.random() * 540) + "deg)", opacity: 1, offset: .55 },
                   { transform: "translate(" + Math.cos(a) * d * 1.2 + "px," + (Math.sin(a) * d + 140) + "px) rotate(" + (Math.random() * 720) + "deg)", opacity: 0 }],
                  { duration: 1100 + Math.random() * 400, easing: "cubic-bezier(.2,.7,.4,1)" }).onfinish = c.remove.bind(c);
      }
    };
    coat();
    cv.addEventListener("pointerdown", function (e) { e.preventDefault(); scratching = true; cv.setPointerCapture(e.pointerId); scratch(e); });
    cv.addEventListener("pointermove", function (e) { if (!scratching) return; scratch(e); if (++moves % 6 === 0 && cleared() > .3) reveal(); });
    cv.addEventListener("pointerup", function () { scratching = false; if (cleared() > .3) reveal(); });
  }

  // 往下拉撕下来：拉过 70px 就撕，存根掉下去，同时复制小票文字
  var tear = document.getElementById("tear"), stub = document.getElementById("stub"), y0 = null;
  tear.addEventListener("pointerdown", function (e) { y0 = e.clientY; tear.setPointerCapture(e.pointerId); });
  tear.addEventListener("pointermove", function (e) {
    if (y0 === null) return; var d = Math.max(0, e.clientY - y0);
    stub.style.transform = "translateY(" + d * .6 + "px) rotate(" + d * .04 + "deg)";
    if (d > 70) {
      y0 = null; stub.style.transform = ""; stub.classList.add("fall"); receipt.classList.add("torn");
      setTimeout(function () { stub.style.display = "none"; }, 1000);
      copy(data.text || "", "撕下来了！小票文字已复制，发给朋友吧");
    }
  });
  tear.addEventListener("pointerup", function () { if (y0 !== null) { y0 = null; stub.style.transition = "transform .3s"; stub.style.transform = ""; setTimeout(function () { stub.style.transition = ""; }, 300); } });

  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-copy]");
    if (b) copy(b.getAttribute("data-copy"), b.getAttribute("data-done") || "已复制");
  });

  // 大促倒计时
  var cd = document.querySelector("[data-deadline]");
  if (cd) {
    var dl = new Date(cd.getAttribute("data-deadline")).getTime();
    var pad = function (n) { return (n < 10 ? "0" : "") + n; };
    var run = function () { var s = Math.max(0, Math.floor((dl - Date.now()) / 1000));
      cd.querySelector("[data-u=d]").textContent = Math.floor(s / 86400); cd.querySelector("[data-u=h]").textContent = pad(Math.floor(s % 86400 / 3600));
      cd.querySelector("[data-u=m]").textContent = pad(Math.floor(s % 3600 / 60)); cd.querySelector("[data-u=s]").textContent = pad(s % 60); };
    if (isFinite(dl)) { run(); setInterval(run, 1000); }
  }
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
