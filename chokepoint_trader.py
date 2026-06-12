#!/usr/bin/env python3
"""
Chokepoint Trader — AI供应链瓶颈股交易信号系统

基于Serenity瓶颈投资法筛选标的，用技术指标判断入场时机，
3批建仓（30%/35%/35%），钉钉推送详细操作建议。

数据源：Sina Finance K线API（历史日K）+ Sina实时行情
指标：SMA/EMA/RSI/MACD/Bollinger/VolumeRatio/ATR（纯Python）

用法：
  python3 chokepoint_trader.py                 # 扫描+终端输出
  python3 chokepoint_trader.py --dingtalk       # 扫描+推送钉钉
  python3 chokepoint_trader.py --ticker AXTI    # 只看单个标的
  python3 chokepoint_trader.py --update-history # 仅更新历史数据
  python3 chokepoint_trader.py --status         # 显示持仓状态
  python3 chokepoint_trader.py --backtest AXTI 90  # 回测过去90天
"""

import argparse
import datetime
import hashlib
import hmac
import json
import math
import os
import re
import sqlite3

try:
    import monitor_db as mdb
    _HAS_DB = True
except ImportError:
    _HAS_DB = False
import ssl
import sys
import time
import urllib.parse
import urllib.request
import base64

# ============================================================
# 配置
# ============================================================

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(WORKSPACE, "state")
DB_PATH = os.path.join(STATE_DIR, "price_history.db")
STATE_FILE = os.path.join(STATE_DIR, "trader_state.json")

DINGTALK_WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=61fded0e8252140ae2fcc761ff20f8cf6df9f75d1623c3be6a4cfadb6d9dc586"
DINGTALK_SECRET = "SECd92d215a87b1a45bd6dd94ed0699bcefa5fc7b98e4ffc29226bf0fbf5a25ae71"

BATCH_RATIOS = [0.30, 0.35, 0.35]

WATCHLIST = {
    # === 核心标的（资金面干净，瓶颈位置确认）===
    "COHR":  {"name": "Coherent",     "layer": "L5光模块", "score": 19, "target_usd": 6000, "stop_loss": -0.15, "entry_zone": [370, 410]},
    "LITE":  {"name": "Lumentum",     "layer": "L5光源",   "score": 20, "target_usd": 4000, "stop_loss": -0.15, "entry_zone": [800, 900]},
    "MU":    {"name": "Micron",       "layer": "L2 HBM",   "score": 16, "target_usd": 4000, "stop_loss": -0.15, "entry_zone": [750, 850]},
    "LEU":   {"name": "Centrus",      "layer": "L8核燃料", "score": 18, "target_usd": 2000, "stop_loss": -0.20, "entry_zone": [140, 170]},
    # === 降级标的（内部人大量卖出/中国风险/非真瓶颈）===
    "CRDO":  {"name": "Credo Tech",   "layer": "L4连接",   "score": 12, "target_usd": 0, "stop_loss": -0.15, "entry_zone": [120, 145],
              "downgrade": "CTO卖$58M+CEO卖$30M/0买入，非真瓶颈(Marvell Golden Cable商品化)，30x P/S"},
    "AXTI":  {"name": "AXT Inc",      "layer": "L6 InP",   "score": 17, "target_usd": 0, "stop_loss": -0.20, "entry_zone": [80, 100], "downgrade": "CEO套现$22M+铟管控"},
    "VICR":  {"name": "Vicor",        "layer": "L7功率",   "score": 15, "target_usd": 0, "stop_loss": -0.15, "entry_zone": [250, 285], "downgrade": "CEO卖$111M/0次买入"},
    # === HK代理标的（港交所ETF跟踪韩国KRX）===
    "HYNIX": {"name": "SK Hynix",     "layer": "L2 HBM",   "score": 17, "target_usd": 4000, "stop_loss": -0.15, "entry_zone": [70, 90],
              "source": "tencent_hk", "hk_code": "07709", "currency": "HKD",
              "note": "通过港交所07709 ETF追踪，价格为HKD"},
    # === 新晋核心标的（Serenity五问通过，2026-06深挖）===
    "PLAB":  {"name": "Photronics",   "layer": "L3光掩模", "score": 20, "target_usd": 3000, "stop_loss": -0.15, "entry_zone": [25, 32],
              "note": "全球merchant光掩模双寡头之一，唯一上市纯正标的。PE10.5x，从$56暴跌47%到$29"},
    "CAMT":  {"name": "Camtek",       "layer": "L3检测",   "score": 17, "target_usd": 3000, "stop_loss": -0.15, "entry_zone": [120, 145],
              "note": "先进封装2D/3D检测隐形冠军，HBM/CoWoS必经检测环节。硬度35/50，从$130反弹中"},
    # === 观察标的（待深挖/等入场时机）===
    "ADEA":  {"name": "Adeia",        "layer": "L0 IP封装", "score": 14, "target_usd": 0, "stop_loss": -0.20, "entry_zone": [18, 24],
              "note": "先进封装DBI专利收费站，半导体IP仅占6%营收，CEO Q4离职，等半导体收入占比>20%"},
    "WLDN":  {"name": "Willdan",      "layer": "L9电网接入", "score": None, "target_usd": 0, "stop_loss": -0.15, "entry_zone": [30, 40],
              "note": "数据中心电网互联工程瓶颈，排队4-10年，工程师不可速成"},
    "GSM":   {"name": "Ferroglobe",   "layer": "L1硅金属", "score": None, "target_usd": 0, "stop_loss": -0.25, "entry_zone": [3, 5],
              "note": "西方最大硅金属生产商，0.39x P/S，中国控制70%产能的替代"},
    "SMR":   {"name": "NuScale",      "layer": "L8 SMR",   "score": None, "target_usd": 0, "stop_loss": -0.25, "entry_zone": [8, 12]},
    "NNE":   {"name": "Nano Nuclear", "layer": "L8核物流", "score": None, "target_usd": 0, "stop_loss": -0.20, "entry_zone": [20, 26]},
    "MX":    {"name": "Magnachip",    "layer": "L7 MOSFET","score": None, "target_usd": 0, "stop_loss": -0.25, "entry_zone": [5, 8]},
}

# 宏观入场条件 — 全部达标才考虑建仓
ENTRY_CONDITIONS = {
    "sox_drawdown_pct": -20,  # SOX从近期高点回撤>20%
    "ten_year_below": 4.30,   # 10Y回落(利率压力缓解)
    "usdjpy_above": 148,      # 日元carry trade平仓完成
    "vxx_spike_pct": 30,      # VXX单周涨幅>30%(恐慌释放)
}

# Sina API 宏观指标代码
MACRO_SYMBOLS = {
    "sox":    "gb_sox",
    "spx":    "gb_$inx",
    "dji":    "gb_$dji",
    "vxx":    "gb_vxx",
    "usdjpy": "fx_susdjpy",
}

# ============================================================
# 工具函数
# ============================================================

def create_ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

_SSL_CTX = create_ssl_context()

def http_get(url, referer=None, timeout=15):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    resp = urllib.request.urlopen(req, context=_SSL_CTX, timeout=timeout)
    return resp.read().decode("utf-8", errors="replace")

# ============================================================
# 数据层 — SQLite + Sina K线
# ============================================================

def init_db():
    os.makedirs(STATE_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_bars (
            ticker TEXT, date TEXT, open REAL, high REAL,
            low REAL, close REAL, volume INTEGER,
            PRIMARY KEY (ticker, date)
        )
    """)
    conn.commit()
    return conn

def fetch_sina_kline(ticker):
    url = f"https://stock.finance.sina.com.cn/usstock/api/jsonp.php/x/US_MinKService.getDailyK?symbol={ticker}&_=1"
    raw = http_get(url, referer="https://finance.sina.com.cn")
    match = re.search(r"x\((\[.*\])\)", raw, re.DOTALL)
    if not match:
        return []
    bars = json.loads(match.group(1))
    return [
        {
            "date": b["d"],
            "open": float(b["o"]),
            "high": float(b["h"]),
            "low": float(b["l"]),
            "close": float(b["c"]),
            "volume": int(b["v"]),
        }
        for b in bars
    ]

def fetch_tencent_hk_kline(hk_code, days=250):
    """从腾讯财经拉取港股日K线"""
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=hk{hk_code},day,,,{days},qfq"
    raw = http_get(url, timeout=15)
    data = json.loads(raw)
    if data.get("code") != 0 or not data.get("data"):
        return []
    stock_data = data["data"].get(f"hk{hk_code}", {})
    klines = stock_data.get("day", stock_data.get("qfqday", []))
    if not klines:
        return []
    return [
        {
            "date": k[0],
            "open": float(k[1]),
            "close": float(k[2]),
            "high": float(k[3]),
            "low": float(k[4]),
            "volume": int(float(k[5])) if len(k) > 5 else 0,
        }
        for k in klines
        if len(k) >= 5
    ]

def update_ticker_history(conn, ticker, verbose=False):
    cursor = conn.execute(
        "SELECT MAX(date) FROM daily_bars WHERE ticker=?", (ticker,)
    )
    last_date = cursor.fetchone()[0]

    config = WATCHLIST.get(ticker, {})
    source = config.get("source", "sina_us")

    if source == "tencent_hk":
        bars = fetch_tencent_hk_kline(config["hk_code"], 300)
    else:
        bars = fetch_sina_kline(ticker)
    if not bars:
        if verbose:
            print(f"  {ticker}: 无数据")
        return 0

    new_count = 0
    for b in bars:
        if last_date and b["date"] <= last_date:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO daily_bars VALUES (?,?,?,?,?,?,?)",
            (ticker, b["date"], b["open"], b["high"], b["low"], b["close"], b["volume"]),
        )
        new_count += 1

    if new_count == 0 and not last_date:
        for b in bars[-250:]:
            conn.execute(
                "INSERT OR REPLACE INTO daily_bars VALUES (?,?,?,?,?,?,?)",
                (ticker, b["date"], b["open"], b["high"], b["low"], b["close"], b["volume"]),
            )
            new_count += 1

    conn.commit()
    if verbose:
        print(f"  {ticker}: +{new_count} bars (latest {bars[-1]['date']})")
    return new_count

def get_bars(conn, ticker, days=250):
    cursor = conn.execute(
        "SELECT date, open, high, low, close, volume FROM daily_bars "
        "WHERE ticker=? ORDER BY date DESC LIMIT ?",
        (ticker, days),
    )
    rows = cursor.fetchall()
    rows.reverse()
    return [
        {"date": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]}
        for r in rows
    ]

# ============================================================
# 宏观指标层 — VIX / 10Y / USD-JPY / SOX
# ============================================================

def fetch_sina_macro():
    """从Sina拉取宏观指标：SOX, S&P500, VXX(恐慌代理), USD/JPY"""
    result = {}
    symbols = list(MACRO_SYMBOLS.values())
    url = f"https://hq.sinajs.cn/list={','.join(symbols)}"
    try:
        raw = http_get(url, referer="https://finance.sina.com.cn")
        for line in raw.strip().split("\n"):
            line = line.strip()
            if not line or "=" not in line:
                continue
            var_part, val_part = line.split("=", 1)
            val_str = val_part.strip('" ;\r')
            if not val_str:
                continue
            for name, sym in MACRO_SYMBOLS.items():
                if sym in var_part:
                    fields = val_str.split(",")
                    if sym.startswith("fx_"):
                        result[name] = {
                            "price": _parse_float(fields[1]) if len(fields) > 1 else None,
                            "change": _parse_float(fields[10]) if len(fields) > 10 else None,
                            "change_pct": _parse_float(fields[11]) if len(fields) > 11 else None,
                        }
                    else:
                        result[name] = {
                            "price": _parse_float(fields[1]) if len(fields) > 1 else None,
                            "change_pct": _parse_float(fields[2]) if len(fields) > 2 else None,
                            "change_amt": _parse_float(fields[4]) if len(fields) > 4 else None,
                            "high_52w": _parse_float(fields[8]) if len(fields) > 8 else None,
                            "low_52w": _parse_float(fields[9]) if len(fields) > 9 else None,
                        }
                    break
    except Exception as e:
        print(f"  宏观数据拉取失败: {e}")
    return result

def _parse_float(s):
    try:
        return float(s)
    except (ValueError, TypeError):
        return None

def fetch_macro_indicators():
    """汇总所有宏观指标，计算入场条件"""
    macro = fetch_sina_macro()

    sox = macro.get("sox", {})
    vxx = macro.get("vxx", {})
    usdjpy = macro.get("usdjpy", {})
    spx = macro.get("spx", {})

    sox_price = sox.get("price")
    sox_chg = sox.get("change_pct")
    sox_high = sox.get("high_52w")
    sox_drawdown = ((sox_price / sox_high) - 1) * 100 if sox_price and sox_high and sox_high > 0 else None

    vxx_price = vxx.get("price")
    vxx_chg = vxx.get("change_pct")

    usdjpy_price = usdjpy.get("price")
    usdjpy_chg = usdjpy.get("change_pct")

    conditions_met = 0
    conditions_total = 3
    condition_details = []

    if sox_drawdown is not None:
        if sox_drawdown <= ENTRY_CONDITIONS["sox_drawdown_pct"]:
            conditions_met += 1
            condition_details.append(f"✅ SOX回撤≥20% ({sox_drawdown:.1f}%)")
        else:
            condition_details.append(f"❌ SOX回撤≥20% (当前{sox_drawdown:.1f}%)")
    else:
        condition_details.append("⚪ SOX回撤: N/A")

    if usdjpy_price is not None:
        if usdjpy_price >= ENTRY_CONDITIONS["usdjpy_above"]:
            conditions_met += 1
            condition_details.append(f"✅ USD/JPY≥{ENTRY_CONDITIONS['usdjpy_above']} ({usdjpy_price:.1f})")
        else:
            condition_details.append(f"❌ USD/JPY≥{ENTRY_CONDITIONS['usdjpy_above']} (当前{usdjpy_price:.1f}，carry trade风险)")
    else:
        condition_details.append("⚪ USD/JPY: N/A")

    fear_level = "NEUTRAL"
    if vxx_chg is not None and sox_chg is not None:
        if vxx_chg > 10 or sox_chg < -5:
            fear_level = "EXTREME_FEAR"
        elif vxx_chg > 5 or sox_chg < -3:
            fear_level = "FEAR"
        elif vxx_chg < -3 and sox_chg > 1:
            fear_level = "GREED"

    if fear_level == "EXTREME_FEAR":
        conditions_met += 1
        condition_details.append(f"✅ 恐慌释放 (VXX{vxx_chg:+.1f}% SOX{sox_chg:+.1f}%)")
    elif fear_level == "FEAR":
        condition_details.append(f"🟡 偏恐慌 (VXX{vxx_chg:+.1f}% SOX{sox_chg:+.1f}%)")
    else:
        condition_details.append(f"❌ 未恐慌 (VXX{vxx_chg or 0:+.1f}% SOX{sox_chg or 0:+.1f}%)")

    return {
        "sox": sox_price,
        "sox_chg_pct": sox_chg,
        "sox_high_52w": sox_high,
        "sox_drawdown": sox_drawdown,
        "vxx": vxx_price,
        "vxx_chg_pct": vxx_chg,
        "usdjpy": usdjpy_price,
        "usdjpy_chg_pct": usdjpy_chg,
        "spx": spx.get("price"),
        "spx_chg_pct": spx.get("change_pct"),
        "fear_level": fear_level,
        "conditions_met": conditions_met,
        "conditions_total": conditions_total,
        "condition_details": condition_details,
        "entry_ready": conditions_met >= 2,
    }

def format_macro_section(macro):
    """格式化宏观环境报告段"""
    lines = []
    lines.append("#### 🌍 宏观环境")

    if macro["sox"] is not None:
        sox_str = f"{macro['sox']:.0f}"
        if macro["sox_chg_pct"] is not None:
            sox_str += f" ({macro['sox_chg_pct']:+.1f}%)"
        dd = f" 距高点{macro['sox_drawdown']:.1f}%" if macro["sox_drawdown"] else ""
        lines.append(f"> SOX半导体: {sox_str}{dd}")

    if macro["vxx"] is not None:
        vxx_icon = "🔴" if macro.get("vxx_chg_pct") and macro["vxx_chg_pct"] > 5 else "🟡" if macro.get("vxx_chg_pct") and macro["vxx_chg_pct"] > 0 else "🟢"
        lines.append(f"> VXX恐慌ETF: ${macro['vxx']:.2f} ({macro['vxx_chg_pct']:+.1f}%) {vxx_icon}")

    if macro["usdjpy"] is not None:
        jpy_icon = "🟢" if macro["usdjpy"] > 155 else "🟡" if macro["usdjpy"] > 148 else "🔴"
        chg_str = f" ({macro['usdjpy_chg_pct']:+.2f}%)" if macro["usdjpy_chg_pct"] else ""
        lines.append(f"> USD/JPY: {macro['usdjpy']:.2f}{chg_str} {jpy_icon}")

    if macro["spx"] is not None:
        lines.append(f"> S&P500: {macro['spx']:.0f} ({macro['spx_chg_pct']:+.1f}%)" if macro["spx_chg_pct"] else f"> S&P500: {macro['spx']:.0f}")

    fear_icon = {"EXTREME_FEAR": "😱", "FEAR": "😰", "NEUTRAL": "😐", "GREED": "🤑"}.get(macro["fear_level"], "")
    lines.append(f"> 恐慌程度: {macro['fear_level']} {fear_icon}")
    lines.append("")

    ready = macro["conditions_met"]
    total = macro["conditions_total"]
    lines.append(f"**入场条件: {ready}/{total}** {'✅ 可考虑建仓' if macro['entry_ready'] else '❌ 继续等待'}")
    for cd in macro["condition_details"]:
        lines.append(f"> {cd}")
    lines.append("")

    return "\n".join(lines)

GRAY_RHINOS = [
    {"name": "日元Carry Trade", "trigger": "BOJ加息/USD-JPY急跌", "monitor": "USD/JPY", "status": "active"},
    {"name": "SpaceX IPO虹吸", "trigger": "6/12上市→指数基金被动卖半导体", "monitor": "SOX", "status": "active"},
    {"name": "加息预期", "trigger": "Fed删除宽松措辞/暗示加息", "monitor": "10Y/VIX", "status": "active"},
    {"name": "AI Capex回报率", "trigger": "任一超算厂暂停/削减Capex", "monitor": "COHR/LITE收入", "status": "watching"},
    {"name": "中国光模块+铟管控", "trigger": "出口管制升级/铟断供", "monitor": "AXTI/InP价格", "status": "structural"},
    {"name": "HALEU叙事gap", "trigger": "SMR延期/LEU产能不及预期", "monitor": "LEU", "status": "watching"},
]

def format_gray_rhino_section():
    active = [r for r in GRAY_RHINOS if r["status"] == "active"]
    if not active:
        return ""
    lines = ["#### ⚠️ 灰犀牛监控"]
    for r in active:
        lines.append(f"> 🦏 **{r['name']}**: {r['trigger']} (监控: {r['monitor']})")
    lines.append("")
    return "\n".join(lines)

# ============================================================
# 指标层 — 纯Python（移植自technicalAnalyzer.js）
# ============================================================

def sma(data, period):
    result = [None] * len(data)
    for i in range(period - 1, len(data)):
        result[i] = sum(data[i - period + 1 : i + 1]) / period
    return result

def ema(data, period):
    result = [None] * len(data)
    k = 2 / (period + 1)
    prev = data[0]
    result[0] = data[0]
    for i in range(1, len(data)):
        prev = data[i] * k + prev * (1 - k)
        result[i] = prev
    return result

def calc_rsi(closes, period=14):
    result = [None] * len(closes)
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(diff if diff > 0 else 0)
        losses.append(-diff if diff < 0 else 0)
    if len(gains) < period:
        return result
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    result[period] = 100 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        result[i + 1] = 100 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return result

def calc_macd(closes):
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    macd_line = [
        (ema12[i] - ema26[i]) if (ema12[i] is not None and ema26[i] is not None) else None
        for i in range(len(closes))
    ]
    valid = [v for v in macd_line if v is not None]
    sig = ema(valid, 9) if len(valid) >= 9 else [None] * len(valid)
    signal_line = [None] * len(macd_line)
    si = 0
    for i in range(len(macd_line)):
        if macd_line[i] is not None:
            signal_line[i] = sig[si] if si < len(sig) else None
            si += 1
    histogram = [
        (macd_line[i] - signal_line[i]) if (macd_line[i] is not None and signal_line[i] is not None) else None
        for i in range(len(macd_line))
    ]
    return macd_line, signal_line, histogram

def calc_bollinger(closes, period=20, mult=2):
    mid = sma(closes, period)
    upper = [None] * len(closes)
    lower = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        sl = closes[i - period + 1 : i + 1]
        mean = mid[i]
        std = math.sqrt(sum((v - mean) ** 2 for v in sl) / period)
        upper[i] = mean + mult * std
        lower[i] = mean - mult * std
    return upper, lower

def calc_volume_ratio(volumes, period=20):
    avg = sma(volumes, period)
    return [volumes[i] / avg[i] if avg[i] and avg[i] > 0 else None for i in range(len(volumes))]

def calc_atr(highs, lows, closes, period=20):
    trs = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    return sma(trs, period)

def compute_indicators(bars):
    if len(bars) < 50:
        return None
    closes = [b["close"] for b in bars]
    volumes = [b["volume"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]

    sma20 = sma(closes, 20)
    sma50 = sma(closes, 50)
    sma200 = sma(closes, 200) if len(closes) >= 200 else [None] * len(closes)
    rsi = calc_rsi(closes, 14)
    macd_line, macd_signal, macd_hist = calc_macd(closes)
    bb_upper, bb_lower = calc_bollinger(closes, 20, 2)
    vol_ratio = calc_volume_ratio(volumes, 20)
    atr = calc_atr(highs, lows, closes, 20)

    last = len(closes) - 1
    prev = last - 1

    return {
        "date": bars[last]["date"],
        "close": closes[last],
        "prev_close": closes[prev],
        "open": bars[last]["open"],
        "high": bars[last]["high"],
        "low": bars[last]["low"],
        "volume": volumes[last],
        "sma20": sma20[last],
        "sma50": sma50[last],
        "sma200": sma200[last],
        "rsi": rsi[last],
        "prev_rsi": rsi[prev] if prev >= 0 else None,
        "macd": macd_line[last],
        "macd_signal": macd_signal[last],
        "macd_hist": macd_hist[last],
        "prev_macd": macd_line[prev],
        "prev_macd_signal": macd_signal[prev],
        "prev_macd_hist": macd_hist[prev] if prev >= 0 else None,
        "bb_upper": bb_upper[last],
        "bb_lower": bb_lower[last],
        "bb_mid": sma20[last],
        "volume_ratio": vol_ratio[last],
        "atr": atr[last],
        "high_20": max(closes[max(0, last - 19) : last + 1]),
        "low_20": min(closes[max(0, last - 19) : last + 1]),
    }

# ============================================================
# 信号层 — 多因子评分
# ============================================================

def compute_signal_score(ind, config, extra_signals=None):
    if ind is None:
        return 0, ["数据不足"], "NO_DATA"

    score = 0
    details = []

    # --- 趋势得分 (0-30) ---
    if ind["sma20"] and ind["sma50"]:
        if ind["close"] > ind["sma20"] > ind["sma50"]:
            if ind["sma200"] and ind["sma20"] > ind["sma200"]:
                score += 30
                details.append("+MA多头排列(20>50>200)")
            else:
                score += 20
                details.append("+MA偏多(20>50)")
        elif ind["close"] > ind["sma20"]:
            score += 10
            details.append("+站上20MA")
        elif ind["close"] < ind["sma20"] < ind["sma50"]:
            details.append("-MA空头排列")

    # --- 动量得分 (0-25) ---
    if ind["macd"] is not None and ind["macd_signal"] is not None:
        if ind["prev_macd"] is not None and ind["prev_macd_signal"] is not None:
            if ind["macd"] > ind["macd_signal"] and ind["prev_macd"] <= ind["prev_macd_signal"]:
                score += 15
                details.append("+MACD金叉")
            elif ind["macd"] < ind["macd_signal"] and ind["prev_macd"] >= ind["prev_macd_signal"]:
                score -= 5
                details.append("-MACD死叉")
        if ind["macd_hist"] is not None and ind["prev_macd_hist"] is not None:
            if ind["prev_macd_hist"] < 0 and ind["macd_hist"] > 0:
                score += 10
                details.append("+MACD柱转正")

    if ind["rsi"] is not None:
        if ind["rsi"] < 30:
            score += 10
            details.append(f"+RSI超卖({ind['rsi']:.0f})")
        elif ind["prev_rsi"] is not None and ind["prev_rsi"] < 30 and ind["rsi"] >= 30:
            score += 15
            details.append(f"+RSI超卖反弹({ind['prev_rsi']:.0f}→{ind['rsi']:.0f})")
        elif 40 <= ind["rsi"] <= 60:
            score += 5
            details.append(f"+RSI健康({ind['rsi']:.0f})")
        elif ind["rsi"] > 70:
            score -= 10
            details.append(f"-RSI超买({ind['rsi']:.0f})")

    # --- 波动位置得分 (0-20) ---
    if ind["bb_lower"] is not None and ind["bb_upper"] is not None:
        if ind["close"] <= ind["bb_lower"] and ind["close"] > ind["prev_close"]:
            score += 15
            details.append("+触布林下轨反弹")
        elif ind["close"] <= ind["bb_lower"]:
            score += 10
            details.append("+触布林下轨")
        elif ind["bb_mid"] and ind["close"] > ind["bb_mid"]:
            score += 10
            details.append("+布林中轨上方")
        elif ind["close"] >= ind["bb_upper"]:
            details.append("-触布林上轨")

    # --- 量价得分 (0-15) ---
    if ind["volume_ratio"] is not None:
        is_up = ind["close"] > ind["prev_close"]
        if ind["volume_ratio"] > 1.5 and is_up:
            score += 15
            details.append(f"+放量上涨(量比{ind['volume_ratio']:.1f})")
        elif ind["volume_ratio"] < 0.7 and not is_up:
            score += 10
            details.append("+缩量回调(健康)")
        elif ind["volume_ratio"] > 1.5 and not is_up:
            score -= 5
            details.append(f"-放量下跌(量比{ind['volume_ratio']:.1f})")

    # --- 入场区间得分 (0-10) ---
    ez_low, ez_high = config["entry_zone"]
    if ez_low <= ind["close"] <= ez_high:
        score += 10
        details.append(f"+入场区间内(${ez_low}-${ez_high})")
    elif ind["close"] < ez_low:
        score += 8
        details.append(f"+低于入场区间(更便宜)")
    else:
        score += 2
        details.append(f"~高于入场区间")

    if extra_signals:
        for sig in extra_signals:
            score += sig["delta"]
            details.append(sig["detail"])

    score = max(0, min(100, score))

    if score >= 75:
        signal = "STRONG_BUY"
    elif score >= 60:
        signal = "BUY"
    elif score >= 40:
        signal = "HOLD"
    elif score >= 25:
        signal = "CAUTION"
    else:
        signal = "SELL"

    return score, details, signal

# ============================================================
# 仓位管理
# ============================================================

def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "last_scan": None,
            "positions": {},
            "signals_history": {},
            "alerts_sent": {},
        }

def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


INSIDER_STATE_FILE = os.path.join(STATE_DIR, "insider_state.json")
FILING_STATE_FILE = os.path.join(STATE_DIR, "filing_state.json")

def load_extra_signals(ticker):
    extra = []
    for path, key in [(INSIDER_STATE_FILE, "insider_scores"),
                      (FILING_STATE_FILE, "filing_scores")]:
        try:
            with open(path) as f:
                data = json.load(f)
            entry = data.get(key, {}).get(ticker)
            if entry and entry.get("score", 0) != 0:
                s = entry["score"]
                dlist = entry.get("details", [])
                label = "👤内部人" if key == "insider_scores" else "📋Filing"
                actionable = [d for d in dlist if d.startswith("-") or d.startswith("+")]
                summary = actionable[0] if actionable else (dlist[0] if dlist else "")
                extra.append({"delta": s, "detail": f"{label}{s:+d}({summary})"})
        except (FileNotFoundError, json.JSONDecodeError):
            pass
    return extra if extra else None


def get_position(state, ticker):
    return state["positions"].get(ticker, {
        "batch": 0, "avg_cost": 0, "shares": 0,
        "total_invested": 0, "first_entry_date": None,
        "last_signal": None, "last_signal_date": None,
        "peak_price": 0,
    })

def determine_action(signal, current_batch, config):
    target = config["target_usd"]
    if target == 0:
        return "WATCH", 0, ""

    if signal == "STRONG_BUY":
        if current_batch == 0:
            amt = target * BATCH_RATIOS[0]
            return "BUILD_BASE", amt, f"建底仓{BATCH_RATIOS[0]:.0%}"
        elif current_batch == 1:
            amt = target * BATCH_RATIOS[1]
            return "ADD_1", amt, f"加仓至{sum(BATCH_RATIOS[:2]):.0%}"
        elif current_batch == 2:
            amt = target * BATCH_RATIOS[2]
            return "ADD_2", amt, f"满仓{sum(BATCH_RATIOS):.0%}"
        else:
            return "HOLD_FULL", 0, "已满仓"
    elif signal == "BUY":
        if current_batch == 0:
            amt = target * BATCH_RATIOS[0]
            return "BUILD_BASE", amt, f"建底仓{BATCH_RATIOS[0]:.0%}"
        else:
            return "HOLD", 0, "等STRONG_BUY加仓"
    elif signal == "CAUTION":
        if current_batch > 0:
            return "TIGHTEN_STOP", 0, "收紧止损"
        return "WAIT", 0, "观望"
    elif signal == "SELL":
        if current_batch > 0:
            return "CHECK_STOP", 0, "检查止损"
        return "AVOID", 0, "回避"
    else:
        return "WAIT", 0, "等待信号"

def check_stop_loss(pos, price, config):
    if pos["batch"] == 0 or pos["avg_cost"] == 0:
        return None
    pnl_pct = (price - pos["avg_cost"]) / pos["avg_cost"]
    if pnl_pct <= config["stop_loss"]:
        return {
            "type": "STOP_LOSS",
            "price": price,
            "cost": pos["avg_cost"],
            "pnl_pct": pnl_pct,
            "shares": pos["shares"],
            "loss_usd": (price - pos["avg_cost"]) * pos["shares"],
        }
    peak = max(pos.get("peak_price", price), price)
    if pnl_pct > 0.20 and peak > 0:
        trailing_pct = (price - peak) / peak
        if trailing_pct < -0.15:
            return {
                "type": "TRAILING_STOP",
                "price": price,
                "peak": peak,
                "drawdown_pct": trailing_pct,
                "shares": pos["shares"],
            }
    if pnl_pct > 0.50:
        return {
            "type": "TAKE_PROFIT",
            "price": price,
            "cost": pos["avg_cost"],
            "pnl_pct": pnl_pct,
            "shares": pos["shares"],
        }
    return None

# ============================================================
# 报告层
# ============================================================

def format_trade_instruction(ticker, config, score, signal, details, action, amount, price, ind, pos):
    lines = []
    batch_label = {0: "无仓", 1: "底仓", 2: "加仓1", 3: "满仓"}.get(pos["batch"], "?")
    signal_icon = {"STRONG_BUY": "🟢🟢", "BUY": "🟢", "HOLD": "🟡", "CAUTION": "🟠", "SELL": "🔴"}.get(signal, "⚪")

    lines.append(f"**{ticker}** ({config['name']}) ${price:.2f} | {signal_icon} {score}/100 {signal}")
    lines.append(f"> {' '.join(details[:5])}")

    if amount > 0:
        shares_est = int(amount / price) if price > 0 else 0
        stop_price = price * (1 + config["stop_loss"])
        lines.append(f"> 操作: {action} = ${amount:.0f} ≈ {shares_est}股")
        lines.append(f"> 止损: ${stop_price:.2f} ({config['stop_loss']:.0%}) | 区间: ${config['entry_zone'][0]}-${config['entry_zone'][1]}")
    elif pos["batch"] > 0:
        pnl_pct = (price - pos["avg_cost"]) / pos["avg_cost"] * 100 if pos["avg_cost"] > 0 else 0
        lines.append(f"> 状态: {batch_label} | 成本${pos['avg_cost']:.2f} | 盈亏{pnl_pct:+.1f}%")
    else:
        lines.append(f"> {action}")

    if ind and ind["rsi"]:
        sma20_str = f"SMA20=${ind['sma20']:.1f}" if ind["sma20"] else ""
        rsi_str = f"RSI={ind['rsi']:.0f}" if ind["rsi"] else ""
        vr_str = f"量比={ind['volume_ratio']:.1f}" if ind["volume_ratio"] else ""
        lines.append(f"> {sma20_str} {rsi_str} {vr_str}")

    return "\n".join(lines)

def format_daily_report(scan_results, state, macro=None):
    today = datetime.date.today().strftime("%m/%d")
    lines = [f"### 📊 瓶颈股信号扫描 ({today})", ""]

    if macro:
        lines.append(format_macro_section(macro))
        rhino = format_gray_rhino_section()
        if rhino:
            lines.append(rhino)

    buys = [r for r in scan_results if r["signal"] in ("STRONG_BUY", "BUY") and r["config"]["target_usd"] > 0]
    watches = [r for r in scan_results if r["signal"] == "HOLD" or r["config"]["target_usd"] == 0]
    cautions = [r for r in scan_results if r["signal"] in ("CAUTION", "SELL")]
    stops = [r for r in scan_results if r.get("stop_alert")]

    if buys:
        lines.append("#### 🟢 买入信号")
        for r in buys:
            lines.append(r["instruction"])
            lines.append("")

    if stops:
        lines.append("#### 🔴 止损警报")
        for r in stops:
            sa = r["stop_alert"]
            if sa["type"] == "STOP_LOSS":
                lines.append(f"**{r['ticker']}** ${sa['price']:.2f} 跌破止损 ${r['config']['entry_zone'][0]} ({sa['pnl_pct']:.1%})")
            elif sa["type"] == "TRAILING_STOP":
                lines.append(f"**{r['ticker']}** 从高点${sa['peak']:.2f}回撤{sa['drawdown_pct']:.1%}")
            elif sa["type"] == "TAKE_PROFIT":
                lines.append(f"**{r['ticker']}** 盈利{sa['pnl_pct']:.1%} 建议减仓1/3锁利")
            lines.append("")

    if watches:
        lines.append("#### 🟡 观察中")
        for r in watches:
            pos = get_position(state, r["ticker"])
            batch_str = f"Batch {pos['batch']}" if pos["batch"] > 0 else "待入场"
            layer = r["config"]["layer"]
            target_str = f"目标${r['config']['target_usd']}" if r["config"]["target_usd"] > 0 else "仅观察"
            lines.append(f"**{r['ticker']}** ${r['price']:.2f} | {r['score']}/100 {r['signal']} | {layer} {batch_str} {target_str}")
        lines.append("")

    if cautions:
        lines.append("#### 🟠 风险关注")
        for r in cautions:
            lines.append(f"**{r['ticker']}** ${r['price']:.2f} | {r['score']}/100 {r['signal']} | {' '.join(r['details'][:3])}")
        lines.append("")

    # 持仓状态
    held = {t: get_position(state, t) for t in WATCHLIST if get_position(state, t)["batch"] > 0}
    if held:
        lines.append("#### 📈 持仓状态")
        lines.append("| 标的 | Batch | 成本 | 现价 | 盈亏 | 金额 |")
        lines.append("|------|-------|------|------|------|------|")
        for t, p in held.items():
            cur = next((r["price"] for r in scan_results if r["ticker"] == t), 0)
            pnl = (cur - p["avg_cost"]) / p["avg_cost"] * 100 if p["avg_cost"] > 0 else 0
            val = cur * p["shares"]
            lines.append(f"| {t} | {p['batch']}/3 | ${p['avg_cost']:.2f} | ${cur:.2f} | {pnl:+.1f}% | ${val:.0f} |")
        lines.append("")

    lines.append("---")
    lines.append("📊 瓶颈股交易系统 | 每日收盘扫描")
    return "\n".join(lines)

def format_stop_alert(ticker, config, alert, pos):
    lines = [f"### 🚨 止损警报 — {ticker} ({config['name']})", ""]
    if alert["type"] == "STOP_LOSS":
        stop_line = pos["avg_cost"] * (1 + config["stop_loss"])
        lines.append(f"当前价 ${alert['price']:.2f} 跌破止损线 ${stop_line:.2f} ({config['stop_loss']:.0%})")
        lines.append(f"成本 ${pos['avg_cost']:.2f} | 亏损 {alert['pnl_pct']:.1%} | 持仓 {pos['shares']:.0f}股")
        lines.append(f"**建议: 立即止损卖出，锁定亏损 ${abs(alert['loss_usd']):.0f}**")
    elif alert["type"] == "TRAILING_STOP":
        lines.append(f"从高点 ${alert['peak']:.2f} 回撤 {alert['drawdown_pct']:.1%}")
        lines.append(f"**建议: 减仓保护利润**")
    elif alert["type"] == "TAKE_PROFIT":
        lines.append(f"盈利 {alert['pnl_pct']:.1%} 🎉")
        lines.append(f"成本 ${pos['avg_cost']:.2f} → 现价 ${alert['price']:.2f}")
        lines.append(f"**建议: 卖出1/3 ({pos['shares'] / 3:.0f}股) 锁定部分利润**")
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
    sign = urllib.parse.quote(base64.b64encode(hmac_code).decode("utf-8"))
    return timestamp, sign

def send_dingtalk(report_md, title="瓶颈股信号"):
    timestamp, sign = dingtalk_sign()
    url = f"{DINGTALK_WEBHOOK}&timestamp={timestamp}&sign={sign}"
    payload = json.dumps({
        "msgtype": "markdown",
        "markdown": {"title": title, "text": report_md},
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, context=_SSL_CTX, timeout=10)
        result = json.loads(resp.read().decode("utf-8"))
        return result.get("errcode") == 0
    except Exception as e:
        print(f"钉钉推送失败: {e}")
        return False

# ============================================================
# 主流程
# ============================================================

def run_update_history(verbose=True):
    conn = init_db()
    if verbose:
        print("更新历史K线数据...")
    for ticker in WATCHLIST:
        try:
            update_ticker_history(conn, ticker, verbose=verbose)
            time.sleep(0.3)
        except Exception as e:
            if verbose:
                print(f"  {ticker}: 错误 {e}")
    conn.close()

def run_scan(dingtalk=False, ticker_filter=None, verbose=True):
    conn = init_db()
    state = load_state()

    for t in WATCHLIST:
        if ticker_filter and t != ticker_filter.upper():
            continue
        try:
            update_ticker_history(conn, t, verbose=False)
            time.sleep(0.2)
        except Exception:
            pass

    scan_results = []
    tickers = [ticker_filter.upper()] if ticker_filter else list(WATCHLIST.keys())

    for ticker in tickers:
        if ticker not in WATCHLIST:
            continue
        config = WATCHLIST[ticker]
        bars = get_bars(conn, ticker, 250)
        if not bars:
            if verbose:
                print(f"{ticker}: 无历史数据")
            continue

        ind = compute_indicators(bars)
        if ind is None:
            if verbose:
                print(f"{ticker}: 数据不足({len(bars)}天)")
            continue

        price = ind["close"]
        extra = load_extra_signals(ticker)
        score, details, signal = compute_signal_score(ind, config, extra)

        pos = get_position(state, ticker)
        action_type, amount, action_desc = determine_action(signal, pos["batch"], config)

        stop_alert = check_stop_loss(pos, price, config) if pos["batch"] > 0 else None

        if pos["batch"] > 0 and price > pos.get("peak_price", 0):
            pos["peak_price"] = price
            state["positions"][ticker] = pos

        instruction = format_trade_instruction(
            ticker, config, score, signal, details, action_desc, amount, price, ind, pos
        )

        result = {
            "ticker": ticker,
            "config": config,
            "price": price,
            "score": score,
            "signal": signal,
            "details": details,
            "action_type": action_type,
            "amount": amount,
            "action_desc": action_desc,
            "instruction": instruction,
            "stop_alert": stop_alert,
            "indicators": ind,
        }
        scan_results.append(result)

        if ticker not in state["signals_history"]:
            state["signals_history"][ticker] = []
        state["signals_history"][ticker].append({
            "date": ind["date"], "score": score, "signal": signal, "price": price,
        })
        state["signals_history"][ticker] = state["signals_history"][ticker][-30:]

        if _HAS_DB:
            try:
                mdb.save_signal(ticker, ind["date"], price, score, signal)
            except Exception:
                pass

    scan_results.sort(key=lambda r: r["score"], reverse=True)

    macro = None
    try:
        macro = fetch_macro_indicators()
        if verbose and macro:
            print(f"\n宏观环境: SOX={macro['sox'] or 'N/A'} VXX={macro['vxx'] or 'N/A'} USD/JPY={macro['usdjpy'] or 'N/A'} 恐慌={macro['fear_level']}")
            print(f"入场条件: {macro['conditions_met']}/{macro['conditions_total']} {'✅' if macro['entry_ready'] else '❌'}")
    except Exception as e:
        if verbose:
            print(f"宏观数据获取失败: {e}")

    if _HAS_DB and macro:
        try:
            mdb.save_macro(
                date=datetime.datetime.now().strftime("%Y-%m-%d"),
                sox=macro.get("sox"), sox_chg=macro.get("sox_chg"),
                vxx=macro.get("vxx"), vxx_chg=macro.get("vxx_chg"),
                usdjpy=macro.get("usdjpy"), fear_level=macro.get("fear_level"),
                conditions_met=macro.get("conditions_met", 0),
                entry_ready=1 if macro.get("entry_ready") else 0,
            )
        except Exception:
            pass

    if verbose:
        print("\n" + "=" * 60)
        for r in scan_results:
            print(r["instruction"])
            print("-" * 40)

    report = format_daily_report(scan_results, state, macro=macro)

    has_actionable = any(
        r["signal"] in ("STRONG_BUY", "BUY") or r.get("stop_alert")
        for r in scan_results
    )

    if dingtalk and (has_actionable or ticker_filter):
        if verbose:
            print("\n推送钉钉...")
        send_dingtalk(report, title="瓶颈股信号扫描")

        for r in scan_results:
            if r.get("stop_alert"):
                pos = get_position(state, r["ticker"])
                alert_report = format_stop_alert(r["ticker"], r["config"], r["stop_alert"], pos)
                send_dingtalk(alert_report, title=f"🚨止损警报-{r['ticker']}")
    elif dingtalk and verbose:
        print("无买入/止损信号，跳过推送")

    state["last_scan"] = datetime.datetime.now().isoformat()
    save_state(state)
    conn.close()
    return scan_results

def run_backtest(ticker, days, verbose=True):
    conn = init_db()
    try:
        update_ticker_history(conn, ticker, verbose=False)
    except Exception:
        pass

    bars = get_bars(conn, ticker, days + 200)
    conn.close()

    if len(bars) < 50:
        print(f"{ticker}: 数据不足")
        return

    config = WATCHLIST.get(ticker, {
        "name": ticker, "layer": "?", "score": None,
        "target_usd": 5000, "stop_loss": -0.15, "entry_zone": [0, 999999],
    })

    print(f"\n{'='*60}")
    print(f"回测: {ticker} ({config['name']}) 过去{days}天")
    print(f"{'='*60}")

    signals = []
    for i in range(200, len(bars)):
        window = bars[max(0, i - 249) : i + 1]
        ind = compute_indicators(window)
        if ind is None:
            continue
        score, details, signal = compute_signal_score(ind, config)
        if signal in ("STRONG_BUY", "BUY", "SELL"):
            signals.append({
                "date": bars[i]["date"],
                "price": bars[i]["close"],
                "score": score,
                "signal": signal,
                "details": details[:3],
            })

    buy_count = sum(1 for s in signals if s["signal"] in ("STRONG_BUY", "BUY"))
    sell_count = sum(1 for s in signals if s["signal"] == "SELL")
    print(f"\n信号统计: {buy_count}次买入信号, {sell_count}次卖出信号")

    if signals:
        print(f"\n{'日期':<12} {'价格':>8} {'分数':>4} {'信号':<12} 详情")
        print("-" * 70)
        for s in signals[-20:]:
            det = " ".join(s["details"])
            icon = {"STRONG_BUY": "🟢🟢", "BUY": "🟢", "SELL": "🔴"}.get(s["signal"], "")
            print(f"{s['date']:<12} ${s['price']:>7.2f} {s['score']:>4} {icon}{s['signal']:<12} {det[:40]}")

    if buy_count > 0:
        first_buy = next(s for s in signals if s["signal"] in ("STRONG_BUY", "BUY"))
        last_price = bars[-1]["close"]
        gain = (last_price - first_buy["price"]) / first_buy["price"] * 100
        print(f"\n首次买入: {first_buy['date']} @ ${first_buy['price']:.2f}")
        print(f"当前价格: ${last_price:.2f} ({gain:+.1f}%)")

def run_status(verbose=True):
    state = load_state()
    if not state["positions"]:
        print("暂无持仓")
        return

    print(f"\n{'='*60}")
    print("当前持仓状态")
    print(f"{'='*60}")
    print(f"\n{'标的':<6} {'Batch':>5} {'成本':>8} {'股数':>6} {'投入':>8} {'入场日':>12}")
    print("-" * 50)
    total = 0
    for ticker, pos in state["positions"].items():
        if pos["batch"] > 0:
            print(f"{ticker:<6} {pos['batch']:>3}/3 ${pos['avg_cost']:>7.2f} {pos['shares']:>5.0f} ${pos['total_invested']:>7.0f} {pos.get('first_entry_date', '?'):>12}")
            total += pos["total_invested"]
    print(f"\n总投入: ${total:.0f}")
    print(f"上次扫描: {state.get('last_scan', '未运行')}")

def run_macro(verbose=True, dingtalk=False):
    macro = fetch_macro_indicators()
    report = format_macro_section(macro) + "\n" + format_gray_rhino_section()
    if verbose:
        print(report)
    if dingtalk:
        send_dingtalk(report, title="宏观环境扫描")

# ============================================================
# 盘中实时监控
# ============================================================

def fetch_realtime_batch():
    """Sina批量实时行情 — 美股+港股+宏观指标一次拉取"""
    us_tickers = [t for t, c in WATCHLIST.items() if c.get("source", "sina_us") == "sina_us"]
    hk_tickers = [(t, c["hk_code"]) for t, c in WATCHLIST.items() if c.get("source") == "tencent_hk"]

    symbols = [f"gb_{t.lower()}" for t in us_tickers]
    symbols += [f"hk{code}" for _, code in hk_tickers]
    symbols += list(MACRO_SYMBOLS.values())

    url = f"https://hq.sinajs.cn/list={','.join(symbols)}"
    raw = http_get(url, referer="https://finance.sina.com.cn")

    prices = {}
    macro_raw = {}

    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        var_part, val_part = line.split("=", 1)
        val_str = val_part.strip('" ;\r')
        if not val_str:
            continue
        fields = val_str.split(",")

        for t in us_tickers:
            if f"gb_{t.lower()}" in var_part:
                prices[t] = {
                    "price": _parse_float(fields[1]),
                    "chg_pct": _parse_float(fields[2]),
                    "prev_close": _parse_float(fields[26]) if len(fields) > 26 else None,
                }
                break
        for t, code in hk_tickers:
            if f"hk{code}" in var_part:
                prices[t] = {
                    "price": _parse_float(fields[6]) if len(fields) > 6 else _parse_float(fields[1]),
                    "chg_pct": _parse_float(fields[7]) if len(fields) > 7 else None,
                }
                break
        for name, sym in MACRO_SYMBOLS.items():
            if sym in var_part:
                if sym.startswith("fx_"):
                    macro_raw[name] = {"price": _parse_float(fields[1]), "chg_pct": _parse_float(fields[11]) if len(fields) > 11 else None}
                else:
                    macro_raw[name] = {"price": _parse_float(fields[1]), "chg_pct": _parse_float(fields[2])}
                break

    return prices, macro_raw

def run_intraday(dingtalk=True, verbose=False):
    """盘中快速扫描：实时价格 + 宏观 → 异常立即推送"""
    state = load_state()
    now = datetime.datetime.now()
    now_str = now.strftime("%H:%M")

    try:
        prices, macro_raw = fetch_realtime_batch()
    except Exception as e:
        if verbose:
            print(f"实时数据拉取失败: {e}")
        return

    sox = macro_raw.get("sox", {})
    vxx = macro_raw.get("vxx", {})
    usdjpy = macro_raw.get("usdjpy", {})

    alerts = []

    # --- 宏观异动检测 ---
    sox_chg = sox.get("chg_pct")
    vxx_chg = vxx.get("chg_pct")
    usdjpy_price = usdjpy.get("price")

    if sox_chg is not None and sox_chg <= -3:
        alerts.append(f"🔴 SOX暴跌 {sox_chg:+.1f}% ({sox.get('price', 0):.0f})")
    if vxx_chg is not None and vxx_chg >= 8:
        alerts.append(f"🔴 VXX恐慌飙升 {vxx_chg:+.1f}% (${vxx.get('price', 0):.2f})")
    if usdjpy_price is not None and usdjpy_price < 155:
        alerts.append(f"🔴 USD/JPY跌破155 ({usdjpy_price:.1f}) carry trade警报")
    if usdjpy_price is not None and usdjpy_price < 150:
        alerts.append(f"🚨 USD/JPY跌破150 ({usdjpy_price:.1f}) carry trade全面平仓")

    # --- 个股入场区间检测 ---
    entry_zone_hits = []
    for ticker, config in WATCHLIST.items():
        if config["target_usd"] == 0:
            continue
        p = prices.get(ticker, {})
        price = p.get("price")
        if price is None:
            continue
        chg = p.get("chg_pct")

        ez_low, ez_high = config["entry_zone"]
        if ez_low <= price <= ez_high:
            entry_zone_hits.append(f"🟢 **{ticker}** ${price:.2f} 进入入场区间 (${ez_low}-${ez_high})")
        elif price < ez_low:
            entry_zone_hits.append(f"🟢🟢 **{ticker}** ${price:.2f} 低于入场区间下限${ez_low} (更便宜!)")

        if chg is not None and chg <= -5:
            alerts.append(f"📉 {ticker} 暴跌{chg:+.1f}% → ${price:.2f}")

    # --- 持仓止损检测 ---
    for ticker, config in WATCHLIST.items():
        pos = get_position(state, ticker)
        if pos["batch"] == 0:
            continue
        p = prices.get(ticker, {})
        price = p.get("price")
        if price is None:
            continue
        stop = check_stop_loss(pos, price, config)
        if stop:
            alerts.append(f"🚨 **{ticker}** 触发{'止损' if stop['type']=='STOP_LOSS' else '移动止损' if stop['type']=='TRAILING_STOP' else '止盈'} @ ${price:.2f}")

    # --- 判断是否推送 ---
    should_push = len(alerts) > 0 or len(entry_zone_hits) > 0

    if not should_push:
        if verbose:
            print(f"[{now_str}] 无异常 SOX={sox.get('price','N/A')} VXX={vxx.get('price','N/A')} JPY={usdjpy_price or 'N/A'}")
        return

    # 防止重复推送：同一小时内相同类型不重复
    alert_key = f"intraday_{now.strftime('%Y%m%d_%H')}"
    if state.get("alerts_sent", {}).get(alert_key):
        if verbose:
            print(f"[{now_str}] 有异常但本小时已推送，跳过")
        return

    # --- 构建报告 ---
    lines = [f"### ⚡ 盘中异动 ({now_str})", ""]

    if alerts:
        lines.append("#### 🚨 宏观/个股异动")
        lines.extend(alerts)
        lines.append("")

    if entry_zone_hits:
        lines.append("#### 🎯 入场区间触发")
        lines.extend(entry_zone_hits)
        lines.append("")

    # 附加当前全景
    lines.append("#### 📊 实时快照")
    core_tickers = [t for t, c in WATCHLIST.items() if c["target_usd"] > 0]
    for t in core_tickers:
        p = prices.get(t, {})
        price = p.get("price")
        chg = p.get("chg_pct")
        if price is None:
            continue
        config = WATCHLIST[t]
        ez_low, ez_high = config["entry_zone"]
        zone_status = "✅区间内" if ez_low <= price <= ez_high else "⬇️低于区间" if price < ez_low else "⬆️高于区间"
        chg_str = f" ({chg:+.1f}%)" if chg is not None else ""
        currency = config.get("currency", "USD")
        sym = "$" if currency == "USD" else "HK$"
        lines.append(f"> {t} {sym}{price:.2f}{chg_str} | {zone_status} ({sym}{ez_low}-{ez_high})")

    lines.append("")
    lines.append(f"> SOX={sox.get('price', 'N/A')} ({sox.get('chg_pct', 0):+.1f}%) | VXX=${vxx.get('price', 'N/A')} ({vxx.get('chg_pct', 0):+.1f}%) | USD/JPY={usdjpy_price or 'N/A'}")
    lines.append("")
    lines.append("---")
    lines.append("⚡ 瓶颈股盘中监控")

    report = "\n".join(lines)

    if verbose:
        print(report)

    if dingtalk:
        title = "🚨盘中异动" if alerts else "🎯入场区间触发"
        send_dingtalk(report, title=title)
        state.setdefault("alerts_sent", {})[alert_key] = True
        save_state(state)
        if verbose:
            print("已推送钉钉")

def main():
    parser = argparse.ArgumentParser(description="Chokepoint Trader — AI供应链瓶颈股交易信号系统")
    parser.add_argument("--dingtalk", action="store_true", help="推送到钉钉")
    parser.add_argument("--ticker", type=str, help="只扫描单个标的")
    parser.add_argument("--update-history", action="store_true", help="仅更新历史数据")
    parser.add_argument("--status", action="store_true", help="显示持仓状态")
    parser.add_argument("--backtest", nargs=2, metavar=("TICKER", "DAYS"), help="回测")
    parser.add_argument("--macro", action="store_true", help="只看宏观环境")
    parser.add_argument("--intraday", action="store_true", help="盘中实时监控（异动推钉钉）")
    parser.add_argument("--quiet", action="store_true", help="静默模式")

    args = parser.parse_args()
    verbose = not args.quiet

    if args.update_history:
        run_update_history(verbose=verbose)
    elif args.status:
        run_status(verbose=verbose)
    elif args.macro:
        run_macro(verbose=verbose, dingtalk=args.dingtalk)
    elif args.intraday:
        run_intraday(dingtalk=args.dingtalk, verbose=verbose)
    elif args.backtest:
        run_backtest(args.backtest[0].upper(), int(args.backtest[1]), verbose=verbose)
    else:
        run_scan(dingtalk=args.dingtalk, ticker_filter=args.ticker, verbose=verbose)

if __name__ == "__main__":
    main()
