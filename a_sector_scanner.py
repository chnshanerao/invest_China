#!/usr/bin/env python3
"""
A股行业右侧机会监测系统
每日收盘后扫描~20个行业ETF，判断哪些板块处于右侧趋势
有新板块进入右侧、或持仓板块趋势变化时推送钉钉

判定逻辑（需积累历史数据）：
- 右侧确认: 价格 > MA20 > MA60, 且近5日涨幅>0
- 右侧初现: 价格 > MA20, MA20开始拐头向上
- 转弱信号: 价格跌破MA20, 或MA20拐头向下
- 左侧/无趋势: 价格 < MA20 < MA60
"""

import urllib.request
import json
import datetime
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from a_stock_monitor import (
    create_ssl_context, parse_sina_a_stock, send_dingtalk,
)

HISTORY_FILE = os.path.join(SCRIPT_DIR, "state", "sector_history.json")
STATE_FILE = os.path.join(SCRIPT_DIR, "state", "sector_scan_state.json")

SECTOR_ETFS = {
    "通信":       {"symbol": "sh515880", "holding": False},
    "半导体设备":  {"symbol": "sz159516", "holding": True},
    "芯片":       {"symbol": "sz159801", "holding": False},
    "电力":       {"symbol": "sz159611", "holding": True},
    "电网设备":    {"symbol": "sz159326", "holding": False},
    "军工":       {"symbol": "sh512660", "holding": False},
    "机器人":      {"symbol": "sh562500", "holding": False},
    "消费电子":    {"symbol": "sz159732", "holding": False},
    "煤炭":       {"symbol": "sh515220", "holding": False},
    "银行":       {"symbol": "sh512800", "holding": False},
    "创新药":      {"symbol": "sz159992", "holding": False},
    "光伏":       {"symbol": "sh515790", "holding": False},
    "新能车":      {"symbol": "sh515030", "holding": False},
    "白酒":       {"symbol": "sz161725", "holding": False},
    "红利":       {"symbol": "sh515180", "holding": True},
    "医药":       {"symbol": "sh512010", "holding": False},
    "房地产":      {"symbol": "sh512200", "holding": False},
    "有色金属":    {"symbol": "sh512400", "holding": False},
    "软件":       {"symbol": "sz159852", "holding": False},
    "卫星":       {"symbol": "sz159206", "holding": True},
}


def fetch_sector_quotes():
    symbols = [cfg["symbol"] for cfg in SECTOR_ETFS.values()]
    url = f"https://hq.sinajs.cn/list={','.join(symbols)}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://finance.sina.com.cn",
    })
    ctx = create_ssl_context()
    try:
        resp = urllib.request.urlopen(req, context=ctx, timeout=10)
        data = resp.read().decode("gbk", errors="replace")
    except Exception as e:
        print(f"[ERROR] 数据获取失败: {e}")
        return {}

    sym_to_name = {cfg["symbol"]: name for name, cfg in SECTOR_ETFS.items()}
    results = {}
    for line in data.strip().split("\n"):
        parsed = parse_sina_a_stock(line.strip())
        if parsed and parsed["ok"]:
            label = sym_to_name.get(parsed["symbol"])
            if label:
                results[label] = parsed
    return results


def load_history():
    try:
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_history(history):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def update_history(quotes):
    history = load_history()
    today = datetime.date.today().isoformat()

    for label, data in quotes.items():
        if label not in history:
            history[label] = []

        entries = history[label]
        if entries and entries[-1]["date"] == today:
            entries[-1]["close"] = data["price"]
            entries[-1]["change_pct"] = data["change_pct"]
        else:
            entries.append({
                "date": today,
                "close": data["price"],
                "change_pct": data["change_pct"],
            })

        history[label] = entries[-120:]

    save_history(history)
    return history


def calc_ma(prices, n):
    if len(prices) < n:
        return None
    return sum(prices[-n:]) / n


def analyze_sector(label, history_entries):
    if not history_entries or len(history_entries) < 5:
        return {"status": "数据不足", "score": 0, "detail": f"需{5 - len(history_entries)}天数据"}

    closes = [e["close"] for e in history_entries]
    current = closes[-1]
    n = len(closes)

    ma5 = calc_ma(closes, 5)
    ma10 = calc_ma(closes, 10) if n >= 10 else None
    ma20 = calc_ma(closes, 20) if n >= 20 else None
    ma60 = calc_ma(closes, 60) if n >= 60 else None

    chg_5d = (current - closes[-5]) / closes[-5] * 100 if n >= 5 else 0
    chg_10d = (current - closes[-10]) / closes[-10] * 100 if n >= 10 else None
    chg_20d = (current - closes[-20]) / closes[-20] * 100 if n >= 20 else None

    is_new_high_20 = n >= 20 and current >= max(closes[-20:])
    is_new_low_20 = n >= 20 and current <= min(closes[-20:])

    ma20_rising = None
    if n >= 25:
        ma20_prev = calc_ma(closes[:-5], 20)
        ma20_rising = ma20 > ma20_prev

    score = 0
    signals = []

    if ma5 and current > ma5:
        score += 1
    if ma10 and current > ma10:
        score += 1
    if ma20 and current > ma20:
        score += 2
    if ma60 and current > ma60:
        score += 2
    if ma20 and ma60 and ma20 > ma60:
        score += 2
    if chg_5d > 0:
        score += 1
    if chg_10d and chg_10d > 0:
        score += 1
    if is_new_high_20:
        score += 2
        signals.append("20日新高")
    if ma20_rising:
        score += 1
        signals.append("MA20上行")

    if ma20 and current < ma20:
        score -= 2
    if ma60 and current < ma60:
        score -= 1
    if is_new_low_20:
        score -= 2
        signals.append("20日新低")
    if ma20_rising is False:
        score -= 1

    if score >= 8:
        status = "强右侧"
    elif score >= 5:
        status = "右侧"
    elif score >= 3:
        status = "右侧初现"
    elif score >= 0:
        status = "震荡"
    elif score >= -3:
        status = "转弱"
    else:
        status = "左侧"

    detail_parts = [f"5日{chg_5d:+.1f}%"]
    if chg_20d is not None:
        detail_parts.append(f"20日{chg_20d:+.1f}%")
    if signals:
        detail_parts.extend(signals)

    return {
        "status": status,
        "score": score,
        "detail": " | ".join(detail_parts),
        "price": current,
        "chg_5d": chg_5d,
        "chg_20d": chg_20d,
        "ma20": ma20,
        "ma60": ma60,
    }


def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"prev_status": {}, "last_scan": None}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def scan():
    now = datetime.datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M")

    quotes = fetch_sector_quotes()
    if not quotes:
        print(f"[{now_str}] 行情获取失败")
        return

    print(f"[{now_str}] 获取{len(quotes)}个行业ETF数据")

    history = update_history(quotes)
    state = load_state()
    prev_status = state.get("prev_status", {})

    results = {}
    for label in SECTOR_ETFS:
        entries = history.get(label, [])
        results[label] = analyze_sector(label, entries)

    right_side = []
    turning_weak = []
    new_right = []
    status_changes = []

    for label, r in sorted(results.items(), key=lambda x: -x[1]["score"]):
        is_holding = SECTOR_ETFS[label]["holding"]
        old = prev_status.get(label, "")

        if r["status"] in ("强右侧", "右侧", "右侧初现"):
            right_side.append((label, r, is_holding))
            if old not in ("强右侧", "右侧", "右侧初现"):
                new_right.append((label, r, is_holding))
                status_changes.append((label, old, r["status"]))
        elif r["status"] in ("转弱", "左侧") and old in ("强右侧", "右侧", "右侧初现"):
            turning_weak.append((label, r, is_holding))
            status_changes.append((label, old, r["status"]))

    n_data = len(history.get(next(iter(SECTOR_ETFS)), []))
    print(f"\n行业趋势扫描 (已积累{n_data}天数据)")
    print("=" * 60)

    for label, r in sorted(results.items(), key=lambda x: -x[1]["score"]):
        is_holding = SECTOR_ETFS[label]["holding"]
        marker = "*" if is_holding else " "
        print(f"  {marker} [{r['status']:4s}] {label:6s} score={r['score']:+3d}  {r['detail']}")

    print()
    print(f"右侧板块: {len(right_side)}个")
    print(f"新进右侧: {len(new_right)}个")
    print(f"转弱板块: {len(turning_weak)}个")

    should_push = bool(new_right or turning_weak)

    if should_push:
        lines = [f"### 行业右侧机会雷达 ({now.strftime('%m/%d')})", ""]

        if new_right:
            lines.append("**新进右侧**:")
            lines.append("")
            for label, r, is_h in new_right:
                tag = "[持仓]" if is_h else ""
                lines.append(f"- **{label}**{tag} {r['status']}(score={r['score']}) {r['detail']}")
            lines.append("")

        if turning_weak:
            lines.append("**趋势转弱**:")
            lines.append("")
            for label, r, is_h in turning_weak:
                tag = "[持仓]" if is_h else ""
                lines.append(f"- **{label}**{tag} {r['status']}(score={r['score']}) {r['detail']}")
            lines.append("")

        if right_side:
            lines.append("**当前右侧板块**:")
            lines.append("")
            for label, r, is_h in right_side:
                tag = "[持仓]" if is_h else ""
                lines.append(f"- {label}{tag}: {r['status']} {r['detail']}")
            lines.append("")

        lines.append("---")
        lines.append("行业轮动自动监测 | 每日15:20")

        md = "\n".join(lines)
        ok, msg = send_dingtalk(md, title="行业右侧机会")
        print(f"\n钉钉推送: {'成功' if ok else '失败'} {msg}")

    elif "--force" in sys.argv:
        lines = [f"### 行业趋势全景 ({now.strftime('%m/%d')})", ""]

        for status_label in ["强右侧", "右侧", "右侧初现", "震荡", "转弱", "左侧", "数据不足"]:
            group = [(l, r) for l, r in results.items() if r["status"] == status_label]
            if not group:
                continue
            lines.append(f"**{status_label}**:")
            for label, r in sorted(group, key=lambda x: -x[1]["score"]):
                is_h = SECTOR_ETFS[label]["holding"]
                tag = "[持仓]" if is_h else ""
                lines.append(f"- {label}{tag} score={r['score']} {r['detail']}")
            lines.append("")

        lines.append("---")
        lines.append("行业轮动监测 | 每日15:20")

        md = "\n".join(lines)
        ok, msg = send_dingtalk(md, title="行业趋势全景")
        print(f"\n钉钉推送: {'成功' if ok else '失败'} {msg}")
    else:
        print("\n无趋势变化，不打扰")

    new_status = {label: r["status"] for label, r in results.items()}
    state["prev_status"] = new_status
    state["last_scan"] = now_str
    save_state(state)


if __name__ == "__main__":
    scan()
