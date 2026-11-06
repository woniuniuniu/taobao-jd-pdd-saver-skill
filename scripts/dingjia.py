#!/usr/bin/env python3
"""蜗牛最爽省钱 Skill · 定时盯价：一条命令跑完整个盯价流程，给 WorkBuddy 定时任务用。

  1. 手机 App 查价（京东、淘宝、拼多多，各跑一次 zidong.py）；手机镜像没连上就这次不查，安全停止就不再操作手机；
  2. 汇总，取全场实时最低价（huizong.py 的规则）；
  3. 判断到没到目标价，记进价格日记（和上次比、和历史最低比）；
  4. 推给桌面到价提醒器（tixing.py）；
  5. 记一笔彩蛋账（caidan.py）：到价时放烟花，平时只记录不打扰。
最后打印一段给用户看的结果，定时任务原样回复即可。

用法：
  python3 dingjia.py "iPhone 18 Pro" --spec 256GB --target 9700
退出码：0 正常（不管到没到价）；1 参数不对。
"""
import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable or "python3"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hz = _load("huizong")


def run(args, timeout):
    try:
        r = subprocess.run([PY, *args], capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "超时"


def load_diary(out_dir):
    rows = []
    for name in sorted(os.listdir(out_dir)):
        if name.startswith("riji-") and name.endswith(".json"):
            try:
                with open(os.path.join(out_dir, name), encoding="utf-8") as f:
                    rows.append(json.load(f))
            except (OSError, ValueError):
                pass
    return rows


def diary_line(diary, price):
    """「📈 价格日记：比上次（10-28 20:00 ¥9,394）便宜 ¥95 · 历史最低！· 近 5 次 9,699 → 9,394 → 9,299」"""
    last = diary[-1]
    diff = price - last["price"]
    if abs(diff) < 0.01:
        change = f"和上次（{last['time'][5:]}）一样"
    elif diff < 0:
        change = f"比上次（{last['time'][5:]} ¥{hz.money(last['price'])}）便宜 ¥{hz.money(-diff)} 📉"
    else:
        change = f"比上次（{last['time'][5:]} ¥{hz.money(last['price'])}）贵了 ¥{hz.money(diff)} 📈"
    low_ever = min(r["price"] for r in diary)
    tag = " · 🏅 历史最低！" if price < low_ever - 0.01 else (f" · 历史最低 ¥{hz.money(low_ever)}" if price > low_ever + 0.01 else " · 平了历史最低")
    recent = " → ".join(hz.money(r["price"]) for r in diary[-4:]) + f" → {hz.money(price)}"
    return f"📒 价格日记：{change}{tag} · 近几次 {recent}"


def main():
    ap = argparse.ArgumentParser(description="蜗牛最爽省钱 Skill · 定时盯价")
    ap.add_argument("keyword", help="型号或商品名，如 iPhone 18 Pro、三只松鼠 每日坚果")
    ap.add_argument("--spec", default="")
    ap.add_argument("--target", type=float, required=True, help="目标价")
    ap.add_argument("--platforms", nargs="+", default=["京东", "淘宝", "拼多多"], help="手机查价的平台")
    ap.add_argument("--out-dir", help="结果文件目录，默认 ~/WorkBuddy/.chaoji-dingjia/<型号>")
    a = ap.parse_args()

    name = f"{a.keyword} {a.spec}".strip()
    out_dir = a.out_dir or os.path.expanduser(os.path.join("~/WorkBuddy/.chaoji-dingjia", re.sub(r"\W+", "_", name)))
    os.makedirs(out_dir, exist_ok=True)
    files, notes = [], []

    # 1. 手机 App（中文关键词由 zidong.py 用拼音输入法打）
    for p in a.platforms:
        # 盯价只点进最低价那一件的详情页（看券后价），控制每次检查的时间
        args = [os.path.join(HERE, "zidong.py"), a.keyword, "--platforms", p, "--out-dir", out_dir, "--details", "1"]
        if a.spec:
            args[2:2] = ["--spec", a.spec]
        code, out, err = run(args, timeout=360)
        try:
            res = json.loads(out)
        except ValueError:
            res = {}
        if code == 0 and res.get("json"):
            files.append(res["json"])
            for r in res.get("results") or []:
                if r.get("status") == "skipped":
                    notes.append(f"手机{p}跳过：{r.get('reason')}")
        elif code == 1:
            notes.append(f"手机镜像没连上，这次没查价（{res.get('error') or err.strip()[:60]}）")
            break
        elif code == 3:
            reason = next((r.get("reason") for r in res.get("results") or [] if r.get("status") == "safety_stop"), "")
            notes.append(f"⚠️ 手机查价安全停止，已不再操作手机：{reason}")
            if res.get("json"):
                files.append(res["json"])
            break
        else:
            notes.append(f"手机{p}查价出错，已跳过（{(err or out).strip()[-80:]}）")

    # 3. 汇总
    low = None
    if files:
        data = hz.load_many(files, a.keyword, a.spec)
        best, plain, skipped, dropped = hz.summarize(data, ())
        low = hz.pick(best.values())  # 和小票一样：有自营 / 旗舰店就先用它们的价

    # 价格日记：每次盯价都记下最低价（每次新建文件），和上次、和历史最低比
    diary = load_diary(out_dir)
    if low:
        with open(os.path.join(out_dir, "riji-%s-%06d.json" % (time.strftime("%Y%m%d-%H%M%S"), int(time.time() * 1e6) % 1000000)),
                  "x", encoding="utf-8") as f:
            json.dump({"time": time.strftime("%Y-%m-%d %H:%M"), "price": low["price"], "platform": low["platform"],
                       "shop": low["shop"]}, f, ensure_ascii=False)

    # 4–5. 判断 + 桌面提醒器
    tixing = [os.path.join(HERE, "tixing.py"), "--model", a.keyword, "--spec", a.spec, "--target", f"{a.target:g}"]
    if low:
        alert = low["price"] <= a.target
        tixing += ["--price", f"{low['price']:g}", "--platform", low["platform"], "--shop", low["shop"]]
        where = f"{low['platform']}·{low['shop']}·{low['source']}"
        if alert:
            lines = [f"🔔 到价提醒：{name} 现在 ¥{hz.money(low['price'])}，已到目标价 ¥{hz.money(a.target)}",
                     f"在 {where}" + (f"（{low['label']}）" if low["label"] else "")]
            if low.get("url"):
                lines.append(f"商品页：{low['url']}")
        else:
            lines = [f"{name} 今日最低 ¥{hz.money(low['price'])}（{where}），未到 ¥{hz.money(a.target)}"]
    else:
        alert = False
        tixing += ["--source-down"]
        lines = [f"{name} 今天没查到实时价"]
    run(tixing, timeout=30)

    # 6. 彩蛋：到价放烟花；平时只记账，不弹窗
    if files and low:
        args = [os.path.join(HERE, "caidan.py"), *files, "--keyword", a.keyword, "--spec", a.spec,
                "--event", "alert" if alert else "watch"]
        if not alert:
            args.append("--quiet")
        code, out, _ = run(args, timeout=60)
        if code == 0:
            fun = [l for l in out.splitlines() if l.startswith(("🎆", "🏆", "📊"))]
            if not alert:
                fun = [l for l in fun if l.startswith("🏆")]  # 平时只报新成就
            lines += fun
    if low and diary:
        lines.append(diary_line(diary, low["price"]))
    lines += [f"（{n}）" for n in notes]
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
