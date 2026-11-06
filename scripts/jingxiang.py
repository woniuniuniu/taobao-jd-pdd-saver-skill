#!/usr/bin/env python3
"""手机比价 · iPhone 镜像操控工具：截图、点击、输入、滚动、回主屏，全部只作用于「iPhone 镜像」窗口。

给没有 Computer Use 工具的环境用。所有坐标都是**最近一次 shot 截图里的像素坐标**，
脚本自己换算成屏幕坐标，所以不用管双屏、Retina 缩放、窗口位置。

每次点击/输入前脚本会：
  1. 确认最近一次截图不超过 120 秒，而且镜像窗口从那以后没有移动或缩放；
  2. 把 iPhone 镜像切到最前面，并确认它真的在最前面，否则不发任何事件；
  3. 确认坐标落在镜像窗口里。
任何一条不满足就报错退出（退出码 1），什么都不做。

依赖：macOS 自带的 python3、osascript、screencapture。需要「屏幕录制」和「辅助功能」权限。

用法：
  python3 jingxiang.py shot                 # 截镜像窗口，输出图片路径和尺寸
  python3 jingxiang.py tap 50 65 --pct      # 点击截图宽 50%、高 65% 的位置（推荐，不怕截图被缩放）
  python3 jingxiang.py tap 23 40 --zoom     # 点击最近一张放大图（zoom）上宽 23%、高 40% 的位置，脚本换算回原图
  python3 jingxiang.py tap 348 1000         # 点击截图里 (348,1000) 这个像素位置
  python3 jingxiang.py type iPhone18Pro      # 只允许英文字母、数字和空格（中文请用 zidong.py，它会用拼音输入法打中文）
  python3 jingxiang.py key return           # return / delete / escape / space / switch，可加 --count 3
  python3 jingxiang.py scroll down          # down 看下面的内容 / up；left 看右边一页 / right
  python3 jingxiang.py home                 # 回主屏幕（⌘1）
  python3 jingxiang.py spotlight            # 打开手机的 Spotlight 搜索（⌘3）
  python3 jingxiang.py zoom 40 30 100 45    # 把最近一张截图的这块区域（宽 40–100%、高 30–45%）放大 3 倍，看清小字
  python3 jingxiang.py ocr                  # 识别最近一张截图里的文字，输出每段文字和它的位置（百分比）
  python3 jingxiang.py tap-text 搜索 --region top   # 点击截图里文字是「搜索」的地方（top=上 30%，bottom=下 15%）
"""
import argparse
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import time

BUNDLE = "com.apple.ScreenContinuity"
OWNER_NAMES = ("iPhone Mirroring", "iPhone 镜像", "iPhone镜像")
STATE_DIR = os.path.join(tempfile.gettempdir(), "shouji-bijia-jingxiang")
# WorkBuddy 沙盒只允许新建文件、不允许覆盖，所以每次截图都写一个新的状态文件，读取时取最新的
STATE_PREFIX = "state-"
ZOOM_PREFIX = "zoomstate-"
MAX_SHOT_AGE = 120

# 美式键盘虚拟键码
KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11,
    "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21,
    "6": 22, "5": 23, "9": 25, "7": 26, "8": 28, "0": 29, "o": 31, "u": 32, "i": 34, "p": 35,
    "l": 37, "j": 38, "k": 40, "n": 45, "m": 46, " ": 49,
}
# switch = Ctrl+空格，切换手机输入法；space 在中文输入法里是「选第一个候选词」；forward_delete 删光标右边的字
SPECIAL_KEYS = {"return": 36, "delete": 51, "escape": 53, "switch": 49, "space": 49, "forward_delete": 117}
# tap-text 拒绝点击的文字：任何可能改变订单、购物车、账号状态的按钮
FORBIDDEN_TAP = ("购买", "加入购物车", "加购", "结算", "领券", "领取", "立即抢", "抢购", "关注", "收藏", "分享",
                 "全选", "支付", "提交订单", "确认订单", "登录", "同意", "授权", "下单", "删除")
REGIONS = {"top": (0, 30), "bottom": (85, 100), "middle": (30, 85), "all": (0, 100)}

# macOS 自带 Vision 框架做文字识别（中英文），不需要装任何东西
OCR_JS = """ObjC.import('Foundation'); ObjC.import('Vision');
function run(argv) {
  var req = $.VNRecognizeTextRequest.alloc.init;
  req.recognitionLevel = 0; req.usesLanguageCorrection = false;
  req.recognitionLanguages = $(['zh-Hans', 'en-US']);
  var h = $.VNImageRequestHandler.alloc.initWithURLOptions($.NSURL.fileURLWithPath(argv[0]), $({}));
  if (!h.performRequestsError($([req]), Ref())) return '[]';
  var res = req.results, out = [];
  for (var i = 0; i < res.count; i++) {
    var o = res.objectAtIndex(i), c = o.topCandidates(1).objectAtIndex(0), b = o.boundingBox;
    out.push({t: ObjC.unwrap(c.string), x: b.origin.x * 100, y: (1 - b.origin.y - b.size.height) * 100,
              w: b.size.width * 100, h: b.size.height * 100});
  }
  return JSON.stringify(out);
}
"""


class Fail(Exception):
    pass


PHONE_LOCK = os.path.expanduser("~/WorkBuddy/.chaoji-phone.lock")
_lock_fd = None


def phone_lock(wait=180):
    """同一时间只让一个任务操作手机（手动比价、购物车体检、到点的定时盯价可能撞在一起，两边的点击会互相打乱）。
    拿不到就每 2 秒再试，等 wait 秒还拿不到就报错退出，一下都不点。锁跟着进程走，进程结束（包括出错、被杀）就自动放开。
    只读打开锁文件：WorkBuddy 的沙盒不允许改写已有文件。"""
    global _lock_fd
    if _lock_fd is not None:
        return
    import fcntl
    os.makedirs(os.path.dirname(PHONE_LOCK), exist_ok=True)
    fd = os.open(PHONE_LOCK, os.O_RDONLY | os.O_CREAT, 0o644)
    deadline = time.time() + wait
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _lock_fd = fd
            return
        except BlockingIOError:
            if time.time() > deadline:
                os.close(fd)
                raise Fail(f"手机正在被另一个查价任务使用（比如到点的定时盯价），等了 {wait} 秒它还没做完。"
                           "请等它做完再让我查一次。本次没有操作手机。")
            time.sleep(2)


def jxa(script):
    r = subprocess.run(["osascript", "-l", "JavaScript", "-e", script], capture_output=True, text=True)
    if r.returncode != 0:
        raise Fail(f"osascript 失败：{r.stderr.strip()}")
    return r.stdout.strip()


def pinyin(text):
    """汉字转拼音（macOS 自带的转换，不带声调），「三只松鼠」→「san zhi song shu」；英文数字原样保留。
    ü 按手机拼音键盘的打法写成 v（「绿」→ lv）。"""
    return jxa(f"""
ObjC.import('Foundation');
var s = $({json.dumps(text)}).stringByApplyingTransformReverse($.NSStringTransformMandarinToLatin, false).js;
$(s.replace(/[üǖǘǚǜ]/g, 'v')).stringByApplyingTransformReverse($.NSStringTransformStripDiacritics, false).js;""").lower()


def mirror_running():
    """iPhone 镜像这个 App 有没有在运行。"""
    return jxa(f"""
ObjC.import('AppKit');
$.NSRunningApplication.runningApplicationsWithBundleIdentifier('{BUNDLE}').count > 0;""") == "true"


def launch_mirror():
    """打开 iPhone 镜像（已经开着就把窗口调出来，最小化了也会恢复）。"""
    r = subprocess.run(["open", "-b", BUNDLE], capture_output=True, text=True)
    if r.returncode != 0:
        raise Fail("这台 Mac 上打不开「iPhone 镜像」（需要 macOS 15 以上、Apple 芯片或 T2 芯片的 Mac，"
                   f"iPhone 需要 iOS 18 以上）：{r.stderr.strip()}")


def find_window():
    """返回镜像窗口 {id, x, y, w, h}（屏幕坐标，单位点，原点在主屏左上角，多屏通用）。"""
    out = jxa("""
ObjC.import('CoreGraphics');
var list = ObjC.deepUnwrap(ObjC.castRefToObject(
  $.CGWindowListCopyWindowInfo($.kCGWindowListOptionOnScreenOnly | $.kCGWindowListExcludeDesktopElements, 0)));
var found = [];
(list || []).forEach(function (w) {
  if (w.kCGWindowLayer === 0) found.push({owner: w.kCGWindowOwnerName, id: w.kCGWindowNumber, b: w.kCGWindowBounds});
});
JSON.stringify(found);
""")
    wins = [w for w in json.loads(out or "[]") if w["owner"] in OWNER_NAMES]
    if not wins:
        raise Fail("没找到 iPhone 镜像窗口：请确认 iPhone 镜像已打开、已连上手机、窗口没有最小化。"
                   "如果镜像明明开着，多半是缺「屏幕录制」权限。")
    w = max(wins, key=lambda w: w["b"]["Width"] * w["b"]["Height"])
    b = w["b"]
    return {"id": w["id"], "x": b["X"], "y": b["Y"], "w": b["Width"], "h": b["Height"]}


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise Fail("截图文件不是 PNG")
    return struct.unpack(">II", head[16:24])


def ocr_image(path):
    """返回 [{t 文字, x y w h（左上角和宽高，单位是图片宽/高的百分比）, cx cy 中心}]，按从上到下排序。"""
    os.makedirs(STATE_DIR, exist_ok=True)
    js = os.path.join(STATE_DIR, "ocr-%s.js" % hashlib.md5(OCR_JS.encode()).hexdigest()[:8])
    if not os.path.exists(js):
        with open(js, "x", encoding="utf-8") as f:
            f.write(OCR_JS)
    r = subprocess.run(["osascript", "-l", "JavaScript", js, path], capture_output=True, text=True)
    if r.returncode != 0:
        raise Fail(f"文字识别失败：{r.stderr.strip()}")
    lines = json.loads(r.stdout.strip() or "[]")
    for o in lines:
        o["cx"], o["cy"] = o["x"] + o["w"] / 2, o["y"] + o["h"] / 2
    lines.sort(key=lambda o: (round(o["y"] * 2), o["x"]))
    return lines


def in_region(o, region):
    lo, hi = REGIONS[region] if region in REGIONS else map(float, region.split("-"))
    return lo <= o["cy"] <= hi


def activate():
    """把镜像切到最前面，确认后才返回；切不过来就报错。
    系统通知、对话框会短暂抢到最前面：多试几次（约 6 秒），中途再用 open 强制切一次。"""
    front = ""
    for i in range(10):
        if i == 4:
            subprocess.run(["open", "-b", BUNDLE], capture_output=True)
        front = jxa(f"""
ObjC.import('AppKit');
var apps = $.NSRunningApplication.runningApplicationsWithBundleIdentifier('{BUNDLE}');
if (apps.count > 0) apps.objectAtIndex(0).activateWithOptions($.NSApplicationActivateIgnoringOtherApps);
delay(0.35);
ObjC.unwrap($.NSWorkspace.sharedWorkspace.frontmostApplication.bundleIdentifier);
""")
        if front == BUNDLE:
            return
        time.sleep(0.3)
    if front in ("com.apple.UserNotificationCenter", "com.apple.SecurityAgent", "com.apple.CoreServicesUIAgent"):
        raise Fail("Mac 屏幕上弹出了一个系统对话框（多半是权限请求，比如「允许 WorkBuddy 访问…」，或者要点按钮的系统通知），"
                   "挡在 iPhone 镜像前面，脚本切不过去。请看一眼 Mac 屏幕，处理掉这个对话框（是权限请求就按需点「允许」），"
                   "然后让我再查一次。本次没有发送任何点击或按键。")
    raise Fail(f"没能把 iPhone 镜像切到最前面（当前最前面的是 {front}）。请别在查价时操作电脑，然后让我再查一次。"
               "本次没有发送任何点击或按键。")


def load_state():
    names = sorted(n for n in os.listdir(STATE_DIR) if n.startswith(STATE_PREFIX)) if os.path.isdir(STATE_DIR) else []
    if not names:
        raise Fail("还没有截图：先运行 shot，再按截图里的坐标操作。")
    with open(os.path.join(STATE_DIR, names[-1]), encoding="utf-8") as f:
        st = json.load(f)
    if time.time() - st["time"] > MAX_SHOT_AGE:
        raise Fail(f"最近一次截图已超过 {MAX_SHOT_AGE} 秒，画面可能变了：先重新 shot。")
    win = find_window()
    if any(abs(win[k] - st["win"][k]) > 1 for k in ("x", "y", "w", "h")):
        raise Fail("镜像窗口在截图后移动或缩放过：先重新 shot。")
    return st


def to_screen(st, px, py):
    sx, sy = st["img_w"] / st["win"]["w"], st["img_h"] / st["win"]["h"]
    if not (0 <= px < st["img_w"] and 0 <= py < st["img_h"]):
        raise Fail(f"坐标 ({px},{py}) 不在截图范围内（{st['img_w']}×{st['img_h']}）。")
    return st["win"]["x"] + px / sx, st["win"]["y"] + py / sy


def post_click(x, y):
    jxa(f"""
ObjC.import('CoreGraphics');
var p = $.CGPointMake({x:.1f}, {y:.1f});
function post(t) {{ $.CGEventPost($.kCGHIDEventTap, $.CGEventCreateMouseEvent(null, t, p, 0)); }}
post($.kCGEventMouseMoved); delay(0.08);
post($.kCGEventLeftMouseDown); delay(0.09);
post($.kCGEventLeftMouseUp);
""")


def paste():
    """在手机当前的输入框里粘贴（⌘V）。要先按下真的 Command 键再按 V：只在 V 上带 Command 标记，镜像会当成打了一个「v」。
    粘贴的是 Mac 剪贴板里的文字（「通用剪贴板」同步到手机）。镜像菜单里的「编辑 → 粘贴」是灰的，用不了。"""
    activate()
    jxa("""
ObjC.import('CoreGraphics');
var src = $.CGEventSourceCreate($.kCGEventSourceStateHIDSystemState);
function key(code, down, flags) { var e = $.CGEventCreateKeyboardEvent(src, code, down); $.CGEventSetFlags(e, flags); $.CGEventPost($.kCGHIDEventTap, e); }
var CMD = $.kCGEventFlagMaskCommand;
key(55, true, CMD); delay(0.05);
key(9, true, CMD); delay(0.05);
key(9, false, CMD); delay(0.05);
key(55, false, 0);
""")


UTF8 = dict(os.environ, LANG="en_US.UTF-8", LC_ALL="en_US.UTF-8")  # 不设的话 pbcopy / pbpaste 会把中文变成问号


def clipboard_get():
    r = subprocess.run(["pbpaste"], capture_output=True, env=UTF8)
    return r.stdout.decode("utf-8", "replace")


def clipboard_text_only():
    """Mac 剪贴板里是不是只有文字（或是空的）。有图片、文件时返回 False：那些放不回去，就别借剪贴板用。"""
    r = subprocess.run(["osascript", "-e", "clipboard info"], capture_output=True, text=True)
    return r.returncode == 0 and not re.search(r"PNGf|TIFF|JPEG|GIFf|PDF|furl|icns|8BPS|BMP|jp2", r.stdout)


def clipboard_set(text):
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), env=UTF8, check=True)


def post_keys(codes, cmd=False, ctrl=False):
    """codes: [(keycode, shift)]"""
    seq = json.dumps(codes)
    jxa(f"""
ObjC.import('CoreGraphics');
var seq = {seq}, CMD = {1 if cmd else 0}, CTRL = {1 if ctrl else 0};
seq.forEach(function (k) {{
  var flags = (k[1] ? $.kCGEventFlagMaskShift : 0) | (CMD ? $.kCGEventFlagMaskCommand : 0) | (CTRL ? $.kCGEventFlagMaskControl : 0);
  [true, false].forEach(function (down) {{
    var e = $.CGEventCreateKeyboardEvent(null, k[0], down);
    $.CGEventSetFlags(e, flags);
    $.CGEventPost($.kCGHIDEventTap, e);
    delay(0.04);
  }});
  delay(0.06);
}});
""")


def cmd_shot(a):
    # iPhone 镜像偶尔会自己重启、窗口号会变：失败时重新找窗口再截，最多 3 次
    for attempt in range(3):
        try:
            return _shot_once(a)
        except Fail:
            if attempt == 2:
                raise
            time.sleep(2)


# ———— 录屏流（2.16）：持续录 iPhone 镜像窗口，从流里取帧，手机不会当成截屏 ————
JIETU_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jietu.swift")
JIETU_DIR = os.path.expanduser("~/WorkBuddy/.chaoji-jietu")
_jietu = {"proc": None, "off": False, "why": ""}


def _jietu_bin():
    """编译好的录屏小助手：按源码内容的哈希缓存（源码改了会重新编译）。没有 swiftc（没装 Xcode 命令行工具）或编译失败返回 None。"""
    try:
        with open(JIETU_SRC, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()[:12]
    except OSError:
        return None
    os.makedirs(JIETU_DIR, exist_ok=True)
    binary = os.path.join(JIETU_DIR, f"jietu-{digest}")
    failed = binary + ".failed"
    if os.path.exists(binary):
        return binary
    if os.path.exists(failed) or not subprocess.run(["which", "swiftc"], capture_output=True).stdout.strip():
        return None
    tmp = binary + ".%d.tmp" % os.getpid()
    try:
        r = subprocess.run(["swiftc", "-O", JIETU_SRC, "-o", tmp], capture_output=True, text=True, timeout=240)
    except (OSError, subprocess.TimeoutExpired) as e:
        r = subprocess.CompletedProcess([], 1, "", str(e))
    if r.returncode != 0 or not os.path.exists(tmp):
        try:
            with open(failed + ".%d" % os.getpid(), "w") as f:
                f.write((r.stderr or "")[-2000:])
            os.replace(failed + ".%d" % os.getpid(), failed)
        except OSError:
            pass
        return None
    os.replace(tmp, binary)
    return binary


def _jietu_proc():
    """启动（或复用）录屏小助手。起不来就记下来，这次运行都用普通截图。"""
    p = _jietu["proc"]
    if p and p.poll() is None:
        return p
    if _jietu["off"] or os.environ.get("CHAOJI_NO_STREAM"):
        return None
    binary = _jietu_bin()
    if not binary:
        _jietu.update(off=True, why="没法编译录屏小助手（没有 swiftc），用普通截图")
        return None
    try:
        p = subprocess.Popen([binary], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        line = _readline(p, 10)
    except OSError as e:
        line = f"fail {e}"
    if line != "ready":
        try:
            p.kill()
        except Exception:
            pass
        _jietu.update(off=True, why=f"录屏小助手没启动起来（{line or '超时'}），用普通截图")
        return None
    _jietu["proc"] = p
    return p


def _readline(p, timeout):
    import select
    r, _, _ = select.select([p.stdout], [], [], timeout)
    return p.stdout.readline().strip() if r else ""


def stream_ok():
    """这次运行能不能用录屏流截图（能的话手机不会弹截屏浮层）。"""
    return _jietu_proc() is not None


def _stream_shot(path):
    p = _jietu_proc()
    if not p:
        return False
    try:
        p.stdin.write(f"shot {path}\n")
        p.stdin.flush()
        line = _readline(p, 5)
    except (OSError, ValueError):
        line = ""
    if line.startswith("ok") and os.path.exists(path):
        return True
    # 录屏断了（镜像窗口关了又开、窗口号变了）：关掉，下次重新起一个
    try:
        p.kill()
    except Exception:
        pass
    _jietu["proc"] = None
    return False


def _shot_once(a):
    win = find_window()
    os.makedirs(STATE_DIR, exist_ok=True)
    stamp = "%s-%06d" % (time.strftime("%Y%m%d-%H%M%S"), int(time.time() * 1e6) % 1000000)
    path = a.out or os.path.join(STATE_DIR, f"shot-{stamp}.png")
    if _stream_shot(path) or _stream_shot(path):  # 第二次会重新起录屏流
        w, h = png_size(path)
        with open(os.path.join(STATE_DIR, f"{STATE_PREFIX}{stamp}.json"), "x", encoding="utf-8") as f:
            json.dump({"time": time.time(), "win": win, "img_w": w, "img_h": h, "path": path}, f)
        return {"ok": True, "image": path, "width": w, "height": h, "via": "stream",
                "note": "看这张图，tap 用 --pct 给目标在图中宽、高的百分比"}
    # 2.15：先直接读窗口的画面（CGWindowListCreateImage），不走 screencapture。实测（11-05）这台 Mac 上用 screencapture 截
    # 镜像窗口，iPhone 镜像会把它当成手机截屏：京东每次都弹「分享截屏」、淘宝弹「识别截图」，点返回退不出去，只能把 App 划掉
    # ——以前以为是辅助触控小白点，其实是截图本身。直接读窗口画面手机那边不知道。
    try:
        jxa(f"""
ObjC.import('CoreGraphics'); ObjC.import('AppKit');
var img = $.CGWindowListCreateImage($.CGRectNull, $.kCGWindowListOptionIncludingWindow, {int(win['id'])},
                                    $.kCGWindowImageBoundsIgnoreFraming | $.kCGWindowImageBestResolution);
var rep = $.NSBitmapImageRep.alloc.initWithCGImage(img);
rep.representationUsingTypeProperties($.NSBitmapImageFileTypePNG, $()).writeToFileAtomically({json.dumps(path)}, true);
""")
    except Exception:
        pass
    r = subprocess.CompletedProcess([], 0, "", "")
    if not os.path.exists(path) or os.path.getsize(path) < 1000:
        r = subprocess.run(["screencapture", "-x", "-o", f"-l{win['id']}", path], capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(path):
        # 有的 Mac 上按窗口截（-l）会报「could not create image from window」，按窗口所在的屏幕区域截也一样（镜像窗口要在最上面）
        activate()
        time.sleep(0.3)
        r = subprocess.run(["screencapture", "-x", f"-R{win['x']},{win['y']},{win['w']},{win['h']}", path],
                           capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(path):
        raise Fail(f"截图失败：{(r.stderr or r.stdout).strip() or '未生成文件'}。多半是缺「屏幕录制」权限。")
    w, h = png_size(path)
    with open(os.path.join(STATE_DIR, f"{STATE_PREFIX}{stamp}.json"), "x", encoding="utf-8") as f:
        json.dump({"time": time.time(), "win": win, "img_w": w, "img_h": h, "path": path}, f)
    return {"ok": True, "image": path, "width": w, "height": h,
            "note": "看这张图，tap 用 --pct 给目标在图中宽、高的百分比"}


def cmd_tap(a):
    st = load_state()
    if a.zoom:
        names = sorted(n for n in os.listdir(STATE_DIR) if n.startswith(ZOOM_PREFIX))
        zs = None
        if names:
            with open(os.path.join(STATE_DIR, names[-1]), encoding="utf-8") as f:
                zs = json.load(f)
        if not zs or zs["shot"] != st["path"]:
            raise Fail("最近一次截图之后还没有 zoom：--zoom 只能用在「shot → zoom」之后。")
        x1, y1, x2, y2 = zs["box"]
        px, py = x1 + a.x / 100 * (x2 - x1), y1 + a.y / 100 * (y2 - y1)
    elif a.pct:
        px, py = a.x / 100 * st["img_w"], a.y / 100 * st["img_h"]
    else:
        px, py = a.x, a.y
    x, y = to_screen(st, px, py)
    activate()
    post_click(x, y)
    time.sleep(a.wait)
    return {"ok": True, "tapped": [a.x, a.y], "mode": "zoom" if a.zoom else ("pct" if a.pct else "px"),
            "screenshot_pct": [round(px / st["img_w"] * 100, 1), round(py / st["img_h"] * 100, 1)], "next": "画面可能已变化，下一步操作前先 shot"}


def cmd_type(a):
    text = a.text
    if not text or not text.isascii() or not text.replace(" ", "").isalnum():
        raise Fail("只能输入英文字母、数字和空格，不能有标点或中文。")
    load_state()
    # 2.16：分成 4 个字一段打，每段之前都确认镜像在最前面——打字时别的窗口（微信、语音输入、WorkBuddy）跳到前面，
    # 后面的字就打进别的窗口里了（11-05 实测京东「搜索框里的文字一直不对」就是这样）
    for i in range(0, len(text), 4):
        activate()
        post_keys([(KEYCODES[c.lower()], c.isupper()) for c in text[i:i + 4]])
    time.sleep(a.wait)
    return {"ok": True, "typed": text, "next": "先 shot 核对搜索框里的文字再继续"}


def cmd_key(a):
    load_state()
    activate()
    post_keys([(SPECIAL_KEYS[a.name], False)] * a.count, ctrl=a.name == "switch")
    time.sleep(a.wait)
    return {"ok": True, "key": a.name, "count": a.count}


def cmd_scroll(a):
    st = load_state()
    win = st["win"]
    cx, cy = win["x"] + win["w"] / 2, win["y"] + win["h"] / 2
    activate()
    if a.direction in ("up", "down"):
        # 竖向：触控板滚轮事件（拖拽在列表页上不滚动）。down = 看下面的内容
        total = int(win["h"] * getattr(a, "frac", 0.6)) * (-1 if a.direction == "down" else 1)
        jxa(f"""
ObjC.import('CoreGraphics');
$.CGEventPost($.kCGHIDEventTap, $.CGEventCreateMouseEvent(null, $.kCGEventMouseMoved, $.CGPointMake({cx:.1f}, {cy:.1f}), 0));
delay(0.05);
for (var i = 0; i < 12; i++) {{
  $.CGEventPost($.kCGHIDEventTap, $.CGEventCreateScrollWheelEvent(null, $.kCGScrollEventUnitPixel, 1, {total // 12}));
  delay(0.02);
}}
""")
    else:
        # 横向：模拟手指快速一划翻页。left = 看右边一页（手指从右往左拖）。
        # 要快、中途不停：主屏幕上按住图标停顿一下就成了长按，图标会被拖走、进入编辑状态
        dx = win["w"] * 0.45 * (-1 if a.direction == "left" else 1)
        sx = cx - dx / 2
        jxa(f"""
ObjC.import('CoreGraphics');
function ev(t, x, y) {{ $.CGEventPost($.kCGHIDEventTap, $.CGEventCreateMouseEvent(null, t, $.CGPointMake(x, y), 0)); }}
var sx = {sx:.1f}, cy = {cy:.1f}, dx = {dx:.1f};
ev($.kCGEventMouseMoved, sx, cy); delay(0.05);
ev($.kCGEventLeftMouseDown, sx, cy);
for (var i = 1; i <= 8; i++) {{ ev($.kCGEventLeftMouseDragged, sx + dx * i / 8, cy); delay(0.008); }}
ev($.kCGEventLeftMouseUp, sx + dx, cy);
""")
    time.sleep(a.wait)
    return {"ok": True, "scrolled": a.direction}


def column_pixels(path, xfrac, y0=0.0, y1=1.0, step=2):
    """截图里竖着一条线上的像素颜色 [(y 百分比, r, g, b)]，用来找文字识别认不出来的图标（弹窗下面的 ⊗）。"""
    out = jxa(f"""
ObjC.import('AppKit');
var rep = $.NSBitmapImageRep.imageRepWithContentsOfFile({json.dumps(path)});
var w = rep.pixelsWide, h = rep.pixelsHigh, x = Math.round(w * {xfrac}), res = [];
for (var y = Math.round(h * {y0}); y < Math.round(h * {y1}); y += {step}) {{
  var c = rep.colorAtXY(x, y);
  res.push([+(y / h * 100).toFixed(2), Math.round(c.redComponent * 255), Math.round(c.greenComponent * 255), Math.round(c.blueComponent * 255)]);
}}
JSON.stringify(res);
""")
    return [tuple(v) for v in json.loads(out or "[]")]


def find_close_circle(path, xfrac=0.5, y0=0.5, y1=0.95):
    """弹窗正下方的关闭按钮 ⊗：一个灰色（不带颜色）的圆，中间是白色的 ×。沿竖线找「一段灰 + 中间有白点」，
    长度在 2%–7% 屏高之间、上下两头接着的是更暗或带颜色的背景。返回圆心 y 百分比，找不到返回 None。"""
    px = column_pixels(path, xfrac, y0, y1)
    gray = lambda r, g, b: max(r, g, b) - min(r, g, b) < 22 and 95 < (r + g + b) / 3 < 236
    white = lambda r, g, b: min(r, g, b) > 236
    i = 0
    while i < len(px):
        if not (gray(*px[i][1:]) or white(*px[i][1:])):
            i += 1
            continue
        j = i
        while j + 1 < len(px) and (gray(*px[j + 1][1:]) or white(*px[j + 1][1:])):
            j += 1
        seg = px[i:j + 1]
        span = seg[-1][0] - seg[0][0]
        whites = [p for p in seg if white(*p[1:])]
        grays = [p for p in seg if gray(*p[1:])]
        if 2.0 <= span <= 7.0 and whites and len(grays) >= len(seg) * 0.5:
            mid = (seg[0][0] + seg[-1][0]) / 2
            core = [p[0] for p in whites if abs(p[0] - mid) < span * 0.35]
            if core:
                return round(sum(core) / len(core), 1)  # × 的中心（白点）比灰段中点准：灰段下面常带一截阴影
        i = j + 1
    return None


def ocr_crop(path, x1, y1, x2, y2, scale=3):
    """只认截图里的一小块（放大 scale 倍再认，小图标、白字也认得出）。返回的坐标换算回整张截图的百分比。"""
    w, h = png_size(path)
    px1, py1, px2, py2 = int(x1 / 100 * w), int(y1 / 100 * h), min(int(x2 / 100 * w), w - 2), min(int(y2 / 100 * h), h - 2)
    stamp = "%s-%06d" % (time.strftime("%Y%m%d-%H%M%S"), int(time.time() * 1e6) % 1000000)
    out = os.path.join(STATE_DIR, f"crop-{stamp}.png")
    r = subprocess.run(["sips", "-c", str(py2 - py1), str(px2 - px1), "--cropOffset", str(py1), str(px1), path, "--out", out],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return []
    subprocess.run(["sips", "-Z", str(max(px2 - px1, py2 - py1) * scale), out], capture_output=True, text=True)
    lines = ocr_image(out)
    for o in lines:
        for k, base, span in (("x", x1, x2 - x1), ("cx", x1, x2 - x1), ("y", y1, y2 - y1), ("cy", y1, y2 - y1)):
            o[k] = base + o[k] / 100 * span
        o["w"], o["h"] = o["w"] / 100 * (x2 - x1), o["h"] / 100 * (y2 - y1)
    return lines


def cmd_zoom(a):
    """只读：从最近一次截图里裁一块放大，用系统自带 sips，不需要装任何包。"""
    st = load_state()
    w, h = st["img_w"], st["img_h"]
    x1, y1, x2, y2 = (int(v / 100 * (w if i % 2 == 0 else h)) for i, v in enumerate((a.x1, a.y1, a.x2, a.y2)))
    if not (0 <= x1 < x2 <= w and 0 <= y1 < y2 <= h):
        raise Fail("区域不对：要求 0 ≤ x1 < x2 ≤ 100、0 ≤ y1 < y2 ≤ 100。")
    # sips 的裁剪区域贴到右边或底边时会不裁、原样输出整张图，所以离边缘留 2 像素
    x2, y2 = min(x2, w - 2), min(y2, h - 2)
    stamp = "%s-%06d" % (time.strftime("%Y%m%d-%H%M%S"), int(time.time() * 1e6) % 1000000)
    out = os.path.join(STATE_DIR, f"zoom-{stamp}.png")
    r = subprocess.run(["sips", "-c", str(y2 - y1), str(x2 - x1), "--cropOffset", str(y1), str(x1),
                        st["path"], "--out", out], capture_output=True, text=True)
    if r.returncode != 0 or png_size(out) != (x2 - x1, y2 - y1):
        raise Fail(f"裁剪失败：{(r.stderr or r.stdout).strip() or '尺寸不对'}")
    r = subprocess.run(["sips", "-Z", str(max(x2 - x1, y2 - y1) * a.scale), out], capture_output=True, text=True)
    if r.returncode != 0:
        raise Fail(f"放大失败：{(r.stderr or r.stdout).strip()}")
    zw, zh = png_size(out)
    with open(os.path.join(STATE_DIR, f"{ZOOM_PREFIX}{stamp}.json"), "x", encoding="utf-8") as f:
        json.dump({"shot": st["path"], "box": [x1, y1, x2, y2]}, f)
    return {"ok": True, "image": out, "width": zw, "height": zh,
            "note": "要点放大图里的东西：tap <放大图上的宽%> <放大图上的高%> --zoom，脚本自己换算，不要自己换算回原图"}


def cmd_ocr(a):
    st = load_state()
    lines = [o for o in ocr_image(st["path"]) if in_region(o, a.region)]
    return {"ok": True, "image": st["path"], "lines": [
        {"text": o["t"], "x": round(o["cx"], 1), "y": round(o["cy"], 1)} for o in lines],
        "note": "x、y 是文字中心在截图宽、高的百分比，可以直接给 tap --pct"}


def find_text(lines, text, region="all", exact=False):
    """找文字是 text（或包含 text）的识别结果，返回 (x, y) 百分比；找不到返回 None。"""
    hits = []
    for o in lines:
        if not in_region(o, region):
            continue
        t = o["t"].replace(" ", "")
        want = text.replace(" ", "")
        if (t == want) if exact else (want in t):
            # 按字符位置估算这几个字在整行里的横向位置
            i = t.find(want)
            frac = (i + len(want) / 2) / max(len(t), 1)
            hits.append((o["x"] + o["w"] * frac, o["cy"], t == want))
    if not hits:
        return None
    hits.sort(key=lambda h: not h[2])  # 完全相等的优先
    return hits[0][0], hits[0][1]


def cmd_tap_text(a):
    if any(w in a.text for w in FORBIDDEN_TAP):
        raise Fail(f"「{a.text}」可能改变订单、购物车或账号状态，不允许点击。")
    st = load_state()
    pos = find_text(ocr_image(st["path"]), a.text, a.region, a.exact)
    if not pos:
        raise Fail(f"截图的 {a.region} 区域里没找到文字「{a.text}」。先 ocr 看看实际有哪些文字。")
    x, y = to_screen(st, pos[0] / 100 * st["img_w"], pos[1] / 100 * st["img_h"])
    activate()
    post_click(x, y)
    time.sleep(a.wait)
    return {"ok": True, "tapped_text": a.text, "screenshot_pct": [round(pos[0], 1), round(pos[1], 1)],
            "next": "画面可能已变化，下一步操作前先 shot"}


def clear_field():
    """清空当前输入框：光标可能落在旧字中间，往右、往左各删一遍。
    不能用 ⌘A：镜像不会把它当快捷键转给手机，手机上会真的打出一个 a。"""
    load_state()
    activate()
    # 2.17：每边删 60 个：粘错时框里可能是一整句话（剪贴板里原来的字），只删 24 个会剩半句，接着打的字就跟在后面
    post_keys([(SPECIAL_KEYS["forward_delete"], False)] * 60 + [(SPECIAL_KEYS["delete"], False)] * 60)
    time.sleep(0.5)


def app_switcher():
    """打开手机的 App 切换器（⌘2）。"""
    find_window()
    activate()
    post_keys([(KEYCODES["2"], False)], cmd=True)
    time.sleep(1.5)


def swipe_up_center(x_frac=0.5):
    """在 App 切换器里把横坐标 x_frac（占窗口宽度的比例）处的卡片往上划走（等于在手机上上滑关掉这个 App）。"""
    st = load_state()
    w = st["win"]
    activate()
    x, y0, y1 = w["x"] + w["w"] * x_frac, w["y"] + w["h"] * 0.55, w["y"] + w["h"] * 0.08
    jxa(f"""
ObjC.import('CoreGraphics');
function ev(t, x, y) {{ $.CGEventPost($.kCGHIDEventTap, $.CGEventCreateMouseEvent(null, t, $.CGPointMake(x, y), 0)); }}
var x = {x:.1f}, y0 = {y0:.1f}, y1 = {y1:.1f};
ev($.kCGEventMouseMoved, x, y0); delay(0.05);
ev($.kCGEventLeftMouseDown, x, y0);
for (var i = 1; i <= 12; i++) {{ ev($.kCGEventLeftMouseDragged, x, y0 + (y1 - y0) * i / 12); delay(0.012); }}
ev($.kCGEventLeftMouseUp, x, y1);
""")
    time.sleep(1.2)


def cmd_spotlight(a):
    find_window()
    activate()
    post_keys([(KEYCODES["3"], False)], cmd=True)
    time.sleep(a.wait)
    return {"ok": True, "spotlight": True, "next": "先 shot；搜索框在屏幕底部，要先点一下才能输入"}


def cmd_home(a):
    find_window()
    activate()
    post_keys([(KEYCODES["1"], False)], cmd=True)
    time.sleep(a.wait)
    return {"ok": True, "home": True, "next": "先 shot 确认已在主屏幕"}


def main():
    ap = argparse.ArgumentParser(description="iPhone 镜像操控工具")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("shot"); p.add_argument("--out")
    p = sub.add_parser("tap"); p.add_argument("x", type=float); p.add_argument("y", type=float)
    p.add_argument("--pct", action="store_true", help="x、y 是截图宽、高的百分比（0–100）")
    p.add_argument("--zoom", action="store_true", help="x、y 是最近一张放大图的宽、高百分比")
    p.add_argument("--wait", type=float, default=1.2)
    p = sub.add_parser("type"); p.add_argument("text"); p.add_argument("--wait", type=float, default=0.6)
    p = sub.add_parser("key"); p.add_argument("name", choices=list(SPECIAL_KEYS))
    p.add_argument("--count", type=int, default=1); p.add_argument("--wait", type=float, default=0.6)
    p = sub.add_parser("scroll"); p.add_argument("direction", choices=["down", "up", "left", "right"])
    p.add_argument("--wait", type=float, default=1.0)
    p = sub.add_parser("home"); p.add_argument("--wait", type=float, default=1.2)
    p = sub.add_parser("spotlight"); p.add_argument("--wait", type=float, default=1.5)
    p = sub.add_parser("ocr"); p.add_argument("--region", default="all", help="top / middle / bottom / all / 起-止，如 80-100")
    p = sub.add_parser("tap-text"); p.add_argument("text"); p.add_argument("--region", default="all")
    p.add_argument("--exact", action="store_true", help="整段文字必须完全等于 text")
    p.add_argument("--wait", type=float, default=1.5)
    p = sub.add_parser("zoom")
    for n in ("x1", "y1", "x2", "y2"):
        p.add_argument(n, type=float)
    p.add_argument("--scale", type=int, default=3)
    a = ap.parse_args()
    try:
        res = {"shot": cmd_shot, "tap": cmd_tap, "type": cmd_type, "key": cmd_key,
               "scroll": cmd_scroll, "home": cmd_home, "spotlight": cmd_spotlight, "zoom": cmd_zoom, "ocr": cmd_ocr,
               "tap-text": cmd_tap_text}[a.cmd](a)
    except Fail as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
