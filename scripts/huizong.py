#!/usr/bin/env python3
"""蜗牛最爽省钱 Skill · 汇总：把手机 App 实时价和 WebSearch 参考价合成一张比价表。

  1. 手机 App 实时价：zidong.py 写的 mirror-*.json（来源「手机App」）—— 唯一进结论的价格
  2. 参考价：WebSearch 查到、按 SKILL.md 规则写的 ref-*.json（每个平台都查；表里单独一列，点开是出处网页）
  （仍兼容 bijia.py --json 的接口价文件，来源「买手接口」，但 2.2 起主流程不再使用）

每个平台取实时价里最低的一条；没有实时价也没有参考价的平台不显示。
只依赖 Python 标准库，不联网。

用法：
  python3 huizong.py mirror-1.json mirror-2.json --ref ref.json
  python3 huizong.py mirror-1.json mirror-2.json --queries     # 还缺价格的平台和 WebSearch 搜索语句
  python3 huizong.py ... --json      # 给定时盯价、提醒器用：每个平台最低价和全场最低价 lowest
"""
import argparse
import itertools
import json
import sys
import re
import urllib.parse

# 平台顺序和「搜同款」网页链接：App 里拿不到商品链接，给一个网页搜索页方便用户核对
PLATFORMS = {
    "京东": "https://search.jd.com/Search?keyword={q}&enc=utf-8",
    "淘宝/天猫": "https://s.taobao.com/search?q={q}",
    "拼多多": "https://mobile.yangkeduo.com/search_result.html?search_key={q}",
    "抖音商城": "https://haohuo.jinritemai.com/views/product/search?keyword={q}",
    "唯品会": "https://category.vip.com/search-3-0-1.html?q={q}",
    "闲鱼": "https://www.goofish.com/search?q={q}",
}
ALIASES = {"淘宝": "淘宝/天猫", "天猫": "淘宝/天猫", "抖音": "抖音商城", "JD": "京东", "jd": "京东", "Taobao": "淘宝/天猫",
           "taobao": "淘宝/天猫", "淘宝天猫": "淘宝/天猫", "拼夕夕": "拼多多"}

# 注意别用单字"贴"，会误伤"补贴"
ACCESSORY_WORDS = ["膜", "壳", "贴纸", "贴膜", "镜头贴", "适用", "适配", "兼容", "配件", "充电器", "充电头",
                   "数据线", "支架", "保护套", "手机套", "收纳", "手柄", "表带", "背夹", "挂绳", "替换",
                   "滤网", "刷头", "吸头", "模型", "机模", "耳机套", "耳套", "耳塞套", "钢化", "镜头膜"]
VARIANT_SUFFIXES = ["pro", "max", "plus", "ultra", "mini", "lite", "air", "se"]
# 紧跟在型号后面、表示另一个型号的词（iPhone 16e、S25 FE、S25+、15S Pro、Mate 70 RS）
VARIANT_TOKENS = {"pro", "max", "plus", "ultra", "mini", "lite", "air", "se", "fe", "edge", "rs", "e", "s", "+", "neo", "turbo"}
NOT_FINAL = ["预售", "定金", "订金"]  # 价格不是最终到手价
USED_WORDS = ["二手", "成新", "准新", "95新", "99新", "9成新", "靓机", "租赁", "租机", "官换", "翻新",
              "展示机", "演示机", "有锁", "美版", "港版", "日版", "海外版", "全球购", "无卡槽", "无eSIM", "卡贴", "监管机",
              "国际版", "港澳版", "韩版", "欧版", "外版", "资源机", "开箱机", "样机", "拆封机", "官翻",
              "已开封", "开封机", "开封走", "国行开封", "已拆封", "已激活", "激活过", "保税", "微瑕", "瑕疵",
              "日本本土", "本土版", "日本版"]  # 化妆品常写「日本本土版」，和日版一样不是国内专柜渠道
USED_SHOP = ("二手", "99新", "95新", "9成新", "回收", "寄卖")  # 店铺名里有这些就是二手店
# 店铺名里有这些就不是国内正品渠道（海外、跨境、代购，以及开在港澳台、日韩的店）
OVERSEAS_SHOP = ("海外", "全球", "国际", "跨境", "境外", "海购", "免税", "买手", "代购", "香港", "台北", "台湾", "澳门", "日本", "韩国")
# 一个链接里港版、国行混着卖（「港国行全新」「港版/国行」「国行/港版」）：列出来的低价多半是港版的
HK_MIX = re.compile(r"港\s*[/／、|]?\s*(?:版\s*[/／、|]?\s*)?国行|国行\s*[/／、|]\s*港")
# 英文品牌 ↔ 标题里常见的中文写法（关键词写英文时标题里常只写中文，反过来也一样）
BRAND_CN = {"apple": "苹果", "dyson": "戴森", "xiaomi": "小米", "huawei": "华为", "sony": "索尼", "samsung": "三星",
            "honor": "荣耀", "oppo": "oppo", "vivo": "vivo", "lenovo": "联想", "nintendo": "任天堂", "philips": "飞利浦",
            "panasonic": "松下", "midea": "美的", "gree": "格力", "haier": "海尔", "bose": "bose", "canon": "佳能",
            "nikon": "尼康", "fujifilm": "富士", "dji": "大疆", "redmi": "红米"}
CN_BRAND = {v: k for k, v in BRAND_CN.items() if not v.isascii()}
# 带符号的品牌：关键词里只能写成 SKII，标题里是 SK-II / sk2，识别还常把 II 认成 I1、ll（都按去掉空格和连字符后比）
BRAND_ALIASES = {"skii": ("skii", "sk2", "ski1", "skil", "skll", "sk11", "skli")}


def brand_in(brand, title):
    t = _norm(title)
    return brand in t or any(a in t for a in BRAND_ALIASES.get(brand, ())) or BRAND_CN.get(brand, "\0") in title


def _norm(s):
    return re.sub(r"[\s\-_]+", "", (s or "").lower())


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def norm_title(title):
    """「AirPods Pro（第三代）」「Pro 3代」统一写成「Pro 3」，再去空格转小写。"""
    def gen(m):
        n = m.group(1)
        return str(CN_NUM.get(n, n))
    t = re.sub(r"[（(]?\s*第?\s*([一二三四五六七八九十]|\d{1,2})\s*代\s*[）)]?", lambda m: gen(m), title or "")
    return _norm(t)


def flex(s):
    """把 s 变成正则：字和字之间允许空格、连字符（识别误差或写法不同）。"""
    return r"[\s\-_]*".join(re.escape(c) for c in s)


def spaced_title(title):
    """小写、「第三代」→「3」，但保留空格（用来区分「iPhone 16 128GB」里的 16 和 128）。"""
    return re.sub(r"[（(]?\s*第?\s*([一二三四五六七八九十]|\d{1,2})\s*代\s*[）)]?",
                  lambda m: str(CN_NUM.get(m.group(1), m.group(1))), (title or "").lower())


def mixed_models(keyword, t, title=""):
    """标题里同一个系列出现了不同代数或别的子型号（如同时挂 pro3、pro2、airpods4），多半是混挂链接。"""
    tokens = keyword.split()
    low = {x.lower() for x in tokens}
    tl = spaced_title(title) if title else t
    for i, tok in enumerate(tokens):
        if tok.isdigit() and i > 0:
            for base in {_norm("".join(tokens[:i])), _norm(tokens[0])}:
                nums = set(re.findall(flex(base) + r"[\s\-_]*(\d+)", tl))
                if nums - {tok}:
                    return True
    if len(tokens) > 1:
        base = _norm(tokens[0])
        if any(len(v) >= 2 and v not in low and base + v in t for v in VARIANT_TOKENS):
            return True
    return False


CAPACITY_RE = re.compile(r"(\d+)\s*(tb|t|gb|g)(?![a-z])", re.I)
BARE_CAPACITY_RE = re.compile(r"(?<![\d.¥￥$/×xX*-])(128|256|512)(?![\d.%]|\s*(?:mm|mah|hz|w\b|k|万|元|块|人|件|台|个|位|色|像素|nits|尼特|克|ml|毫|cm|米|期|天|小时|分钟|次|款|种|kg|斤))", re.I)


def capacities(text):
    """标题里的存储容量（单位 GB）。「5G」「4G」是网络制式，不算；「12GB+256GB」取到 12 和 256。"""
    out = []
    for n, unit in CAPACITY_RE.findall(text or ""):
        n, unit = int(n), unit.lower()
        if unit.startswith("t"):
            out.append(n * 1024)
        elif unit == "gb" or n >= 16:
            out.append(n)
    if not out:
        # 2.17：标题折行、被截断时「256GB」常只剩「256」（「苹果iPhone 18 Pro 256」「256全网通5G」）：
        # 没有写单位的容量时，单独的 128 / 256 / 512 也当容量（后面紧跟别的单位的不算）
        out = [int(n) for n in BARE_CAPACITY_RE.findall(text or "")]
    return out


def boundary_ok(keyword, title):
    """型号后面不能紧跟别的型号后缀：「iPhone 16」不匹配「iPhone 16e」「iPhone 16 Plus」，「Galaxy S25」不匹配「S25 FE」「S25+」。
    允许识别误差造成的字间空格（「AirPod s Pro 3」）。"""
    tl = spaced_title(title)
    tokens = [x.lower() for x in keyword.split()]
    chars = [c for c in "".join(tokens)]
    pat = r"[\s\-_]*".join(re.escape(c) for c in chars)
    for m in re.finditer(pat, tl):
        rest = tl[m.end():]
        glued = re.match(r"[a-z+]+", rest)
        if glued:  # 紧贴着字母或「+」：16e、18pro、s25+
            continue
        if re.match(r"\d", rest) and not re.match(r"\d+\s*(gb|tb|g|t)(?![a-z])", rest):
            continue  # 紧贴着别的数字：hd160
        nxt = re.match(r"[\s\-_]+([a-z+]+)", rest)
        if nxt and nxt.group(1) in VARIANT_TOKENS and nxt.group(1) not in tokens:
            continue  # 空一格跟着子型号：S25 FE、iPhone 16 Plus
        return True
    return False


def split_brand(keyword):
    """「Apple AirPods Pro 3」→ ("apple", "AirPods Pro 3")，「华为 Mate 70」→ ("huawei", "Mate 70")；
    开头不是已知品牌就原样返回 (None, keyword)。"""
    tokens = keyword.split()
    cn = next((b for b in CN_BRAND if tokens and tokens[0].startswith(b) and len(tokens[0]) > len(b)), None)
    if cn:  # 「戴森吹风机」→「戴森 吹风机」
        tokens = [cn, tokens[0][len(cn):]] + tokens[1:]
    if len(tokens) >= 2 and (tokens[0].lower() in BRAND_CN or tokens[0] in CN_BRAND or _norm(tokens[0]) in BRAND_ALIASES):
        return CN_BRAND.get(tokens[0], _norm(tokens[0])), " ".join(tokens[1:])
    return None, keyword


# 同名不同款：无酒精 / 0 度版本（「诺迪思 无酒精金酒 0度」不是诺迪思金酒）。「40度」里的「0度」不算
VARIANT_RE = re.compile(r"无酒精|(?<![\d.])0\s*度|零度|0酒精|脱醇")


def variant_mismatch(keyword, title):
    """标题是关键词没写的特殊版本（无酒精、0 度）：返回标题里的那个词，否则 None。"""
    m = VARIANT_RE.search(title or "")
    return m.group(0) if m and not VARIANT_RE.search(keyword or "") else None


def matches(keyword, title, platform):
    """标题是不是用户要的那款：型号连写出现、没有多出的型号后缀、不是配件、没有混挂别的代数；闲鱼以外不要二手。
    关键词开头是品牌时（Apple、Dyson…），品牌单独判断：标题里有英文或中文品牌名就行，不要求和型号连在一起
    （标题常写成「Apple/苹果 AirPods Pro（第三代）」）。"""
    if variant_mismatch(keyword, title):
        return False
    brand, rest = split_brand(keyword)
    if brand:
        if not brand_in(brand, title):
            return False
        if rest.split()[0].lower() not in BRAND_CN or len(rest.split()) == 1:
            keyword = rest
    if is_bundle(title, keyword):
        return False
    tokens = keyword.split()
    if not keyword.isascii():
        return matches_cn(tokens, title, platform)
    q, t = _norm(keyword), norm_title(title)
    if mixed_models(keyword, t, title):
        return False
    model = _norm("".join(x for x in tokens if x.isascii()))
    words = [_norm(x) for x in tokens if not x.isascii()]
    # 含「字母+数字」型号码（HD16、WH1000XM5）的关键词比较独特：各个词都在标题里出现就算
    coded = [x for x in tokens if x.isascii() and re.search(r"[a-z]", x, re.I) and re.search(r"\d", x) and len(x) >= 3]
    loose = bool(coded) and all(_norm(x) in t or BRAND_CN.get(x.lower(), "\0") in t for x in tokens)
    if q not in t and not (len(model) >= 2 and model in t and all(w in t for w in words)) and not loose:
        return False
    anchor = q if q in t else (model if model in t else _norm(coded[0]) if coded else model)
    if not boundary_ok(" ".join(coded) if loose and q not in t and model not in t else keyword, title):
        return False
    if any(w in title for w in NOT_FINAL):
        return False
    if any(sfx not in q and anchor + sfx in t for sfx in VARIANT_SUFFIXES):
        return False
    if "max" not in q and "max" in t:
        return False
    return not_excluded(t, anchor, title, platform)


NEGATE = ("无", "不带", "没有", "非")
# 同一个东西的另一种叫法（SK-II 大红瓶面霜的官方名是「精华霜」）
CN_SYNONYMS = {"面霜": ("精华霜", "乳霜")}
# 文字识别常认错的字：「面霜」认成「面箱」、「蓝氏」认成「篮氏」、「猫粮」认成「猫粗」
OCR_ALIKE = {"霜": "箱", "蓝": "篮", "粮": "粗"}


def word_forms(w):
    """关键词里的一个中文词在标题里可能的写法：原样、同义词，以及把一个常认错的字换掉后的写法。"""
    forms = [w, *CN_SYNONYMS.get(w, ())]
    return forms + [f[:i] + b + f[i + 1:] for f in forms for i, c in enumerate(f) for b in OCR_ALIKE.get(c, "")]


def word_in(w, t):
    return any(f in t for f in word_forms(w))


def matches_cn(tokens, title, platform):
    """关键词里有中文（「三只松鼠 每日坚果」「戴森 吹风机 HD16」）：每个中文词都要在标题里（品牌中英文都算）；
    有英文数字型号的，型号要连写出现、后面不能紧跟别的型号后缀。"""
    t = norm_title(title)
    words = [_norm(x) for x in tokens if not x.isascii()]
    model_tokens = [x for x in tokens if x.isascii()]
    if any(neg + w.removesuffix("版") in t for w in words for neg in NEGATE):  # 「无光驱版」不是「光驱版」
        return False
    if not all(word_in(w, t) or (w in CN_BRAND and CN_BRAND[w] in t) or (len(w) >= 3 and w.endswith("版") and w[:-1] in t) for w in words):
        return False
    anchor = next((w for w in words if w in t), words[0])
    if model_tokens:
        model = " ".join(model_tokens)
        anchor = _norm(model)
        if anchor not in t or mixed_models(model, t, title) or not boundary_ok(model, title):
            return False
        if any(anchor + sfx in t for sfx in VARIANT_SUFFIXES if sfx not in anchor) or ("max" not in anchor and "max" in t):
            return False
    if any(w in title for w in NOT_FINAL):
        return False
    # 配件词是按数码产品定的（膜、壳、收纳…），纯中文商品名（坚果、面膜）不查，免得误伤「薄壳碧根果」
    return not_excluded(t, anchor, title, platform, accessories=bool(model_tokens))


def not_excluded(t, anchor, title, platform, accessories=True):
    """不是配件、不是二手。"""
    for w in ACCESSORY_WORDS if accessories else ():
        i = t.find(w)
        if i < 0:
            continue
        # 「适用/适配/兼容」写在型号后面是在说这件商品兼容什么（如「耳机 适用iPhone」），写在前面才是配件
        if w in ("适用", "适配", "兼容") and i > t.find(anchor):
            continue
        return False
    if platform != "闲鱼" and (any(w in title for w in USED_WORDS) or HK_MIX.search(title or "")):
        return False
    return True


OVERSEAS = ["日本本土", "本土版", "日本版", "美版", "港版", "日版", "海外版", "全球购", "无卡槽", "无eSIM", "国际版", "港澳版", "韩版", "欧版", "外版", "卡贴", "有锁", "资源机", "监管机",
            "保税"]
# 套装 / 多件装：「神仙水230ml+面霜50g」「明星3件套」「230ml*2」。用户自己要套装时不算；「1000+人逛过」「1万+件」是销量
BUNDLE_RE = re.compile(r"套组|件套|[*×]\s*[2-9](?!\d)|[+＋]\s*(?!(?:国补|补贴|政府|以旧|换新|分期|免息|赠|送|包邮|券|礼|运费|保修|碎屏|人|件))[\u4e00-\u9fff]{2,}")


def is_bundle(title, keyword=""):
    """套装 / 多件装。除了「件套 / 套组 / *2 / +面霜」，标题里同时写着好几种毫升 / 克数（「神仙水230ml面霜50g小灯泡30ml」）也算。"""
    if re.search(r"套装|套组|件套|组合|礼盒", keyword or ""):
        return False
    sizes = sizes_in(title)
    multi = len(sizes) > 1 and any(u in ("ml", "毫升") for _, u in sizes)
    return bool(BUNDLE_RE.search(title or "")) or multi


def why_not(keyword, title, platform):
    """matches() 为 False 时，说清楚是哪一类原因（给用户看，也给彩蛋判断成就）。"""
    v = variant_mismatch(keyword, title)
    if v:
        return f"是「{v}」的版本，不是同一款"
    brand, rest = split_brand(keyword)
    if brand and not brand_in(brand, title):
        name = brand.upper() if len(brand) <= 4 else brand.capitalize()
        return f"标题里没有品牌「{name}」" + (f"或「{BRAND_CN[brand]}」" if brand in BRAND_CN else "")
    keyword = rest if brand else keyword
    if is_bundle(title, keyword):
        return "套装或多件装（还带了别的商品），不是单品"
    t = norm_title(title)
    hit = lambda words: next((w for w in words if w in title), None)
    if platform != "闲鱼" and HK_MIX.search(title or ""):
        return "港版、国行混在一个链接里，列出来的低价可能是港版的"
    if platform != "闲鱼" and hit(OVERSEAS):
        return f"非国行（标题里有「{hit(OVERSEAS)}」）"
    if platform != "闲鱼" and hit(USED_WORDS):
        return f"二手或非全新（标题里有「{hit(USED_WORDS)}」）"
    if hit(NOT_FINAL):
        return f"预售定金，不是最终价（标题里有「{hit(NOT_FINAL)}」）"
    if mixed_models(keyword, t, title):
        return "同一个链接混挂了好几个型号"
    if any(w in t for w in ACCESSORY_WORDS if w not in ("适用", "适配", "兼容")):
        return "配件，不是主机"
    missing = [w for w in keyword.split() if not w.isascii() and not word_in(_norm(w), t) and CN_BRAND.get(w, "\0") not in t]
    if missing:
        return f"标题里没有「{'」「'.join(missing)}」"
    return f"标题里找不到完整型号「{keyword}」，或是别的型号"


VOLUME_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(ml|毫升|l|升)(?![a-z])", re.I)


def fix_ml(text):
    """识别常把「230ml」里的数字断开（「23 0ml」），只对毫升把数字接回去（「18 256GB」这种不动）。"""
    return re.sub(r"(\d)\s+(?=\d+\s*(?:ml|毫升)(?![a-z]))", r"\1", text or "", flags=re.I)


def main_part(text):
    """去掉「赠 / 送 / 享 / 附」后面的赠品信息（「230ml 礼盒 赠神仙水30ml」），只看商品本身的规格。"""
    t = fix_ml(text)
    first = re.search(r"\d+(?:\.\d+)?\s*(?:ml|毫升|g|克)(?![a-z])", t, re.I)
    if not first:
        return t
    cut = re.search(r"赠|送|享|附", t[first.end():])
    return t[:first.end() + cut.start()] if cut else t


def sizes_in(text):
    return {(round(float(n), 1), u.lower()) for n, u in
            re.findall(r"(\d+(?:\.\d+)?)\s*(ml|毫升|g|克)(?![a-z])", main_part(text), re.I)}


def volumes(text):
    """标题里的容量（毫升）：「230ml」「230 毫升」「1L」。"""
    return [float(n) * (1000 if u.lower() in ("l", "升") else 1) for n, u in VOLUME_RE.findall(fix_ml(text))]


WEIGHT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|公斤|千克|斤|克|g|k(?=\s|$))(?![a-z])", re.I)  # 「k」：标题行尾截断的「10k(g)」
WEIGHT_SPEC = re.compile(r"\d\s*(?:kg|公斤|千克|斤|克)|\d\s*g(?![a-zA-Z])", re.I)


def is_weight_spec(spec):
    """规格是重量：10KG、10公斤、5斤、50g、500克。「256G」（大写 G）是存储容量，不算。"""
    return bool(WEIGHT_SPEC.search(spec or "")) and not re.search(r"\d\s*G(?![a-zA-Z])|gb", spec or "")


def weights(text):
    """文字里的重量，统一换成克：「10kg」→ 10000，「5斤」→ 2500，「50g」→ 50。"""
    out = []
    for n, u in WEIGHT_RE.findall(fix_ml(text)):
        u = u.lower()
        out.append(float(n) * (1000 if u in ("kg", "k", "公斤", "千克") else 500 if u == "斤" else 1))
    return out


def spec_values(spec, text):
    """按规格的单位取文字里的同类数值：规格写 ml 就比毫升，写 kg / 克就比重量，否则比存储容量。"""
    if volumes(spec):
        return volumes(text)
    return weights(text) if is_weight_spec(spec) else capacities(text)


def spec_want(spec):
    return (volumes(spec) or (weights(spec) if is_weight_spec(spec) else capacities(spec)))[:1]


def spec_ok(spec, title):
    """规格（如 256GB、230ml）写在标题里就必须对上：存储取标题里最大的容量（「12GB+256GB」是内存+存储），
    毫升要有一个对得上。标题没写的不在这里判断。"""
    want = spec_want(spec)
    have = spec_values(spec, title)
    if not want or not have:
        return True
    if volumes(spec):
        sizes = sizes_in(title)
        if len(sizes) > 1:  # 一个链接挂了几种容量，或者是套装（赠品的容量不算）
            return False
        return any(abs(v - want[0]) < 0.5 for v in have)
    if is_weight_spec(spec):  # 标题里常还有「2kg以下」这类筛选词：有一个重量对得上就算
        return any(abs(v - want[0]) < 1 for v in have)
    return max(have) == want[0]


IDENTITY = ("新人", "入会", "会员", "PLUS", "88VIP", "学生")  # 要特定身份才能拿到的价格


def money(v):
    """9294.0 → 9,294；9993.23 → 9,993.23（不四舍五入成整数）。"""
    return f"{v:,.2f}".rstrip("0").rstrip(".")


# 2.15：只比官方认证的店——自营、旗舰店（官方旗舰店 / 品牌旗舰店）、直营店、官方店、平台自己的超市、Apple Store。
# 专营店、专卖店、授权店、个人店、代购店一律不算：不进比价单、不进小票、不进结论（用户要求）。
OFFICIAL_SHOP = ("自营", "旗舰店", "直营", "官方", "Apple Store", "天猫超市", "京东超市", "猫超")
NOT_OFFICIAL = ("代购", "专营", "专卖", "授权", "经销", "分销", "工作室", "个人", "小店", "二手", "回收", "寄卖",
                "海外", "全球购", "跨境", "买手", "官方精选")


# 2.17：拼多多只比品牌官方旗舰店和拼多多自营（用户要求）。第三方店一律不看：专营店、百亿补贴里「商品由第三方商家提供」的、
# 店名写「官方」「直营」但不是旗舰店的，都不算。
PDD_OFFICIAL = ("旗舰店", "自营")


def official(i, platform=None):
    shop = re.sub(r"\s+", "", (i.get("shop") or "") if isinstance(i, dict) else str(i or ""))
    platform = platform or (i.get("platform") if isinstance(i, dict) else "") or ""
    if any(w in shop for w in NOT_OFFICIAL):
        return False
    if platform == "拼多多":
        return any(w in shop for w in PDD_OFFICIAL)
    return any(w.replace(" ", "") in shop for w in OFFICIAL_SHOP)


# 旧名字：以前「自营 / 旗舰店优先、没有才用第三方店」，现在第三方店直接不要，两者是一回事
FLAGSHIP_SHOP = OFFICIAL_SHOP
flagship = official
NOT_OFFICIAL_WHY = "不是官方认证店（只比旗舰店 / 自营 / 直营店；拼多多只比品牌官方旗舰店和拼多多自营），代购、第三方和个人小店不算"


def pick(items):
    """几条同款里选最便宜的一条（只会有官方认证店的，别的店在 summarize 里已经去掉了）。没有就返回 None。"""
    items = list(items)
    return min([i for i in items if official(i)] or items, key=lambda i: i["price"], default=None)


def low_label(best, low):
    """选中的那条是不是全场最便宜：是就叫「最低」；别的店更便宜、按自营 / 旗舰店优先选了它，就叫「自营/旗舰店最低」。"""
    return "自营/旗舰店最低" if any(i["price"] < low["price"] - 0.005 for i in best.values()) else "最低"


def identity_price(i):
    return any(w in i["label"] or w in i["note"] for w in IDENTITY)


# ———————— 优惠怎么算（2.15）：读到的券、满减、立减、国补 → 自己算一遍到手价 ————————
# 要满足特定条件才有的优惠不算进去，只列出来：会员 / 新人 / 学生、以旧换新、金币积分、指定支付方式、要买好几件
PROMO_COND = re.compile(r"PLUS|88VIP|会员|新人|新客|首单|首购|学生|以旧换新|旧机|回收|淘金币|金币|积分|京豆|白条|分期|花呗|"
                        r"信用卡|银行|云闪付|支付宝|微信支付|数字人民币|第\s*[二2]\s*件|[2-9两三四五]\s*件|满\s*\d+\s*件|多件|定金|"
                        r"直播间|粉丝|关注|入会|开卡|邀请|助力|抽奖|签到|已抢光|已领完|已失效|已过期|即将开始|未开始", re.I)
_N = r"(\d+(?:\.\d+)?)"
MAN_RE = re.compile(r"(每)?满" + _N + r"元?(?:可用)?已?(?:减|-|－)" + _N + r"元?")
COUPON_RE = (re.compile(r"[¥￥]" + _N + r"元?[^\d满¥￥]{0,8}满" + _N + r"元?可?(?:用|使用)"),
             re.compile(r"(?<![\d.])" + _N + r"元[^\d满¥￥]{0,8}满" + _N + r"元?可?(?:用|使用)"))
FREE_RE = (re.compile(r"[¥￥]" + _N + r"元?[^\d¥￥]{0,8}无门槛"), re.compile(r"无门槛[^\d¥￥]{0,8}[¥￥]?" + _N + r"元?"))
DIRECT_RE = re.compile(r"(官方立减|限时立减|单品立减|新品立减|购买立减|下单立减|限时直降|单品直降|官方直降|直降|立减|限量补贴|品牌补贴|平台补贴|店铺补贴)"
                       r"[:：]?[¥￥]?" + _N + r"元?")
DISCOUNT_RE = re.compile(r"(?<![\d.])(\d(?:\.\d)?)\s*折(?!起)")
GOV_RE = re.compile(r"国补|政府|国家补贴|购新补贴|以旧换新补贴|数码补贴|家电补贴")
GOV_CAP = re.compile(r"(?:最高|至高|上限)(?:可)?(?:补贴|补|减|立减|省|抵)?[¥￥]?" + _N + r"元?")
PANEL_ITEM = re.compile(r"^([一-鿿A-Za-z]{2,8})[^\d¥￥\s]{0,2}\s*[¥￥]\s*(\d+(?:\.\d+)?)$")  # 优惠明细面板上配好的「店铺优惠 ¥21.1」
BASE_WORDS = ("优惠前", "京东价", "原价", "日常价", "页面价")
PLATFORM_COUPON = re.compile(r"平台|跨店|品类|全品|购物券|加补|超市|百亿|津贴|消费券|通用|东券|全场")


def promo_rules(texts):
    """把读到的优惠文字拆成规则：立减 / 折扣 / 满减 / 券 / 国补。同一条（门槛和金额一样）只算一次。
    返回 (规则列表, 没算进去的条件优惠)。"""
    rules, cond, seen = [], [], set()

    def add(r):
        key = (r["kind"] in ("man", "coupon"), r.get("need"), r.get("cut"), r.get("pct")) if r["kind"] != "gov" else ("gov",)
        if key in seen:
            # 券和满减说的是同一件事（「满3000减300」的券条 + 券面板里「¥300 满3000可用」）：留券
            if r["kind"] == "coupon":
                for x in rules:
                    if x["kind"] == "man" and (x.get("need"), x.get("cut")) == (r.get("need"), r.get("cut")):
                        x.update(r)
            return
        seen.add(key)
        rules.append(r)

    gov_pct = gov_cap = gov_cut = None
    segs = [seg for raw in texts or [] for seg in re.split(r"[|｜;；、]|\s{2,}", str(raw))]
    for k, seg in enumerate(segs):
        if True:
            t = re.sub(r"\s+", "", seg)
            if not t or not re.search(r"\d", t):
                continue
            m = PANEL_ITEM.match(t)  # 面板上的合计项（「店铺优惠 ¥21.1」「平台优惠 ¥15」）不是规则；「无门槛 ¥5」「购买立减 ¥600」是
            if m and not re.search(r"无门槛|立减|直降|国补|政府|国家|补贴", m.group(1)):
                continue
            t = t.replace("百亿补贴", "").replace("減", "减")
            if PROMO_COND.search(t):
                if re.search(r"减|券|补贴|折|红包|返|优惠|可用|专享", t) and not re.search(r"已抵|已减|已享|【", t) and seg.strip() not in cond:
                    cond.append(seg.strip())
                continue
            if GOV_RE.search(t):
                m = re.search(_N + r"\s*%", t)
                if m and 0 < float(m.group(1)) <= 30:
                    gov_pct = float(m.group(1))
                    if not GOV_CAP.search(t):  # 京东把「最高可减500元」写在下一行
                        # 只认紧接着的续行（「，最高可减500元」）；「淘金币+补贴至高减1000」这种别的活动的上限不算
                        nxt = next((re.sub(r"\s+", "", x) for x in segs[k + 1:k + 3]
                                    if re.match(r"^[\d\s，,。、]*(最高|至高|上限)", x) and GOV_CAP.search(re.sub(r"\s+", "", x))), "")
                        if nxt:
                            gov_cap = float(GOV_CAP.search(nxt).group(1))
                m = GOV_CAP.search(t)
                if m:
                    gov_cap = float(m.group(1))
                elif not gov_pct:
                    m = re.search(r"(?:国补|补贴)[^\d后前]{0,4}[¥￥]?" + _N + r"元?(?!%)", t)
                    if m and not re.search(r"补贴后|国补后|补贴价|国补价", t):
                        gov_cut = float(m.group(1))
                continue
            if re.search(r"补贴后|国补后|到手|券后|优惠后|折后|预估|估算|合计|已省|共省", t) and not re.search(r"满\d|券|立减|直降", t):
                continue  # 这是算好的总价，不是规则
            is_coupon = "券" in t
            sub = "平台券" if PLATFORM_COUPON.search(t) else "店铺券"
            for m in MAN_RE.finditer(t):
                need, cut = float(m.group(2)), float(m.group(3))
                if 0 < cut < need:
                    cap = GOV_CAP.search(t[m.end():]) if m.group(1) else None
                    add({"kind": "coupon" if is_coupon else "man", "sub": sub, "need": need, "cut": cut, "every": bool(m.group(1)),
                         "cap": float(cap.group(1)) if cap else 0,
                         "text": f"{sub + ' ' if is_coupon else ''}{'每' if m.group(1) else ''}满{need:g}减{cut:g}"})
            for rx in COUPON_RE:
                for m in rx.finditer(t):
                    cut, need = float(m.group(1)), float(m.group(2))
                    if 0 < cut < need:
                        add({"kind": "coupon", "sub": sub, "need": need, "cut": cut, "every": False, "cap": 0,
                             "text": f"{sub} 满{need:g}减{cut:g}"})
            for rx in FREE_RE:
                for m in rx.finditer(t):
                    cut = float(m.group(1))
                    if cut > 0:
                        add({"kind": "coupon", "sub": "平台券" if "红包" in t or PLATFORM_COUPON.search(t) else sub,
                             "need": 0, "cut": cut, "every": False, "cap": 0, "text": f"无门槛券 ¥{cut:g}"})
            for m in DIRECT_RE.finditer(t):
                cut = float(m.group(2))
                if cut > 0 and not re.match(r"\s*%", t[m.end():]):
                    add({"kind": "direct", "cut": cut, "text": m.group(1)})
            for m in DISCOUNT_RE.finditer(t):
                x = float(m.group(1))
                if 1 <= x < 10 and "券" in t and not GOV_CAP.search(t):  # 「8折金刚券」：没写最多减多少，不算，列出来
                    if seg.strip() not in cond:
                        cond.append(seg.strip())
                elif 1 <= x < 10 and not re.search(r"件|起|最低", t):
                    add({"kind": "discount", "pct": x, "text": f"{x:g}折"})
    # 「白条券 ¥5」这类条件券，同一张券的「¥5 满1000元可用」常在另一行、没带「白条」两个字：金额一样的券不算
    cond_cuts = {float(x) for c in segs if "券" in c and PROMO_COND.search(c) for x in re.findall(r"[¥￥]\s*(\d+(?:\.\d+)?)", c)}
    rules = [r for r in rules if not (r["kind"] == "coupon" and r["cut"] in cond_cuts)]
    if gov_pct and not gov_cap and gov_cut:
        gov_cap = gov_cut  # 「领取政府补贴10%」+「领政府补贴省500」：按 10%、最多 500 算
    if gov_pct and not gov_cap:
        # 只写了比例、没看到上限：国补都有上限（手机常见 500），猜错会把价算得太低，不算，只列出来
        cond.append(f"国补 {gov_pct:g}%（页面上没看到上限，没算进去）")
        gov_pct = gov_cut = None
    if gov_pct or gov_cut:
        rules.append({"kind": "gov", "pct": gov_pct or 0, "cap": gov_cap or 0, "cut": 0 if gov_pct else gov_cut,
                      "text": f"国补 {gov_pct:g}%" + (f"（最高 ¥{gov_cap:g}）" if gov_cap else "") if gov_pct else "国补"})
    return rules, cond[:4]


def apply_rules(base, rules):
    """按「立减 → 折扣 → 满减 → 店铺券 → 平台券 → 国补」的顺序算；每一类只用一条（门槛够得着、减得最多的那条）。
    满减和券的门槛按立减、折扣之后的价格看；国补按前面都减完的价格算比例。返回 [(说明, 减了多少)]。"""
    steps, p = [], base
    direct = [r for r in rules if r["kind"] == "direct" and r["cut"] < base * 0.6]
    if direct:
        r = max(direct, key=lambda r: r["cut"])
        steps.append((r["text"], r["cut"]))
        p -= r["cut"]
    disc = [r for r in rules if r["kind"] == "discount"]
    if disc:
        r = min(disc, key=lambda r: r["pct"])
        cut = round(p * (1 - r["pct"] / 10), 2)
        steps.append((r["text"], cut))
        p -= cut
    gate = p
    for kind, sub in (("man", None), ("coupon", "店铺券"), ("coupon", "平台券")):
        best = None
        for r in rules:
            if r["kind"] != kind or (sub and r.get("sub") != sub) or gate < r["need"]:
                continue
            cut = r["cut"] * (int(gate // r["need"]) if r["every"] and r["need"] else 1)
            if r.get("cap"):
                cut = min(cut, r["cap"])
            if best is None or cut > best[1]:
                best = (r["text"], cut)
        if best and best[1] < p:
            steps.append(best)
            p -= best[1]
    gov = next((r for r in rules if r["kind"] == "gov"), None)
    if gov:
        cut = round(p * gov["pct"] / 100, 2) if gov["pct"] else gov["cut"]
        if gov["pct"] and gov["cap"]:
            cut = min(cut, gov["cap"])
        if 0 < cut < p:
            steps.append((gov["text"], cut))
    return [(t, round(c, 2)) for t, c in steps if c > 0.004]


def panel_breakdown(detail, price):
    """优惠明细面板能对上账时（几项加起来正好等于「优惠前 − 到手价」），这就是 App 自己的算法：
    返回 (优惠前, [(项目, 金额)])；对不上返回 (优惠前或 0, [])。"""
    start, cands = 0.0, []
    for raw in (detail or {}).get("promos") or []:
        for p in re.split(r"[|｜]", str(raw)):
            p = p.strip()
            m = re.fullmatch(r"(优惠前|京东价|原价)\s*[¥￥]\s*(\d+(?:\.\d+)?)", p)
            if m:
                start = start or float(m.group(2))
                continue
            m = re.fullmatch(r"([一-鿿A-Za-z]{2,8})[^\d¥￥\s]{0,2}\s*[¥￥]\s*(\d+(?:\.\d+)?)", p)  # 「平台优惠へ ¥309.06」：标签后面识别带进来的杂字
            if m and not re.search(r"后|前|到手|满|可用|预估|价", m.group(1)) and (m.group(1), float(m.group(2))) not in cands:
                cands.append((m.group(1), float(m.group(2))))
    if not start or start <= price or not cands:
        return start, []
    gap, cands = start - price, cands[:8]
    for n in range(len(cands), 0, -1):
        for combo in itertools.combinations(cands, n):
            if abs(sum(v for _, v in combo) - gap) < 0.02:
                return start, list(combo)
    return start, []


def price_calc(list_price, list_label, detail):
    """用详情页上读到的券、满减、立减、国补自己算一遍到手价。
    - 优惠明细面板对得上账：就用 App 的算法（面板里的几项），页面上写了国补、面板里却没有，再按国补规则减；
    - 对不上：从「优惠前」（详情页写明的；没有就用列表上不带「券后 / 到手」字样的价格）开始，按规则一条条减。
    返回 {base, base_from, steps, final, app, mode, cond, coupons, trusted}；什么都算不出来返回 None。
    trusted：起算价是详情页写明的（不会和 App 已经减过的优惠重复算），这时算出来更低才可以拿来当结论价。"""
    detail = detail or {}
    if not detail or detail.get("error"):
        return None
    app = _f(detail.get("price"))
    if app and not (list_price * 0.5 < app < list_price * 1.5):
        app = 0
    texts = list(detail.get("promos") or []) + list(detail.get("coupons") or [])
    rules, cond = promo_rules(texts)
    # 价格本身已经是「政府补贴价 / 补贴后 / 国补后」：国补已经减过了，不能再减一次
    if re.search(r"国补|政府|补贴", f"{detail.get('label') or ''} {list_label or ''}"):
        rules = [r for r in rules if r["kind"] != "gov"]
    coupons = [r["text"] for r in rules if r["kind"] == "coupon"]
    start, steps = panel_breakdown(detail, app) if app else (0, [])
    if steps:
        steps = [(t, v) for t, v in steps]
        final = app
        gov = next((r for r in rules if r["kind"] == "gov"), None)
        if gov and not any(re.search(r"国补|政府|国家", t) for t, _ in steps):
            extra = apply_rules(final, [gov])
            for t, v in extra:
                steps.append((t + "（面板里没算）", v))
                final -= v
        return {"base": start, "base_from": "优惠前", "steps": steps, "final": round(final, 2), "app": app,
                "mode": "panel", "cond": cond, "coupons": coupons, "trusted": True}
    base, base_from = _f(detail.get("base")), "优惠前"
    if not (list_price * 0.5 < base < list_price * 2):
        base = 0
    if not base and not any(w in (list_label or "") for w in ("券后", "到手", "补贴", "折后", "国补", "百亿", "加补", "预估")):
        base, base_from = list_price, "列表价"
        # 2.17：列表价和详情页的「券后 / 到手」价一样（拼多多百亿补贴卡片上写的就是券后价）：优惠已经减过了，不能再减一次
        if app and abs(base - app) < 0.5 and re.search(r"券后|到手|补贴|折后|国补|加补|预估", detail.get("label") or ""):
            base = 0
    if not base or not rules:
        return {"base": base, "base_from": base_from, "steps": [], "final": app or 0, "app": app, "mode": "none",
                "cond": cond, "coupons": coupons, "trusted": False} if (cond or coupons or app) else None
    steps = apply_rules(base, rules)
    final = round(base - sum(v for _, v in steps), 2)
    if not steps or final < base * 0.4:  # 减掉一大半多半是认错了数字，不用
        return {"base": base, "base_from": base_from, "steps": [], "final": app or 0, "app": app, "mode": "none",
                "cond": cond, "coupons": coupons, "trusted": False} if (cond or coupons or app) else None
    return {"base": base, "base_from": base_from, "steps": steps, "final": final, "app": app, "mode": "rules",
            "cond": cond, "coupons": coupons, "trusted": base_from == "优惠前"}


def settle_price(list_price, list_label, detail):
    """这件商品按多少算：App 详情页的到手价、自己按优惠规则算的价，选哪个。返回 (价格, 说法, calc)。
    - 有 App 到手价：用它；自己算出来更低、而且起算价是详情页写明的「优惠前」（不会重复减），就用算出来的；
    - 没有 App 到手价：自己算出来的比列表价低，就用算出来的；
    - 都没有：用列表价。"""
    calc = price_calc(list_price, list_label, detail)
    app = (calc or {}).get("app") or 0
    if not app:
        dp = _f((detail or {}).get("price"))
        app = dp if list_price * 0.5 < dp < list_price * 1.5 else 0
    cp = (calc or {}).get("final") if (calc or {}).get("steps") else 0
    dl = ((detail or {}).get("label") or "优惠").rstrip("价")
    if app and cp and cp < app - 0.5 and calc.get("trusted"):
        return cp, "按页面优惠算的到手价", calc
    if app:
        return app, f"详情页{dl}价", calc
    if cp and cp < list_price - 0.005:
        return cp, "按页面优惠算的到手价", calc
    return list_price, list_label, calc


def calc_line(i):
    """一行写清楚怎么算的：「¥10,299 − 满3000减300 ¥300 − 国补 15% ¥500 = ¥9,499」。没有就返回空字符串。"""
    c = i.get("calc") or {}
    if not c.get("steps") or not c.get("base"):
        return ""
    return (f"¥{money(c['base'])} " + " ".join(f"− {t} ¥{money(v)}" for t, v in c["steps"])
            + f" = ¥{money(c['final'])}")


def summarize(data, drops=()):
    keyword, spec = data["keyword"], data.get("spec") or ""
    cands, offi, dropped, unofficial = {}, {}, [], {}
    for v in data.get("items") or []:
        platform = ALIASES.get(v.get("platform"), v.get("platform"))
        price = _f(v.get("price"))
        title = v.get("title") or ""
        if platform not in PLATFORMS or price <= 0:
            dropped.append((v.get("platform"), title, "平台不认识或没有价格"))
            continue
        if any(platform == p and abs(price - d) < 0.01 and (not shop or shop in (v.get("shop") or "")) for p, d, shop in drops):
            dropped.append((platform, title, "复核截图时排除（--drop）"))
            continue
        if not matches(keyword, title, platform) or not spec_ok(spec, title):
            dropped.append((platform, title, "型号/规格不符或是配件、二手"))
            continue
        if platform != "闲鱼" and any(w in (v.get("shop") or "") for w in OVERSEAS_SHOP):
            dropped.append((platform, title, "海外 / 跨境 / 代购店，不是国内正品渠道"))
            continue
        if platform != "闲鱼" and not official({"shop": clean_shop(v.get("shop"))}, platform):
            dropped.append((platform, title, NOT_OFFICIAL_WHY))
            unofficial[platform] = unofficial.get(platform, 0) + 1
            continue
        item = {"platform": platform, "title": title, "price": price, "original": _f(v.get("original")),
                "label": v.get("label") or "", "shop": clean_shop(v.get("shop")), "note": v.get("note") or "",
                "source": v.get("source") or "手机App", "url": v.get("url") or "", "couponUrl": v.get("couponUrl") or "",
                "detail": v.get("detail") or {}, "list_price": 0, "calc": None}
        # 点进详情页看到的到手价 / 券后价 / 加补后价才是实际要付的：按详情页的算，列表价写进备注。
        # 2.15：再用详情页上读到的券、满减、立减、国补自己算一遍（price_calc），App 没算进去的也能算上。
        # 详情页比列表还贵时也按详情页（列表上的常是新人价、限量价）。差得离谱的当识别错，不用
        promos = " ".join(item["detail"].get("promos") or [])
        if re.search(r"国补后|领取?政府补贴|领国补", promos):  # 详情页的价格里算了国补：得先在 App 里领到补贴资格
            item["note"] = "；".join(x for x in (item["note"].replace("有政府补贴", "").replace("有国补", "").strip("；"),
                                                "含国补（需先在 App 领政府补贴）") if x)
        if item["source"] == "手机App":
            dp, dl, item["calc"] = settle_price(price, item["label"], item["detail"])
            if abs(dp - price) > 0.005:
                item.update(list_price=price, price=dp, label=dl + (f"（列表标的是{item['label']}）" if item["label"] else ""))
                price = dp
            if item["calc"] and any(r[0].startswith("国补") for r in item["calc"].get("steps") or []) and "国补" not in item["note"]:
                item["note"] = "；".join(x for x in (item["note"], "算上了国补（需先在 App 领政府补贴）") if x)
        cands.setdefault(platform, []).append(item)
        if official(item) and (platform not in offi or price < offi[platform]["price"]):
            offi[platform] = item
    # 每个平台一条：只在点进过商品页、核实过券和优惠的里面选（用户要求：列表价不准）；一件都没点进去才用列表价
    cands = {p: ([i for i in v if not unverified(i)] or v) for p, v in cands.items()}
    best = {p: pick(v) for p, v in cands.items()}
    plain = {p: x for p, v in cands.items() if (x := pick(i for i in v if not identity_price(i)))}
    skipped = {ALIASES.get(s.get("platform"), s.get("platform")): s.get("reason") or "" for s in data.get("skipped") or []}
    for p in data.get("searched") or []:
        p = ALIASES.get(p, p)
        if p not in best:  # 后来重查过：以「搜过了」为准，覆盖更早的跳过原因
            skipped[p] = ("手机上搜过了，同款只在非官方店（专营店、第三方商家、代购、个人小店等）有，按规则不算" if unofficial.get(p)
                          or any(str(r.get("why") or "").startswith("不是官方认证店") and ALIASES.get(r.get("platform"), r.get("platform")) == p
                                 for r in data.get("rejected") or [])
                          else "手机上搜过了，但前五屏没有对得上型号规格、又是官方店（旗舰店 / 自营 / 直营）卖的同款")
    data["_official"] = offi
    return best, plain, skipped, dropped


def clean_shop(v):
    """去掉识别带进店名的按钮字：「中国联通手机官方旗舰店 进店」→「中国联通手机官方旗舰店」。"""
    shop = re.sub(r"\s*(进店|进入店铺|关注)\s*[>›]?\s*$", "", str(v or "")).strip()
    shop = re.sub(r"^(\d+年老店|本月上新\d*件?|回头客[\d.]+[万千]?\+?|好评率\s*\d+%)\s*", "", shop)  # 店名前面的标签：「20年老店 劈腿大师」
    shop = re.sub(r"\s+(这里上新|本月上新|上新\d*件?|进店逛逛)$", "", shop)
    shop = re.sub(r"^由?\s*「|」?\s*运营(?=（|$)", "", shop).replace("「", "").replace("」", "")  # 拼多多「由「XX专营店」运营」  # 店名后面的标签：「荷仙姑的店 这里上新」
    shop = re.sub(r"旗$", "旗舰店", re.sub(r"旗舰$", "旗舰店", shop))  # 2.17：列表上被截断的「…自营旗」
    return "" if "百亿补贴" in shop else shop  # 「百亿补贴官方精选」是频道名，不是店铺，也不算官方


def clean_promos(promos, limit=6):
    """详情页优惠是识别出来的碎片：按「|」拆开，去掉按钮字（立即领取、去凑单…）和「已享受：」这类前缀，
    只留带金额或满减数字的；太短、断了半句、重复的不要。"""
    out = []
    for raw in promos or []:
        for p in re.split(r"[|｜]", str(raw)):
            p = re.sub(r"\s+", " ", p)
            p = re.sub(r"^(已享受|可再享|开通享|已减)[:：]?\s*", "", p.strip())
            p = re.sub(r"\s*(立即领取|立即领|去领取|领取|去凑单|去参与|去使用|立即|查看)\s*[>›]?\s*$", "", p)
            p = re.sub(r"\s*[>›].*$", "", p).strip(" ，,；;>")
            if (len(p) < 4 or p[0] in "）)，,；;" or "热销" in p or not re.search(r"\d", p)
                    or p in out):
                continue
            out.append(p)
    return out[:limit]


def tidy(text):
    """备注用中点分隔，读起来像标签：「补贴后，支持以旧换新」→「补贴后 · 支持以旧换新」。"""
    parts = []
    for x in re.split(r"[，；,;]", str(text or "")):
        x = x.strip()
        if x and x not in parts:
            parts.append(x)
    return " · ".join(parts)


def stamp_time(t):
    """「2026-10-29 15:24 至 15:28」→「2026.10.29 15:24–15:28」。"""
    return str(t or "").replace("-", ".", 2).replace(" 至 ", "–")


def cell(v):
    """表格单元格里不能有「|」和换行（识别出来的文字可能带这些）。"""
    return str(v).replace("|", "｜").replace("\n", " ")


def ref_cell(r):
    """参考价：有出处链接就做成链接（回复里显示成蓝字，点开是查到这个价格的网页）。"""
    text = cell(r["text"].replace("约 ", "≈ "))
    return f"[{text}]({r['url']})" if r.get("url") else text


def render_md(data, best, plain, skipped, refs=None):
    """总报告（比价单）：标题 → 一行最低价 → 价格表 → 一行小字。
    价格表：平台 / 手机 App 到手价 / 店铺 / 参考价（每个平台都用 WebSearch 查过，蓝字可点开出处）。
    附注、详情页优惠、标题没写容量的商品都在小票里看；小字里只留会影响判断的提醒。"""
    keyword, spec = data["keyword"], data.get("spec") or ""
    refs = refs or {}
    lines = [f"### {keyword}" + (f" · {spec}" if spec else ""), ""]
    low = pick(best.values())
    if low:
        lines += [f"**{low_label(best, low)} ¥{money(low['price'])}**　{low['platform']}" + (f" · {low['shop']}" if low["shop"] else ""), ""]
        how = calc_line(low)
        if how:
            lines += [f"优惠怎么算：{how}" + ("（App 标的到手价 ¥%s）" % money(low["calc"]["app"])
                                          if low["calc"].get("app") and abs(low["calc"]["app"] - low["calc"]["final"]) > 0.5 else ""), ""]
    elif refs:
        r = min(refs.values(), key=lambda r: r["low"])
        lines += [f"**这次手机 App 里没有读到实时价**　参考价最低 {ref_cell(r)}（{r['platform']}）", ""]
    else:
        lines += ["**这次手机 App 里没有读到实时价，网上也没查到近期价格**", ""]

    rows = []
    with_ref = bool(refs)
    for platform, i in sorted(best.items(), key=lambda kv: kv[1]["price"]):
        price = f"**¥{money(i['price'])}**" if i is low else f"¥{money(i['price'])}"
        row = f"| {platform} | {price} | {cell(i['shop'] or '—')} |"
        rows.append(row + (f" {ref_cell(refs[platform]) if platform in refs else '—'} |" if with_ref else ""))
    for platform, r in sorted(refs.items(), key=lambda kv: kv[1]["low"]):
        if platform not in best:
            rows.append(f"| {platform} | — | — | {ref_cell(r)} |")
    if rows:
        head = ["| 平台 | 到手价 | 店铺 | 参考价 |", "|:--|--:|:--|--:|"] if with_ref else \
               ["| 平台 | 到手价 | 店铺 |", "|:--|--:|:--|"]
        lines += head + rows + [""]

    tips = []
    if low:
        if identity_price(low):
            if plain:
                p = pick(plain.values())
                tips.append(f"最低价要新人/会员身份，不限身份最低 ¥{money(p['price'])}（{p['platform']}）")
            else:
                tips.append("最低价要新人/会员身份")
        if (low.get("calc") or {}).get("cond"):
            tips.append("会员、以旧换新等要满足条件的优惠没算进去")
        if unverified(low):
            tips.append("最低价是列表价，没能点进详情页核实优惠，实际可能更低")
        # 闲鱼是二手，不和全新的实时价比
        if any(p != "闲鱼" and r["low"] < low["price"] for p, r in refs.items()):
            tips.append("有参考价比实时价低，下单前请到该平台核实")
    if refs:
        tips.append("参考价来自公开网页，点开可看出处，不是实时价")
    lines.append("> " + " · ".join(tips + ["只比官方旗舰店 / 自营 / 直营店（拼多多只比品牌旗舰店和拼多多自营）", "详情、优惠和口碑见小票", "下单以 App 为准"]))
    return "\n".join(lines)


def unverified(i):
    """手机查到、但没点进商品页（或点进去出错了）：券和立减没核实。点进去了、页面上没有别的优惠，也算核实过。"""
    d = i.get("detail") or {}
    return i.get("source") == "手机App" and (not d or bool(d.get("error")))


def conditions(i):
    """说法 + 备注，去掉重复的（「入会价」在说法和备注里各出现一次时只留一个）。"""
    parts = []
    for x in re.split(r"[，；]", "，".join(x for x in (i["label"], i["note"]) if x)):
        if x and x not in parts:
            parts.append(x)
    return "，".join(parts)


def unconfirmed_lines(data, best):
    """标题没写容量（天猫常这样）、详情页也没确认规格的同款：不进结论，但告诉用户有这么个价。
    只在比这个平台已确认的最低价还便宜、或者这个平台没有已确认的价格时才提。"""
    out = []
    by = {}
    for r in data.get("rejected") or []:
        if not str(r.get("why") or "").startswith(("标题里没写容量", "标题没写容量")):
            continue
        p = ALIASES.get(r.get("platform"), r.get("platform"))
        by.setdefault(p, []).append(r)
    for p, rs in by.items():
        low = min(rs, key=lambda r: _f(r.get("price")))
        if p in best and _f(low.get("price")) >= best[p]["price"]:
            continue
        out.append(f"{p}另有 {len(rs)} 件型号对得上、但标题没写容量的商品（最低 ¥{money(_f(low['price']))}），"
                   f"可能是别的容量的起价，没算进结论。")
    return out


def detail_lines(items, limit=6):
    """每件点进过详情页的商品一行：「- 京东 · 三只松鼠旗舰店　满49减10 · 红包抵0.09」。"""
    out, seen = [], set()
    for i in items:
        promos = clean_promos((i.get("detail") or {}).get("promos"), limit)
        key = (i["platform"], i["shop"], i["price"])
        if not promos or key in seen:
            continue
        seen.add(key)
        out.append(f"- {i['platform']} · {i['shop'] or '—'}（¥{money(i['price'])}）　" + " · ".join(promos))
    return out


def render_platform(data, best, plain, skipped):
    """单个平台查完时的小报告：一个平台一行。"""
    keyword, spec = data["keyword"], data.get("spec") or ""
    lines = []
    platforms = list(dict.fromkeys([ALIASES.get(p, p) for p in data.get("searched") or []] + list(skipped)))
    for platform in platforms:
        apps = [x for x in data.get("apps") or [] if x != platform.split("/")[0]]  # 天猫 App 查的，标一下
        name = f"{platform}（{'、'.join(apps)} App）" if apps else platform
        i = best.get(platform)
        if not i:
            # 淘宝、天猫是两个 App：这里只说这次查的那个 App，不能写成「淘宝/天猫」都没有
            ran = [x for x in dict.fromkeys(data.get("apps") or []) if ALIASES.get(x, x) == platform]
            other = [x for x in platform.split("/") if x not in ran]
            lines.append(f"**{'、'.join(f'{x} App' for x in ran) or platform}**　没查到 · {skipped.get(platform) or '原因不明'}"
                         + (f"（不影响{other[0]}的结果）" if len(ran) == 1 and other else ""))
            continue
        n = sum(1 for v in data.get("items") or []
                if ALIASES.get(v.get("platform"), v.get("platform")) == platform and (platform == "闲鱼" or official(v))
                and matches(keyword, v.get("title") or "", platform) and spec_ok(spec, v.get("title") or ""))
        off = sum(v for _, v in (i.get("calc") or {}).get("steps") or [])
        lines.append(f"**{name}**　¥{money(i['price'])}" + (f" · {i['shop']}" if i["shop"] else "")
                     + (f" · 算上券和补贴共减 ¥{money(off)}" if off else "")
                     + (f" · 官方店同款 {n} 件" if n > 1 else ""))
    return "  \n".join(lines) or "这次没有查到任何平台。"


def from_bijia(d):
    """bijia.py --json 的输出 → 统一格式的 items。"""
    items = []
    for spec, plats in (d.get("best") or {}).items():
        for platform, cands in plats.items():
            for c in cands:
                tags = list(c.get("tags") or []) + (["百亿补贴"] if c.get("superSubsidy") else [])
                if c.get("coupon"):
                    tags.insert(0, f"券-{c['coupon']:g}" + (f" 至{c['couponEnd']}" if c.get("couponEnd") else ""))
                items.append({"platform": platform, "title": c.get("title") or "", "price": c.get("price"),
                              "original": c.get("original") or 0, "label": "券后价" if c.get("coupon") else "",
                              "shop": c.get("shop") or "", "note": "；".join(tags)[:40],
                              "source": "京东页面" if c.get("live") else "买手接口",
                              "url": c.get("url") or "", "couponUrl": c.get("couponUrl") or ""})
    return items


def load_many(paths, keyword=None, spec=None):
    """合并多个结果文件：zidong.py 的 mirror-*.json（每个平台一个）和 bijia.py --json 的输出。
    型号以手机查价文件为准（关键词和规格必须一致）；只有接口价时用 --keyword/--spec。"""
    merged = {"keyword": keyword, "spec": spec or "", "times": [], "items": [], "skipped": [], "searched": [], "rejected": [], "apps": []}
    for path in paths:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if "best" in d and "source_status" in d:  # bijia.py
            merged["items"] += from_bijia(d)
            if not (d.get("source_status") or {}).get("ok"):
                merged["bijia_down"] = True
            merged["bijia_keyword"] = d.get("keyword")
            continue
        if not merged.get("from_mirror"):  # 型号和规格以第一个手机查价文件为准（不管文件顺序）
            if merged["keyword"] and _norm(d["keyword"]) != _norm(merged["keyword"]):
                raise SystemExit(f"{path} 的关键词和 --keyword 不一致，不能合并")
            merged["keyword"], merged["spec"], merged["from_mirror"] = d["keyword"], d.get("spec") or "", True
        elif _norm(d["keyword"]) != _norm(merged["keyword"]) or (d.get("spec") or "") != merged["spec"]:
            raise SystemExit(f"{path} 的关键词/规格和其他文件不一致，不能合并")
        merged["times"].append(d.get("time") or "")
        for it in d.get("items") or []:
            it.setdefault("source", "手机App")
            merged["items"].append(it)
        merged["skipped"] += d.get("skipped") or []
        merged["rejected"] += d.get("rejected") or []
        merged["apps"] += d.get("apps") or []
        merged["searched"] += d.get("searched") or []
    merged["keyword"] = merged["keyword"] or merged.get("bijia_keyword")
    if not merged["keyword"]:
        raise SystemExit("没有关键词：只给了接口价文件时请加 --keyword")
    times = sorted(t for t in merged.pop("times") if t)
    merged["time"] = "" if not times else times[0] if len(set(times)) == 1 else f"{times[0]} 至 {times[-1][-5:]}"
    return merged


# 2.17：推广 / 跳转链接（一长串加密参数，点开先跳转、带返利追踪）不当出处：要文章、帖子本身的网址
AFFILIATE_URL = re.compile(r"^https?://(union-click\.jd\.com|u\.jd\.com|s\.click\.taobao\.com|uland\.taobao\.com|s\.click\.tmall\.com|"
                           r"p\.pinduoduo\.com|mobile\.yangkeduo\.com/duo_coupon|go\.smzdm\.com|click\.|[^/]*\.?t\.cn/|dwz\.cn|"
                           r"[^/]*/(jdc|redirect|go|jump|click)\?)", re.I)


def safe_url(v):
    """参考价的出处链接：只认 http(s)；括号、空格转义掉，免得弄坏 Markdown 链接。"""
    v = str(v or "").strip()
    if not re.match(r"^https?://\S+$", v) or AFFILIATE_URL.search(v):
        return ""
    return v.replace("(", "%28").replace(")", "%29").replace(" ", "%20")


def load_refs(path):
    """WebSearch 参考价：[{"platform", "low", "high"（可省）, "url", "source", "date"}]。
    每个平台留最低的一条；url 是查到这个价格的网页，表格和小票里点参考价就打开它。"""
    if not path:
        return {}
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    refs = {}
    for r in rows:
        p = ALIASES.get(r.get("platform"), r.get("platform"))
        low, high = _f(r.get("low")), _f(r.get("high")) or _f(r.get("low"))
        if p not in PLATFORMS or low <= 0 or not safe_url(r.get("url")):  # 没有能点开的出处（或是推广跳转链接）的不用
            continue
        text = f"约 ¥{money(low)}" if abs(high - low) < 0.01 else f"约 ¥{money(low)}–{money(high)}"
        note = "参考最新资料"  # 不写第三方网站、媒体的名字（source 只留在文件里备查）
        if p not in refs or low < refs[p]["low"]:
            refs[p] = {"platform": p, "low": low, "high": max(high, low), "text": text, "note": note, "url": safe_url(r.get("url"))}
    return refs


def parse_drop(v):
    """「平台:价格」或「平台:价格:店铺里的几个字」。只写价格时，这个平台所有这个价格的商品都排除。
    容错：全角冒号、¥ 符号、千位逗号都可以。"""
    parts = [x.strip() for x in v.replace("：", ":").split(":", 2)]
    if len(parts) < 2:
        raise argparse.ArgumentTypeError(f"--drop {v!r} 格式不对，应写成 平台:价格，如 京东:9294")
    p = ALIASES.get(parts[0], parts[0])
    if p not in PLATFORMS:
        raise argparse.ArgumentTypeError(f"--drop {v!r} 里的平台不认识，可用：京东、淘宝、拼多多、唯品会、闲鱼")
    try:
        price = float(re.sub(r"[¥￥,，\s]", "", parts[1]))
    except ValueError:
        raise argparse.ArgumentTypeError(f"--drop {v!r} 里的价格不是数字")
    return p, price, parts[2] if len(parts) > 2 else ""


HAOJIA_SITES = {"京东": "jd.zol.com.cn"}
SHORT_NAME = {"淘宝/天猫": "天猫", "抖音商城": "抖音"}
SITES = {"京东": "jd.com", "淘宝/天猫": "taobao.com", "拼多多": "pinduoduo.com", "唯品会": "vip.com",
         "抖音商城": "jinritemai.com", "闲鱼": "goofish.com"}


def web_queries(name, platforms):
    """给 WebSearch 用的搜索语句，按优先级排好：好价帖 → 好价站 → 平台站内。"""
    out = {}
    for p in platforms:
        p = ALIASES.get(p, p)
        if p not in PLATFORMS:
            continue
        short = SHORT_NAME.get(p, p)
        if p == "闲鱼":
            qs = [f"{name} 闲鱼 二手 成色 价格", f"{name} site:{SITES[p]}"]
        else:
            qs = [f"{name} {short} 到手价 好价"]
            if p in HAOJIA_SITES:
                qs.append(f"{name} 好价 site:{HAOJIA_SITES[p]}")
            qs.append(f"{name} {short} 价格 site:{SITES[p]}")
        out[p] = qs
    return out


def main():
    ap = argparse.ArgumentParser(description="汇总手机 App 实时价和 WebSearch 参考价")
    ap.add_argument("files", nargs="*", help="zidong.py 的 mirror-*.json，可以给多个")
    ap.add_argument("--queries", action="store_true",
                    help="不汇总，只输出每个平台的 WebSearch 搜索语句（JSON）；手机上查到了的平台也要查参考价")
    ap.add_argument("--ref", help="WebSearch 参考价文件（SKILL.md 规定的格式）")
    ap.add_argument("--keyword", help="只有接口价文件时用：型号")
    ap.add_argument("--spec", help="只有接口价文件时用：规格")
    ap.add_argument("--drop", action="append", default=[], type=parse_drop,
                    help="复核截图时发现对不上的商品：--drop 京东:9294 排除京东所有 9294 的商品；"
                         "--drop 京东:9694:奎屯 只排除店铺名里有「奎屯」的那条。可重复")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--platform-report", action="store_true",
                    help="单个平台查完时用：只输出这个平台的小报告（最低价、同款数量、详情页优惠）")
    a = ap.parse_args()

    if a.queries:
        if a.files:
            data = load_many(a.files, a.keyword, a.spec)
            best = summarize(data, a.drop)[0]
            name = f"{data['keyword']} {data['spec']}".strip()
        else:
            if not a.keyword:
                ap.error("没有结果文件时，--queries 需要 --keyword")
            best, name = {}, f"{a.keyword} {a.spec or ''}".strip()
        every = list(PLATFORMS)
        print(json.dumps({"keyword": name, "have_realtime": list(best), "need_reference": every,
                          "queries": web_queries(name, every)}, ensure_ascii=False, indent=1))
        return
    if not a.files and not a.keyword:
        ap.error("需要 zidong.py 的结果文件（mirror-*.json）；只有参考价时用 --keyword")
    data = load_many(a.files, a.keyword, a.spec)
    refs = load_refs(a.ref)
    best, plain, skipped, dropped = summarize(data, a.drop)
    for p, price, shop in a.drop:
        if not any(r.startswith("复核") and pl == p for pl, _, r in dropped):
            print(f"注意：--drop {p}:{price:g}{':' + shop if shop else ''} 没有排除掉任何商品，请检查平台、价格是否写对", file=sys.stderr)
    if a.platform_report:
        print(render_platform(data, best, plain, skipped))
        return
    if a.json:
        low = pick(best.values())
        print(json.dumps({"keyword": data["keyword"], "spec": data["spec"], "time": data["time"],
                          "lowest": low, "best": best, "best_without_identity": plain, "skipped": skipped,
                          "references": refs, "interface_down": bool(data.get("bijia_down")),
                          "dropped": [{"platform": p, "title": t, "reason": r} for p, t, r in dropped]},
                         ensure_ascii=False, indent=1))
    else:
        print(render_md(data, best, plain, skipped, refs))


if __name__ == "__main__":
    main()
