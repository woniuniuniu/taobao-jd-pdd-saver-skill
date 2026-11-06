#!/usr/bin/env python3
"""把一次盯价结果交给桌面「到价提醒器」显示。

只写一个 JSON 文件，不碰硬件：Mac 上常驻的 bridge.py 盯着这个目录，负责把画面发到板子。
没接提醒器时也可以照常调用，文件写了没人读而已，不影响盯价。

用法：
  python3 tixing.py --model "iPhone 18 Pro" --spec 256GB --price 9699 --target 9700 \
      --platform 京东 --shop "Apple产品京东自营旗舰店"
  python3 tixing.py --model "iPhone 18 Pro" --spec 256GB --target 9700 --source-down
"""

import argparse
import hashlib
import json
import os
import sys
import time

STATE_DIR = os.environ.get("CHAOJI_TIXING_DIR") or os.path.expanduser("~/WorkBuddy/.chaoji-tixingqi")  # 环境变量只给测试用


def main():
    ap = argparse.ArgumentParser(description="写入一次盯价结果，给桌面到价提醒器显示")
    ap.add_argument("--model", required=True, help="商品型号，如 iPhone 18 Pro")
    ap.add_argument("--spec", default="", help="规格，如 256GB")
    ap.add_argument("--target", type=float, required=True, help="目标价")
    ap.add_argument("--price", type=float, help="今日最低实时券后价")
    ap.add_argument("--platform", default="", help="最低价所在平台")
    ap.add_argument("--shop", default="", help="最低价所在店铺")
    ap.add_argument("--source-down", action="store_true", help="实时价格源不可用，今天跳过")
    ap.add_argument("--time", help="检查时间，默认现在（北京时间 YYYY-MM-DD HH:MM）")
    a = ap.parse_args()

    if a.price is None and not a.source_down:
        ap.error("需要 --price，或者用 --source-down 表示价格源不可用")

    item_id = hashlib.md5(("%s|%s" % (a.model, a.spec)).encode()).hexdigest()[:10]
    item = {
        "id": item_id,
        "model": a.model,
        "spec": a.spec,
        "target": a.target,
        "price": a.price,
        "platform": a.platform,
        "shop": a.shop,
        "ok": not a.source_down,
        "alert": (not a.source_down) and a.price <= a.target,
        "time": a.time or time.strftime("%Y-%m-%d %H:%M"),
    }

    # 每次写一个新文件：WorkBuddy 沙盒允许新建文件，但不允许覆盖或替换已有文件。
    # 旧结果由 bridge.py 清理。
    os.makedirs(STATE_DIR, exist_ok=True)
    stamp = "%s-%d" % (time.strftime("%Y%m%d-%H%M%S"), os.getpid())
    path = os.path.join(STATE_DIR, "item-%s-%s.json" % (item_id, stamp))
    with open(path, "x", encoding="utf-8") as f:
        json.dump(item, f, ensure_ascii=False, indent=1)
    print("已推送到桌面提醒器：%s%s" % ("🔔 到价 " if item["alert"] else "", path))


if __name__ == "__main__":
    sys.exit(main())
