#!/usr/bin/env python3
"""
A股ETF持仓监控系统
配合CloudCLI定时任务，盘前/收盘推送完整报告到钉钉

持仓: 卫星ETF(159206), 红利ETF(515180)
数据源: 新浪财经API (无需第三方依赖)
"""

import urllib.request
import json
import datetime
import ssl
import sys
import os
import re
import time
import hmac
import hashlib
import base64

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ============================================================
# 配置区
# ============================================================

HOLDINGS = {
    "卫星ETF": {
        "sina_symbol": "sz159206",
        "shares": 10,
        "weight_current": 0.42,
        "weight_target": 0.30,
        "notes": "SpaceX IPO后反弹减仓",
    },
    "半导体设备": {
        "sina_symbol": "sz159516",
        "shares": 3,
        "weight_current": 0.22,
        "weight_target": 0.25,
        "notes": "国产替代+存储扩产,PE高位注意波动",
    },
    "红利ETF": {
        "sina_symbol": "sh515180",
        "shares": 2.7,
        "weight_current": 0.15,
        "weight_target": 0.20,
        "notes": "压舱石,持有到10月吃分红",
    },
    "振华股份": {
        "sina_symbol": "sh603067",
        "shares": 1.5,
        "weight_current": 0.13,
        "weight_target": 0.15,
        "notes": "铬盐龙头,有色金属周期",
    },
    "电力ETF": {
        "sina_symbol": "sz159611",
        "shares": 0.7,
        "weight_current": 0.04,
        "weight_target": 0.10,
        "notes": "电力改革+AI算力用电,新开仓",
    },
}

PRICE_TARGETS = {
    "sz159206": {
        "name": "卫星ETF",
        "sell_low": 1.65, "sell_high": 1.70,
        "stop_loss": 1.37,
        "buy_low": None, "buy_high": None,
    },
    "sz159516": {
        "name": "半导体设备",
        "sell_low": None, "sell_high": None,
        "stop_loss": 1.10,
        "buy_low": None, "buy_high": None,
    },
    "sh515180": {
        "name": "红利ETF",
        "sell_low": None, "sell_high": None,
        "stop_loss": None,
        "buy_low": None, "buy_high": 1.40,
    },
    "sh603067": {
        "name": "振华股份",
        "sell_low": None, "sell_high": None,
        "stop_loss": 27.00,
        "buy_low": None, "buy_high": None,
    },
    "sz159611": {
        "name": "电力ETF",
        "sell_low": None, "sell_high": None,
        "stop_loss": None,
        "buy_low": 1.00, "buy_high": 1.05,
    },
}

MARKET_INDICES = {
    "上证指数": "sh000001",
    "创业板指": "sz399006",
}

KEY_EVENTS = [
    {"date": "2026-06-12", "event": "SpaceX IPO(纳斯达克)", "action": "卫星ETF反弹减仓催化"},
    {"date": "2026-06-15", "event": "5月经济数据(CPI/PPI/社融)", "action": "观察通胀和流动性"},
    {"date": "2026-06-20", "event": "LPR报价", "action": "关注是否降息"},
    {"date": "2026-07-01", "event": "财新PMI", "action": "经济景气验证"},
    {"date": "2026-07-25", "event": "7月政治局会议(预估)", "action": "下半年政策定调"},
    {"date": "2026-08-31", "event": "中报披露截止", "action": "验证半导体设备/振华股份业绩"},
    {"date": "2026-09-15", "event": "8月经济数据", "action": "Q3景气验证"},
    {"date": "2026-10-20", "event": "红利ETF分红(预估)", "action": "持有吃分红,10月权益登记"},
    {"date": "2026-10-31", "event": "三季报披露截止", "action": "全面业绩验证"},
    {"date": "2026-11-15", "event": "10月经济数据", "action": "评估组合,决定是否调整"},
]

DINGTALK_WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=61fded0e8252140ae2fcc761ff20f8cf6df9f75d1623c3be6a4cfadb6d9dc586"
DINGTALK_SECRET = "SECd92d215a87b1a45bd6dd94ed0699bcefa5fc7b98e4ffc29226bf0fbf5a25ae71"

TREND_TRACKER_FILE = os.path.join(SCRIPT_DIR, "state", "a_trend_tracker.json")

# ============================================================
# 数据获取
# ============================================================

def create_ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def parse_sina_a_stock(raw_line):
    match = re.search(r'var hq_str_(s[hz]\d+)="(.+)"', raw_line)
    if not match or not match.group(2):
        return None
    symbol = match.group(1)
    parts = match.group(2).split(",")
    if len(parts) < 32:
        return None

    name = parts[0]
    prev_close = float(parts[2]) if parts[2] and parts[2] != "0.000" else 0
    price = float(parts[3]) if parts[3] and parts[3] != "0.000" else 0
    high = float(parts[4]) if parts[4] else 0
    low = float(parts[5]) if parts[5] else 0
    volume = int(float(parts[8])) if parts[8] else 0
    turnover = float(parts[9]) if parts[9] else 0
    date_str = parts[30] if len(parts) > 30 else ""
    time_str = parts[31] if len(parts) > 31 else ""

    is_auction = False
    if prev_close > 0 and price <= 0:
        # 集合竞价阶段: 用买一价(竞价匹配价)作为参考价
        bid1 = float(parts[11]) if len(parts) > 11 and parts[11] and parts[11] != "0.000" else 0
        ask1 = float(parts[21]) if len(parts) > 21 and parts[21] and parts[21] != "0.000" else 0
        price = bid1 or ask1 or prev_close
        is_auction = True

    if prev_close <= 0 or price <= 0:
        return None

    change_pct = (price - prev_close) / prev_close * 100

    return {
        "symbol": symbol,
        "name": name,
        "price": price,
        "change_pct": change_pct,
        "prev_close": prev_close,
        "high": high,
        "low": low,
        "volume": volume,
        "turnover": turnover,
        "timestamp": f"{date_str} {time_str}",
        "ok": True,
        "is_auction": is_auction,
    }


def fetch_all_quotes():
    all_symbols = []
    symbol_map = {}

    for label, cfg in HOLDINGS.items():
        sym = cfg["sina_symbol"]
        all_symbols.append(sym)
        symbol_map[sym] = label

    for label, sym in MARKET_INDICES.items():
        all_symbols.append(sym)
        symbol_map[sym] = label

    url = f"https://hq.sinajs.cn/list={','.join(all_symbols)}"
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
        return {}, {}

    holdings_data = {}
    index_data = {}

    for line in data.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parsed = parse_sina_a_stock(line)
        if not parsed:
            continue
        sym = parsed["symbol"]
        label = symbol_map.get(sym, sym)
        if sym in [cfg["sina_symbol"] for cfg in HOLDINGS.values()]:
            holdings_data[label] = parsed
        else:
            index_data[label] = parsed

    return holdings_data, index_data


# ============================================================
# 信号检查
# ============================================================

def check_price_signals(holdings_data):
    signals = []
    for label, cfg in HOLDINGS.items():
        sym = cfg["sina_symbol"]
        data = holdings_data.get(label)
        if not data or not data.get("ok"):
            continue

        price = data["price"]
        target = PRICE_TARGETS.get(sym, {})

        if target.get("stop_loss") and price <= target["stop_loss"]:
            signals.append({
                "id": f"stop_{sym}",
                "type": "critical",
                "name": f"止损警报: {label}",
                "detail": f"{label} 当前{price:.3f}, 已跌破止损线{target['stop_loss']:.2f}",
                "action": "立即考虑卖出",
            })

        if target.get("sell_low") and target.get("sell_high"):
            if target["sell_low"] <= price <= target["sell_high"]:
                signals.append({
                    "id": f"sell_{sym}",
                    "type": "warning",
                    "name": f"卖出提醒: {label}",
                    "detail": f"{label} 当前{price:.3f}, 进入卖出区[{target['sell_low']:.2f}-{target['sell_high']:.2f}]",
                    "action": "反弹减仓",
                })

        if target.get("buy_high"):
            buy_low = target.get("buy_low") or 0
            if buy_low <= price <= target["buy_high"]:
                signals.append({
                    "id": f"buy_{sym}",
                    "type": "info",
                    "name": f"买入提醒: {label}",
                    "detail": f"{label} 当前{price:.3f}, 进入买入区[{buy_low:.2f}-{target['buy_high']:.2f}]",
                    "action": "可分批建仓",
                })

    return signals


def check_market_crash(index_data):
    signals = []
    for label, data in index_data.items():
        if not data or not data.get("ok"):
            continue
        if data["change_pct"] <= -3.0:
            signals.append({
                "id": f"crash_{data['symbol']}",
                "type": "warning",
                "name": f"大盘暴跌: {label}",
                "detail": f"{label} 跌{data['change_pct']:.2f}%, 全线警戒",
                "action": "检查所有持仓止损线",
            })
    return signals


def check_event_calendar():
    today = datetime.date.today()
    upcoming = []
    for ev in KEY_EVENTS:
        try:
            ev_date = datetime.date.fromisoformat(ev["date"])
        except ValueError:
            continue
        delta = (ev_date - today).days
        if -3 <= delta <= 7:
            if delta == 0:
                prefix = "今天"
            elif delta == 1:
                prefix = "明天"
            elif delta == 2:
                prefix = "后天"
            elif delta > 0:
                prefix = f"{delta}天后"
            else:
                prefix = f"{-delta}天前"
            upcoming.append({
                "prefix": prefix,
                "delta": delta,
                "event": ev["event"],
                "action": ev["action"],
            })
    upcoming.sort(key=lambda x: x["delta"])
    return upcoming


# ============================================================
# 趋势跟踪
# ============================================================

def load_trend_tracker():
    try:
        with open(TREND_TRACKER_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"low": None, "low_date": None, "no_new_low_days": 0, "history": []}


def save_trend_tracker(tracker):
    with open(TREND_TRACKER_FILE, "w") as f:
        json.dump(tracker, f, ensure_ascii=False, indent=2)


def update_trend_tracker(index_data):
    sh_data = index_data.get("上证指数")
    if not sh_data or not sh_data.get("ok"):
        return None

    tracker = load_trend_tracker()
    price = sh_data["price"]
    today_str = datetime.date.today().isoformat()

    if tracker["history"] and tracker["history"][-1].get("date") == today_str:
        return tracker

    if tracker["low"] is None or price < tracker["low"]:
        tracker["low"] = price
        tracker["low_date"] = today_str
        tracker["no_new_low_days"] = 0
    else:
        tracker["no_new_low_days"] += 1

    tracker["history"].append({"date": today_str, "price": price})
    tracker["history"] = tracker["history"][-30:]

    save_trend_tracker(tracker)
    return tracker


# ============================================================
# 报告生成
# ============================================================

def format_price_distance(price, target, direction="above"):
    if target is None:
        return ""
    pct = (price - target) / target * 100
    return f"{pct:+.1f}%"


def generate_report(holdings_data, index_data, signals, events, tracker):
    now = datetime.datetime.now()
    lines = []

    any_auction = any(d.get("is_auction") for d in list(holdings_data.values()) + list(index_data.values()) if d)
    auction_tag = " [集合竞价]" if any_auction else ""
    lines.append(f"### A股持仓监控 ({now.strftime('%m/%d %H:%M')}{auction_tag})")
    lines.append("")

    idx_parts = []
    for label in ["上证指数", "创业板指"]:
        d = index_data.get(label)
        if d and d.get("ok"):
            icon = "+" if d["change_pct"] >= 0 else ""
            idx_parts.append(f"{label} {d['price']:.0f}({icon}{d['change_pct']:.2f}%)")
    if idx_parts:
        lines.append(f"**大盘**: {' | '.join(idx_parts)}")
        lines.append("")

    lines.append("**持仓明细**:")
    lines.append("")
    for label, cfg in HOLDINGS.items():
        d = holdings_data.get(label)
        if not d or not d.get("ok"):
            lines.append(f"- {label}: 数据获取失败")
            continue

        sym = cfg["sina_symbol"]
        target = PRICE_TARGETS.get(sym, {})
        price = d["price"]
        chg = d["change_pct"]
        icon_chg = "+" if chg >= 0 else ""
        value = price * cfg["shares"] * 100

        status_parts = []
        if target.get("stop_loss"):
            dist = format_price_distance(price, target["stop_loss"])
            if price <= target["stop_loss"]:
                status_parts.append(f"**已破止损{target['stop_loss']:.2f}**")
            else:
                status_parts.append(f"距止损{target['stop_loss']:.2f}({dist})")

        if target.get("sell_low"):
            if price >= target["sell_low"]:
                status_parts.append(f"**在卖出区**")
            else:
                dist = format_price_distance(price, target["sell_low"])
                status_parts.append(f"距卖出区{target['sell_low']:.2f}({dist})")

        if target.get("buy_high"):
            if target.get("buy_low") and price <= target["buy_high"]:
                status_parts.append(f"**在买入区**")
            elif price > target["buy_high"]:
                dist = format_price_distance(price, target["buy_high"])
                status_parts.append(f"距买入区{target['buy_high']:.2f}({dist})")

        weight_now = cfg["weight_current"]
        weight_tgt = cfg["weight_target"]
        if weight_now > weight_tgt + 0.05:
            weight_hint = "需减仓"
        elif weight_now < weight_tgt - 0.05:
            weight_hint = "需加仓"
        else:
            weight_hint = "达标"

        status = " | ".join(status_parts) if status_parts else "稳定"
        lines.append(
            f"- **{label}** {price:.3f}({icon_chg}{chg:.2f}%) "
            f"{cfg['shares']}手={value:.0f}元 "
            f"[{weight_hint}:{weight_now:.0%}->{weight_tgt:.0%}] "
            f"{status}"
        )
    lines.append("")

    if signals:
        lines.append("**操作信号**:")
        lines.append("")
        for sig in signals:
            icon = {"critical": "!!!", "warning": "!", "info": ">"}.get(sig["type"], ">")
            lines.append(f"{icon} **{sig['name']}**: {sig['detail']}")
            lines.append(f"> {sig['action']}")
            lines.append("")

    if events:
        lines.append("**近期事件**:")
        lines.append("")
        for ev in events:
            lines.append(f"- {ev['prefix']}: {ev['event']} -- {ev['action']}")
        lines.append("")

    if tracker and tracker.get("low"):
        trend_text = f"上证低点{tracker['low']:.0f}({tracker['low_date']}), 已{tracker['no_new_low_days']}日未创新低"
        lines.append(f"**趋势**: {trend_text}")
        lines.append("")

    lines.append("---")
    lines.append("A股自动监控 | 盘前09:15 盘中扫描 收盘15:15")

    return "\n".join(lines)


# ============================================================
# 钉钉推送
# ============================================================

def dingtalk_sign():
    timestamp = str(round(time.time() * 1000))
    string_to_sign = f"{timestamp}\n{DINGTALK_SECRET}"
    hmac_code = hmac.new(
        DINGTALK_SECRET.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    sign = urllib.request.quote(base64.b64encode(hmac_code).decode("utf-8"))
    return timestamp, sign


def send_dingtalk(report_md, title="A股持仓监控"):
    timestamp, sign = dingtalk_sign()
    url = f"{DINGTALK_WEBHOOK}&timestamp={timestamp}&sign={sign}"

    payload = json.dumps({
        "msgtype": "markdown",
        "markdown": {"title": title, "text": report_md},
    }).encode("utf-8")

    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    ctx = create_ssl_context()
    try:
        resp = urllib.request.urlopen(req, context=ctx, timeout=10)
        result = json.loads(resp.read().decode("utf-8"))
        if result.get("errcode") == 0:
            return True, "OK"
        return False, result.get("errmsg", str(result))
    except Exception as e:
        return False, str(e)


# ============================================================
# 主流程
# ============================================================

def main():
    push_dingtalk = "--dingtalk" in sys.argv
    quiet = "--quiet" in sys.argv

    now = datetime.datetime.now()
    if not quiet:
        print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] A股持仓监控启动")

    holdings_data, index_data = fetch_all_quotes()

    if not holdings_data:
        msg = f"[{now.strftime('%H:%M')}] A股数据获取失败(非交易时段或网络异常)"
        print(msg)
        if push_dingtalk:
            send_dingtalk(f"### A股监控\n\n{msg}", title="A股监控-数据异常")
        return

    signals = check_price_signals(holdings_data)
    signals.extend(check_market_crash(index_data))
    events = check_event_calendar()
    tracker = update_trend_tracker(index_data)

    report = generate_report(holdings_data, index_data, signals, events, tracker)

    if not quiet:
        print(report)
        print()

    report_dir = os.path.join(SCRIPT_DIR, "reports")
    os.makedirs(report_dir, exist_ok=True)
    report_file = os.path.join(report_dir, f"a_stock_{now.strftime('%Y%m%d_%H%M')}.txt")
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(report)

    if push_dingtalk:
        has_critical = any(s["type"] == "critical" for s in signals)
        title = "A股止损警报" if has_critical else "A股持仓监控"
        ok, msg = send_dingtalk(report, title=title)
        if not quiet:
            print(f"钉钉推送: {'成功' if ok else '失败'} {msg}")


if __name__ == "__main__":
    main()
