#!/usr/bin/env python3
"""蜗牛最爽省钱 Skill · 手机 App 一键查价：在 iPhone 镜像里依次打开各平台 App → 回首页 → 搜索 → 读搜索结果，写出比价 JSON。

所有点击位置都来自屏幕文字识别（macOS Vision）或固定的系统位置（左上角返回键），不靠模型看图估坐标；
价格数字来自文字识别，不靠模型读图。模型只需要拿输出里的截图复核一遍。

读完五屏后，再点进自营 / 旗舰店里最便宜的那件和全场最低价那件同款（如果不是同一件）的商品详情页，
读页面上的券、立减、满减、补贴和到手价，再点开「优惠明细」面板看价格是怎么算出来的，看完返回。只看不领。

安全：
  - 只点：App 图标、「跳过/关闭」类弹窗按钮、底部最左边的首页标签、左上角返回键、搜索框、「搜索」按钮；
    看详情时还会点商品标题、价格那一行（打开优惠明细）、领券浮层下面的 ×。
  - 从不点「领取 / 领券 / 开心收下 / 立即使用 / 一键开通 / 加入购物车 / 立即购买 / 领券购买」。
  - 每次点击后都会重新截图识别；一旦页面出现「结算/合计/立即购买/加入购物车」等字样且不是本来就在的页面，
    或者购物车合计金额发生变化，立即停止整个流程并报告，不做任何恢复动作。
  - 从不点勾选框、购买/加购/领券类按钮；商品卡片只点标题文字（进详情页看优惠）。

输入：英文数字直接打；有中文时切到手机的拼音输入法打拼音、按空格选词，再用文字识别核对输入框里的中文；
中文没打对（候选词不对、切不了输入法）就清空，改成直接搜拼音（京东、淘宝都能把拼音联想成中文）。

用法：
  python3 zidong.py "iPhone 18 Pro" --spec 256GB --platforms 京东 淘宝 --out-dir <工作目录>
  python3 zidong.py "三只松鼠 每日坚果" --platforms 京东 淘宝 --out-dir <工作目录>
输出（stdout，JSON）：每个平台的状态、候选商品、复核用截图路径，以及写好的 mirror-*.json 路径。
退出码：0 跑完（可能有平台跳过）；2 关键词不合法（有标点符号）；3 安全停止（需要告诉用户）；1 其他错误（如镜像没连上）。
"""
import argparse
import difflib
import importlib.util
import json
import os
import re
import sys
import time
from types import SimpleNamespace as NS

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


jx = _load("jingxiang")
hz = _load("huizong")

# 主屏幕上 App 图标下面的名字（中文系统 / 英文系统）
# queries：在 Spotlight 里搜的拼音（还没选词时 Spotlight 就会按拼音出结果，两种输入法都一样）
APPS = {
    "京东": {"labels": ["京东", "JD", "JD.com"], "platform": "京东", "queries": ["jd", "jingdong"]},  # 英文系统里 App 叫「JD」
    "淘宝": {"labels": ["淘宝", "Taobao"], "platform": "淘宝/天猫", "queries": ["taobao"]},
    "拼多多": {"labels": ["拼多多", "Pinduoduo"], "platform": "拼多多", "queries": ["pinduoduo"]},
    "唯品会": {"labels": ["唯品会", "Vipshop"], "platform": "唯品会", "queries": ["weipinhui"]},
    "闲鱼": {"labels": ["闲鱼", "Xianyu", "Goofish"], "platform": "闲鱼", "queries": ["xianyu"]},
}
# Spotlight 的分区标题。只点「Siri 建议」「最佳结果」「App」分区里的 App；
# 「Suggestions/建议」是 Safari 网页搜索建议，「Settings/设置」是设置里的同名条目，点了会打开 Safari 或设置
SPOT_GRID = ("Siri Suggestions", "Siri 建议", "Siri建议", "Siri 推荐", "Siri推荐", "Siri Suggested", "建议的 App", "建议的App", "App 建议", "App建议")
SPOT_LIST = ("Apps", "App", "应用", "应用程序", "App 程序")
# 2.15：中文系统的「最佳结果」实际写法不止一种（「最佳搜索结果」「热门点击」…），同事手机上 jd / jingdong 搜到了京东却不点，就是这个分区标题没认出来
SPOT_OK = SPOT_GRID + ("Top Hit", "Top Hits", "最佳结果", "最佳搜索结果", "最佳搜寻结果", "热门结果", "热门搜索结果", "热门点击", "最佳匹配",
                       "Apps", "App", "应用", "应用程序", "App 程序")
SPOT_OK_RE = re.compile(r"^(Siri\s*(建议|推荐|Suggest\w*)|Top\s*Hits?|最佳\S{0,4}|热门\S{0,4}结果|热门点击|Apps?|应用(程序)?|建议的\s*App|App\s*建议)$", re.I)
# 主屏幕最后一页往右翻就是「App 资源库」：顶上的搜索框里写着这几个字
LIBRARY = ("App Library", "App 资源库", "App资源库", "App 資料庫", "App資料庫")
SPOT_HEADERS = SPOT_OK + ("Suggestions", "建议", "搜索建议", "Settings", "设置", "Photos", "照片", "Search in Apps",
                          "在 App 中搜索", "Websites", "网站", "Messages", "信息", "Notes", "备忘录", "Contacts", "通讯录",
                          "Maps", "地图", "Mail", "邮件", "Files", "文件", "Calendar", "日历", "Shortcuts", "快捷指令")
SPOT_PAGE = ("Siri Suggestions", "Siri 建议", "Siri建议", "Show More Results", "显示更多结果", "Search in App", "在 App 中搜索",
             "最佳搜索结果", "搜索 App Store", "Search App Store", "Top Hit")
SHOT_SHEET = ("识别截图", "分享截图", "分享截屏", "截图识别", "截屏分享", "把截屏分享至", "截屏分享至")  # 截屏后 App 弹的浮层
EXIT_TAPS = 4                  # 退出一个详情页最多点几下
BACK = (8.3, 14.1)             # 左上角返回键（各 App 基本一致；11-05 京东实测箭头中心在 8.2%, 14.0%，原来的 13.4% 点不中）
ARROWS = set("←<〈く‹个＜")       # 返回箭头常被文字识别认成的符号
TAB_WORDS = {"首页", "购物车", "我的", "消息", "我的淘宝", "视频", "逛", "分类", "个人中心", "聊天", "推荐", "闲鱼", "卖闲置",
             "百页", "首頁", "自页", "首贞", "新品", "购物年", "我旳", "逛逛", "多多视频", "秒送"}  # 前几个是识别常认错的「首页」「购物车」「我的」
DETAIL_BAR = re.compile(r"去购买|百补频道|免拼购买|领券购买|券后购买|到手价购买|立即抢购|领券抢购|马上抢|加购物车")  # 详情页底部按钮的其他写法
BUY_BAR = ("立即购买", "加入购物车", "单独购买", "发起拼单")  # 详情页底部的购买按钮（后两个是拼多多的）
DANGER = ("结算", "合计", "去支付", "提交订单", "确认订单") + BUY_BAR
POPUP_BUTTONS = ("跳过", "关闭", "以后再说", "我知道了", "暂不", "取消",
                 # iOS 的「“JD”想从“…的 MacBook”粘贴」等权限弹窗：一律选不允许
                 "Don’tAllowPaste", "Don'tAllowPaste", "Don’tAllow", "Don'tAllow", "不允许粘贴", "不允许")
REMOVE_DIALOG = re.compile(r"^Remove\b|移除|删除 App|Delete App")  # 主屏幕上「移除 App」的弹窗
SPLASH = re.compile(r"^(跳过|广告|Skip|skip)")  # 开屏广告上的字
SYSTEM_DENY = ("Don’tAllowPaste", "Don'tAllowPaste", "Don’tAllow", "Don'tAllow", "不允许粘贴", "不允许")  # 系统权限弹窗：一律不允许
POPUP_HINTS = ("无门槛", "立即使用", "去使用", "红包", "优惠券", "恭喜", "领取成功")  # 结果页上盖着的弹窗/浮层
LIST_HEADER = ("综合", "销量", "筛选")  # 只认筛选栏；「官方」「新品」也会出现在商品标题里
# 标题区里一定不是标题的行（销量、榜单、用户评价）
DROP_LINES = ("人已买", "人加购", "浏览", "好评", "榜", "“", "\"", "看过", "付款", "已售", "人想要", "可变光圈", "分钟充",
              "预估", "到手价", "叠加", "立减", "补贴低价", "售后无忧", "官方精选", "甄选")  # 最后几个是商品图上印的宣传字
# 参数格子里的短行（标题本身也可能含「4800万像素」，所以只删短行）
ATTR_SHORT = ("CPU", "像素", "分辨率", "充50%", "mAh", "Hz", "屏幕", "电池", "可变光圈", "无线充", "英寸", "尺寸", "特征", "芯片")
BADGES = r"^(自营|自|天猫|双11|双1|双川|秒送|品牌旗舰|官方|旗舰|[’'，,。.·•\s|丨])+"
LABEL_WORDS = ("到手", "券后", "补贴", "百亿", "国补", "折后", "超V", "拼单", "直播价", "政府", "新人", "入会", "会员", "PLUS", "88VIP")
# 店铺行：「店」在行尾或后面跟「-分店名」、含旗舰店/专营/专卖，或被截断后以箭头结尾（「Apple Store 官方…>」）。
# 标题里的「门店现货」「店铺推荐」不算。
# 天猫卡片上价格和标题之间的杂行（中间还隔着一大段图片高度的空白）
FILLER_RE = re.compile(r"刚刚购买|猫猫币|次日达|^加补|^政府补贴\d+%|人购买过|^包邮$|小时达|^当季新品$")
SHOP_RE = re.compile(r"店\s*$|店\s*[-－]|旗舰店|专营|专卖|[>＞〉›]\s*$")


def is_shop(t):
    return bool(SHOP_RE.search(t)) and not re.search(r"榜|第\s*\d+\s*名|更多|分钟达|小时达", t)
PDD_PRICE = re.compile(r"^\W*([^\d¥￥\s]{0,5}?)\s*[¥￥]\s*(\d[\d,]*(?:\.\d+)?)")  # 「券后¥5899」「秒杀价¥8799」「快要抢光¥9798」
# 「百亿补贴」红底白字常被认成「百仁补贴」「百江补贴」「百仁神贴」；后面常跟一个绿底的「新品首发」
PDD_BADGE = re.compile(r"^(百亿补贴|百\s*\S{0,2}\s*[贴帖]|新品首发|品牌|\S{0,2}直播中)\s*")
PDD_USED = re.compile(r"二手|翻新|美版|港版|日版|韩版|有锁|海外|官换|准新|99新|95新|保税|全球购")  # 拼多多上常见，写在标题第三行也算
PDD_TAG = re.compile(r"狂补|品牌实拍|官方实拍|^\W*[¥￥]\s*\D|立减|下单送|质保|收藏|老店|好评|限购|限时优惠|正品发票|分期|包邮|即将售罄|仅剩|非新赔|保证金|免息|已拼|总售|已抢|\d+人"
                     r"|\d{1,2}:\d{2}:\d{2}|\d(?:\.\d)?折")  # 后两个：双列网格标签行上的倒计时「00:59:55.3」、「限时8.5折」
PRICE_RE = re.compile(r"[¥￥]\s*(\d[\d,]*(?:\.\d+)?)")
PER_SPEC = re.compile(r"[/／]\s*(\d+(?:\.\d+)?\s*(?:kg|公斤|千克|斤|克|g|ml|毫升|l|升))(?![a-z])", re.I)  # 淘宝「¥230.58 /10kg」
# 影响「这个价格能不能拿到」的条件，写进 note
CONDITIONS = ("需当面签收激活", "签收激活", "需签收激活", "需现场激活", "需激活", "收货后激活", "联系激活", "以旧换新", "焕新补贴", "新人", "国补", "政府补贴",
              "限地区", "需领券", "入会价", "会员价", "PLUS价", "88VIP", "学生", "预售")


CJK = re.compile(r"[\u3400-\u9fff]+")


def segments(text):
    """「华为 Mate 70 256GB」→ [("cn", "华为"), ("en", "Mate70256GB")]：中文按空格分段（短一点选词更准），英文数字连写。"""
    segs = []
    for part in re.findall(CJK.pattern + r"|[A-Za-z0-9]+", text):
        kind = "en" if part.isascii() else "cn"
        if kind == "en" and segs and segs[-1][0] == "en":
            segs[-1] = ("en", segs[-1][1] + part)
        else:
            segs.append((kind, part))
    return segs


def field_norm(t):
    """搜索框文字统一写法：去空格、去左边的返回箭头/放大镜、右边的光标/清除按钮（常被认成 x），转小写。
    期望值也过一遍同样的处理，两边才对得上。"""
    t = re.sub(r"^[<←〈‹＜个Q🔍]+", "", t.replace(" ", ""))
    t = re.sub(r"^商品[、,，.·v∨>]*", "", t)  # 拼多多搜索框左边的分类「商品」
    return re.sub(r"[|×xX⊗ⓧ]+$", "", t).lower()


def label_norm(t):
    """App 名字统一写法：去掉空格、识别带进来的点和引号、粘在前面的角标数字，英文转小写。"""
    t = re.sub(r"[\s·•.。,，'’\"“”:：]", "", str(t or ""))
    t = re.sub(r"^\d{1,3}(?=[^\d])", "", t)
    return t.lower()


class Skip(Exception):
    pass


class SafetyStop(Exception):
    pass


class Runner:
    def __init__(self, keyword, spec, details=3):
        self.keyword, self.spec, self.details = keyword, spec, details
        self.segs = segments(f"{keyword} {spec}")
        self.has_cn = any(kind == "cn" for kind, _ in self.segs)
        self.typed = "".join(text for _, text in self.segs)          # 只有英文数字时，直接打这串
        self.pinyin = ""  # 「三只松鼠 每日坚果」→ sanzhisongshumeirijianguo；中英文之间留空格：huawei Mate70256GB
        for i, (kind, text) in enumerate(self.segs):
            if i and not (kind == "cn" and self.segs[i - 1][0] == "cn"):
                self.pinyin += " "
            self.pinyin += jx.pinyin(text).replace(" ", "") if kind == "cn" else text
        self.accepts = {field_norm(self.typed), field_norm(self.pinyin)}  # 搜索框里是这两种之一都算对
        self.input_used = ""
        self.image = None
        self.steps, self.shots = [], []
        self.lines = []

    # ---------- 基础动作 ----------
    def log(self, msg):
        self.steps.append(msg)

    def snap(self, keep=False, _depth=0):
        """截一张图、识别文字。
        2.15（11-05 实测）：Mac 截镜像窗口会被手机当成截屏，App 大约 1 秒后弹「分享截屏 / 识别截图 / 找相似」浮层，挡住下一次点击。
        - 浮层上有「不再自动弹出」的勾选框（京东）：勾上、关掉，以后就不弹了；
        - 没有（淘宝的「找相似」）：这个 App 以后每次截图都等浮层弹出来，再截一张确认（浮层开着时截图不会再触发），关掉它，
          屏幕干净了再去点。浮层开着时截的那张只用来确认，页面内容用第一张。"""
        t0 = time.time()
        r = jx.cmd_shot(NS(out=None))
        self.image = r["image"]
        self.lines = jx.ocr_image(r["image"])
        if _depth < 3 and self.shot_sheet():
            self.dismiss_shot_sheet()
            time.sleep(0.4)
            return self.snap(keep, _depth + 1)
        if getattr(self, "sheet_mode", False) and _depth < 3:
            time.sleep(max(0.0, 1.6 - (time.time() - t0)))
            lines, image = self.lines, self.image
            self.lines = jx.ocr_image(jx.cmd_shot(NS(out=None))["image"])
            if self.shot_sheet():
                self.dismiss_shot_sheet(quiet=True)
            self.lines, self.image = lines, image
        if keep:
            self.shots.append(r["image"])
        return self.lines

    def shot_sheet(self):
        """截屏后 App 弹的浮层：京东「分享截屏」、淘宝「识别截图」，或者淘宝的「找相似」（中间一排截图缩略图，右边「搜索」和 ×）。"""
        hit = next((o for o in self.lines if any(w in o["t"].replace(" ", "") for w in SHOT_SHEET) and "不再" not in o["t"]), None)
        if hit:
            return hit
        return next((o for o in self.lines if o["t"].replace(" ", "") == "搜索" and 30 < o["cy"] < 85 and 62 < o["cx"] < 90
                     and (any("截图价" in p["t"] for p in self.lines) or any(
                         p["t"].strip(" .•") in ("×", "X", "x", "✕") and p["cx"] > 86 and abs(p["cy"] - o["cy"]) < 2.5 for p in self.lines))), None)

    def dismiss_shot_sheet(self, quiet=False):
        """截屏后 App 弹的分享浮层（京东「分享截屏」、淘宝「识别截图」…）：有「不再自动弹出…」的勾选框就先勾上（只是这个浮层的显示设置），
        再点标题那一行右边的 ×；找不到 × 就点标题右边固定的位置。只点这两处。"""
        title = self.shot_sheet()
        box = next((o for o in self.lines if re.search(r"不再.{0,4}(弹|提示|显示)", o["t"])), None)
        if box and not getattr(self, "_sheet_off", False):
            self.tap(box["x"] + 1.2, box["cy"], f"「{box['t'].strip(' •·')[:14]}」的勾选框（以后不再弹截屏分享）", wait=0.6)
            self._sheet_off = True
        elif not box and not getattr(self, "sheet_mode", False) and not jx.stream_ok():
            self.sheet_mode = True  # 关不掉源头：以后每次截图都先等浮层弹出来、关掉
            self.log(f"{getattr(self, 'app_name', '')}每次截图后都会弹「{title['t'].strip()[:6]}」浮层：以后截图后等它弹出来再关掉")
        x = next((o for o in self.lines if o["t"].strip(" .•") in ("×", "X", "x", "✕") and o["cx"] > 75
                  and abs(o["cy"] - title["cy"]) < 2.5), None)
        if quiet:
            jx.cmd_tap(NS(x=x["cx"] if x else 91.5, y=x["cy"] if x else title["cy"], pct=True, zoom=False, wait=0.6))
        else:
            self.tap(x["cx"] if x else 91.5, x["cy"] if x else title["cy"], f"「{title['t'].strip()[:8]}」浮层右边的 ×", wait=1.0)

    def text_all(self):
        return " ".join(o["t"] for o in self.lines)

    def tap(self, x, y, why, wait=1.5):
        jx.cmd_tap(NS(x=x, y=y, pct=True, zoom=False, wait=wait))
        self.log(f"点击 {why}（{x:.1f}%, {y:.1f}%）")

    def cart_state(self):
        """购物车页的「合计」金额和「结算(N)」件数，用来发现误勾选。
        识别时「合计:」和「¥0」常被拆成两段，所以金额取同一行、在「合计」右边最近的价格。"""
        total = count = None
        for o in self.lines:
            if "合计" in o["t"]:
                m = PRICE_RE.search(o["t"])
                if m:
                    total = m.group(1)
                else:
                    right = [p for p in self.lines if abs(p["cy"] - o["cy"]) < 1.5 and p["x"] > o["x"] and PRICE_RE.search(p["t"])]
                    total = PRICE_RE.search(min(right, key=lambda p: p["x"])["t"]).group(1) if right else "?"
            m = re.search(r"结算\s*[(（]\s*(\d+)", o["t"])
            if m:
                count = m.group(1)
        return total, count

    def checked_tap(self, x, y, why, wait=1.5):
        """点击后检查：不能新出现危险页面，购物车合计不能变。"""
        before_danger = set(self.danger_words())
        before = self.cart_state()
        self.tap(x, y, why, wait)
        self.snap()
        found = self.danger_words()
        after_danger = set(found)
        after = self.cart_state()
        for name, b, a in (("合计金额", before[0], after[0]), ("结算件数", before[1], after[1])):
            if b is not None and a is not None and b != a:
                raise SafetyStop(f"点击「{why}」后购物车{name}从 {b} 变成了 {a}，可能误勾选了商品")
        new = after_danger - before_danger
        if new:
            if set(BUY_BAR) & new and not ({"结算", "合计", "去支付", "提交订单", "确认订单"} & new):
                # 误进商品详情页：只读页面，按返回键退出是安全的
                self.log(f"点击「{why}」后进入了商品详情页，退出来")
                for _ in range(3):
                    close = self.corner_close()
                    if close:
                        self.tap(*close, "右边的 ×（关掉详情页上的面板 / 直播窗）")
                    else:
                        self.tap(*BACK, "返回键（退出商品详情页）")
                    self.snap()
                    self.check_pay()
                    if not self.is_detail_page():
                        break
                else:
                    raise Skip("误进了商品详情页，退不出来")
            else:
                seen = "、".join(f"「{found[w]}」" for w in sorted(new))
                raise SafetyStop(f"点击「{why}」后页面上出现了按钮 {seen}（可能进了购物车、结算或支付页）")

    # ---------- 页面判断 ----------
    def danger_words(self):
        """页面上像按钮的短文字里出现的危险词 → {词: 那段文字}。
        只看 10 个字以内的短文字：商品图片、规则说明里的长句（如「按实际剩余金额结算退款」）不算。"""
        out = {}
        for o in self.lines:
            t = o["t"].replace(" ", "")
            if len(t) > 10:
                continue
            for w in DANGER:
                if w not in t:
                    continue
                # 详情页的「立即购买/加入购物车」、购物车的「结算/合计」都在屏幕底部那排；
                # 屏幕中间的（如品牌广告图上的「立即购买」）不算
                if w in BUY_BAR and o["cy"] < 85:
                    continue
                if w in ("结算", "合计") and o["cy"] < 60:
                    continue
                out.setdefault(w, o["t"])
        return out

    def is_detail_page(self):
        d = self.danger_words()
        return any(w in d for w in BUY_BAR) or any(
            o["cy"] >= 85 and DETAIL_BAR.search(o["t"].replace(" ", "")) and len(o["t"].replace(" ", "")) <= 10 for o in self.lines)

    def tab_labels(self):
        """App 底部标签栏：至少 3 个标签词、横向铺开；商品详情页底部那排（店铺/客服/购物车/立即购买）不算。"""
        tabs = [o for o in self.lines if o["cy"] > 88 and o["t"].replace(" ", "") in TAB_WORDS]
        # 京东、淘宝选中的「首页」是图标没有文字，所以至少 2 个标签词就算
        if len({o["t"].replace(" ", "") for o in tabs}) < 2 or self.is_detail_page():
            return []
        if max(o["cx"] for o in tabs) - min(o["cx"] for o in tabs) < 30:
            return []
        return tabs

    def top_search_button(self):
        for o in self.lines:
            if o["cy"] < 26 and o["x"] > 60 and o["t"].replace(" ", "") == "搜索":
                return o
        # 拼多多首页的搜索框右边没有「搜索」字：底部有标签栏、搜索框下面一行是「推荐」才算首页
        if getattr(self, "app_name", "") == "拼多多" and self.tab_labels() and any(o["t"].strip() == "推荐" and o["cy"] < 22 for o in self.lines):
            return next((o for o in self.lines if 9 < o["cy"] < 16 and 15 < o["cx"] < 85), {"cx": 40, "cy": 12.7})
        return None

    def top_bar(self):
        """页面顶上有导航栏（左上角的返回箭头，或者搜索框 / 搜索按钮）：说明没被往下翻过，返回键就在原位。"""
        return bool(self.top_search_button()) or any(o["cy"] < 17 and o["cx"] < 12 and o["t"].strip()[:1] in ARROWS for o in self.lines)

    def top_texts(self):
        """搜索框那一行的文字（去掉空格、两边的图标），只看「搜索」按钮那一行及以上（京东的输入框在按钮上面一行）；
        按钮下面的搜索建议不算。"""
        btn = self.top_search_button()
        return [field_norm(o["t"]) for o in self.lines if 10 <= o["cy"] < 24 and (btn is None or o["cy"] < btn["cy"] + 1.5)]

    def field_residue(self):
        """搜索框里除了刚打的英文，还剩着一大段中文（粘错后没删干净的旧字）。按钮、分类标签那几个字不算。"""
        return [t for t in self.top_texts() if CJK.search(t) and len(t) >= 5 and not re.search(r"搜索|智能模式|取消|拍照", t)]

    def typed_in_top(self, want=None):
        """搜索框里的文字要和关键词完全一致（忽略大小写、空格、末尾的光标或清除符号）。
        拼音例外：iOS 会给拼音加拼写检查的波浪下划线，识别常错一两个字母，所以拼音只要求没有汉字、相似度 ≥ 80%。"""
        wants = {field_norm(want)} if want is not None else self.accepts
        py = field_norm(self.pinyin) if self.has_cn else None
        loose = lambda x: re.sub(r"[il1|]+", "i", x)  # 「SKII」常被认成「SKI」，I l 1 | 也常互相认错
        for t in self.top_texts():
            if t in wants or loose(t) in {loose(w) for w in wants}:
                return True
            if py in wants and not CJK.search(t) and difflib.SequenceMatcher(None, t, py).ratio() >= 0.8:
                return True
        return False

    def is_results(self):
        if not any(w in self.text_all() for w in ("综合", "销量", "筛选")):
            return False
        # 结果页的搜索框有时把中文词拆成几个小块显示，所以也接受「几块拼起来等于关键词」
        joined = "".join(self.top_texts())
        if self.typed_in_top() or (self.has_cn and any(a in joined for a in self.accepts)):
            return True
        # 点搜索前已经核对过输入框；结果页上再认一遍常错一个字（拼多多「面霜」认成「面箱」）：只差一个字也算
        return any(len(t) == len(a) >= 4 and sum(x != y for x, y in zip(t, a)) <= 1
                   for t in self.top_texts() for a in self.accepts)

    def coupon_modal(self):
        """红包 / 领券弹窗：京东结果页「¥3 无门槛立减 … 立即使用」、天猫首页「新人红包 5元 … 立即领取」、
        淘宝详情页「获得平台加补券 … 开心收下」。认法：居中的大按钮，按钮上方有「红包 / 券 / 立减 / 福利…」。
        它们的关闭按钮都是按钮正下方的 ⊗ 图标（识别不到文字），返回按钮那一行。"""
        for o in self.lines:
            if o["t"].replace(" ", "") in SHEET_BUTTONS and 40 < o["cx"] < 60 and 40 < o["cy"] < 85 and any(
                    w in p["t"] for p in self.lines if 25 < p["cx"] < 75 and o["cy"] - 40 < p["cy"] < o["cy"] - 1
                    for w in MODAL_HINTS):
                return o
        return None

    def close_icon_popup(self):
        hint = next((o for o in self.lines if 38 < o["cx"] < 62 and 30 < o["cy"] < 78
                     and re.search(r"领|红包|券|[¥￥]\s*\d|恭喜|福利|开心收下|去使用|立即|补贴|金币|免单|抽奖|签到", o["t"])), None)
        if not hint or not self.image:
            return False
        y = jx.find_close_circle(self.image, 0.5, max(hint["cy"], 50) / 100, 0.95)
        if y is None:
            return False
        before = {o["t"] for o in self.lines}
        self.checked_tap(50, y, f"弹窗下面的关闭按钮 ⊗（弹窗里有「{hint['t'][:10]}」，不领）", wait=1.3)
        self.check_pay()
        return len(before & {o["t"] for o in self.lines}) < len(before) * 0.9  # 画面变了才算关掉

    def close_coupon_modal(self, btn, where):
        """点按钮正下方的 ⊗：各 App 离按钮的距离不一样（9%–12% 屏高），从近到远试，弹窗没了就停。
        从不点按钮本身（点了就领券了）；点空了也只会点在遮罩上。"""
        for dy in (9.3, 10.1, 11.2, 12.3):
            self.checked_tap(50, btn["cy"] + dy, f"{where}领券弹窗下面的关闭按钮 ⊗", wait=1.3)
            self.check_pay()
            if not self.coupon_modal():
                return
        raise Skip(f"{where}有红包 / 领券弹窗（「{btn['t'].strip()}」），关不掉，被挡住了")

    def check_screenshots(self):
        """App 一再弹「分享截屏 / 识别截图」：可能是 iPhone 的「辅助触控」小白点放在左上角、单击设成了截屏（点返回键就截一张屏），
        也可能是淘宝退出商品页时的挽留浮层（实测会带「识别截图」小窗）。只跳过这个平台，不中止整次查价。"""
        if any(w in o["t"].replace(" ", "") for o in self.lines for w in SHOT_SHEET):
            self.shot_popups = getattr(self, "shot_popups", 0) + 1  # snap() 已经关过三次还在，才走到这里
        if getattr(self, "shot_popups", 0) >= 1:  # 关不掉：交给上一层划掉 App 重开
            raise Skip("App 一直弹「截屏分享 / 识别截图」的小窗，退不回去。可能是淘宝退出商品页时的挽留浮层（会带这个小窗），"
                       "也可能是 iPhone 的「辅助触控」小白点放在左上角、单击被设成了截屏（在「设置 → 辅助功能 → 触控 → 辅助触控」里挪到右下角）。"
                       "可以先在手机上把这个 App 切回首页，再让我重新查价")

    def close_popups(self):
        for _ in range(3):
            # 手机截了屏，京东会弹「分享截屏」、淘宝会弹「识别截图」：点标题那一行右边的 ×
            share = next((o for o in self.lines if o["t"].replace(" ", "") in ("分享截屏", "分享截图", "截图识别") and o["cy"] > 40), None)
            self.check_screenshots()
            if share:
                x = next((o for o in self.lines if o["t"].strip(" .•") in ("×", "X", "x") and o["cx"] > 80
                          and abs(o["cy"] - share["cy"]) < 2), None)
                self.checked_tap(x["cx"] if x else 91.5, share["cy"], "截屏分享弹层右边的 ×", wait=1.2)
                continue
            for o in self.lines:
                # 只点屏幕中间的（贴在右下角的「我知道了」是新功能引导气泡，点了常会点到下面的商品）
                skip = o["t"].replace(" ", "").startswith("跳过")
                if skip and not self.tab_labels():
                    continue  # 开屏广告的「跳过」：不点，wait_splash 会等它结束
                if 20 < o["cy"] < 90 and 15 < o["cx"] < 85 and (o["t"].replace(" ", "") in POPUP_BUTTONS or skip):
                    self.checked_tap(o["cx"], o["cy"], f"弹窗按钮「{o['t']}」", wait=1.2)
                    break
            else:
                # 京东结果页有时会自己弹出「京言AI助手」半屏面板，盖住商品：点标题同一行最右边的 ×
                ai = next((o for o in self.lines if "京言AI助手" in o["t"].replace(" ", "") and 25 < o["cy"] < 90), None)
                if ai:
                    self.checked_tap(91.5, ai["cy"], "「京言AI助手」面板右上角的 ×", wait=1.2)
                    if any("京言AI助手" in o["t"].replace(" ", "") for o in self.lines):
                        raise Skip("搜索结果页上弹出了「京言AI助手」面板，关不掉，被挡住了")
                    continue
                use = self.coupon_modal()
                if use:
                    self.close_coupon_modal(use, "页面上的")
                    continue
                # 2.15：按钮文字认不出来的领券 / 红包弹窗（淘宝「淘金币 ¥188 领」只有一个「领」字）：
                # 屏幕中间有领券类的字，就按像素找弹窗正下方的灰色 ⊗（文字识别认不出这个图标），点它关掉。从不点「领」
                if not self.close_icon_popup():
                    return

    # ---------- 流程 ----------
    def go_springboard(self):
        for attempt in range(2):
            jx.cmd_home(NS(wait=1.2))
            # 从 App 里回主屏幕，回到的是上次离开时那一页（常常不是第一页），这时只看这一页、再往右翻，前面几页就漏了。
            # 在主屏幕上再按一次才回第一页（在第一页上再按没有反应；开着的文件夹也会被这一下关掉）
            jx.cmd_home(NS(wait=1.0))
            self.log("回主屏幕第一页")
            self.snap()
            self.deny_system_alert()
            self.leave_edit_mode()
            # 主屏幕上不会有 App 的底部标签栏、搜索按钮或商品详情页按钮；有的话说明 ⌘1 没生效
            if not (self.tab_labels() or self.top_search_button() or self.is_detail_page() or self.in_spotlight()):
                return
        raise Skip("按 ⌘1 后没回到 iPhone 主屏幕")

    def deny_system_alert(self):
        """App 留下的系统权限弹窗（如「“JD”想粘贴」）回到主屏幕也不消失，会挡住 Spotlight 和翻页：选不允许。"""
        for _ in range(3):
            btn = next((o for o in self.lines if o["t"].replace(" ", "") in SYSTEM_DENY), None)
            if not btn:
                return
            self.tap(btn["cx"], btn["cy"], f"系统弹窗的「{btn['t']}」", wait=1.0)
            self.snap()

    def leave_edit_mode(self):
        """主屏幕进了编辑状态（图标在抖，顶上有「完成」）或弹了「移除 App」：点「取消」、按 ⌘1 退出，绝不点移除 / 删除。"""
        for _ in range(2):
            if any(REMOVE_DIALOG.search(o["t"]) for o in self.lines if 25 < o["cy"] < 75):
                cancel = next((o for o in self.lines if o["t"].strip() in ("Cancel", "取消") and o["cy"] > 40), None)
                if cancel:
                    self.tap(cancel["cx"], cancel["cy"], f"「{cancel['t'].strip()}」（关掉移除 App 的弹窗）", wait=1.0)
                    self.snap()
            if any(o["t"].strip() in ("Done", "完成") and o["cy"] < 12 and o["cx"] > 70 for o in self.lines):
                jx.cmd_home(NS(wait=1.2))
                self.log("主屏幕在编辑状态（图标在抖），按 ⌘1 退出")
                self.snap()
                continue
            return

    def wait_splash(self):
        """App 刚打开时的开屏广告：整屏是广告图，点哪儿都可能点进广告（「跳过」也常在广告图上，广告一结束那个位置就是首页的按钮）。
        等它自己结束（最多约 10 秒），这期间一下都不点。"""
        for i in range(6):
            self.snap()
            if self.tab_labels() or self.top_search_button() or self.is_detail_page() or self.is_results():
                return
            if not any(SPLASH.match(o["t"].strip()) for o in self.lines) and len(self.lines) > 12:
                return  # 不像开屏（有很多字、没有「跳过 / 广告」）：交给后面的步骤
            if i == 0:
                self.log("App 还在开屏广告，等它自己结束（不点）")
            time.sleep(1.2)

    def home_label(self, cfg):
        """主屏幕上这个 App 图标下面的名字。识别常带进杂字（「京东 .」「·淘宝」、角标数字粘在前面），统一去掉再比；
        「京东金融」「淘宝特价版」这类别的 App 不算。最下面一排图标的名字在 85%–90% 屏高，以前只看到 85%，会漏。"""
        names = {label_norm(x) for x in cfg["labels"]}
        hits = [o for o in self.lines if label_norm(o["t"]) in names and 6 < o["cy"] < 90]
        return max(hits, key=lambda o: o["cy"]) if hits else None  # 图标里也可能印着名字，取最下面那个（图标下方的名字）

    def open_app(self, name):
        """先看主屏幕第一页；没有就用 Spotlight 搜（⌘3）；再一页页往右翻主屏幕；翻到最后是「App 资源库」，在它的搜索框里搜。
        App 收在文件夹里、放在 Dock 上（Dock 上的图标没有名字）、在 Spotlight 里被关掉了，最后那一步都能找到。"""
        cfg = APPS[name]
        self.go_springboard()
        label = self.home_label(cfg)
        self.spot_missing = False
        if not label and self.open_via_spotlight(name, cfg):
            return
        if self.spot_missing:
            jx.cmd_home(NS(wait=1.0))
            raise Skip(f"手机上没装「{name}」（Spotlight 里只有 App Store 的「获取」）")
        same = 0
        for page in range(10):
            if label:
                # 不用 checked_tap：App 打开时停在购物车页（有「结算」「合计」）是正常的，回首页那一步会处理
                self.tap(label["cx"], label["cy"] - 4.8, f"{name} 图标", wait=3.5)
                self.wait_splash()
                if self.landed_wrong(name):
                    break
                return
            if self.in_library():
                break
            before = {o["t"] for o in self.lines}
            jx.cmd_scroll(NS(direction="left", wait=1.0))
            self.log("翻到下一页主屏幕")
            self.snap()
            label = self.home_label(cfg)
            # 2.17：往右划了页面没变（最后一页、或者划不动）：两次都没变就别再划了
            same = same + 1 if {o["t"] for o in self.lines} == before else 0
            if same >= 2:
                self.log("主屏幕翻不动了")
                break
        if self.open_via_library(name, cfg):
            return
        jx.cmd_home(NS(wait=1.0))
        raise Skip(f"手机上没找到「{name}」：主屏幕、Spotlight、App 资源库里都没找到它。"
                   f"请确认手机装了{name}，并把它的图标放到 iPhone 主屏幕第一页（不要放进文件夹或 Dock）")

    def in_library(self):
        # 2.17：顶上的搜索框常连着左边的放大镜认成「Q App资源库」：包含就算
        return any(label_norm(x) in label_norm(o["t"]) for x in LIBRARY for o in self.lines if o["cy"] < 20)

    def landed_wrong(self, name):
        """点了以后打开的是不是别的东西（Safari 网页、设置、别的 App 的同名条目）：是就回主屏幕，返回 True。
        App 刚打开时页面五花八门，所以只认几种一眼就能看出来「不是购物 App」的页面。"""
        top = " ".join(o["t"] for o in self.lines if o["cy"] < 20)
        text = self.text_all()
        wrong = (re.search(r"搜索或输入网站名称|Search or enter website|百度一下|必应|Bing|Google", text)
                 or (re.search(r"^(设置|Settings)$", top.strip()) and any(w in text for w in ("通知", "蜂窝", "Notifications", "Cellular")))
                 or self.in_library() or any(w.replace(" ", "") in text.replace(" ", "") for w in SPOT_PAGE))
        if wrong:
            self.log(f"点了以后打开的不是{name}（像是网页、设置或还在资源库 / Spotlight），回主屏幕")
            self.go_springboard()
            return True
        return False

    def open_via_library(self, name, cfg):
        """主屏幕最后一页往右是「App 资源库」：点顶上的搜索框，粘贴（或打拼音）App 名字，列表里点名字完全对上的那一行。"""
        same = 0
        for _ in range(12):  # 还没到资源库：接着往右翻
            if self.in_library():
                break
            before = {o["t"] for o in self.lines}
            jx.cmd_scroll(NS(direction="left", wait=1.0))
            self.snap()
            same = same + 1 if {o["t"] for o in self.lines} == before else 0
            if same >= 2:  # 2.17：划了两次页面都没变：到不了资源库
                self.log("往右划不动，到不了 App 资源库")
                return False
        else:
            self.log("翻了很多页也没到 App 资源库")
            return False
        box = next((o for o in self.lines if o["cy"] < 20 and any(label_norm(x) in label_norm(o["t"]) for x in LIBRARY)), None)
        self.tap(50, box["cy"] if box else 12, "App 资源库顶上的搜索框", wait=1.5)
        names = {label_norm(x) for x in cfg["labels"]}
        for q in [name] + cfg["queries"]:
            jx.clear_field()
            if CJK.search(q):
                if not self.paste_text(q):
                    continue
            else:
                jx.cmd_type(NS(text=q, wait=1.8))
            self.log(f"在 App 资源库里搜 {q}")
            self.snap()
            # 2.17：还是资源库主页（分类「最近添加」「建议」都在）：搜索框没点开，再换词也没用
            if sum(w in self.text_all() for w in ("最近添加", "建议", "Suggestions", "Recently Added")) >= 2:
                self.log("App 资源库的搜索框没点开")
                break
            # 资源库的搜索结果是一列：左边图标、右边名字；名字靠左、在键盘上面
            hit = next((o for o in sorted(self.lines, key=lambda o: o["cy"]) if label_norm(o["t"]) in names
                        and 12 < o["cy"] < 70 and 12 < o["x"] < 45), None)
            if hit:
                self.tap(hit["cx"], hit["cy"], f"App 资源库里的「{hit['t'].strip()}」", wait=3.5)
                self.snap()
                if self.in_library():
                    continue
                self.wait_splash()
                return not self.landed_wrong(name)
        self.log(f"App 资源库里也没搜到「{name}」")
        return False

    def paste_text(self, text):
        """借 Mac 剪贴板往手机当前的输入框里粘一段中文（「通用剪贴板」同步到手机），用完把原来的文字放回去。
        剪贴板里是图片、文件（放不回去）时不借，返回 False。"""
        if not jx.clipboard_text_only():
            return False
        old = jx.clipboard_get()
        try:
            jx.clipboard_set(text)
            time.sleep(1.5)
            jx.paste()
            time.sleep(1.8)
            return True
        finally:
            try:
                jx.clipboard_set(old)
            except Exception:
                pass

    def spot_bar(self):
        """Spotlight 底部的搜索栏：左边是放大镜（识别成「Q」），右边是「Search / 搜索」或者上次搜过、还留着的字。"""
        q = next((o for o in self.lines if o["t"].strip() in ("Q", "q", "🔍") and o["cx"] < 16 and o["cy"] > 85), None)
        if not q:
            # 放大镜有时认不出来：屏幕中间一个字都没有、只有底部左边一段字，也是留着上次搜索词的 Spotlight
            bottom = [o for o in self.lines if o["cy"] > 85]
            if len(bottom) == 1 and 88 < bottom[0]["cy"] < 97 and bottom[0]["x"] < 30 \
                    and bottom[0]["t"].replace(" ", "") not in TAB_WORDS and not any(15 < o["cy"] < 85 for o in self.lines):
                return bottom[0]
            return None
        right = [o for o in self.lines if abs(o["cy"] - q["cy"]) < 2 and o["cx"] > q["cx"] + 3]
        return min(right, key=lambda o: o["x"]) if right else q

    def in_spotlight(self):
        # 识别出来的字常常没有空格（「在App中搜索」），统一去掉空格再比
        flat = self.text_all().replace(" ", "")
        return any(w.replace(" ", "") in flat for w in SPOT_PAGE) or self.spot_bar() is not None

    def spotlight_hit(self, labels, loose=False):
        """Spotlight 里可以点的 App：名字完全对上，而且在「Siri 建议」「最佳结果」「App」分区里。
        返回 (那一行, 是不是图标网格)；网格里名字在图标下面，列表里名字在图标右边。"""
        headers = [o for o in self.lines if (o["t"].strip() in SPOT_HEADERS or SPOT_OK_RE.match(o["t"].strip())) and o["x"] < 12]  # 分区标题都靠左
        want = {label_norm(x) for x in labels}
        names = [o for o in self.lines if label_norm(o["t"]) in want and 10 < o["cy"] < 88]
        for o in sorted(names, key=lambda o: o["cy"]):
            # 图标上也可能印着名字（美团、天猫）：列表里名字在图标右边，网格里名字在图标下面，不要认成图标上那个
            if any(p["x"] > o["x"] + 5 and abs(p["cy"] - o["cy"]) < 2 or abs(p["cx"] - o["cx"]) < 6 and 0 < p["cy"] - o["cy"] < 9
                   for p in names):
                continue
            above = [h for h in headers if h["cy"] < o["cy"]]
            if above:
                head = max(above, key=lambda h: h["cy"])["t"].strip()
                if head not in SPOT_OK and not SPOT_OK_RE.match(head):
                    continue
            elif o["x"] < 20 and not loose:  # 分区标题被壁纸盖住没认出来：这个位置可能是 Safari 的搜索建议，先不点
                continue
            else:
                head = ""
            # 设置里的同名条目下面紧跟一行灰字「Apps」，这种不是 App 本身
            if any(p is not o and abs(p["cy"] - o["cy"] - 2.3) < 1.5 and abs(p["x"] - o["x"]) < 3
                   and p["t"].strip() in ("Apps", "App", "应用", "App 与网站") for p in self.lines):
                continue
            # 网格（图标在上、名字在下）：Siri 建议一定是；「最佳结果」也常是一排图标——名字靠左，或者同一行还有别的名字
            # 「App」分区一定是列表；同一行别的字不能是图标上印的这个名字，也不能是分区右边的「显示更多」
            grid = head in SPOT_GRID or bool(re.match(r"Siri", head)) or (head not in SPOT_LIST and (o["x"] < 22 or any(
                p is not o and p not in names and abs(p["cy"] - o["cy"]) < 1 and abs(p["cx"] - o["cx"]) > 10 for p in self.lines)))
            return o, grid
        return None, False

    def open_via_spotlight(self, name, cfg):
        jx.cmd_spotlight(NS(wait=1.5))
        self.log("打开 Spotlight 搜索（⌘3）")
        self.snap()
        for _ in range(2):  # Spotlight 弹出有动画，截早了是一片模糊、一个字都认不出：等一下再看
            if self.in_spotlight():
                break
            time.sleep(1.5)
            self.snap()
        # 留着上次搜索词、还没出结果的 Spotlight 只是一片模糊背景，常常除了顶部时间什么字都认不出：按 ⌘3 之后这样也算打开了
        blank = all(o["cy"] < 12 for o in self.lines)
        if not self.in_spotlight() and not blank and \
                not any(o["t"].strip() in ("Search", "搜索") and o["cy"] > 80 for o in self.lines):
            self.log("⌘3 没打开 Spotlight")
            self.go_springboard()
            return False
        hit, grid = self.spotlight_hit(cfg["labels"])
        if not hit:
            box = next((o for o in self.lines if o["t"].strip() in ("Search", "搜索") and o["cy"] > 80), None) or self.spot_bar()
            # 点搜索栏中间（点到左边的放大镜不会进入输入状态）
            self.tap(40, box["cy"] if box else 92.6, "Spotlight 底部的搜索框", wait=1.3)
            # Spotlight 会留着上次搜的字（如「taobao」「淘宝」），点下去光标还可能落在字中间：全选删掉、两边再各删一遍
            jx.clear_field()
            # 2.15：先粘贴中文名（「京东」）：不受手机输入法影响，Spotlight 一定按名字出 App；粘不了再打拼音
            pasted = False
            if self.paste_text(name):
                self.log(f"在 Spotlight 里粘贴「{name}」")
                self.snap()
                hit, grid = self.spotlight_hit(cfg["labels"])
                # 2.17：搜索栏里确实是这个名字、也出了搜索结果，却没有这个 App：就是没装，不用再打拼音试（省 1 分多钟）
                pasted = any(o["cy"] > 80 and label_norm(name) in label_norm(o["t"]) for o in self.lines) and \
                    any(w in self.text_all() for w in ("在App中搜索", "搜索 App Store", "建议", "Search in App", "Search App Store"))
                if not hit and not pasted:
                    jx.clear_field()
            for q in ([] if hit or pasted else cfg["queries"]):
                for attempt in range(2):
                    if attempt:  # 第一次没搜到：可能是输入法不对，清空、切换输入法再搜一次
                        jx.clear_field()
                        jx.cmd_key(NS(name="switch", count=1, wait=0.8))
                    # 先不选词：按空格选的第一个词常常不对（xianyu → 限于），拼音本身就能搜到 App
                    jx.cmd_type(NS(text=q, wait=1.8))
                    self.log(f"在 Spotlight 里输入 {q}")
                    self.snap()
                    hit, grid = self.spotlight_hit(cfg["labels"])
                    if not hit:  # 搜不到再按空格选词（英文输入法里只是多一个空格）
                        jx.cmd_key(NS(name="space", count=1, wait=1.8))
                        self.snap()
                        hit, grid = self.spotlight_hit(cfg["labels"])
                    if hit:
                        break
                if hit:
                    break
                jx.clear_field()
        if not hit:
            # 分区标题一个都没认出来（不同系统版本、语言写法不一样）：名字完全对上的也点一下试试，点完核对打开的是不是这个 App
            hit, grid = self.spotlight_hit(cfg["labels"], loose=True)
            if hit:
                self.log(f"Spotlight 的分区标题没认出来，试着点名字对得上的「{hit['t'].strip()}」，点完核对")
        if not hit:
            # 2.17：没装的 App，Spotlight 里只出 App Store 的条目（名字旁边是「获取 / Get」或下载的云朵）：确定没装，不用再翻主屏幕
            names = {label_norm(x) for x in cfg["labels"]}
            rows_ = [o for o in self.lines if label_norm(o["t"]) in names]
            if any(g["cx"] > o["cx"] and abs(g["cy"] - o["cy"]) < 4 and g["t"].strip() in ("获取", "Get", "GET")
                   for o in rows_ for g in self.lines):  # 「获取」按钮和名字在同一行、在名字右边
                self.log(f"Spotlight 里「{name}」只有 App Store 的「获取」：手机上没装")
                self.spot_missing = True
            else:
                self.log(f"Spotlight 里没找到「{name}」，改成翻主屏幕找图标")
            self.go_springboard()
            return False
        for _ in range(3):  # 刚打完字时，第一下点击只会收起输入状态，App 不会打开：再点
            # 网格里点名字正上方的图标（只点名字有时没反应），列表里直接点名字那一行
            self.tap(hit["cx"], hit["cy"] - (4.8 if grid else 0), f"Spotlight 里的「{hit['t']}」", wait=3.5)
            self.snap()
            if not self.in_spotlight():
                self.wait_splash()
                return not self.landed_wrong(name)
            hit, grid = self.spotlight_hit(cfg["labels"], loose=True)
            if not hit:
                break
        self.log("点了以后还停在 Spotlight，改成翻主屏幕找图标")
        self.go_springboard()
        return False

    def text_at(self, x, y, pad=1.0):
        """(x, y) 这个位置上有没有识别到的文字（有的话说明它不是图标按钮）。
        返回箭头本身常被识别成「←」「<」「个」「く」等单个符号，这种不算文字。"""
        hits = []
        for o in self.lines:
            if not (o["x"] - pad <= x <= o["x"] + o["w"] + pad and o["y"] - pad <= y <= o["y"] + o["h"] + pad):
                continue
            t = o["t"]
            i = min(max(int((x - o["x"]) / max(o["w"], 0.1) * len(t)), 0), len(t) - 1)
            if t.strip()[:1] in ARROWS and i <= 1:
                continue
            hits.append(o)
        return hits

    def scroll_top(self):
        for _ in range(2):
            jx.cmd_scroll(NS(direction="up", wait=0.8))
        self.log("往上滚到页面顶部")
        self.snap()

    def go_home_tab(self, name, reopened=False):
        """回到 App 首页。App 停在回不去的页面（上次留下的详情页、截屏分享弹层、直播间…）时，
        最后一招：在 App 切换器里把它划掉、重新打开（新打开的 App 从首页开始）。"""
        try:
            return self._go_home_tab(name, reopened)
        except Skip as e:
            if getattr(self, "_restarted", False):
                raise
            self._restarted = True
            self.log(f"{e}：把 {name} 关掉重新打开（在 App 切换器里上滑划掉）")
            if not self.restart_app(name):
                raise
            return self._go_home_tab(name, True)

    def card_label(self, o, name):
        """App 切换器里卡片左上方的 App 名字。最右边那张常被屏幕边缘截掉一半（「拼多多」只露出「拼多」）。"""
        t = o["t"].strip()
        return o["cy"] < 32 and any(t == x or (len(x) >= 3 and len(t) >= 2 and x.startswith(t)) for x in APPS[name]["labels"])

    def restart_app(self, name, in_app=True):
        """在 App 切换器里把这个 App 划掉、重新打开。从 App 里打开切换器时，当前 App 的卡片在最右边，
        正中间的是上一个用过的 App（实测），所以按卡片左上方的 App 名字找到它，在名字下面往上划；找不到就什么都不划。"""
        jx.app_switcher()
        self.snap()
        hit = next((o for o in sorted(self.lines, key=lambda o: -o["cx"]) if self.card_label(o, name)), None)
        if not hit and not in_app:
            self.log(f"App 切换器里没认出{name}的卡片，不划")
            jx.cmd_home(NS(wait=1.0))
            return False
        # 名字认不出（最右边那张常被截掉一半，「拼多多」认成「拼：」）：从 App 里打开切换器时，当前 App 就是最右边那张
        jx.swipe_up_center(min(max(hit["cx"], 10), 92) / 100 if hit else 0.88)
        if not hit:
            self.log(f"App 切换器里{name}的名字没认出来，划最右边那张（当前 App）")
        self.snap()
        if hit and any(self.card_label(o, name) for o in self.lines):
            self.log(f"往上划了{name}的卡片，但它还在，不再试")
            jx.cmd_home(NS(wait=1.0))
            return False
        jx.cmd_home(NS(wait=1.2))
        self.log(f"已经把 {name} 划掉，重新打开")
        self.open_app(name)
        return True

    def _go_home_tab(self, name, reopened=False):
        self.wait_splash()
        self.close_popups()
        scrolled = False
        for _ in range(6):  # 从「详情页 + 优惠面板」回来要：关面板、退详情、退结果页、退搜索页，每步之前可能还要滚到顶
            # 关掉系统弹窗后有时会退回手机主屏幕 / Spotlight：已经不在 App 里了，重新打开一次（在主屏幕上往上滚会拉出 Spotlight）
            # 只认 Spotlight 的分区标题（App 自己的搜索页左边也有放大镜，不能用底部放大镜判断）
            self.check_screenshots()
            if not reopened and (any(w.replace(" ", "") in self.text_all().replace(" ", "") for w in SPOT_PAGE) or (self.home_label(APPS[name]) and not self.tab_labels())):
                self.log("已经不在 App 里了，重新打开")
                self.open_app(name)
                return self._go_home_tab(name, reopened=True)
            if self.tab_labels() and self.top_search_button():
                self.log("已在首页")
                return
            home_btn = next((o for o in self.lines if o["t"].replace(" ", "") in ("回到首页", "返回首页")), None)
            if home_btn:  # 京东「二楼」等页面底部的「回到首页」按钮
                self.checked_tap(home_btn["cx"], home_btn["cy"], f"「{home_btn['t']}」按钮")
            elif (self.is_detail_page() or not self.tab_labels()) and (close := self.corner_close()):
                self.checked_tap(*close, "右上角的 ×（关掉悬浮的直播小窗 / 直播间）")
            elif self.is_detail_page() and not self.text_at(*BACK):
                self.checked_tap(*BACK, "左上角返回键（退出商品详情页）")
            elif self.tab_labels():
                y = max(o["cy"] for o in self.tab_labels())
                self.checked_tap(11, y, "底部最左边的首页标签")
            elif self.text_at(*BACK):
                # 返回键的位置上是文字（页面被往下翻过，顶部导航栏藏起来了）：先滚回顶部，不乱点
                if scrolled:
                    raise Skip(f"左上角返回键的位置被「{self.text_at(*BACK)[0]['t']}」占着，找不到返回键")
                self.scroll_top()
                scrolled = True
                continue
            elif self.top_bar():
                self.checked_tap(*BACK, "左上角返回键")
            elif not scrolled:
                # 结果页被往下翻过时顶部导航栏会藏起来，返回键的位置上是商品图片（没有文字，上一条判断不出来），
                # 直接点会进商品详情页：先滚回顶部再点
                self.scroll_top()
                scrolled = True
                continue
            else:
                self.checked_tap(*BACK, "左上角返回键")
            scrolled = False
            self.close_popups()
        if self.tab_labels() and self.top_search_button():  # 最后一下刚好回到首页
            self.log("已在首页")
            return
        raise Skip("没能回到 App 首页（找不到底部标签栏或顶部搜索框）")

    def type_paste(self):
        """有中文时先用粘贴：把关键词放进 Mac 剪贴板（「通用剪贴板」会同步到 iPhone），在搜索框里按 ⌘V。
        输入法打不出的书名、小众品牌名（「其主之声」「诺迪思」）也能一字不差。粘贴后核对输入框；粘不对（没开通用剪贴板、
        同步慢）就清空、返回 False，改用拼音输入法打。用完把 Mac 剪贴板原来的文字放回去。"""
        text = f"{self.keyword} {self.spec}".strip()
        if not jx.clipboard_text_only():
            self.log("Mac 剪贴板里有图片或文件（用完放不回去），不借剪贴板，用输入法打字")
            return False
        old = jx.clipboard_get()
        try:
            for attempt in range(3):
                # 末尾空一格：光标紧贴着最后一个字时，识别常把最后那个词漏掉。
                # 2.17：「通用剪贴板」同步到手机有时慢，手机上粘出来的还是剪贴板里原来的字（实测粘出了用户刚发给 AI 的那句话）：
                # 每次重试都重新写一遍剪贴板（末尾空格数不同，算一次新的复制），等得更久一点
                jx.clipboard_set(text + " " * (attempt + 1))
                time.sleep(1.5 + attempt * 2.5)  # 等剪贴板同步到手机
                jx.paste()
                time.sleep(1.2)
                self.log(f"粘贴关键词（⌘V）{'，再试一次' if attempt else ''}")
                self.snap()
                if self.typed_in_top() or self.field_zoom_has(text):
                    return True
                self.deny_system_alert()  # App 发现剪贴板变了，自己弹「想粘贴」的系统弹窗，会吞掉这次 ⌘V
                jx.clear_field()
            tops = [o["t"] for o in self.lines if o["cy"] < 24]
            self.log(f"粘贴后输入框里不是「{text}」（识别到：{' / '.join(tops)}），清空后改用输入法打字")
            return False
        finally:
            try:
                jx.clipboard_set(old)
            except Exception:
                pass

    def field_zoom_has(self, text):
        """整屏识别没认全搜索框（光标、清除按钮挨着字）：把搜索框那一条放大再认一次。只认搜索框那一行（京东的输入框在按钮
        上面一行，淘宝、拼多多在同一行），放大图里多带一行别的字反而认不准。框里只可能是刚粘贴的字或剪贴板里原来的字，
        所以允许认错一两个形近字（「莱姆」认成「菜姆」）。"""
        btn = self.top_search_button()
        if not btn or not self.image:
            return False
        want = field_norm(text)
        for y0, y1 in ((btn["cy"] - 6.5, btn["cy"] - 1.5), (btn["cy"] - 2.5, btn["cy"] + 2.5)):
            for o in jx.ocr_crop(self.image, 5, max(y0, 8), btn["x"], y1):
                got = field_norm(o["t"])
                if want in got or (len(got) >= len(want) - 1 and difflib.SequenceMatcher(None, want, got).ratio() >= 0.8):
                    return True
        return False

    def type_and_verify(self):
        # 2.16：英文关键词也先粘贴（一下 ⌘V 就进去了，打字过程中别的窗口跳到前面也来不及抢走按键），粘不进再一个个打
        if not self.has_cn:
            if self.type_paste():
                self.input_used = "粘贴"
                return
            self.type_ascii(self.typed)
            self.input_used = "英文"
            return
        if self.type_paste():
            self.input_used = "粘贴"
            return
        if self.type_chinese():
            self.input_used = "中文"
            return
        jx.cmd_key(NS(name="delete", count=40, wait=0.5))
        self.log("中文没打对，清空输入框，改用拼音搜索")
        self.type_ascii(self.pinyin)
        self.input_used = "拼音"

    def type_ascii(self, text):
        """在英文输入法里打英文、数字、空格。末尾补一个空格：英文输入法里只是多个空格，
        中文输入法里会把字母选成汉字，核对时就能发现输入法不对。"""
        for attempt in range(3):
            if attempt:
                jx.cmd_key(NS(name="delete", count=40, wait=0.5))
                jx.cmd_key(NS(name="switch", count=1, wait=0.8))
                self.log("清空输入框，Ctrl+空格 切换手机输入法")
            jx.cmd_type(NS(text=text + " ", wait=1.0))
            self.log(f"输入 {text}")
            self.snap()
            left = self.field_residue()
            if left:  # 2.17：框里还剩着粘错的旧字，打的字跟在后面：清空重打（不换输入法）
                self.log(f"输入框里还剩着别的字（{left[0][:16]}），清空重打")
                jx.clear_field()
                jx.cmd_type(NS(text=text + " ", wait=1.0))
                self.snap()
                if self.field_residue():
                    continue
            if self.typed_in_top(text):
                return
        tops = [o["t"] for o in self.lines if o["cy"] < 24]
        raise Skip(f"搜索框里的文字一直不对（识别到：{' / '.join(tops)}）")

    def tap_suggestion(self):
        """打的是拼音时，搜索联想列表里如果有和中文关键词完全一样的词，点它（等于用中文搜）。
        直接搜拼音，京东会按字母去搜，结果不相关。"""
        btn = self.top_search_button()
        for o in self.lines:
            if btn and btn["cy"] + 2 < o["cy"] < 70:
                t = re.sub(r"^[OoQ〇○◯0]\s*", "", o["t"].strip())  # 左边的放大镜图标
                if field_norm(t) == field_norm(self.typed):
                    self.checked_tap(o["cx"], o["cy"], f"搜索联想「{t}」", wait=3.5)
                    self.input_used = "拼音联想"
                    return True
        self.log("搜索联想里没有和关键词一样的中文词，直接搜拼音")
        return False

    def type_segment(self, kind, text, done):
        """打一段字，打完核对输入框。"""
        if kind == "en":
            jx.cmd_type(NS(text=text + " ", wait=1.0))
            self.log(f"输入 {text}")
            self.snap()
        else:
            py = jx.pinyin(text).replace(" ", "")
            jx.cmd_type(NS(text=py, wait=0.6))
            jx.cmd_key(NS(name="space", count=1, wait=1.0))
            self.log(f"输入拼音 {py}，按空格选词（想要「{text}」）")
            self.snap()
            for _ in range(3):  # 只选上了前半个词，后面还有没上屏的拼音：再按空格
                if self.typed_in_top(done + text):
                    break
                # 只有「已上屏的字 + 拼音剩下的那截」才是没上屏；整段都是字母，是英文输入法打的
                if not any(t.startswith(field_norm(done)) and (done or CJK.search(t))
                           and (m := re.search(r"[a-z]+$", t)) and py.lower().endswith(m.group())
                           for t in self.top_texts()):
                    break
                jx.cmd_key(NS(name="space", count=1, wait=1.0))
                self.log("还有拼音没上屏，再按一次空格")
                self.snap()
        return self.typed_in_top(done + text)

    def type_chinese(self):
        """中文段：打拼音、按空格选第一个候选词；英文数字段：直接打。
        手机当前是什么输入法不知道，也不打字试探（试探的字会留在搜索框里）：第一段打不对就清空、切换输入法再打一次。
        每段打完都核对输入框，候选词不对就返回 False（由调用方改用拼音）。"""
        for attempt in range(2):
            if attempt:
                jx.cmd_key(NS(name="delete", count=40, wait=0.5))
            mode, done = None, ""
            for kind, text in self.segs:
                if mode not in (None, kind):
                    jx.cmd_key(NS(name="switch", count=1, wait=0.8))
                    self.log(f"Ctrl+空格 切换到{'中文' if kind == 'cn' else '英文'}输入法")
                ok = self.type_segment(kind, text, done)
                if not ok and mode is None:
                    jx.cmd_key(NS(name="delete", count=40, wait=0.5))
                    jx.cmd_key(NS(name="switch", count=1, wait=0.8))
                    self.log("第一段没打对，清空输入框，Ctrl+空格 切换手机输入法再打一次")
                    ok = self.type_segment(kind, text, done)
                if not ok:
                    tops = [o["t"] for o in self.lines if o["cy"] < 24]
                    self.log(f"输入框里不是「{done + text}」（识别到：{' / '.join(tops)}）")
                    break
                mode, done = kind, done + text
            else:
                return True
        return False

    def enter_search(self):
        """点首页搜索框，进入搜索输入页。进得去返回 True。"""
        for attempt in range(2):
            btn = self.top_search_button()
            if not btn:
                raise Skip("首页上找不到搜索框")
            self.checked_tap(35, btn["cy"], "首页搜索框" + ("（再点一次）" if attempt else ""), wait=2.0)
            # 搜索输入页没有底部标签栏；标签栏还在说明还停在首页（首页本身也有「搜索」按钮，不能只看按钮）
            if self.top_search_button() and not self.tab_labels():
                return True
        return False

    def search(self):
        if not self.enter_search():
            # 首页活动弹窗（天猫「恭喜你被惊喜砸中了」）每次样子都不一样、按钮也认不准，但只弹一次：把 App 划掉重开就没了
            self.log("点了搜索框没反应，可能被首页弹窗挡住了：把 App 划掉重新打开")
            if not self.restart_app(self.app_name):
                raise Skip("点了搜索框，但没进入搜索输入页（可能被首页的动画或弹窗挡住了）")
            self.go_home_tab(self.app_name)
            self.close_popups()
            if not self.enter_search():
                raise Skip("点了搜索框，但没进入搜索输入页（可能被首页的动画或弹窗挡住了）")
        self.type_and_verify()
        btn = self.top_search_button()
        if self.input_used == "拼音" and self.tap_suggestion():
            pass
        elif btn:
            self.checked_tap(btn["cx"], btn["cy"], "「搜索」按钮", wait=3.5)
        else:
            jx.cmd_key(NS(name="return", count=1, wait=3.5))
            self.log("按回车搜索")
            self.snap()
        self.close_popups()
        if not self.is_results():
            raise Skip("搜索后没有出现搜索结果列表页")

    def read_cards_pdd(self, platform, shot=1):
        """拼多多的结果页：单列，标题在卡片最上面，下面是标签行，价格在卡片最下面（「抢券后¥9899」「限时¥11298.5」「¥9899」），
        列表上没有店铺名。以价格行为锚点，这张卡的范围是上一个价格到这个价格之间。"""
        start = max([o["y"] + o["h"] for o in self.lines if o["cy"] < 36 and any(w in o["t"] for w in LIST_HEADER + ("存储容量", "机身颜色"))] + [0])
        pp = [o for o in self.lines if o["y"] > start and PDD_PRICE.search(o["t"])]
        # 双列网格（搜品牌词时常见）：左列价格在最左边、右列在屏幕中间，同一排的两个价格一样高
        if any(a["x"] < 22 and 46 <= b["x"] <= 60 and abs(a["cy"] - b["cy"]) < 1.5 for a in pp for b in pp):
            return self.read_cards_pdd_grid(platform, shot, start)
        body = sorted((o for o in self.lines if o["y"] > start and o["cy"] < 98 and o["x"] >= 30), key=lambda o: (round(o["cy"]), o["x"]))
        cards, top = [], start
        for o in body:
            m = PDD_PRICE.search(o["t"])
            if not m:
                continue
            # 和上一张卡价格同一行的字（「本店已拼9130」「品牌热销3157万+件」）属于上一张卡，不算
            above = [p for p in body if top + (1.2 if top > start else 0) < p["cy"] < o["cy"] - 0.5]
            top = o["cy"]
            # 2.17：按行拼标题。百亿补贴的卡片标题前面有「百亿补贴」「新品首发」两个徽标，标题本身折成两行
            # （「新品首发【需当面签收激活】苹」「果iPhone18 Pro全网通5G 双卡双待」），型号常在第二行，要两行连起来看
            lines_ = []
            for p in above:
                if lines_ and abs(p["cy"] - lines_[-1][0]["cy"]) < 1.2:
                    lines_[-1].append(p)
                else:
                    lines_.append([p])
            title, spec_hint = [], ""
            for row in lines_:
                row.sort(key=lambda p: p["x"])
                t = " ".join(p["t"].strip() for p in row)
                if PDD_TAG.search(t):
                    break
                while PDD_BADGE.match(t) and PDD_BADGE.sub("", t, count=1).strip():  # 「百亿补贴」「品牌」「直播中」「新品首发」徽标
                    t = PDD_BADGE.sub("", t, count=1).strip()
                if PDD_BADGE.fullmatch(t):
                    continue
                main = max((p for p in row if not PDD_BADGE.fullmatch(p["t"].strip())), key=lambda p: len(p["t"]), default=row[0])
                title.append(dict(main, t=t, raw=main["t"]))
            # 标签行下面单独一行「•• 256GB」是这张卡默认选中的规格：标题没写容量时拿来补上
            for p in above:
                mm = re.fullmatch(r"[•●·.\s]*(\d+\s*(?:GB|TB|G|T))\s*", p["t"].strip(), re.I)
                if mm:
                    spec_hint = mm.group(1)
            if o["cy"] > 96 or not title:  # 被屏幕底部截断，或这张卡的标题在上一屏：下一屏会读到完整的
                continue
            text = " ".join(p["t"] for p in above)
            word = m.group(1) or ""
            label = "券后价" if "券后" in word else ""  # 「秒杀价 / 快抢光」只是叫法
            title = title[:2]  # 标题最多两行，再往下是卖点
            joined = title[0]["t"]
            for nxt in title[1:]:  # 折行处是汉字接着写的（「苹」「果iPhone」），不加空格
                joined += ("" if re.search(r"[\u4e00-\u9fff]$", joined) and re.match(r"[\u4e00-\u9fff]", nxt["t"]) else " ") + nxt["t"]
            if spec_hint and not re.search(r"(?<![\d.])(?:\d{2,4}\s*GB?|[1-8]\s*TB?)(?![A-Za-z])", joined, re.I):  # 「5G」不是容量
                joined += " " + spec_hint
            cards.append({"platform": platform, "title": clean_title([dict(title[0], t=joined)]), "shop": "", "first": title[0]["raw"],
                          "price": float(m.group(2).replace(",", "")), "original": 0, "label": label, "note": "",
                          "ocr": [p["t"] for p in above] + [o["t"]], "y": round(o["cy"], 1), "layout": "list", "shot": shot, "garbled": False})
        for c in cards:
            text = " ".join(c["ocr"])
            c["note"] = "；".join(NOTE_WORDING.get(w, w) for w in CONDITIONS
                                 if w in text and not (w == "需激活" and ("需签收激活" in text or "需现场激活" in text)))
            c["match"], c["why"] = self.judge(c, platform)
            if PDD_USED.search(text):
                c["match"], c["why"] = False, "二手、翻新或海外版，不是全新国行"
                c.pop("spec_unknown", None)  # 不要再点进详情页「确认容量」，确认完会被当成同款
        return cards

    def read_cards_pdd_grid(self, platform, shot, start):
        """拼多多双列网格：每张卡从上到下是商品图（图上印着大字卖点）、一行标题、一行标签（倒计时、收藏数）、价格。
        标题取价格上面第一行不是标签的字；再往上是商品图里印的字，不当标题。"""
        body = [o for o in self.lines if o["y"] > start and o["cy"] < 98]
        cards = []
        for left, col in ((True, [o for o in body if o["cx"] < 50]), (False, [o for o in body if o["cx"] >= 50])):
            col.sort(key=lambda o: (round(o["cy"]), o["x"]))
            top = start
            for o in col:
                m = PDD_PRICE.search(o["t"])
                if not m or not (o["x"] < 22 if left else 46 <= o["x"] <= 60):
                    continue
                above = [p for p in col if top < p["cy"] < o["cy"] - 0.5]
                top = o["cy"]
                head = next((p for p in reversed(above) if o["cy"] - p["cy"] < 7 and not PDD_TAG.search(p["t"])
                             and not PDD_BADGE.fullmatch(p["t"].strip())), None)
                if o["cy"] > 96 or not head:  # 被屏幕底部截断，或这张卡的标题在上一屏：下一屏会读到完整的
                    continue
                t = head["t"]
                while PDD_BADGE.match(t) and PDD_BADGE.sub("", t, count=1).strip():  # 「百亿补贴 品牌 SK-II …」前面的几个徽标
                    t = PDD_BADGE.sub("", t, count=1).strip()
                word = m.group(1) or ""
                near = [p for p in above if o["cy"] - p["cy"] < 7 and not re.search(r"分享给好友", p["t"])]  # 标题、标签行；商品图里的字、截屏小窗不算
                cards.append({"platform": platform, "title": clean_title([dict(head, t=t)]), "shop": "", "first": head["t"],
                              "price": float(m.group(2).replace(",", "")), "original": 0,
                              "label": "券后价" if "券后" in word else "", "note": "",
                              "ocr": [p["t"] for p in near] + [o["t"]], "y": round(o["cy"], 1), "layout": "grid", "shot": shot,
                              "garbled": False})
        for c in cards:
            text = " ".join(c["ocr"])
            c["note"] = "；".join(NOTE_WORDING.get(w, w) for w in CONDITIONS
                                 if w in text and not (w == "需激活" and ("需签收激活" in text or "需现场激活" in text)))
            c["match"], c["why"] = self.judge(c, platform)
            if PDD_USED.search(text):
                c["match"], c["why"] = False, "二手、翻新或海外版，不是全新国行"
                c.pop("spec_unknown", None)
            elif not c["match"] and "找不到完整型号" in c["why"] and len(re.sub(r"\s+", "", c["title"])) <= 24 \
                    and not re.search(r"max|plus|mini|ultra|\be\b|fe\b", c["title"], re.I) \
                    and all(n in re.findall(r"\d+", self.keyword) for n in re.findall(r"\d{2,}", c["title"])):
                # 2.17：网格卡片的标题只有一行，常截到「【需当面签」就没了，看不到型号：进商品页看完整标题再判断
                c["title_cut"] = True
                c["why"] = "网格卡片的标题被截断，看不到型号"
        return cards

    def read_cards(self, platform, shot=1):
        if platform == "拼多多":
            return self.read_cards_pdd(platform, shot)
        """从文字识别结果里拆商品卡片。以价格行为锚点：标题 = 紧挨在价格上方的文字，店铺 = 紧挨在价格下方带「店」的那行。
        自动识别布局：单列列表（左图右文，只看右侧文字列）或双列网格（左右两列分别拆）。"""
        start = 0
        for o in self.lines:
            if o["cy"] < 36 and any(w in o["t"] for w in LIST_HEADER):
                start = max(start, o["y"] + o["h"])
        body = [o for o in self.lines if o["y"] > start and o["cy"] < 98]
        prices = [o for o in body if PRICE_RE.match(o["t"].strip().lstrip("。.·• ")) and o["h"] >= 1.4]
        grid = any(o["x"] < 22 for o in prices) and any(46 <= o["x"] <= 60 for o in prices)
        if grid:
            cols = [[o for o in body if o["cx"] < 50], [o for o in body if o["cx"] >= 50]]
        else:
            cols = [[o for o in body if 30 <= o["x"] <= 95]]
        cards = []
        for col in cols:
            col.sort(key=lambda o: (round(o["cy"]), o["x"]))
            for k, o in enumerate(col):
                if o not in prices:
                    continue
                t = o["t"].strip()
                # 标题：从价格往上收，碰到上一张卡的价格/店铺行，或超出 10% 屏高就停
                # 标题各行之间的间距不超过 2.7% 屏高；间距更大说明上面是商品图里印的字（如「预估到手价」）
                above, fillers = [], []
                for p in reversed(col[:k]):
                    if o["y"] - p["y"] > 11 or p in prices or is_shop(p["t"]) or PRICE_RE.search(p["t"]):
                        break
                    if FILLER_RE.search(p["t"]):  # 天猫卡片价格上方的「刚刚购买 / 次日达 / 加补满减」：不当标题，接着往上找
                        fillers.append(p["t"])     # 但里面的「政府补贴」等条件要进备注
                        continue
                    # 单列列表里夹在标题和价格之间的榜单、买家评价、卖点（「美白乳液面霜榜第8名」「“日期很新鲜”」
                    # 「幼崽护航|多猫囤货」「全阶段」）：不当标题，也不拿它算行距（不然上面真正的标题会被行距挡掉）
                    if not grid and not above and (not title_part(p["t"]) or re.search(r"[|丨｜≥]", p["t"])):
                        fillers.append(p["t"])
                        continue
                    if sum(1 for q in above if title_part(q["t"])) >= 4:
                        break
                    if above and above[0]["y"] - p["y"] > 2.7:
                        break
                    above.insert(0, p)
                # 店铺：价格下方 9% 屏高以内第一行带「店」的；碰到下一张卡的价格就停
                shop, below = "", []
                for p in col[k + 1:]:
                    if p["y"] - o["y"] > 9 or p in prices:
                        break
                    below.append(p["t"])
                    if is_shop(p["t"]):
                        shop = clean_shop(p["t"])
                        break
                nums = PRICE_RE.findall(t)
                parts = PRICE_RE.split(t)
                rest = parts[2].strip() if len(parts) > 2 else ""
                label = rest.split(" ")[0] if rest else ""
                if not any(w in label for w in LABEL_WORDS) and any(w in parts[0] for w in LABEL_WORDS):
                    label = parts[0].strip(" :：")  # 价格前面写着「到手价」「券后」：这个价已经减过优惠，算优惠时不能再从它往下减
                ocr = [p["t"] for p in above] + fillers + [t] + below
                head = next((p for p in above if title_part(p["t"])), None)  # 标题第一行：进详情页时点它
                per = PER_SPEC.search(t)
                cards.append({"platform": platform, "title": clean_title(above), "shop": shop,
                              "first": head["t"] if head else "", "per": per.group(1).replace(" ", "") if per else "",
                              "price": float(nums[0].replace(",", "")),
                              "original": float(nums[1].replace(",", "")) if len(nums) > 1 else 0,
                              "label": label if any(w in label for w in LABEL_WORDS) else "",
                              "note": "", "ocr": ocr, "y": round(o["cy"], 1), "layout": "grid" if grid else "list",
                              "shot": shot, "garbled": bool(re.match(r"\s\d", t[PRICE_RE.search(t).end():]))})
        for c in cards:
            text = " ".join(c["ocr"])
            c["note"] = "；".join(NOTE_WORDING.get(w, w) for w in CONDITIONS
                                 if w in text and not (w == "需激活" and ("需签收激活" in text or "需现场激活" in text)))
            c["match"], c["why"] = self.judge(c, platform)
        return cards

    # ---------- 商品详情页：看券和立减（只看不领） ----------
    def corner_close(self):
        """右上角单独的 ×：淘宝详情页上悬浮的直播小窗、点进去的直播间。它盖住了左上角的返回键（点返回键会点进直播），
        要先点这个 × 关掉。整屏识别常漏掉白色的小 ×，所以再把右上角放大认一次。普通详情页的右上角是分享、购物车、更多，不是 ×。"""
        # 只有详情页（底部有购买栏）才往下找面板的 ×；别的页面只看顶部：相似商品、推荐卡片上也有小 ×，点了会弹出更多卡片
        bottom = 72 if self.is_detail_page() else 17
        def find(lines):
            return next((o for o in lines if o["t"].strip(" .•…") in ("×", "X", "x", "✕") and o["cx"] > 82 and 8 < o["cy"] < bottom
                         # 「单独的」×：左边紧挨着文字的是卡片、优惠条上的
                         and not any(p is not o and abs(p["cy"] - o["cy"]) < 1.5 and 0 < o["cx"] - p["cx"] < 25 for p in lines)), None)
        hit = find(self.lines) or (find(jx.ocr_crop(self.image, 70, 8, 100, 17)) if self.image else None)
        return (hit["cx"], hit["cy"]) if hit else None

    def check_pay(self):
        """详情页、优惠面板上本来就有「加入购物车」「合计」（PLUS 开通合计）等字，所以这里只认真正的付款页。"""
        for o in self.lines:
            t = o["t"].replace(" ", "")
            if len(t) <= 10 and any(w in t for w in PAY_WORDS):
                raise SafetyStop(f"看商品详情时页面上出现了「{o['t']}」（可能进了下单或支付页）")

    def results_like(self):
        if self.is_detail_page():
            return False
        joined = "".join(self.top_texts())
        return self.typed_in_top() or any(a in joined for a in self.accepts) or \
            any(w in self.text_all() for w in ("综合", "销量", "筛选"))

    def card_title_line(self, c):
        """当前屏上这件商品的标题第一行：先按价格找（价格数字很稳定），再核对上方标题和第一次读到的相似、
        下方店铺名对得上。同一行文字两次识别常有一两个字不同，所以标题用相似度比。"""
        want = re.sub(r"\s+", "", c["first"])
        shop = re.sub(r"\s+", "", c["shop"])[-6:]
        if c.get("platform") == "拼多多":  # 标题和价格隔得远，也没有店铺名：只认标题，同一屏下面的价格对得上才算
            for o in self.lines:
                if 15 < o["cy"] < 90 and difflib.SequenceMatcher(None, re.sub(r"\s+", "", o["t"]), want).ratio() >= 0.8:
                    # 价格和标题左对齐（单列在图片右边，双列网格在各自那一列）。2.17：标题前面有「百亿补贴」「新品首发」徽标时，
                    # 记下的标题那段往右挪了（x 大 25 左右），价格在它左下方也算
                    nxt = next((PDD_PRICE.search(p["t"]) for p in sorted(self.lines, key=lambda p: p["cy"])
                                if p["cy"] > o["cy"] and -30 < p["x"] - o["x"] < 15 and PDD_PRICE.search(p["t"])), None)
                    if nxt and abs(float(nxt.group(2).replace(",", "")) - c["price"]) <= c["price"] * 0.03:
                        return o
            return None
        # 第一遍要价格对得上；第二遍放宽：再搜一次价格常变一点（券、补贴到账），标题和店铺都对得上也算同一件
        for tol, need_shop, min_sim in ((1, False, 0.5), (c["price"] * 0.03, True, 0.8)):
            for o in self.lines:
                m = PRICE_RE.match(o["t"].strip().lstrip("。.·• "))
                # 小数（「.91」）有时认得出有时认不出，差 1 块以内都算
                if not m or o["h"] < 1.4 or abs(float(m.group(1).replace(",", "")) - c["price"]) >= tol:
                    continue
                col = [p for p in self.lines if abs(p["x"] - o["x"]) < 25]
                shop_ok = bool(shop) and any(shop in re.sub(r"\s+", "", p["t"]) for p in col if 0 < p["y"] - o["y"] < 9)
                if need_shop and not shop_ok:
                    continue
                above = [p for p in col if 0 < o["y"] - p["y"] < 11 and title_part(p["t"]) and 15 < p["cy"] < 85]
                sim = [(difflib.SequenceMatcher(None, re.sub(r"\s+", "", p["t"]), want).ratio(), p) for p in above]
                # 店铺名常被识别错：店铺没对上时，标题要更像才算
                sim = [x for x in sim if x[0] >= (min_sim if shop_ok or not shop else 0.75)]
                if sim:
                    return max(sim, key=lambda x: x[0])[1]
        return None

    def find_card(self, c):
        """回到结果页上这件商品（点进它的商品页用）。2.16：每个平台只搜一次。
        商品页按「从下往上」的顺序看（run_platform 里排好了），从上一个商品页返回后，结果页还停在原来的位置，
        只要往上滚到这件所在的那一屏（读的时候每屏往下滚 0.6 屏）。离第 1 屏近的时候每次只滚一小截：
        淘宝在顶部再往上拉会刷新、顺序就变了。实在找不到（或 App 被划掉重开过、已经不在结果页），才重新搜一次。"""
        if getattr(self, "lost_results", False) or self.is_detail_page():
            # App 被划掉重开过（已经不在结果页）才重新搜。翻过几屏的结果页顶上没有搜索框和「综合 / 销量」，不能拿它判断
            return self.research_find(c)
        hit = self.card_title_line(c)
        if hit:
            return hit
        while self.screen > c["shot"]:
            near_top = self.screen - 1 <= 1
            jx.cmd_scroll(NS(direction="up", wait=1.0, frac=0.5 if near_top else 0.6))
            self.screen -= 1
            self.snap()
            hit = self.card_title_line(c)
            if hit:
                return hit
        while self.screen < c["shot"]:
            jx.cmd_scroll(NS(direction="down", wait=1.0, frac=0.6))
            self.screen += 1
            self.snap()
            hit = self.card_title_line(c)
            if hit:
                return hit
        # 往回滚常有 0.1–0.2 屏的误差：先往上一小截（第 1 屏时滚得更少），再往下看
        for d, frac in (("up", 0.25 if self.screen <= 1 else 0.35), ("down", 0.5), ("down", 0.5)):
            jx.cmd_scroll(NS(direction=d, wait=1.0, frac=frac))
            self.screen += -frac / 0.6 if d == "up" else frac / 0.6
            self.snap()
            hit = self.card_title_line(c)
            if hit:
                return hit
        self.log(f"¥{c['price']:g} 那件往回滚没找到，重新搜一次")
        return self.research_find(c)

    def research_find(self, c):
        """重新搜一次再找（新结果页从顶上开始）。只在往回滚找不到、或者已经不在结果页时用。"""
        if not getattr(self, "app_name", None):
            raise Skip("回到结果页时没找到这件商品")
        self.log(f"¥{c['price']:g} 那件：回首页重新搜一次再找")
        self.go_home_tab(self.app_name)
        self.search()
        self.lost_results = False
        self.snap()
        self.screen = 1
        hit = self.card_title_line(c)
        for _ in range(SCREENS + 1):  # 再搜一次顺序常常变：多翻几屏
            if hit:
                return hit
            jx.cmd_scroll(NS(direction="down", wait=1.2, frac=0.6))
            self.screen += 1
            self.snap()
            hit = self.card_title_line(c)
        if hit:
            return hit
        raise Skip(f"重新搜了一次也没找到这件商品（¥{c['price']:g} {c['first'][:12]}）")

    def sheet_button(self):
        """详情页一打开就弹的领券浮层（淘宝「获得平台加补券 … 开心收下」等）。"""
        return self.coupon_modal()

    def close_sheet(self, btn):
        self.close_coupon_modal(btn, "详情页的")

    def read_detail(self, c, more=False):
        hit = self.find_card(c)
        x = hit["x"] + min(hit["w"] / 2, 12)  # 点标题文字靠左的地方，离右边的加购「+」远一点
        self.tap(x, hit["cy"], f"商品标题「{c['first'][:14]}」（进详情页看优惠）", wait=3.5)
        return self.read_detail_here(c["price"], more, c.get("platform"))

    def read_detail_here(self, list_price, more=False, platform=""):
        """刚点进详情页时调用：读券、立减、到手价，点开优惠明细面板再读一遍。list_price 用来排除离谱的数字。"""
        self.snap(keep=True)
        self.check_pay()
        if not self.is_detail_page() and not self.sheet_button():
            raise Skip("点了商品标题，但没进商品详情页")
        promos, prices, sheet_rows, texts = [], [], [], []
        btn = self.sheet_button()
        if btn:
            sheet = [o for o in self.lines if 15 < o["cx"] < 85 and btn["cy"] - 45 < o["cy"] < btn["cy"] - 2]
            sheet_rows = [r for r in rows(sheet, tol=2.2) if PROMO_RE.search(r) and not JUNK_RE.search(r)]
            self.log("详情页弹出了领券浮层：记下内容，点下面的 × 关掉（不领）")
            self.close_sheet(btn)
            self.snap(keep=True)  # 关了浮层才需要再看一眼；没有浮层就用刚才那张
        texts += [o["t"] for o in self.lines]
        # 2.17：第一屏的商品标题（价格下面那两行），给列表上标题被截断的拼多多卡片核对型号用
        # 商品标题在第一屏的位置不固定（上面有几行券就往下挪几行）：第一屏去掉商品图那块，整屏的字都算（第一屏没有别的商品）
        head = " ".join(rows([o for o in self.lines if 45 < o["cy"] < 92]))
        # 2.15：商品页上选中的规格（标题、「已选」那一行）对不对：同一个链接不同容量价格不一样，列表上的价可能是别的规格的
        spec_seen = hz.spec_values(self.spec, " ".join(o["t"] for o in self.lines if 40 < o["cy"] < 92)) if self.spec else []
        want = hz.spec_want(self.spec) if self.spec else []
        spec_ok = None if not (spec_seen and want) else want[0] in spec_seen
        page = [o for o in self.lines if 48 < o["cy"] < 90]  # 上半屏是商品图，图上印的宣传字不算
        promos += sheet_rows + [r for r in rows(page) if PROMO_RE.search(r) and not JUNK_RE.search(r)]
        prices += detail_prices(self.lines)
        # 2.15：「领券 / 优惠 / 促销」那一行点开，把能用的券、满减、国补规则都看一遍（每张券后面的「领取」不点）
        coupons, shop = self.read_coupons(), ""
        texts += coupons
        left = not self.is_detail_page() and not self.sheet_button()
        if left:  # 关列表时退出了详情页：后面几步不做了，已经读到的照样用
            self.log("关优惠券列表时退出了详情页，不再往下看")
            more, platform = False, ""
        if platform == "拼多多":  # 拼多多列表上不写店铺名：往下翻到店铺那一栏，看是不是官方店
            shop, seen = self.read_shop_below()
            texts += seen
            if self.spec and spec_ok is None:  # 2.17：往下翻时看到的参数表「机身内存 / 存储容量  256GB」
                for k, t in enumerate(seen):
                    if re.search(r"机身内存|存储容量|机身存储|存储空间", t):
                        got = hz.spec_values(self.spec, " ".join(seen[k:k + 7]))
                        if got and want:
                            spec_seen, spec_ok = sorted(set(got)), want[0] in got
                            break
        if more:
            # 确认规格：容量常写在详情页往下一点的「已选：颜色 / 存储容量」那一行。往下看两屏再滚回来（面板要在顶上打开）
            for _ in range(2):
                jx.cmd_scroll(NS(direction="down", wait=1.2, frac=0.5))
                self.snap(keep=True)
                self.check_pay()
                texts += [o["t"] for o in self.lines]
            for _ in range(2):
                jx.cmd_scroll(NS(direction="up", wait=0.8, frac=0.5))
            self.snap()
        # 「已享受：满49已减10 >」「平台加补后¥52.47」这一行点开是优惠明细面板
        trig = next((o for o in self.lines if 25 < o["cy"] < 85 and PANEL_TRIGGER.search(o["t"].replace(" ", ""))
                     and not NO_TAP.search(o["t"])), None)
        if trig and not left:
            self.tap(trig["cx"], trig["cy"], f"「{trig['t'][:14]}」（打开优惠明细）", wait=2.5)
            self.snap(keep=True)
            self.check_pay()
            if any(w in self.text_all() for w in PANEL_WORDS):
                self.log("读优惠明细面板")
                texts += [o["t"] for o in self.lines]
                panel = [o for o in self.lines if 24 < o["cy"] < 97]  # 面板上面露出来的商品图不算
                promos = formula_items(panel) + amounts(panel) + promos + panel_rules(panel)
                prices += detail_prices(panel)
                # 京东「优惠」面板下半截是「可再享优惠」（国补、券、白条…）：内容排到最底下了才往下再看一截
                if any(o["cy"] > 93 for o in panel):
                    jx.cmd_scroll(NS(direction="down", wait=1.0, frac=0.4))
                    self.snap(keep=True)
                    self.check_pay()
                    more_panel = [o for o in self.lines if 24 < o["cy"] < 97]
                    texts += [o["t"] for o in self.lines]
                    promos += panel_rules(more_panel)
        prices = plausible_prices(prices, list_price)
        if platform == "拼多多" and not prices:  # 拼多多的到手价写在底部按钮上面（「¥9790 免拼购买」）
            for b in self.lines:
                if b["cy"] > 85 and re.search(r"免拼购买|发起拼单", b["t"]):
                    prices += [("拼单", float(m.group(1).replace(",", ""))) for p in self.lines if abs(p["cx"] - b["cx"]) < 15
                               and 0 <= b["cy"] - p["cy"] < 4 and (m := PRICE_RE.search(p["t"]))]
        prices = plausible_prices(prices, list_price)
        low = min(prices, key=lambda p: p[1]) if prices else None
        bases = plausible_prices(base_prices(texts), list_price)
        return {"price": low[1] if low else 0, "label": low[0] if low else "", "promos": dedup(promos)[:24],
                "coupons": dedup(coupons)[:20], "base": max((b for _, b in bases), default=0), "shop": shop, "head": head,
                "spec_ok": spec_ok, "spec_seen": sorted(set(spec_seen)), "_texts": texts}

    def read_coupons(self):
        """详情页上「领券 / 优惠 / 促销」那一行：点开是一张优惠券、满减、国补的列表（每张券后面有「领取」按钮，从不点）。
        只点这一行最右边的箭头（右边写着「领取 / 去领」时改点左边的「优惠 / 促销」标签字），读完点面板上方的遮罩关掉。
        打不开、页面变了就算了，返回已经读到的。"""
        row = None
        for o in sorted(self.lines, key=lambda o: ("券" not in o["t"], o["cy"])):  # 有「券」字的那行优先
            t = o["t"].replace(" ", "")
            if 40 < o["cy"] < 88 and o["x"] < 24 and COUPON_LABEL.match(t):
                row = o
                break
        if not row:
            return []
        right = [o for o in self.lines if abs(o["cy"] - row["cy"]) < 1.6 and o["cx"] > 78]
        if any(o["t"].strip(" .•") in ("×", "X", "x", "✕") for o in right):
            x = row["cx"] if not NO_TAP.search(row["t"]) else 0
            if not x:
                return []
        elif not any(NO_TAP.search(o["t"]) or re.search(r"领|抢|购", o["t"]) for o in right):
            x = 93
        elif not NO_TAP.search(row["t"]):
            x = row["cx"]
        else:
            self.log("券那一行右边是「领取」按钮、左边是「领券」，都不点")
            return []
        before = {o["t"] for o in self.lines}
        self.tap(x, row["cy"], f"「{row['t'].strip()}」那一行（打开优惠券列表，只看不领）", wait=2.2)
        self.snap(keep=True)
        self.check_pay()
        got = [o for o in self.lines if 22 < o["cy"] < 97]
        opened = sum(1 for o in got if o["t"] not in before) >= 3 and any(
            re.search(r"券|满\d|立减|补贴|可用|领取|已领|促销|优惠", o["t"]) for o in got if o["t"] not in before)
        if not opened:
            self.log("点了没打开优惠券列表")
            return []
        self.log("读优惠券列表（只看不领）")
        out = coupon_pairs(got) + [r for r in rows(got) if re.search(r"\d", r) and (PROMO_RE.search(r) or "%" in r)
                                   and not JUNK_RE.search(r) and not AMOUNT_ONLY.match(r)]
        if any(o["cy"] > 93 for o in got):  # 列表一直排到屏幕最底下：券多，往下再看一截；没排满就不滚了
            jx.cmd_scroll(NS(direction="down", wait=1.0, frac=0.4))
            self.snap(keep=True)
            self.check_pay()
            got = [o for o in self.lines if 22 < o["cy"] < 97]
            out += coupon_pairs(got) + [r for r in rows(got) if re.search(r"\d", r) and (PROMO_RE.search(r) or "%" in r)
                                        and not JUNK_RE.search(r) and not AMOUNT_ONLY.match(r)]
        for _ in range(2):  # 关面板：点上方露出来的遮罩（返回键的位置）；面板盖满全屏时这一下就是返回，回到详情页
            self.tap(*BACK, "优惠券列表上方的遮罩（关掉列表）", wait=1.6)
            self.snap()
            self.check_pay()
            # 回到详情页了：打开列表前的那些字又大半都在（面板开着时底部也常有「立即购买」，不能只看购买栏）
            now = {o["t"] for o in self.lines}
            if len(before & now) >= len(before) * 0.6:
                break
        return [r for r in dedup(out) if not re.search(r"领取成功|已抢光|已领完", r)]

    def read_shop_below(self):
        """拼多多详情页往下翻（最多三下），找店铺那一栏的店名（旁边是「进店逛逛」），看完滚回顶上。返回 (店名, 看到的文字)。"""
        shop, seen, n = "", [], 0
        for n in range(1, 5):
            jx.cmd_scroll(NS(direction="down", wait=1.0, frac=0.75))
            self.snap(keep=True)
            self.check_pay()
            seen += [o["t"] for o in self.lines]
            enter = [o for o in self.lines if re.search(r"进店|进入店铺|逛逛", o["t"]) and 12 < o["cy"] < 92]
            cand = [o for o in self.lines if 12 < o["cy"] < 92 and 3 <= len(o["t"].replace(" ", "")) <= 26 and is_shop(o["t"])
                    and re.search(r"店|旗舰|专营|专卖|官方", o["t"]) and o["t"].replace(" ", "") not in ("店铺", "进店")
                    and not re.search(r"进店|逛逛|全部|查看|更多|店铺优惠|同店|好评|评价|已拼|关注|收藏|问题|推荐", o["t"])]
            near = [o for o in cand if any(abs(o["cy"] - e["cy"]) < 6 for e in enter)]
            pick = (near or cand)[:1]
            if pick:
                shop = clean_shop(pick[0]["t"])
                self.log(f"店铺：{shop}")
                break
        for _ in range(n):
            jx.cmd_scroll(NS(direction="up", wait=0.5, frac=0.75))
        self.snap()
        return shop, seen

    def confirm_spec(self, cards):
        """天猫等的标题常不写容量。标题没写容量、但型号对得上的里面最便宜那件（比已确认的最低价还便宜才看），
        点进详情页：详情页上出现的容量里最小的就是要的规格（列表价是最低配的「起」价），就算同款。"""
        if not self.spec or self.details <= 0:
            return
        want = hz.spec_want(self.spec)
        unk = [c for c in cards if c.get("spec_unknown") and c.get("first") and c["title"]]
        ok = [c["price"] for c in cards if c["match"]]
        if not want or not unk or (ok and min(unk, key=lambda c: c["price"])["price"] >= min(ok)):
            return
        c = min(unk, key=lambda c: c["price"])
        self.log(f"¥{c['price']:g} 那件标题没写容量，点进详情页确认是不是 {self.spec}")
        try:
            c["detail"] = self.read_detail(c, more=True)
        except Skip as e:
            c["detail"] = {"error": str(e)}
        try:
            self.leave_detail()
        except Skip as e:
            self.log(str(e))
        have = hz.spec_values(self.spec, " ".join((c["detail"] or {}).get("_texts") or []))
        if have and min(have) == want[0]:
            c["match"], c["why"] = True, ""
            c["note"] = "；".join(x for x in (c["note"], f"标题没写容量，详情页确认最低配是 {self.spec}") if x)
            sanity_check(cards)
        else:
            unit = "ml" if hz.volumes(self.spec) else "GB"
            c["why"] = f"标题没写容量，详情页上也没确认是 {self.spec}" + (f"（看到 {'、'.join(f'{x:g}{unit}' for x in sorted(set(have)))}）" if have else "")

    def spec_from_detail(self, c):
        """商品页上选中的规格和要的不一样（列表上写 256GB、点进去选中的是 512GB 那档）：这件不算。"""
        d = c.get("detail") or {}
        if d.get("spec_ok") is False:
            unit = "ml" if hz.volumes(self.spec) else "GB"
            c["match"], c["why"] = False, f"商品页上选中的规格是 {'、'.join(f'{x:g}{unit}' for x in d.get('spec_seen') or [])}，不是 {self.spec}"
            self.log(f"¥{c['price']:g} 那件：{c['why']}")

    def pdd_shops(self, cards):
        """拼多多的列表上不写店铺名：型号规格对得上的，从便宜到贵点进详情页看店铺（顺便读券和优惠），
        找到第一家官方店（旗舰店 / 自营 / 直营）就停。看过不是官方店的、没来得及看的，都不算。"""
        tries = self.details + 1 if self.details > 0 else 0
        found = False
        # 拼多多双列卡片的标题只有一行，常把容量截掉：型号对得上、只差容量的也点进去看（商品页上核对规格）
        for c in cards:
            if c.get("spec_unknown") and not c["match"]:
                c["match"], c["_spec_pending"] = True, True
            if c.pop("title_cut", False):  # 2.17：网格卡片标题被截断：进商品页看完整标题
                c["match"], c["_model_pending"], c["_spec_pending"] = True, True, True
        ok_prices = sorted(c["price"] for c in cards if c["price"] > 0)
        mid = ok_prices[len(ok_prices) // 2] if ok_prices else 0
        seen = []  # 2.17：同一件在几屏里都读到了（标题每次认得稍有不同）：价格一样、标题相像的只点一次
        for c in sorted([c for c in cards if c["match"]], key=lambda c: c["price"]):
            if mid and c["price"] < mid * 0.4:  # 「限时狂补600元」这类认错的价格
                c["match"], c["why"] = False, "价格离谱，多半是把补贴金额认成了价格"
                continue
            twin = next((d for d in seen if abs(d["price"] - c["price"]) < 0.01
                         and difflib.SequenceMatcher(None, d["title"], c["title"]).ratio() >= 0.6), None)
            if twin and not c.get("detail"):
                c["match"], c["why"], c["shop"] = twin["match"], twin["why"] or "和前面那件是同一件", twin.get("shop", "")
                c.pop("_model_pending", None), c.pop("_spec_pending", None)
                continue
            seen.append(c)
            if found or tries <= 0 or self.exit_stuck or not c.get("first"):
                c["match"], c["why"] = False, "拼多多列表上不写店铺名，没点进详情页确认是不是官方店"
                continue
            if not c.get("detail"):
                tries -= 1
                self.log(f"¥{c['price']:g} 那件点进详情页看店铺是不是官方店")
                try:
                    c["detail"] = self.read_detail(c)
                except Skip as e:
                    c["detail"] = {"error": str(e)}
                    self.log(f"没看成详情页：{e}")
                try:
                    self.leave_detail()
                except Skip as e:
                    self.log(str(e))
                    self.exit_stuck = True
            shop = (c.get("detail") or {}).get("shop") or ""
            if shop and not hz.official({"shop": shop}, "拼多多"):  # 2.17：先看店：不是品牌旗舰店 / 拼多多自营，就不用再核对型号规格
                c.pop("_model_pending", None), c.pop("_spec_pending", None)
                c["shop"], c["match"], c["why"] = shop, False, f"{hz.NOT_OFFICIAL_WHY}（店铺：{shop}）"
                continue
            if c.pop("_model_pending", False):
                head = (c.get("detail") or {}).get("head") or ""
                if head and hz.matches(self.keyword, head, "拼多多") and not PDD_USED.search(head):
                    c["title"] = clean_title([{"t": head}]) or head[:60]
                    if self.spec and hz.spec_ok(self.spec, head) and hz.spec_values(self.spec, head):
                        c["_spec_pending"] = False  # 完整标题里写了规格，对上了
                else:
                    c.pop("_spec_pending", None)
                    c["match"], c["why"] = False, f"商品页上的完整标题不是「{self.keyword}」" if head else "商品页上没认出完整标题"
                    continue
            self.spec_from_detail(c)
            if c.pop("_spec_pending", False) and (c.get("detail") or {}).get("spec_ok") is not True:
                c["match"], c["why"] = False, f"标题没写容量，商品页上也没确认是 {self.spec}"
            if not c["match"]:
                continue
            shop = (c.get("detail") or {}).get("shop") or ""
            c["shop"] = shop
            # 2.17：拼多多只比品牌官方旗舰店和拼多多自营；百亿补贴里「商品由第三方商家提供」、专营店等第三方店一律不算（用户要求）
            third = any("第三方商家" in t for t in (c.get("detail") or {}).get("_texts") or [])
            if shop and hz.official(c, "拼多多") and not (third and "旗舰店" not in shop and "自营" not in shop):
                found = True
            elif shop:
                c["match"], c["why"] = False, f"{hz.NOT_OFFICIAL_WHY}（店铺：{shop}）"
            else:
                c["match"], c["why"] = False, ("商品由第三方商家提供，不是品牌官方旗舰店或拼多多自营" if third
                                               else "详情页上没找到店铺名，不知道是不是品牌官方旗舰店")

    def leave_detail(self):
        """关掉面板、退出详情页，回到结果页（面板开着时，点返回键的位置会点在遮罩上，先把面板关掉）。
        正常最多点 3 下（小窗、面板、返回）。点满还没回去，说明每点一下又弹出新东西（挽留卡片、相似商品）：
        不再点了，把 App 划掉，这个平台也不再看别的详情页。"""
        for _ in range(EXIT_TAPS):
            self.snap()
            self.check_pay()
            if self.results_like():
                return
            if any(w in o["t"].replace(" ", "") for o in self.lines for w in SHOT_SHEET):
                # 淘宝百亿补贴页：按返回会截一张页面图、弹「识别截图」找相似浮层；再按只是关掉浮层，接着又弹，永远退不出去
                self.log("退出时弹出了「识别截图」找相似浮层，再按返回只会一直弹：不再点")
                break
            close = self.corner_close() if not any(w in self.text_all() for w in PANEL_WORDS) else None
            if close:
                self.tap(*close, "右上角的 ×（关掉悬浮的直播小窗 / 直播间）", wait=2.0)
            else:
                self.tap(*BACK, "左上角返回键（关面板 / 退出详情页）", wait=2.0)
        self.snap()
        if self.results_like():
            return
        self.stuck_count = getattr(self, "stuck_count", 0) + 1
        if self.stuck_count >= 2 or not self.restart_app(self.app_name):
            self.exit_stuck = True
            self.log("退不回结果页：这个平台不再看其他详情页")
            jx.cmd_home(NS(wait=1.0))
            return
        self.lost_results = True  # 已经不在结果页了：下一件回首页重新搜
        self.shot_popups = 0
        self.log("把 App 划掉重开了，下一件回首页重新搜")

    def judge(self, c, platform):
        if not c["title"]:
            return False, "没识别到标题"
        if not c["shop"] and platform != "拼多多":  # 拼多多的结果页上不写店铺名
            return False, "没识别到店铺名（可能是图片里的宣传价，或卡片被屏幕边缘截断）"
        if platform != "闲鱼" and any(w in c["shop"] for w in hz.OVERSEAS_SHOP):
            return False, "海外 / 全球购 / 代购店，不是国行正品渠道"
        if platform != "闲鱼" and any(w in c["shop"] for w in hz.USED_SHOP):
            return False, "二手店铺，不是全新"
        # 2.15：只要官方认证的店（旗舰店 / 自营 / 直营店），代购、个人小店、专营店直接不要。
        # 拼多多列表上不写店铺名：先按型号规格判断，再点进详情页看店铺（pdd_shops）
        if platform not in ("闲鱼", "拼多多") and not hz.official(c):
            return False, hz.NOT_OFFICIAL_WHY
        if not hz.matches(self.keyword, c["title"], platform):
            return False, hz.why_not(self.keyword, c["title"], platform)
        if c.get("garbled"):
            return False, "价格数字中间有空格，可能识别错了"
        # 淘宝价格后面的「/10kg」「/100g」是这个价对应的规格，比标题可靠（标题写 100g、价格是 /80g 那档的也有）
        per = self.spec and c.get("per") and hz.spec_values(self.spec, c["per"]) and c["per"]
        if per and not hz.spec_ok(self.spec, per):
            return False, f"规格不符（这个价是「/{per}」那一档的）"
        if self.spec and not hz.spec_values(self.spec, c["title"]):
            if per:
                c["note"] = "；".join(x for x in (c["note"], f"标题没写容量，价格后面标着「/{per}」") if x)
                return True, ""
            c["spec_unknown"] = True  # 型号对得上，只是标题没写容量：最便宜的那件会点进详情页确认
            return False, "标题里没写容量，无法确认规格"
        if not hz.spec_ok(self.spec, c["title"]):
            return False, "规格不符（或一个链接挂了几种容量 / 是套装）"
        return True, ""

    def run_platform(self, name):
        cfg = APPS[name]
        self.steps, self.shots = [], []
        self.app_name = name
        self._restarted = False
        self.exit_stuck = False
        self.stuck_count = 0
        self.lost_results = False
        self.shot_popups = 0
        # 录屏流可用时手机不会当成截屏，不用等浮层；只有退回普通截图时，淘宝才一开始就按「等浮层、关浮层」的模式
        self.sheet_mode = name == "淘宝" and not jx.stream_ok()
        result = {"platform": cfg["platform"], "app": name}
        try:
            self.open_app(name)
            self.go_home_tab(name)
            self.search()
            result["input"] = {"粘贴": f"粘贴「{self.typed}」", "中文": f"中文「{self.typed}」",
                               "拼音联想": f"拼音「{self.pinyin}」，点了联想词「{self.typed}」（中文没打对）",
                               "拼音": f"拼音「{self.pinyin}」（中文没打对，联想里也没有）",
                               "英文": self.typed}[self.input_used]
            self.snap(keep=True)
            if any(w in o["t"] for o in self.lines if 25 < o["cy"] < 85 for w in POPUP_HINTS):
                result["notice"] = "首屏有弹窗遮挡（优惠券/红包），被盖住的商品读不到；弹窗里的金额会被读成无标题条目，已排除"
                self.log("首屏有弹窗遮挡")
            cards = self.read_cards(cfg["platform"], shot=1)
            # 固定读 SCREENS 屏：每次往下翻约 0.6 屏，相邻两屏有重叠，重复的卡片按「价格 + 店铺 + 标题开头」去重
            for shot in range(2, SCREENS + 1):
                jx.cmd_scroll(NS(direction="down", wait=1.5))
                self.log(f"往下翻一屏（第 {shot} 屏）")
                self.snap(keep=True)
                seen = {card_key(c) for c in cards}
                cards += [c for c in self.read_cards(cfg["platform"], shot=shot) if card_key(c) not in seen]
            sanity_check(cards)
            self.screen = SCREENS  # 现在停在第几屏（看详情时原路滚回去）
            if name != "拼多多":
                self.confirm_spec(cards)
            if name == "拼多多":
                self.pdd_shops(cards)
            # 从下往上看（离当前位置最近的先看），这样只用往上滚，不用重新搜
            for c in sorted(detail_targets(cards, self.details), key=lambda c: -c["shot"]):
                if self.exit_stuck:
                    break
                if c.get("detail"):
                    continue
                try:
                    c["detail"] = self.read_detail(c)  # 读到的优惠先存下来，退出详情页出问题也不丢
                except Skip as e:
                    c["detail"] = {"error": str(e)}
                    self.log(f"没看成详情页：{e}")
                self.spec_from_detail(c)
                try:
                    self.leave_detail()
                except Skip as e:
                    self.log(str(e))
                    break  # 回不到结果页就不再看下一件；已经读到的列表价、优惠都不受影响
            result.update(status="ok", cards=cards)
        except Skip as e:
            self.snap(keep=True)
            # 查到一半镜像断了（手机被拿起来用 / 镜像暂停）：如实报「断开」，不要报成页面问题
            if len(self.lines) <= 12 and any(w in self.text_all() for w in DISCONNECTED + PAUSED):
                raise jx.Fail("iPhone 镜像中途断开了（可能是手机被拿起来用了）：请把 iPhone 锁屏放在 Mac 旁边，在镜像窗口里点连接")
            result.update(status="skipped", reason=str(e))
        finally:
            result.update(steps=self.steps, screenshots=self.shots)
        return result


# 卡片上的标签只说明「有这个活动」，不代表这个价格必须满足它：措辞写成「支持…」「有…」，避免被读成硬性条件
NOTE_WORDING = {"以旧换新": "支持以旧换新", "焕新补贴": "有焕新补贴", "国补": "有国补", "政府补贴": "有政府补贴", "新人": "新人价",
                "学生": "学生价", "预售": "预售价（现在付定金，到货再付尾款）"}

PAUSED = ("Connection Paused", "连接已暂停")
CONNECTING = ("Connecting", "正在连接", "连接中")
RECONNECT = ("Connect", "Try Again", "连接", "重试", "重新连接")  # 镜像断开页上的重连按钮
IN_USE = ("iPhone in Use", "iPhone 正在使用", "iPhone正在使用")
UNLOCK = ("Unlock your iPhone", "解锁你的 iPhone", "解锁你的iPhone", "Unlock iPhone")
DISCONNECTED = IN_USE + UNLOCK + ("Lock your iPhone", "Try Again", "锁定你的 iPhone", "重试", "Get Started", "开始使用",
                                   "Continue", "继续", "Couldn't", "无法", "not available", "不可用")
CONNECT_STEPS = ("按这几步连上：\n"
                 "1. iPhone 锁屏，放在 Mac 旁边（正在用的手机连不上）；\n"
                 "2. Mac 和 iPhone 登录同一个 Apple 账户，两边都打开 Wi-Fi 和蓝牙；\n"
                 "3. 在 Mac 上的「iPhone 镜像」窗口里点「Try Again / 重试」或「Continue / 继续」，第一次用按提示在 iPhone 上确认、在 Mac 上输入密码或用触控 ID；\n"
                 "4. 镜像窗口里出现手机主屏幕就是连上了，再让我重新查价。\n"
                 "（iPhone 镜像需要 macOS 15 以上、iOS 18 以上。）")


def connect_help(text):
    if any(w in text for w in IN_USE):
        head = ("iPhone 镜像连不上：你的 iPhone 正在使用中（脚本已经点过「Connect」重连，还是不行）。"
                "请把手机锁屏，然后让我再查一次。")
    elif any(w in text for w in UNLOCK):
        head = "iPhone 镜像连不上：iPhone 重启后要先解锁一次。请在手机上解锁一次再锁屏，然后在镜像窗口里点「Try Again / 重试」。"
    else:
        head = "iPhone 镜像还没连上手机（镜像窗口里显示：" + (text[:60] or "空白") + "）。"
    return head + "\n" + CONNECT_STEPS


def ensure_connected(r):
    """查价前的检查：iPhone 镜像没开就帮用户打开；窗口最小化了就调出来；正在连接就等一会儿；
    闲置暂停（Connection Paused）就点 Resume；真连不上就报错，错误信息里写清楚用户该怎么做。
    只在画面文字很少（是镜像的系统提示页，不是 App 页面）时才判断，避免 App 里的字误判。"""
    if not jx.mirror_running():
        jx.launch_mirror()
        r.log("iPhone 镜像没开，帮你打开了")
        time.sleep(3)
    for attempt in range(8):
        try:
            jx.find_window()
            break
        except jx.Fail:
            if attempt in (0, 4):
                jx.launch_mirror()  # 窗口最小化或被关掉时，重新打开会把窗口调出来
            time.sleep(1.5)
    else:
        raise jx.Fail("打开了 iPhone 镜像，但找不到它的窗口。如果镜像窗口明明开着，多半是 WorkBuddy 缺「屏幕录制」权限"
                      "（系统设置 → 隐私与安全性 → 屏幕录制，打开 WorkBuddy 后重启它）。\n" + CONNECT_STEPS)
    reconnects = 0
    for _ in range(8):
        r.snap()
        if len(r.lines) > 12:
            return
        text = r.text_all()
        # 手机用完锁屏了，镜像也不会自己重连，停在「iPhone in Use … Connect」：点一次「Connect / Try Again」重连
        btn = next((o for o in r.lines if o["t"].strip() in RECONNECT), None)
        if btn and reconnects < 2 and not any(w in text for w in PAUSED):
            reconnects += 1
            r.tap(btn["cx"], btn["cy"], f"iPhone 镜像的「{btn['t'].strip()}」（重新连接）", wait=6)
            continue
        if any(w in text for w in PAUSED):
            btn = next((o for o in r.lines if o["t"].strip() in ("Resume", "继续", "恢复")), None)
            if btn:
                r.tap(btn["cx"], btn["cy"], "iPhone 镜像的「Resume」（恢复连接）", wait=6)
                continue
        if any(w in text for w in CONNECTING) or not text.strip():
            r.log("iPhone 镜像正在连接，等一下")
            time.sleep(2.5)
            continue
        if any(w in text for w in DISCONNECTED):
            raise jx.Fail(connect_help(text))
        return
    raise jx.Fail(connect_help(r.text_all()))


OFFICIAL_SHOP = hz.OFFICIAL_SHOP
SCREENS = 5  # 每个平台读几屏搜索结果


PAY_WORDS = ("去支付", "提交订单", "确认订单", "立即支付", "确认付款", "收银台", "支付密码")
SHEET_BUTTONS = ("开心收下", "立即使用", "收下", "去使用", "立即领取", "一键领取", "全部领取", "立即抢", "去领取")
MODAL_HINTS = ("红包", "券", "立减", "无门槛", "福利", "恭喜", "获得", "可用", "即将失效", "补贴")
PROMO_RE = re.compile(r"券|减|減|补贴|補贴|红包|折|返|赠|到手|优惠|淘金币|PLUS|礼金|立享|可再享|已享受|满\d|加补")
JUNK_RE = re.compile(r"有效期|无使用门槛|已享优惠$|以上优惠|当前券后价|一键开通|合计|实际优惠金额|人说|评价|已售|保质期|送达|运费|"
                     r"退换|退货|查看详情|开心收下|加入购物车|立即购买|领券购买|距结束|后过期|\d+:\d+:\d+|全部优惠|更多优惠|"
                     r"优惠详情|^优惠\s*[①1]?\s*[X×]?$|已享受优惠\s*可再享优惠|使用说明|低价热卖|先到先得|^优惠前\s*店铺优惠")
PANEL_TRIGGER = re.compile(r"^已享受|(加补后|国补后|券后价?|到手价|补贴后|优惠后|折后|入会后)约?[¥￥]\d")
NO_TAP = re.compile(r"领取|领券|收下|立即|去参与|开通|使用")
PANEL_WORDS = ("优惠前", "店铺优惠", "平台优惠", "已享受优惠", "购买立减", "优惠明细", "全部优惠", "更多优惠", "可再享优惠")
# 详情页上点开优惠券列表的那一行左边的标签字
COUPON_LABEL = re.compile(r"^(领券|领劵|优惠券|优惠|促销|满减|券|店铺券|优惠活动)[>›＞]?$")  # 「政府补贴」那条是带 × 的横幅，不是券
BASE_PRICE = re.compile(r"(优惠前|京东价|日常价|页面价|单买价)\s*[:：]?\s*[¥￥]\s*(\d[\d,]*(?:\.\d+)?)")
AMOUNT_ONLY = re.compile(r"^[\s+\-=]*[¥￥]\s*[\d,.]+[\s+\-=]*$")
# 「店铺入会后¥9688.23起」是会员价（汇总时按身份价处理）
DETAIL_PRICE = re.compile(r"(到手价?|券后价?|加补后|国补后|补贴后|优惠后|折后|入会后)[\s•·]*约?\s*[¥￥]\s*(\d[\d,]*(?:\.\d+)?)")
PRICE_THEN_LABEL = re.compile(r"^[¥￥]\s*(\d[\d,]*(?:\.\d+)?)\s*[\u4e00-\u9fff]{0,3}?(到手价|券后价|加补后|补贴后|优惠后)")


def detail_targets(cards, n):
    """要点进详情页看的商品，按顺序：官方店（旗舰店 / 自营 / 直营）里最便宜的、再便宜一点的那件（列表价第二低的官方店常常券更多），
    重复的只算一次。最多 n 件。（非官方店在 judge 里已经排除了）"""
    ok = [c for c in cards if c["match"] and c.get("first")]
    if not ok or n <= 0:
        return []
    return sorted([c for c in ok if hz.official(c)] or ok, key=lambda c: c["price"])[:n]


def tidy(t):
    """去掉开头单独一个字符的图标（「4 首单礼金…」「补 本商品…」）、结尾的箭头和 ×。"""
    t = re.sub(r"^(\S\s+)+(?=\S{2})", "", t.strip())
    return re.sub(r"[\s＞>〉›X×，,]+$", "", t).strip()


def rows(lines, tol=1.6):
    """把同一行（纵向位置差不多）的几段文字按从左到右拼成一行，如「6元 超市加补券 满50元可用」。"""
    out, cur = [], []
    for o in sorted(lines, key=lambda o: o["cy"]):
        if cur and abs(o["cy"] - cur[0]["cy"]) > tol:
            out.append(cur)
            cur = []
        cur.append(o)
    if cur:
        out.append(cur)
    return [tidy(re.sub(r"\s+", " ", " ".join(o["t"].strip() for o in sorted(r, key=lambda o: o["x"])))) for r in out]


def amounts(lines):
    """优惠明细里「¥21.1」下面写着「店铺优惠」这种上下两行的，配成「店铺优惠 ¥21.1」。
    几个标签常被认成一行（「限量补贴 淘金币」）：每个词按它在那一行里的位置估计横坐标，取离金额最近的词。"""
    out = []
    for o in lines:
        m = re.match(r"^[\s+\-=]*([¥￥]\s*[\d,.]+)\s*$", o["t"])
        if not m:
            continue
        best = None
        for p in lines:
            if not (1.5 < p["cy"] - o["cy"] < 7 and p["x"] - 3 < o["cx"] < p["x"] + p["w"] + 3) \
                    or re.search(r"[¥￥]", p["t"]) or len(p["t"].strip()) > 14:
                continue
            label, pos = p["t"].strip(), 0
            for tok in label.split():
                i = label.find(tok, pos)
                pos = i + len(tok)
                tx = p["x"] + p["w"] * (i + len(tok) / 2) / max(len(label), 1)
                d = abs(tx - o["cx"]) + (p["cy"] - o["cy"]) * 0.3  # 横向最近，同样近时取上面那行
                if len(tok) >= 2 and (best is None or d < best[0]):
                    best = (d, tok)
        if best:
            label = re.sub(r"[^\w\u4e00-\u9fff]+$", "", best[1])  # 去掉结尾的「［」「^」等杂符号
            label = re.sub(r"(?<=[\u4e00-\u9fff])[vV]$", "", label)  # 下拉箭头「⌄」常被认成 v
            if not JUNK_RE.search(label):
                out.append(f"{label} {m.group(1).replace(' ', '')}")
    return out


def detail_prices(lines):
    """详情页上写明的到手价、券后价、加补后价 → [(说法, 价格)]。保留空格再匹配：
    「¥52.47 | 优惠前」里的 | 常被认成 1，去掉空格会粘成 52.471。"""
    out = []
    for r in rows(lines) + [o["t"] for o in lines] + amounts(lines):
        t = r.strip()
        if re.search(r"预估|换新|以旧|估算|最高", t):  # 「补贴后预估¥6084」是以旧换新后的估算价，不是到手价
            continue
        for m in DETAIL_PRICE.finditer(t):
            out.append((m.group(1), float(m.group(2).replace(",", ""))))
        m = PRICE_THEN_LABEL.match(t)
        if m:
            out.append((m.group(2), float(m.group(1).replace(",", ""))))
    return [p for p in out if p[1] > 0]


def base_prices(texts):
    """详情页上写明的「优惠前 / 京东价」：算优惠从它开始减（不会和到手价里已经减过的重复）。"""
    out = []
    for t in texts:
        for m in BASE_PRICE.finditer(str(t)):
            out.append((m.group(1), float(m.group(2).replace(",", ""))))
    return out


def formula_items(lines):
    """京东优惠面板里的算式「= ¥9999 − 优惠券 ¥300 − 立减 ¥50」：拆成「优惠前 ¥9999」「优惠券 ¥300」…，和淘宝的「优惠前 / 店铺优惠」一个格式。"""
    out = []
    for r in rows(lines):
        t = r.replace(" ", "")
        # 「= ¥9999 − 优惠券 ¥300」：开头的「=」常被识别漏掉，只要是「¥数字 − 名目 ¥数字」的样子就算
        if not re.match(r"^[=＝]?[¥￥]\d[\d,.]*[-－−—][一-鿿A-Za-z]{2,8}[¥￥]\d", t):
            continue
        m = re.match(r"^[=＝]?\s*[¥￥]\s*(\d[\d,]*(?:\.\d+)?)", t)
        if not m:
            continue
        out.append(f"优惠前 ¥{m.group(1).replace(',', '')}")
        for label, v in re.findall(r"[-－−—]\s*([一-鿿A-Za-z]{2,8})\s*[¥￥]\s*(\d[\d,]*(?:\.\d+)?)", t):
            out.append(f"{label} ¥{v.replace(',', '')}")
    return out


def panel_rules(lines):
    """优惠面板上的规则行 + 优惠券卡片（大字金额配上下面的「满X可用」）。"""
    return coupon_pairs(lines) + [r for r in rows(lines) if PROMO_RE.search(r) and not JUNK_RE.search(r)
                                  and not AMOUNT_ONLY.match(r) and re.search(r"\d", r)]


def coupon_pairs(lines):
    """优惠券卡片上大字金额「¥300」和下面一行小字「满3000元可用 / 无门槛」是分开的两段：配成「¥300 满3000元可用」。
    同一排右边的券名（「店铺券」「平台券」「东券」）也带上，算的时候分得清是哪一种券。"""
    out = []
    for o in lines:
        m = re.match(r"^[¥￥]?\s*(\d+(?:\.\d+)?)\s*元?$", o["t"].strip())
        if not m or not re.search(r"[¥￥元]", o["t"]):
            continue
        below = [p for p in lines if 0.8 < p["cy"] - o["cy"] < 6 and abs(p["cx"] - o["cx"]) < 14
                 and re.search(r"满\s*\d+.*(可用|使用|减)|无门槛", p["t"])]
        if not below:
            continue
        cond = min(below, key=lambda p: p["cy"])["t"].strip()
        # 券名：金额正下方的小角标（京东「白条券」「东券」）或同一排右边的字（「店铺券」「平台券」）
        name = next((p["t"].strip() for p in lines if p is not o and (abs(p["cx"] - o["cx"]) < 10 and 0.5 < p["cy"] - o["cy"] < 4.5
                                                                    or abs(p["cy"] - o["cy"]) < 3 and p["x"] > o["x"] + o["w"] + 2)
                     and re.search(r"券|红包|补贴", p["t"]) and len(p["t"]) <= 20), "")
        out.append(f"{name} ¥{m.group(1)} {cond}".strip())
    return out


def plausible_prices(prices, list_price):
    """只留和列表价差不多的详情页价格：「券后减 20」这种金额混进来取最低，整个详情价都会被丢掉。
    小数点常被识别丢（8999.1 → 89991），除回来还对得上就用。"""
    out = []
    for label, p in prices:
        for v in (p, p / 10, p / 100):
            if list_price * 0.5 < v < list_price * 1.5:
                out.append((label, v))
                break
    return out


def dedup(items):
    """去重：去掉空格后一样的、或者被别的条目包含的。"""
    norm = [re.sub(r"\s+", "", x) for x in items]
    out = []
    for i, x in enumerate(items):
        if not norm[i] or any(norm[i] == norm[j] and j < i for j in range(len(items))):
            continue
        if any(j != i and norm[i] in norm[j] and len(norm[j]) > len(norm[i]) for j in range(len(items))):
            continue
        out.append(x.strip())
    return out


def card_key(c):
    return (c["price"], re.sub(r"\s+", "", c["shop"])[:6], re.sub(r"\s+", "", c["title"])[:8])


def sanity_check(cards):
    """1. 同款里价格比中位数低一半以上的，多半是识别错误、定金或配件价，不算；高一倍以上的多半是小数点没认出来
       （「¥9399.7」认成 93997），也不算。
    2. 第三方店比同平台自营/官方/授权店的最低价还低 40% 以上的，可能不是正品，不算。"""
    ok = sorted(c["price"] for c in cards if c["match"])
    if len(ok) >= 2:
        med = ok[len(ok) // 2]
        for c in cards:
            if c["match"] and c["price"] < med * 0.5:
                c["match"], c["why"] = False, f"价格比同款中位数 ¥{med:g} 低一半以上，可能识别错误或是定金"
            elif c["match"] and c["price"] > med * 2:
                c["match"], c["why"] = False, f"价格比同款中位数 ¥{med:g} 高一倍以上，可能是小数点没认出来"
    offi = [c["price"] for c in cards if c["match"] and hz.official(c)]
    if offi:
        ref = min(offi)
        for c in cards:
            if c["match"] and not hz.official(c) and c["price"] < ref * 0.6:
                c["match"], c["why"] = False, f"第三方店价格比自营/官方店最低 ¥{ref:g} 低 40% 以上，可能不是正品"


def title_part(t):
    """标题里的一段：去掉角标后返回文字；销量、榜单、参数格子这类噪声行返回空字符串。"""
    t = re.sub(r"[！!]?\s*问京言", "", t).strip()  # 京东右侧悬浮的「问京言」按钮常和标题认成一行
    n = len(t.replace(" ", ""))
    if any(w in t for w in DROP_LINES) or "¥" in t or "￥" in t or re.search(r"\$\s*\d", t) or (any(w in t for w in ATTR_SHORT) and n <= 14):
        return ""
    t = re.sub(BADGES, "", t)
    t = re.sub(r"\bJ(?=iPhone)", "", t)
    if len(t.replace(" ", "")) <= 4 and not re.search(r"\d+\s*(GB|TB|G)\b|色", t, re.I):
        return ""
    return t


def clean_title(lines):
    t = " ".join(filter(None, (title_part(o["t"]) for o in lines))).strip()
    return re.sub(r"SK\s*[-－]\s*[I1l|]{1,2}(?![A-Za-z0-9])", "SK-II", t)  # 「SK-I［定制礼物］」是 SK-II 认错了


SHOP_JUNK = re.compile(r"^(\S*\d\S*[万千]\S*|本月上新|品牌旗舰|\S*直播中|新增\S*|月涨粉\S*|回头客\S*|店铺粉丝\S*|[逛买看关][过注]的店|常买的店|本周上新|"
                       r"\d+(\.\d+)?k?m|[◎⑨©®○•·。.Il|丨]+)$")


def clean_shop(t):
    """去掉店铺名前面的距离、粉丝数、直播中等杂项和末尾的箭头。"""
    t = re.sub(r"(进店)?\s*(\.\.\.|…|⋯|\.\.)?\s*[>＞〉›]+\s*$", "", t.strip())
    t = re.sub(r"^.*?直播中\s*", "", t)                                  # 「al 直播中」「。1 直播中」
    t = re.sub(r"^[◎⑨©®○•·。.\s]*\d+(\.\d+)?\s?k?m\s+", "", t)          # 「◎1.4km」「113m」
    t = re.sub(r"\s*[.…⋯]+$", "", t)                                       # 被截断的「官方…」
    t = re.sub(r"[，,。、；;：:\s]+$", "", t)                                # 结尾的标点
    t = re.sub(r"SK\s*[-－]?\s*[I1l|]{1,2}(?![A-Za-z0-9])", "SK-II", t)           # 「SK-I|」「SK-I1」是 SK-II 认错了
    t = re.sub(r"^(回头客|店铺粉丝|月涨粉)\s*[\d.]+[万千]?\+?\s*", "", t)          # 粘在店名前面的「回头客10万+」
    t = re.sub(r"^(\d+年老店|好评率\s*\d+%)\s*", "", t)                                       # 「20年老店 劈腿大师」
    t = re.sub(r"^由?\s*「|」?\s*运营$", "", t).strip("「」 ")                                       # 拼多多百亿补贴「由「XX专营店」运营」
    tokens = t.split()
    while len(tokens) > 1 and SHOP_JUNK.match(tokens[0]):
        tokens.pop(0)
    return " ".join(tokens).strip()  # 被截断的「…自营旗」在 huizong.clean_shop 里补全（这里要留原样，回结果页找这件时要对店名）


def main():
    ap = argparse.ArgumentParser(description="手机比价一键自动查价")
    ap.add_argument("keyword")
    ap.add_argument("--spec", default="")
    ap.add_argument("--platforms", nargs="+", default=["京东", "淘宝"])
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--details", type=int, default=3, help="每个平台点进几件商品的详情页看券和优惠（0 = 不看）")
    a = ap.parse_args()

    bad = re.sub(r"[\sA-Za-z0-9]|" + CJK.pattern, "", a.keyword + a.spec)
    if bad or not (a.keyword + a.spec).strip():
        print(json.dumps({"ok": False, "error": f"关键词只能有中文、英文字母、数字和空格，去掉「{bad}」再试"}, ensure_ascii=False))
        return 2
    unknown = [p for p in a.platforms if p not in APPS]
    r = Runner(a.keyword, a.spec, a.details)
    results, code = [], 0
    try:
        jx.phone_lock()
        ensure_connected(r)
        for p in a.platforms:
            if p in APPS:
                results.append(r.run_platform(p))
    except SafetyStop as e:
        code = 3
        shots = list(r.shots)
        try:
            shots.append(jx.cmd_shot(NS(out=None))["image"])
        except Exception:
            pass
        results.append({"platform": "—", "status": "safety_stop", "reason": str(e), "steps": r.steps, "screenshots": shots})
    except jx.Fail as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 1
    finally:
        if code != 3:
            try:
                jx.cmd_home(NS(wait=1.0))
            except jx.Fail:
                pass

    for res in results:
        for c in res.get("cards", []):
            if isinstance(c.get("detail"), dict):
                c["detail"].pop("_texts", None)
    data = {"keyword": a.keyword, "spec": a.spec, "time": time.strftime("%Y-%m-%d %H:%M"), "source": "zidong.py",
            "searched": [res["platform"] for res in results if res["status"] == "ok"],
            "apps": [res["app"] for res in results if res.get("app")],
            "items": [dict({k: c[k] for k in ("platform", "title", "price", "original", "label", "shop", "note")}, source="手机App",
                           **({"detail": c["detail"]} if c.get("detail") else {}))
                      for res in results for c in res.get("cards", []) if c["match"]],
            "rejected": [{"platform": c["platform"], "title": c["title"], "price": c["price"], "why": c["why"]}
                         for res in results for c in res.get("cards", []) if not c["match"]],
            "skipped": [{"platform": res["platform"], "app": res.get("app"), "reason": res["reason"]} for res in results
                        if res["status"] == "skipped"] + [{"platform": p, "reason": "自动查价暂不支持这个平台"} for p in unknown]}
    os.makedirs(a.out_dir, exist_ok=True)
    out = os.path.join(a.out_dir, "mirror-%s-%06d.json" % (time.strftime("%Y%m%d-%H%M%S"), int(time.time() * 1e6) % 1000000))
    with open(out, "x", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    brief = []
    for res in results:
        r2 = {k: res[k] for k in ("platform", "status", "input", "reason", "notice", "screenshots") if k in res}
        r2["cards"] = [dict({k: c[k] for k in ("price", "label", "title", "shop", "note", "match", "why", "shot")},
                            **({"detail": c["detail"]} if c.get("detail") else {})) for c in res.get("cards", [])]
        r2["steps"] = res.get("steps", [])
        brief.append(r2)
    # 这个平台的小报告（和 huizong.py --platform-report 一样），WorkBuddy 原样发给用户就行，不用再跑一条命令
    try:
        merged = hz.load_many([out])
        best, plain, skipped, _ = hz.summarize(merged)
        report = hz.render_platform(merged, best, plain, skipped)
    except (Exception, SystemExit) as e:
        report = f"（单平台报告生成失败：{e}）"
    if code == 3:
        report = f"### ⛔ 安全停止：{next((r['reason'] for r in results if r['status'] == 'safety_stop'), '')}"
    print(json.dumps({"report": report, "ok": code == 0, "safety_stop": code == 3, "json": out, "results": brief},
                     ensure_ascii=False, indent=1))
    return code


if __name__ == "__main__":
    sys.exit(main())
