#!/usr/bin/env python3
"""蜗牛最爽省钱 Skill · 购物车体检：读用户手机淘宝 / 京东购物车里的商品，挨个点进详情页看到手价，
再用 zidong.py 去各平台搜同款、WebSearch 查参考价，最后出一张「购物车体检单」，告诉用户哪件在别处更便宜、能省多少。

两步：
  read    操作 iPhone 镜像：打开 App → 底部「购物车」→ 读前几件商品（标题、规格、购物车价）→
          挨个点标题进详情页，读到手价、券和立减（只看不领）→ 返回购物车。
  report  不碰手机：把每件商品在各平台查到的实时价、参考价和购物车价放在一起，算能省多少，
          输出体检单（Markdown）和体检小票（HTML）。

安全（在 zidong.py 的规则之上）：
  - 购物车页上只点两个地方：底部「购物车」标签、商品标题文字靠左的地方。
    不点勾选圈、数量（×1 / − +）、规格那一行、「领取」「结算」，不左右滑（左滑会露出「删除」），不长按。
  - 每次回到购物车都核对「合计」金额和「结算(N)」件数：和刚进购物车时不一样，说明误勾选了商品，立即安全停止。
  - 退不回购物车（淘宝退出详情页时偶尔一直弹「找相似」浮层）：把 App 划掉重开，再进购物车接着读下一件。

用法：
  python3 gouwuche.py read --app 淘宝 --items 3 --out-dir <工作目录>
  python3 gouwuche.py read --app 京东 --pick 戴森 --out-dir <工作目录>      # 只看标题里有「戴森」的
  python3 gouwuche.py report <cart-*.json> --item 1 <mirror-*.json …> <ref-*.json> --item 2 … --out-dir <工作目录>
退出码：0 跑完（购物车空也算）；3 安全停止；1 镜像没连上等错误。
"""
import argparse
import datetime
import difflib
import html
import importlib.util
import json
import os
import re
import string
import sys
import time
import urllib.parse
from collections import Counter
from types import SimpleNamespace as NS

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


zd = _load("zidong")
jx, hz = zd.jx, zd.hz
xp = _load("xiaopiao")  # 单品比价小票：体检单用它的模板和组件，样子、互动都一样

CART_APPS = ("淘宝", "京东")
EMPTY = ("暂无商品", "空空如也", "购物车是空的", "购物车竟然是空的", "还没有商品", "购物车还是空")
INVALID = ("缺货", "已不能购买", "失效", "已下架", "无货", "售罄", "不在配送范围", "暂不支持配送", "已抢光", "不能购买")
END_WORDS = ("猜你喜欢", "为你推荐", "你可能还喜欢", "大家都在买", "超值推荐", "看了又看", "精选推荐")
# 标题下面的标签行（「官方立减31元 礼金直降 满2件9.5折」「退货宝 大促价保」「比加购降9.88」）
CHIP_RE = re.compile(r"退货宝|价保|包退|极速退款|假一赔|立减|礼金|直降|满\d+件|满\d+减|\d折|赠|包邮|运费险|进口税|比加购|降价|已降|"
                     r"补贴\d|分期|免息|以旧换新|次日达|当日达|小时达|送达|极速达|保障|发票|限购|仅剩|库存|预计")
NOT_TITLE = re.compile(r"^(比加购降|进口税|官方立减|退货宝|已降|降价|预计|限购|仅剩|库存|共\d|已选|赠品|【赠品】|换购|凑单|再买|还差|满\d)")
QTY_RE = re.compile(r"\s*[×xX]\s*\d{1,3}\s*$")
SPEC_END = re.compile(r"[>＞〉›∨⌄～~]\s*$|[:：]")  # 京东规格行末尾的「⌵」常被认成「～」
CART_PRICE = re.compile(r"^(?P<label>[^¥￥\d]{0,10}?)\s*[¥￥]\s*(?P<p>\d[\d,]*(?:\.\d+)?)\s*起?(?:[^¥￥]{0,6}[¥￥]\s*(?P<o>\d[\d,]*(?:\.\d+)?))?")
COUPON_HINT = re.compile(r"券后约|领.{0,8}券|领取")
SHOP_HEAD = re.compile(r"[>＞〉›]\s*$")
SIMILAR = ("包装规格", "净含量", "找相似", "相似商品", "同款")  # 淘宝退出详情页时弹的「找相似」浮层上的字
MAX_SCREENS = 10  # 每次往下翻约三分之一屏


def num(s):
    return float(s.replace(",", ""))


def is_chips(t):
    """标签行：好几个短词（每个不超过 8 个字），或者整行就是一个标签。"""
    toks = t.split()
    return bool(CHIP_RE.search(t)) and (len(toks) >= 2 and all(len(x) <= 9 for x in toks) or len(t) <= 9)


def cart_bounds(lines):
    """购物车列表的上下边界：上面是「购物车」标题，下面是「全选 / 合计 / 结算」那一排；推荐区（猜你喜欢）不算购物车。"""
    head = [o["cy"] for o in lines if o["cy"] < 20 and o["t"].strip().startswith("购物车")]
    # 京东购物车标题下面还有一排分类（全部 / 购物 / 秒送 外卖 …）
    head += [o["cy"] for o in lines if o["cy"] < 26 and o["t"].strip() == "全部"]
    top = max(head) + 1.5 if head else 18
    foot = [o["cy"] for o in lines if o["cy"] > 75 and re.search(r"结算|合计|全选", o["t"])]
    bottom = min(foot) - 1.5 if foot else 88
    end = [o["cy"] for o in lines if top < o["cy"] < bottom and any(w in o["t"] for w in END_WORDS)]
    return top, min(end + [bottom])


def text_rows(lines, top, bottom):
    """右边文字栏（商品图右边）按行拼起来：[{x, cy, w, t}]，x 是这一行最左边那段的位置。"""
    col = sorted((o for o in lines if top < o["cy"] < bottom and o["x"] >= 30), key=lambda o: o["cy"])
    rows, cur = [], []
    for o in col:
        if cur and abs(o["cy"] - cur[0]["cy"]) > 1.0:
            rows.append(cur)
            cur = []
        cur.append(o)
    if cur:
        rows.append(cur)
    out = []
    for r in rows:
        r = sorted(r, key=lambda o: o["x"])
        out.append({"x": r[0]["x"], "w": r[0]["w"], "cy": r[0]["cy"], "t": " ".join(o["t"].strip() for o in r),
                    "first": r[0]["t"].strip()})
    return out


def shop_of(lines, top, cy):
    """商品上方最近的店铺行（「淘宝 劈腿大师 >」「天猫 译林出版社旗舰店 >」「百亿补贴品牌优选 >」）。"""
    heads = [o for o in lines if top < o["cy"] < cy and 8 < o["x"] < 30 and SHOP_HEAD.search(o["t"]) and len(o["t"].strip()) >= 3]
    if not heads:
        return ""
    t = max(heads, key=lambda o: o["cy"])["t"]
    # 店名前面的角标：「淘宝 劈腿大师」「天猫 …旗舰店」「自营 京东超市 京东超市官方自营」
    toks = t.strip().split()
    while len(toks) > 1 and re.fullmatch(r"淘宝|天猫|京东|拼多多|自营|京东超市|超市|京东国际|百亿补贴", toks[0]):
        toks.pop(0)
    t = " ".join(toks)
    if "百亿补贴" in t:
        return "百亿补贴"
    return hz.clean_shop(zd.clean_shop(t)) or ""


def parse_cart(lines):
    """一屏购物车 → 商品列表。每件：标题（一两行）、规格、购物车价（和价格前的说法，如「平台加补后」）、划线价、店铺、
    点标题的位置。价格没露出来的（被屏幕边缘截断）标 complete=False；失效、缺货的标 invalid。
    只认和文字栏左边对齐的行：缺货商品下面的「更多同款」推荐卡片（往右缩进）不算。"""
    top, bottom = cart_bounds(lines)
    rows = text_rows(lines, top, bottom)
    xs = Counter(round(r["x"]) for r in rows if 30 <= r["x"] <= 50)
    if not xs:
        return []
    col = xs.most_common(1)[0][0]
    items, cur, cut = [], None, True  # cut：屏幕最上面那件可能被截断，看到第一个标题之前的行都不要
    for r in rows:
        t = QTY_RE.sub("", r["t"]).strip()
        if not t or abs(r["x"] - col) > 2.5:
            continue
        if any(w in t for w in INVALID):
            if cur is not None and cur["price"] is None:
                cur["invalid"] = True
            continue
        m = CART_PRICE.match(t)
        if m:
            if cur is None:
                continue
            if cur["price"] is None and not COUPON_HINT.search(t):
                cur.update(price=num(m.group("p")), label=m.group("label").strip(" ：:"),
                           original=num(m.group("o")) if m.group("o") else 0)
            else:
                cur["hints"].append(t)
            continue
        if cur is None or cur["price"] is not None:
            if cut and cur is None and SPEC_END.search(t):
                continue  # 被截断那件的规格行
            if len(t) >= 6 and not NOT_TITLE.match(t) and not is_chips(t):
                cur = {"title": [t], "spec": "", "tags": [], "hints": [], "price": None, "label": "", "original": 0,
                       "x": r["x"], "w": r["w"], "cy": r["cy"], "invalid": False}
                items.append(cur)
                cut = False
            continue
        # 标题下面紧跟的一行就是规格（淘宝、京东购物车的标题都只显示一行，长了截断）；之后的是标签
        if not cur["spec"] and not cur["tags"] and not is_chips(t) and not NOT_TITLE.match(t):
            cur["spec"] = re.sub(r"\s*[>＞〉›∨⌄～~]\s*$", "", t)
        else:
            cur["tags"].append(t)
    out = []
    for c in items:
        title = re.sub(zd.BADGES, "", " ".join(c["title"])).strip()
        out.append({"title": title, "spec": c["spec"], "price": c["price"] or 0, "label": c["label"], "original": c["original"],
                    "hints": c["hints"], "tags": c["tags"], "shop": shop_of(lines, top, c["cy"]),
                    "tap": (c["x"] + min(c["w"] / 2, 8), c["cy"]), "complete": c["price"] is not None,
                    "invalid": c["invalid"]})
    return out


def item_key(c):
    return re.sub(r"\W", "", c["title"])[:10]


SHOPISH = re.compile(r"店铺|老店|回头客|粉丝|旗舰店|专营店|进店|已售|好评|评价")  # 详情页上店铺那一栏的字，不是标题


def detail_title(lines, cart_title):
    """详情页上的完整标题：和购物车里（被截短的）标题最像的那一行，再接上紧挨着的下一行。"""
    want = re.sub(r"\W", "", cart_title)
    best, score = None, 0.0
    for o in lines:
        t = o["t"].strip()
        if not (35 < o["cy"] < 92) or len(t) < 8 or re.search(r"[¥￥]", t) or zd.JUNK_RE.search(t) or SHOPISH.search(t):
            continue
        s = difflib.SequenceMatcher(None, want, re.sub(r"\W", "", t)).ratio()
        if s > score:
            best, score = o, s
    if not best or score < 0.3:
        return ""
    parts = [best["t"].strip()]
    nxt = [o for o in lines if 1.5 < o["cy"] - best["cy"] < 4.2 and o["x"] < 20 and len(o["t"].strip()) >= 4
           and not re.search(r"[¥￥]", o["t"]) and not zd.PROMO_RE.search(o["t"])]
    if nxt:
        parts.append(min(nxt, key=lambda o: o["cy"])["t"].strip())
    t = re.sub(r"\s*(送礼|分享|收藏)$", "", "".join(parts))
    return re.sub(r"^((百亿补贴|产地优选|源头好货|品牌|官方|自营|天猫|京东超市|京东国际|超市|国补)\s*)+", "", re.sub(zd.BADGES, "", t)).strip()


class CartRunner(zd.Runner):
    def __init__(self, app):
        super().__init__("购物车", "", details=1)
        self.app_name = app
        self.platform = zd.APPS[app]["platform"]
        self.exit_stuck, self.stuck_count, self.lost_results, self.shot_popups, self._restarted = False, 0, False, 0, False
        self.out, self.base = [], (None, None)  # 已经看完的商品：中途出错也不丢

    def is_cart(self):
        head = any(o["cy"] < 20 and o["cx"] < 45 and o["t"].strip().startswith("购物车") for o in self.lines)
        return head and (any(o["cy"] > 75 and re.search(r"结算|合计|全选", o["t"]) for o in self.lines)
                         or any(w in self.text_all() for w in EMPTY) or bool(self.tab_labels()))

    def results_like(self):  # leave_detail 等通用代码里「回到列表了」的判断
        return self.is_cart()

    def cart_state(self):
        """合计金额和结算件数。京东底部只写「¥250.60 查看明细」、没有「合计」两个字：取「结算」按钮同一排左边的金额。"""
        total, count = super().cart_state()
        if total is None:
            btn = next((o for o in self.lines if o["cy"] > 75 and "结算" in o["t"]), None)
            if btn:
                left = [o for o in self.lines if abs(o["cy"] - btn["cy"]) < 2.5 and o["x"] < btn["x"] and zd.PRICE_RE.search(o["t"])]
                if left:
                    total = zd.PRICE_RE.search(max(left, key=lambda o: o["x"])["t"]).group(1)
        return total, count

    def cart_count(self):
        m = next((re.search(r"购物车\s*[(（]\s*(\d+)", o["t"]) for o in self.lines if o["cy"] < 20 and "购物车" in o["t"]), None)
        return int(m.group(1)) if m else None

    def open_cart(self, launch=True):
        if launch:
            self.open_app(self.app_name)
        self.snap()
        if not self.is_cart():
            self.go_home_tab(self.app_name)
            self.snap()
            tab = next((o for o in self.lines if o["cy"] > 88 and o["t"].replace(" ", "") == "购物车"), None)
            if not tab:
                raise zd.Skip(f"{self.app_name} 底部没找到「购物车」标签")
            self.tap(tab["cx"], tab["cy"], "底部「购物车」标签", wait=3)
            self.snap()
            self.close_popups()
        self.snap(keep=True)
        if not self.is_cart():
            raise zd.Skip("点了「购物车」标签，但没进购物车页")
        # App 停在上次往下翻过的购物车：点右边悬浮的「顶部」回到最上面，从第一件开始看
        top = next((o for o in self.lines if o["t"].strip() == "顶部" and o["cx"] > 80 and 60 < o["cy"] < 88), None)
        if top:
            self.tap(top["cx"], top["cy"] - 1.5, "购物车右边的「顶部」（回到第一件）", wait=2)
            self.snap(keep=True)
            if not self.is_cart():
                raise zd.Skip("点了「顶部」后不在购物车页了")
        self.log("进了购物车")

    def similar_close(self):
        """「找相似」浮层（顶上一排商品图、「搜索」按钮和 ×）：返回 × 的位置。"""
        if not any(w in self.text_all() for w in SIMILAR):
            return None
        for s in self.lines:
            if s["t"].replace(" ", "") == "搜索" and s["cx"] > 65:
                x = next((o for o in self.lines if o["t"].strip(" .•") in ("×", "X", "x", "✕") and o["cx"] > 84
                          and abs(o["cy"] - s["cy"]) < 2.5), None)
                return (x["cx"], x["cy"]) if x else (91.5, s["cy"])
        return None

    def back_to_cart(self):
        """退出详情页回到购物车。返回 True = 回到原来的位置；False = App 重开过，购物车从头开始。"""
        similar = 0
        for _ in range(zd.EXIT_TAPS + 1):
            self.snap()
            self.check_pay()
            if self.is_cart():
                return True
            close = self.similar_close()
            if close:
                similar += 1
                if similar >= 2:
                    self.log("退出详情页时一直弹「找相似」浮层：不再点")
                    break
                self.tap(*close, "「找相似」浮层右上角的 ×", wait=1.5)
                continue
            if any(w in o["t"].replace(" ", "") for o in self.lines for w in zd.SHOT_SHEET):
                self.log("退出时弹出了「识别截图」浮层：不再点")
                break
            corner = self.corner_close() if not any(w in self.text_all() for w in zd.PANEL_WORDS) else None
            if corner:
                self.tap(*corner, "右上角的 ×（关掉悬浮的直播小窗 / 直播间）", wait=2.0)
            else:
                self.tap(*zd.BACK, "左上角返回键（关面板 / 退出详情页）", wait=2.0)
        self.stuck_count += 1
        if self.stuck_count > 2:
            raise zd.Skip("连着几次退不回购物车，不再往下看")
        self.log(f"退不回购物车：把 {self.app_name} 划掉重开，再进购物车")
        if not self.restart_app(self.app_name):
            raise zd.Skip("退不回购物车，App 也没能重开")
        self.open_cart(launch=False)  # restart_app 已经把 App 打开了
        return False

    def guard(self, base):
        """回到购物车后核对合计和结算件数：变了就是误勾选了商品。"""
        now = self.cart_state()
        for name, b, a in (("合计金额", base[0], now[0]), ("结算件数", base[1], now[1])):
            if b is not None and a is not None and b != a:
                raise zd.SafetyStop(f"回到购物车后{name}从 {b} 变成了 {a}，可能误勾选了商品")

    def find_on_screens(self, done, pick):
        """从当前位置往下翻，找下一件没看过的、完整的有效商品。"""
        last = None
        for s in range(MAX_SCREENS):
            items = parse_cart(self.lines)
            for c in items:
                if c["invalid"] and item_key(c) not in self.invalid:
                    self.invalid.add(item_key(c))
            todo = [c for c in items if c["complete"] and not c["invalid"] and item_key(c) not in done
                    and (not pick or pick.lower() in c["title"].lower())]
            if todo:
                return todo[0]
            sig = self.text_all()
            if sig == last or any(w in sig for w in EMPTY) or any(w in o["t"] for o in self.lines if o["cy"] < 85 for w in END_WORDS):
                return None  # 到底了 / 进了推荐区
            last = sig
            self.cart_drag()
            self.log("购物车往下翻")
            self.snap(keep=True)
            if not self.is_cart():
                raise zd.Skip("往下翻购物车时离开了购物车页")
            self.guard(self.base)
        return None

    def cart_drag(self):
        """购物车往下翻约三分之一屏。淘宝购物车对滚轮没反应，只能拖：从最左边卡片外的灰色空隙（不会点到勾选圈、
        右边悬浮的「反馈 / 顶部」）竖着往上慢慢拖，停一下再松手，免得惯性一下滑过好几件。"""
        win = jx.find_window()
        x = win["x"] + win["w"] * 0.03
        y0, y1 = win["y"] + win["h"] * 0.72, win["y"] + win["h"] * 0.52
        jx.activate()
        jx.jxa(f"""
ObjC.import('CoreGraphics');
function ev(t, y) {{ $.CGEventPost($.kCGHIDEventTap, $.CGEventCreateMouseEvent(null, t, $.CGPointMake({x:.1f}, y), 0)); }}
var y0 = {y0:.1f}, y1 = {y1:.1f};
ev($.kCGEventMouseMoved, y0); delay(0.05);
ev($.kCGEventLeftMouseDown, y0);
for (var i = 1; i <= 40; i++) {{ ev($.kCGEventLeftMouseDragged, y0 + (y1 - y0) * i / 40); delay(0.02); }}
delay(0.6);
ev($.kCGEventLeftMouseUp, y1);
""")
        time.sleep(1.5)

    def run(self, want, pick):
        self.invalid = set()
        self.open_cart()
        self.base = base = self.cart_state()
        total = self.cart_count()
        if any(w in self.text_all() for w in EMPTY) and not any(c["complete"] for c in parse_cart(self.lines)):
            return {"status": "empty", "items": [], "total": 0}
        done, out = set(), self.out
        while len(out) < want:
            c = self.find_on_screens(done, pick)
            if not c:
                break
            done.add(item_key(c))
            rec = {k: c[k] for k in ("title", "spec", "price", "label", "original", "hints", "shop")}
            rec["n"] = len(out) + 1
            self.tap(*c["tap"], f"商品标题「{c['title'][:14]}」（进详情页看到手价）", wait=3.5)
            try:
                self.snap()
                self.check_pay()
                if self.is_cart():
                    raise zd.Skip("点了商品标题，还停在购物车")
                rec["detail_title"] = detail_title(self.lines, c["title"])  # 先读标题：后面打开优惠明细面板会把它盖住
                d = self.read_detail_here(c["price"], platform=self.platform)
                d.pop("_texts", None)
                rec["detail"] = d
            except zd.Skip as e:
                rec["detail"] = {"error": str(e)}
                self.log(f"没看成详情页：{e}")
            rec["screenshot"] = self.shots[-1] if self.shots else ""
            out.append(rec)
            same = self.back_to_cart()
            self.guard(base)
            if not same:
                self.log("App 重开过，从购物车顶上往下找下一件")
        return {"status": "ok", "items": out, "total": total, "invalid": len(self.invalid)}


def final_price(rec):
    """这件商品现在要付多少：详情页读到的到手价、按页面上的券 / 满减 / 国补算出来的价（和比价同一套算法 hz.settle_price），
    都没有才用购物车价。"""
    if not rec["price"]:
        return rec["price"], rec.get("label") or "购物车价"
    p, how, _ = hz.settle_price(rec["price"], rec.get("label") or "", rec.get("detail") or {})
    return p, how or "购物车价"


def render_read(cart):
    """read 的报告：购物车里看了哪几件、各多少钱。"""
    app = cart["app"]
    if cart["status"] == "empty":
        return f"**{app}购物车是空的**，没有可以体检的商品。"
    lines = [f"**{app}购物车**" + (f"（共 {cart['total']} 件）" if cart.get("total") else "") + f"　看了前 {len(cart['items'])} 件：", ""]
    for r in cart["items"]:
        p, how = final_price(r)
        name = r.get("detail_title") or r["title"]
        lines.append(f"{r['n']}. {name}" + (f"（{r['spec']}）" if r["spec"] else "") + f"　**¥{hz.money(p)}**　{how}"
                     + (f" · {r['shop']}" if r["shop"] else ""))
        promos = hz.clean_promos((r.get("detail") or {}).get("promos"), 3)
        if promos:
            lines.append(f"   优惠：{' · '.join(promos)}")
        if (r.get("detail") or {}).get("error"):
            lines.append(f"   （没看成详情页：{r['detail']['error']}，按购物车价算）")
    if cart.get("invalid"):
        lines.append(f"\n另有 {cart['invalid']} 件失效 / 缺货，没看。")
    return "\n".join(lines)


def cmd_read(a):
    if a.app not in CART_APPS:
        print(json.dumps({"ok": False, "error": f"购物车体检只支持{'、'.join(CART_APPS)}"}, ensure_ascii=False))
        return 2
    r = CartRunner(a.app)
    code, cart = 0, {"app": a.app, "platform": zd.APPS[a.app]["platform"], "time": time.strftime("%Y-%m-%d %H:%M"),
                     "source": "gouwuche.py", "status": "skipped", "items": []}
    try:
        jx.phone_lock()
        zd.ensure_connected(r)
        cart.update(r.run(a.items, a.pick))
    except zd.SafetyStop as e:
        code = 3
        cart.update(status="safety_stop", reason=str(e))
    except zd.Skip as e:
        cart.update(status="ok" if r.out else "skipped", reason=str(e), items=r.out)
    except jx.Fail as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 1
    finally:
        if code != 3:
            try:
                jx.cmd_home(NS(wait=1.0))
            except jx.Fail:
                pass
    cart["steps"], cart["screenshots"] = r.steps, r.shots
    os.makedirs(a.out_dir, exist_ok=True)
    out = os.path.join(a.out_dir, "cart-%s-%06d.json" % (time.strftime("%Y%m%d-%H%M%S"), int(time.time() * 1e6) % 1000000))
    with open(out, "x", encoding="utf-8") as f:
        json.dump(cart, f, ensure_ascii=False, indent=1)
    if code == 3:
        report = f"### ⛔ 安全停止：{cart['reason']}"
    elif cart["status"] == "skipped" and not cart["items"]:
        report = f"**{a.app}购物车没读成**：{cart.get('reason', '')}"
    else:
        report = render_read(cart)
    print(json.dumps({"report": report, "ok": code == 0, "safety_stop": code == 3, "json": out,
                      "items": [{"n": x["n"], "title": x.get("detail_title") or x["title"], "spec": x["spec"],
                                 "price": final_price(x)[0], "screenshot": x.get("screenshot", "")} for x in cart["items"]],
                      "steps": r.steps}, ensure_ascii=False, indent=1))
    return code


# ---------- report ----------

def check_item(rec, files, platform):
    """一件商品：各平台实时价里最低的那条 vs 购物车价。"""
    mirrors, refs, keyword, err, kb = [], {}, "", "", None
    for f in files:
        with open(f, encoding="utf-8") as fh:
            d = json.load(fh)
        if isinstance(d, list):
            for p, r in hz.load_refs(f).items():
                if p not in refs or r["low"] < refs[p]["low"]:
                    refs[p] = r
        elif isinstance(d, dict) and "items" not in d and any(k in d for k in ("pros", "cons", "points", "rating")):
            kb = xp.load_kb(f)  # 口碑文件 kb-*.json
        else:
            mirrors.append(f)
    best, spec, skipped, kw = {}, "", {}, ""
    if mirrors:
        try:
            data = hz.load_many(mirrors)
            best, _, skipped, _ = hz.summarize(data)
            kw, spec = data["keyword"], data["spec"]
            keyword = f"{kw} {spec}".strip()
        except SystemExit as e:
            err = str(e)
    mine, how = final_price(rec)
    low = hz.pick(best.values())  # 有自营 / 旗舰店就和它们比，一家都没有才和别的店比
    save = round(mine - low["price"], 2) if low and low["price"] < mine - 0.005 else 0
    # 没有规格（容量、重量）时，「同款」只是名字对得上：可能是单包对三包、小瓶对大瓶，便宜的不算进「能省」，只提醒去看一眼
    maybe = bool(save) and not spec
    ref_low = min((r for p, r in refs.items() if p != "闲鱼"), key=lambda r: r["low"], default=None)
    return {"n": rec["n"], "name": rec.get("detail_title") or rec["title"], "spec": rec["spec"], "keyword": keyword,
            "mine": mine, "how": how, "shop": rec["shop"], "platform": platform, "low": low, "save": save,
            "ref": ref_low, "searched": sorted(best), "error": err, "maybe": maybe,
            "promos": hz.clean_promos((rec.get("detail") or {}).get("promos"), 4),
            "plats": sorted(((p, i["price"], i["shop"]) for p, i in best.items()), key=lambda x: x[1]),
            "best": best, "refs": refs, "kb": kb, "skipped": skipped, "kw": kw or rec["title"], "data_spec": spec,
            "cart_item": {"platform": platform, "title": rec.get("detail_title") or rec["title"], "price": mine,
                          "original": rec.get("original") or 0, "label": "" if how == "购物车价" else how,
                          "shop": rec["shop"], "note": "", "source": "购物车", "url": "", "couponUrl": "",
                          "detail": rec.get("detail") or {}, "list_price": rec["price"] if abs(rec["price"] - mine) > 0.005 else 0,
                          "calc": hz.settle_price(rec["price"], rec.get("label") or "", rec.get("detail") or {})[2] if rec["price"] else None}}


def render_report(cart, rows):
    app = cart["app"]
    saved = sum(r["save"] for r in rows if not r["maybe"])
    cheaper = [r for r in rows if r["save"] > 0 and not r["maybe"]]
    maybe = [r for r in rows if r["maybe"]]
    lines = [f"### 🛒 {app}购物车体检 · {len(rows)} 件", ""]
    compared = [r for r in rows if r["low"]]
    if cheaper:
        lines += [f"**能省 ¥{hz.money(saved)}**　{len(cheaper)} 件在别处更便宜"
                  + (f"；另有 {len(maybe)} 件可能更便宜，规格没法确认" if maybe else ""), ""]
    elif maybe:
        lines += [f"**{len(maybe)} 件可能在别处更便宜**　规格没法确认，下单前看一眼是不是同一件（几包装、多大容量）", ""]
    elif compared and len(compared) == len(rows):
        lines += ["**购物车里这几件都不用换**　有自营或旗舰店时只和它们比" if any(map(third_cheaper, rows))
                  else "**购物车里这几件已经是这次查到的最低价**", ""]
    elif compared:
        lines += [f"**查到同款的 {len(compared)} 件，购物车里已经是最低价**　另外 {len(rows) - len(compared)} 件别处没查到同款实时价", ""]
    else:
        lines += ["**这次没在其他地方查到同款的实时价**", ""]
    lines += ["| 商品 | 购物车里 | 查到的实时价（只比官方旗舰店/自营/直营） | 能省 | 参考价 |", "|:--|--:|:--|--:|--:|"]
    for r in rows:
        low = r["low"]
        where = f"¥{hz.money(low['price'])} {low['platform']}" + (f" · {hz.cell(low['shop'])}" if low["shop"] else "") if low else "—"
        ref = hz.ref_cell(r["ref"]) + f"（{r['ref']['platform']}）" if r["ref"] else "—"
        name = hz.cell(r["keyword"] or r["name"][:18])
        lines.append(f"| {r['n']}. {name} | ¥{hz.money(r['mine'])} | {where} | "
                     + (f"可能 ¥{hz.money(r['save'])}" if r["maybe"] else f"**¥{hz.money(r['save'])}**" if r["save"]
                        else "不用换" if low and third_cheaper(r) else "已最低" if low else "—") + f" | {ref} |")
    tips = ["购物车价是详情页读到的到手价", "实时价来自你手机 App 搜索结果前五屏",
            "同款按型号和规格判断，食品、酒、首饰这类没有型号的商品，下单前看一眼是不是同一件"]
    if any(r["ref"] and r["ref"]["low"] < min([r["mine"]] + ([r["low"]["price"]] if r["low"] else [])) for r in rows):
        tips.append("有参考价比实时价低，下单前请到该平台核实")
    lines += ["", "> " + " · ".join(tips + ["下单以 App 为准"])]
    return "\n".join(lines)


def third_cheaper(r):
    """购物车不用换（别处的自营 / 旗舰店不比它便宜），但第三方店有更便宜的。"""
    return not r["save"] and any(i["price"] < r["mine"] - 0.005 for i in r["best"].values())


def cart_verdict(r):
    """这件买贵了没有：只看购物车价和这次查到的同款实时价、参考价，按固定规则下结论。返回 (等级, 标题, 副标题)。"""
    low, ref = r["low"], r["ref"]
    if r["save"] and not r["maybe"]:
        return "wait", "买贵了", f"去{low['platform']}买能省 ¥{hz.money(r['save'])}"
    if r["maybe"]:
        return "ok", "可能买贵了", f"别处有便宜 ¥{hz.money(r['save'])} 的，但规格没法确认，看一眼是不是同一件"
    if low:
        if third_cheaper(r):
            return "ok", "价格合理", "比查到的自营/旗舰店都便宜；第三方店铺有更低的，没算进能省多少"
        if ref and ref["low"] < r["mine"] * 0.97:
            return "ok", "价格合理", f"实时价里购物车最低，但{ref['platform']}的网上参考价更低，下单前可以去核实"
        return "good", "买得值", "购物车这件就是这次查到的最低价"
    if ref:
        if ref["low"] < r["mine"] * 0.97:
            return "ok", "价格合理", f"别处没读到同款实时价；{ref['platform']}的网上参考价 ¥{hz.money(ref['low'])} 更低，要去核实"
        return "good", "买得值", "别处没读到同款实时价，购物车价不高于网上参考价"
    return "unknown", "数据还不够", "别处没读到同款实时价，也没查到参考价"


def cart_judge(r, before):
    """价格鉴定：表盘（买得值 / 合理 / 买贵了）+ 一张对比表（购物车价比各平台、参考价、查到过的最低、划线价）。"""
    e = html.escape
    level, title, sub = cart_verdict(r)
    price, rows = r["mine"], []
    for p, i in sorted(r["best"].items(), key=lambda kv: kv[1]["price"]):
        rows.append((f"{p}实时价", i["shop"], i["price"]))
    if before:
        h = min(before, key=lambda x: x["price"])
        rows.append(("你查到过的最低", h["time"][5:].replace("-", "."), h["price"]))
    if r["ref"]:
        rows.append((f"{r['ref']['platform']}参考价", "不是实时价，要去核实", r["ref"]["low"]))
    if r["cart_item"]["original"] > price:
        rows.append(("购物车划线价", "", r["cart_item"]["original"]))
    cmp = ""
    if rows:
        body = "".join(f'<p><span>{e(t)}{f"<small>{e(x)}</small>" if x else ""}</span><b>¥{e(hz.money(v))}</b>'
                       f'<span class="d {xp.delta(price, v)[0]}">{e(xp.delta(price, v)[1])}</span></p>' for t, x, v in rows[:5])
        cmp = f'''
        <div class="cmp num">
          <p class="cmp-h"><span>购物车 ¥{e(hz.money(price))} 比</span><span></span><span>差多少</span></p>{body}
        </div>'''
    head = f'''
        <p class="vd">{e(title)}</p>
        <p class="vs">{e(sub)}</p>{cmp}'''
    if level == "unknown":
        return head
    deg = {"good": -60, "ok": 6, "wait": 60}[level]
    return xp.GAUGE.format(deg=deg, on1=" on" if level == "good" else "", on2=" on" if level == "ok" else "",
                           on3=" on" if level == "wait" else "", l1="买得值", l2="合理", l3="买贵了") + head


def cart_section(r, before):
    """一件商品一段：标题 → 平台明细（第一行是购物车，点开看怎么算的）→ 价格鉴定 → 买家口碑。"""
    e = html.escape
    q = urllib.parse.quote(f"{r['kw']} {r['data_spec']}".strip())
    mine = r["cart_item"]
    low = r["low"] if r["save"] else mine  # 和 row() 一样按自营 / 旗舰店优先：别处的首选比购物车便宜才标它
    # 标出来的那行不是最便宜的（第三方店更便宜）：角标写清楚为什么选它
    tag = "" if not any(i["price"] < low["price"] - 0.005 for i in r["best"].values()) else "首选" if low is mine else "自营/旗舰"
    entries = [{"platform": r["platform"], "label": "你的购物车", "kind": "best" if low is mine else "live", "item": mine,
                "ref": None, "tag": tag}]
    for p, i in sorted(r["best"].items(), key=lambda kv: kv[1]["price"]):
        entries.append({"platform": p, "kind": "best" if i is low else "live", "item": i, "ref": r["refs"].get(p), "tag": tag})
    for p, ref in sorted(r["refs"].items(), key=lambda kv: kv[1]["low"]):
        if p not in r["best"]:
            entries.append({"platform": p, "kind": "ref", "ref": ref})
    for p, why in r["skipped"].items():
        if p not in r["best"] and p not in r["refs"]:
            entries.append({"platform": p, "kind": "miss", "reason": why})
    rows = "".join(xp.item_html(x, low, q) for x in entries if x["platform"] in hz.PLATFORMS or x.get("label"))
    level, title, _ = cart_verdict(r)
    spec = f"<span>{e(r['spec'])}</span>" if r["spec"] else ""
    return f'''
        <hr class="cut">
        <p class="h">第 {r["n"]} 件 · {e(title)}<small>按「{e(r["keyword"] or r["kw"])}」比</small></p>
        <p class="prod2">{e(r["name"])}{spec}</p>
        <ul class="items">{rows}
        </ul>
        <p class="h sub">价格鉴定<small>按固定规则比出来的</small></p>{cart_judge(r, before)}
        <p class="h sub">买家口碑<small>来自公开网页</small></p>
        {xp.kb_html(r["kb"])}'''


CART_CSS = '''
  .prod2 { margin: -2px 0 6px; font-size: 18px; line-height: 1.35; font-weight: 800; }
  .prod2 span { display: block; margin-top: 2px; font-size: 12.5px; font-weight: 400; color: var(--muted); }
  .h.sub { margin-top: 18px; }
  .total .big.save { color: var(--ink); }
</style>'''


def cart_page():
    """单品比价小票（xiaopiao.PAGE）的样子和脚本原样拿来，只把中间换成购物车的内容。"""
    page = xp.PAGE.replace("</style>", CART_CSS, 1).replace("<b>比价小票</b>", "<b>购物车体检单</b>", 1)
    page = page.replace("<title>比价小票 · ${title}</title>", "<title>购物车体检单</title>", 1)
    start = page.index('\n        <hr class="cut">\n        <p class="h">查了')
    end = page.index('\n        <div class="tear" id="tear">')
    return page[:start] + "${body}" + page[end:]


def render_ticket(cart, rows, name="", now=None):
    e = html.escape
    now = now or datetime.datetime.now()
    day, clock, no = xp.stamp(cart.get("time"))
    cart_total = sum(r["mine"] for r in rows)
    sure = [r for r in rows if r["save"] and not r["maybe"]]
    saved = round(sum(r["save"] for r in sure), 2)
    best_total = cart_total - saved
    stats = {r["n"]: xp.load_stats(r["kw"], r["data_spec"], None) for r in rows}

    # 最上面：都按查到的最低价买，一共多少；刮开贴纸是比购物车省多少
    switch = [f"{r['n']}. {xp.search_title(r['low']) or r['name']} → {r['low']['platform']}"
              + (f" · {r['low']['shop']}" if r["low"]["shop"] else "") + f" ¥{hz.money(r['low']['price'])}" for r in sure]
    arrow = '<svg viewBox="0 0 24 24"><path d="M5 12h14M13 6l6 6-6 6"/></svg>'
    sticker = (f'<div class="sticker" id="sticker"><div><small>比购物车省</small><b class="num">¥{e(hz.money(saved))}</b></div>'
               f'<canvas aria-label="刮开看省了多少"></canvas></div><span class="scratch-tip">刮开看看</span>') if saved > 0 else ""
    go = (f'<button type="button" class="go" data-copy="{e(chr(10).join(switch))}" data-done="已复制，到手机 App 里搜这几件">'
          f'复制要换平台买的 {len(sure)} 件{arrow}</button>') if sure else ""
    notes = [f"第 {r['n']} 件别处便宜 ¥{hz.money(r['save'])}，但规格没法确认（几包装、多大容量），没算进去。" for r in rows if r["maybe"]]
    for r in sure:
        low = r["low"]
        if not low["shop"]:
            notes.append(f"第 {r['n']} 件的最低价在{low['platform']}，列表上看不到店铺名，下单前看一眼是不是官方店。")
        elif not hz.official(low):
            notes.append(f"第 {r['n']} 件的最低价来自第三方店铺（{low['shop']}），下单前看一眼是不是官方店。")
        cond = hz.conditions(low)
        if cond:
            notes.append(f"第 {r['n']} 件的最低价备注：{cond}。")
    warn = f'<div class="warn">{"".join(f"<p>{e(n)}</p>" for n in notes)}</div>' if notes else ""
    where = (f"购物车里 {len(rows)} 件合计 ¥{hz.money(cart_total)}" + (f"，其中 {len(sure)} 件在别处更便宜" if sure else "，都已经是查到的最低价"))
    total = f'''
        <hr class="cut">
        <div class="total">
          <p class="lab">都按查到的最低价买</p>
          <p class="big num"><i>¥</i><span data-count="{best_total}">{e(hz.money(round(best_total, 2)))}</span></p>
          <p class="where">{e(where)}</p>{sticker}
          {go}{warn}
        </div>'''
    body = total + "".join(cart_section(r, (stats.get(r["n"]) or {}).get("history") or []) for r in rows)

    fest = xp.next_festival(now)
    count = ""
    if fest:
        left, fname, fday = fest
        count = (f'''
        <div class="count">
          <small>距{e(fname)} 零点</small>
          <b class="num" data-deadline="{fday:%Y-%m-%d}T00:00:00+08:00"><span data-u="d">{left}</span><em>天</em><span data-u="h">00</span>:<span data-u="m">00</span>:<span data-u="s">00</span></b>
        </div>''' if left else f'\n        <div class="count"><small>大促</small><b>今天就是{e(fname)}</b></div>')
    body += count

    # 撕下来时复制的文字、背景跑马灯（只放这单的真实数字）
    text = [f"购物车体检单 No.{no}", f"{cart['app']}购物车 {len(rows)} 件"]
    for r in rows:
        level, title, _ = cart_verdict(r)
        where_low = f" · 最低 {r['low']['platform']} ¥{hz.money(r['low']['price'])}" if r["low"] else ""
        text.append(f"{r['n']}. {r['name'][:30]}　购物车 ¥{hz.money(r['mine'])}{where_low}　{title}")
    text += ["—", f"比购物车能省 ¥{hz.money(saved)}" if saved else "购物车已经是最低价", "下单以 App 为准"]
    r1 = [f"购物车 {len(rows)} 件", f"能省 ¥{hz.money(saved)}" if saved else "都是最低价"] + \
         [f"{r['kw']} {cart_verdict(r)[1]}" for r in rows]
    r2 = ([f"离{fest[1]} 不到 {fest[0]} 天" if fest[0] else f"今天就是{fest[1]}"] if fest else []) + ["下单以 App 为准", "只看不领"]

    def ribbon(words):
        words = words * (2 if len(words) > 3 else 3)
        return "".join(f"{e(w)}<i>/</i>" for w in words)

    cd = xp.caidan()
    try:
        acc_total = cd.total_saved(cd.load_events()) if cd else 0
    except Exception:
        acc_total = 0
    fine = ["购物车价是点进详情页读到的到手价", "实时价是你手机 App 里看到的", "有自营或旗舰店的先用它们的价", "同款按型号和规格判断"]
    if any(r["refs"] for r in rows):
        fine.append("参考价来自公开网页")
    fine.append("下单以 App 为准")
    pop = f"比购物车省 ¥{hz.money(saved)}" if saved else ""
    meta = json.dumps({"text": "\n".join(text), "pop": pop}, ensure_ascii=False).replace("</", "<\\/")
    who = f" · {e(name)}" if name else ""
    return string.Template(cart_page()).safe_substitute(
        title="购物车体检", r1=ribbon(r1), r2=ribbon(r2), head=f"No.{e(no)} · {e(day[5:])} {e(clock)}{who}",
        keyword=f"{e(cart['app'])}购物车体检", spec=f"<span>{len(rows)} 件</span>", body=body,
        barcode=xp.barcode(no + "cart", 220, 40), no=e(no),
        acc=f'<p class="acc num">累计帮你省 ¥{e(hz.money(round(acc_total, 2)))}</p>' if acc_total > 0 else "",
        fine=e(" · ".join(fine)), meta=meta)


def cmd_report(a):
    with open(a.cart, encoding="utf-8") as f:
        cart = json.load(f)
    groups = {}
    for g in a.item or []:
        try:
            groups[int(g[0])] = g[1:]
        except ValueError:
            print(json.dumps({"ok": False, "error": f"--item 后面先写商品序号（1、2、3），再写文件：{g}"}, ensure_ascii=False))
            return 2
    rows = [check_item(rec, groups.get(rec["n"], []), cart["platform"]) for rec in cart.get("items") or []]
    if not rows:
        print(json.dumps({"ok": False, "error": "购物车文件里没有商品"}, ensure_ascii=False))
        return 2
    report = render_report(cart, rows)
    os.makedirs(a.out_dir, exist_ok=True)
    path = os.path.join(a.out_dir, "购物车体检单-%s.html" % time.strftime("%Y%m%d-%H%M%S"))
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_ticket(cart, rows, getattr(a, "name", "")))
    print(json.dumps({"ok": True, "report": report, "html": path,
                      "saved": round(sum(r["save"] for r in rows if not r["maybe"]), 2),
                      "errors": [f"第 {r['n']} 件：{r['error']}" for r in rows if r["error"]]}, ensure_ascii=False, indent=1))
    return 0


def main():
    ap = argparse.ArgumentParser(description="蜗牛最爽省钱 Skill · 购物车体检")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("read", help="操作手机：读购物车里的商品、点进详情页看到手价")
    r.add_argument("--app", default="淘宝", help="淘宝 或 京东")
    r.add_argument("--items", type=int, default=3, help="看前几件（默认 3）")
    r.add_argument("--pick", default="", help="只看标题里有这几个字的商品")
    r.add_argument("--out-dir", default=".")
    p = sub.add_parser("report", help="不碰手机：汇总每件商品的比价结果，出体检单")
    p.add_argument("cart", help="read 写出的 cart-*.json")
    p.add_argument("--item", nargs="+", action="append", metavar="N 文件",
                   help="第 N 件商品的结果文件：zidong.py 的 mirror-*.json、参考价 ref-*.json、口碑 kb-*.json，可以给多个；每件商品写一次 --item")
    p.add_argument("--name", default="", help="用户的称呼，印在小票抬头；不知道就不写")
    p.add_argument("--out-dir", default=".")
    a = ap.parse_args()
    return cmd_read(a) if a.cmd == "read" else cmd_report(a)


if __name__ == "__main__":
    sys.exit(main())
