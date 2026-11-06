#!/usr/bin/env python3
"""蜗牛最爽省钱 Skill · 一键比价（给所有模型用的简单入口）。

每条命令做一件事，输出是纯文字：哪段原样发给用户、下一条命令是什么，都写好了，模型照做就行。

  python3 yijian.py "iPhone 18 Pro" --spec 256GB --platform 京东 --out-dir <工作目录>
  python3 yijian.py "iPhone 18 Pro" --spec 256GB --platform 淘宝 --out-dir <工作目录>
  python3 yijian.py "iPhone 18 Pro" --spec 256GB --platform 拼多多 --out-dir <工作目录>
  python3 yijian.py "iPhone 18 Pro" --spec 256GB --platform 拼多多 --out-dir <工作目录>
  python3 yijian.py "iPhone 18 Pro" --spec 256GB --web --out-dir <工作目录>
  python3 yijian.py "iPhone 18 Pro" --spec 256GB --finish --out-dir <工作目录> [--name 小林]

--platform：在手机上查这一个平台（打开 App → 搜索 → 只看官方旗舰店 / 自营 / 直营店 → 点进商品页看券、满减、国补 → 算到手价）。
--web：告诉你用 WebSearch 查网上参考价和买家口碑要搜什么、查到的写进哪两个文件（文件格式也给好了）。
--finish：把这次查过的平台合起来，出比价单、省钱战报和小票网页；工作目录里这次写的 ref-*.json、kb-*.json 会自动带上。
退出码：0 正常（包括这个平台没查到）；3 安全停止（不要再运行任何操作手机的命令）；1 手机连不上（不要再查别的平台，直接 --finish）；2 关键词写错了。
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ORDER = ["京东", "淘宝", "拼多多"]  # 2.17：不再单独查天猫 App（淘宝里搜得到天猫店，用户要求）
BAR = "=" * 12


def say(title, body):
    print(f"{BAR} {title} {BAR}")
    print(body.strip())
    print(f"{BAR} 结束 {BAR}")


def next_cmd(a, platform=None, finish=False, web=False):
    base = f'python3 {os.path.join(HERE, "yijian.py")} "{a.keyword}"' + (f' --spec "{a.spec}"' if a.spec else "")
    if web:
        return base + f' --web --out-dir "{a.out_dir}"'
    if finish:
        return base + f' --finish --out-dir "{a.out_dir}"'
    return base + f' --platform {platform} --out-dir "{a.out_dir}"'


def run_platform(a):
    cmd = [sys.executable, os.path.join(HERE, "zidong.py"), a.keyword, "--platforms", a.platform, "--out-dir", a.out_dir]
    if a.spec:
        cmd[3:3] = ["--spec", a.spec]
    if a.details is not None:
        cmd += ["--details", str(a.details)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    try:
        res = json.loads(p.stdout)
    except ValueError:
        res = {}
    try:  # 每一步做了什么都留一份（排查问题用，不用发给用户）
        with open(os.path.join(a.out_dir, "log-%s-%s.json" % (a.platform, time.strftime("%Y%m%d-%H%M%S"))), "w", encoding="utf-8") as f:
            json.dump({"returncode": p.returncode, "stdout": res or p.stdout[-4000:], "stderr": p.stderr[-4000:]}, f, ensure_ascii=False, indent=1)
    except OSError:
        pass
    later = [x for x in ORDER[ORDER.index(a.platform) + 1:]] if a.platform in ORDER else []
    nxt = next_cmd(a, later[0]) if later else next_cmd(a, web=True)
    if p.returncode == 2:
        say("发给用户", res.get("error") or "关键词里有标点符号。")
        print("下一步：把关键词里的标点去掉（只留中文、英文、数字、空格），重新运行这一条命令。")
        return 2
    if p.returncode == 1:
        say("发给用户", res.get("error") or (p.stderr or p.stdout).strip()[-400:] or "手机镜像没连上。")
        print("下一步：不要再查别的平台。直接运行（查网上参考价和口碑）：")
        print(next_cmd(a, web=True))
        return 1
    if p.returncode == 3:
        say("发给用户", res.get("report") or "安全停止。")
        print("下一步：安全停止了，不要再运行任何查价命令（不要查别的平台）。直接运行（查网上参考价和口碑）：")
        print(next_cmd(a, web=True))
        return 3
    if p.returncode != 0 or not res:
        say("发给用户", f"{a.platform}查价出错了：{(p.stderr or p.stdout).strip()[-300:]}")
        print("下一步：跳过这个平台，运行：")
        print(nxt)
        return 0
    say("发给用户", res.get("report") or f"{a.platform}：没有结果")
    print("下一步：把上面「发给用户」那段原样发给用户，然后运行：")
    print(nxt)
    return 0


REF_NAME = re.compile(r"^ref-\d{8}-\d{6}\.json$")
KB_NAME = re.compile(r"^kb-\d{8}-\d{6}\.json$")


def web(a):
    """第 4 步：参考价和口碑。给出每个平台的搜索语句、两个文件的完整路径和格式，模型照着写文件就行。"""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    ref, kb = os.path.join(a.out_dir, f"ref-{stamp}.json"), os.path.join(a.out_dir, f"kb-{stamp}.json")
    files = session_files(a)
    q = subprocess.run([sys.executable, os.path.join(HERE, "huizong.py")] + (files or ["--keyword", a.keyword] + (["--spec", a.spec] if a.spec else []))
                       + ["--queries"], capture_output=True, text=True)
    try:
        d = json.loads(q.stdout)
    except ValueError:
        d = {"queries": {}, "have_realtime": [], "keyword": f"{a.keyword} {a.spec}".strip()}
    name = d.get("keyword") or f"{a.keyword} {a.spec}".strip()
    print("这一步不用发任何话给用户。没有 WebSearch（联网搜索）工具的话，跳过这一步，直接运行最后那条命令。")
    print()
    print(f"【A. 网上参考价】用 WebSearch 按下面的语句搜（手机上已经查到的平台：{('、'.join(d.get('have_realtime') or []) or '无')}，这些平台搜 1 次就够，其他平台最多 2 次）：")
    for p, qs in (d.get("queries") or {}).items():
        print(f"  {p}：" + " ／ ".join(qs))
    print("  只采用：14 天以内的价格；型号和规格完全一样（不是 Pro Max、不是别的容量）；写明是哪个平台；官方旗舰店 / 自营 / 直营店的价（代购、港版、二手、个人店不要）；有网页地址（文章、帖子本身的地址；union-click.jd.com、s.click.taobao.com、u.jd.com 这类推广跳转链接不算，这种结果换用文章本身的地址，没有就不用）。")
    print(f"  查到的写进这个新文件（查不到的平台不写；一个都没查到就不建这个文件）：\n  {ref}")
    print('  格式：[{"platform": "京东", "low": 9299, "high": 9299, "url": "查到这个价的网页地址，原样复制", "source": "单一来源", "date": "11月5日"}]')
    print()
    print(f"【B. 买家口碑】用 WebSearch 搜 1–2 次：「{name} 评价 口碑」，结果太少再搜「{a.keyword} 评测 优缺点」。")
    print("  只写网页里真有的内容，型号要一致；pros / cons 每个最多 5 个、每个不超过 8 个字；points 最多 3 条、每条带 url；rating 只在网页写明了分数或好评率时写。")
    print(f"  写进这个新文件（查不到就不建）：\n  {kb}")
    print('  格式：{"rating": {"value": "97%", "label": "京东好评率", "url": "https://…"}, "pros": ["续航出色"], "cons": ["机身偏重"], '
          '"points": [{"text": "多数评测认为续航明显提升", "url": "https://…"}], "sources": ["https://…"], "date": "11月5日"}')
    print()
    print("  回复用户时不要提任何网站、媒体的名字；参考价和口碑只写进文件，不要自己发给用户（最后的结果里会有）。")
    print()
    print("下一步：两个文件写好（或者确实查不到）之后，运行：")
    print(next_cmd(a, finish=True))
    return 0


def latest_file(a, pattern, kind):
    """工作目录里这次写的参考价 / 口碑文件：名字对、3 小时以内、内容是合法 JSON 的最新一个。"""
    best = None
    for name in os.listdir(a.out_dir):
        path = os.path.join(a.out_dir, name)
        if not pattern.match(name) or time.time() - os.path.getmtime(path) > 3 * 3600:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                ok = isinstance(json.load(f), kind)
        except (OSError, ValueError):
            ok = False
        if ok and (best is None or os.path.getmtime(path) > os.path.getmtime(best)):
            best = path
    return best


def session_files(a):
    """这次比价的 mirror 文件：同一个关键词和规格、3 小时以内的；同一个 App 查了好几次只用最后一次。"""
    latest = {}
    for path in glob.glob(os.path.join(a.out_dir, "mirror-*.json")):
        if time.time() - os.path.getmtime(path) > 3 * 3600:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if re.sub(r"\s+", "", str(d.get("keyword", ""))).lower() != re.sub(r"\s+", "", a.keyword).lower() \
                or (d.get("spec") or "") != (a.spec or ""):
            continue
        for app in d.get("apps") or ["?"]:
            if app not in latest or os.path.getmtime(path) > os.path.getmtime(latest[app]):
                latest[app] = path
    return sorted(set(latest.values()))


def finish(a):
    files = session_files(a)
    a.ref = a.ref or latest_file(a, REF_NAME, list)  # 第 4 步写的参考价、口碑文件：没写 --ref / --kb 也自动带上
    a.kb = a.kb or latest_file(a, KB_NAME, dict)
    extra = (["--ref", a.ref] if a.ref else [])
    if not files:
        base = ["--keyword", a.keyword] + (["--spec", a.spec] if a.spec else [])
        if not a.ref:
            say("发给用户", "这次手机上一个平台都没查到，也没有参考价，没法比价。")
            return 0
    else:
        base = files
    hz = subprocess.run([sys.executable, os.path.join(HERE, "huizong.py")] + base + extra, capture_output=True, text=True)
    report = hz.stdout.strip() or hz.stderr.strip()
    cd = ""
    if files:
        c = subprocess.run([sys.executable, os.path.join(HERE, "caidan.py")] + base + extra, capture_output=True, text=True)
        cd = c.stdout.strip()
    xp_cmd = [sys.executable, os.path.join(HERE, "xiaopiao.py")] + base + extra + (["--kb", a.kb] if a.kb else []) \
        + (["--name", a.name] if a.name else [])
    xp = subprocess.run(xp_cmd, capture_output=True, text=True)
    html = ""
    try:
        html = json.loads(xp.stdout.strip().splitlines()[-1]).get("html", "")
    except (ValueError, IndexError):
        pass
    intro = subprocess.run([sys.executable, os.path.join(HERE, "caidan.py"), "--intro"], capture_output=True, text=True).stdout.strip()
    say("发给用户", report + ("\n\n" + cd if cd else "") + "\n\n要不要我帮你盯价，降到目标价就提醒你？再把抢购时间排进日历？"
        + ("\n\n" + intro if intro else ""))
    if html:
        print(f"小票网页：{html}")
        print("下一步（按顺序做完就结束）：1. 用 present_files 打开上面的小票网页（files 填这个路径）；"
              "2. 把「发给用户」那段原样发给用户，一个字都不要改、不要加；3. 结束，不要再补充点评。")
    else:
        print("小票没生成：" + (xp.stderr or xp.stdout).strip()[-200:])
        print("下一步：把「发给用户」那段原样发给用户，结束。")
    return 0


def main():
    ap = argparse.ArgumentParser(description="一键比价：一个平台一条命令，最后 --finish 出结果")
    ap.add_argument("keyword")
    ap.add_argument("--spec", default="")
    ap.add_argument("--platform", choices=ORDER)
    ap.add_argument("--finish", action="store_true")
    ap.add_argument("--web", action="store_true", help="第 4 步：参考价和口碑要搜什么、写进哪个文件")
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--ref")
    ap.add_argument("--kb")
    ap.add_argument("--name", default="")
    ap.add_argument("--details", type=int)
    a = ap.parse_args()
    a.out_dir = os.path.abspath(os.path.expanduser(a.out_dir))
    os.makedirs(a.out_dir, exist_ok=True)
    if a.finish:
        return finish(a)
    if a.web:
        return web(a)
    if not a.platform:
        ap.error("要么 --platform 京东/淘宝/拼多多，要么 --web，要么 --finish")
    return run_platform(a)


if __name__ == "__main__":
    sys.exit(main())
