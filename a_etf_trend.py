#!/usr/bin/env python3
"""
A股ETF右侧趋势交易系统

只做右侧: 趋势确认入场, 自适应ATR追踪止损出场
核心: trailing_stop = highest_close - K(gain,accel) × ATR(20)
     涨得越多/越急, K越小, 止损越紧, 保护利润

用法:
  python3 a_etf_trend.py scan                # 扫描入场信号
  python3 a_etf_trend.py signal sz159516      # 单标的信号详情
  python3 a_etf_trend.py check               # 检查持仓止损线
  python3 a_etf_trend.py positions            # 查看持仓
  python3 a_etf_trend.py daily --dingtalk     # 每日例行+推送
  python3 a_etf_trend.py backtest sz159516    # 回测
  python3 a_etf_trend.py backtest-all         # 批量回测
  python3 a_etf_trend.py shares sz159516      # 查看ETF份额变化
  python3 a_etf_trend.py shares-all           # 全部ETF份额变化排行
"""

import json
import os
import sys
import math
import re
import sqlite3
import time
import urllib.request
import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from chokepoint_trader import (
    init_db, get_bars,
    sma, ema, calc_rsi, calc_macd, calc_bollinger,
    calc_volume_ratio, calc_atr, calc_kdj,
)
from a_trend_trader import fetch_tencent_cn_kline, update_cn_ticker
from a_stock_monitor import send_dingtalk

# ============================================================
# ETF监测篮子 — 行业 + 细分行业 + 策略指数
# ============================================================

ETF_BASKET = {
    # ---- 行业ETF (20) ----
    "通信":       {"symbol": "sh515880", "cat": "行业"},
    "半导体设备":  {"symbol": "sz159516", "cat": "行业", "holding": True},
    "芯片":       {"symbol": "sz159801", "cat": "行业"},
    "电力":       {"symbol": "sz159611", "cat": "行业", "holding": True},
    "电网设备":    {"symbol": "sz159326", "cat": "行业"},
    "军工":       {"symbol": "sh512660", "cat": "行业"},
    "机器人":      {"symbol": "sh562500", "cat": "行业"},
    "消费电子":    {"symbol": "sz159732", "cat": "行业"},
    "煤炭":       {"symbol": "sh515220", "cat": "行业"},
    "银行":       {"symbol": "sh512800", "cat": "行业"},
    "创新药":      {"symbol": "sz159992", "cat": "行业"},
    "光伏":       {"symbol": "sh515790", "cat": "行业"},
    "新能车":      {"symbol": "sh515030", "cat": "行业"},
    "白酒":       {"symbol": "sz161725", "cat": "行业"},
    "红利":       {"symbol": "sh515180", "cat": "行业", "holding": True},
    "医药":       {"symbol": "sh512010", "cat": "行业"},
    "房地产":      {"symbol": "sh512200", "cat": "行业"},
    "有色金属":    {"symbol": "sh512400", "cat": "行业"},
    "软件":       {"symbol": "sz159852", "cat": "行业"},
    "卫星":       {"symbol": "sz159206", "cat": "行业", "holding": True},

    # ---- 细分行业ETF (12) ----
    "AI算力":      {"symbol": "sz159819", "cat": "细分"},
    "云计算":      {"symbol": "sz159890", "cat": "细分"},
    "游戏":       {"symbol": "sz159869", "cat": "细分"},
    "家电":       {"symbol": "sz159996", "cat": "细分"},
    "稀土":       {"symbol": "sz159713", "cat": "细分"},
    "储能":       {"symbol": "sh516760", "cat": "细分"},
    "证券":       {"symbol": "sh512880", "cat": "细分"},
    "养殖":       {"symbol": "sz159865", "cat": "细分"},
    "钢铁":       {"symbol": "sh515210", "cat": "细分"},
    "化工":       {"symbol": "sz159870", "cat": "细分"},
    "中药":       {"symbol": "sz159647", "cat": "细分"},
    "旅游":       {"symbol": "sz159766", "cat": "细分"},

    # ---- 策略指数ETF (4) ----
    "红利低波":    {"symbol": "sh512890", "cat": "策略"},
    "央企创新":    {"symbol": "sh515900", "cat": "策略"},
    "恒生科技":    {"symbol": "sh513180", "cat": "策略"},
    "科创芯片":    {"symbol": "sh588200", "cat": "策略"},
}

BENCHMARK = "sh000001"
POSITIONS_FILE = os.path.join(SCRIPT_DIR, "state", "etf_positions.json")
CALIBRATION_FILE = os.path.join(SCRIPT_DIR, "state", "etf_calibration.json")
SETTINGS_FILE = os.path.join(SCRIPT_DIR, "state", "etf_settings.json")
STRATEGY_FILE = os.path.join(SCRIPT_DIR, "state", "etf_strategy.json")
OPTIMIZE_FILE = os.path.join(SCRIPT_DIR, "state", "etf_optimize.json")
HARD_STOP_PCT = 0.08
BASE_K = 3.0
MIN_K = 1.2
CAPITAL_PER_TRADE = 100000

DEFAULT_STRATEGY = {
    "monthly": {
        "enabled": True,
        "ma_period": 20,
        "require_ma_rising": True,
    },
    "weekly": {
        "enabled": True,
        "ma_period": 10,
        "require_ma_rising": True,
        "require_macd_positive": True,
        "min_conditions": 2,
    },
    "entry": {
        "ma_period": 20,
        "ma_slope_lookback": 5,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "vol_short": 5,
        "vol_long": 20,
        "vol_ratio_threshold": 1.2,
        "conditions_enabled": [True, True, True, True],
        "use_kdj": False,
        "kdj_period": 9,
        "kdj_k_smooth": 3,
        "kdj_d_smooth": 3,
        "kdj_oversold": 20,
    },
    "exit": {
        "base_k": 3.0,
        "min_k": 1.2,
        "hard_stop_pct": 0.08,
        "atr_period": 20,
        "ma_exit_period": 20,
        "ma_exit_days": 3,
    },
    "bonus": {
        "new_high_period": 20,
        "ma_long_period": 60,
        "rsi_period": 14,
        "rsi_threshold": 50,
    },
}

def _deep_merge(base, override):
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result

def load_strategy_config():
    try:
        with open(STRATEGY_FILE, "r") as f:
            saved = json.load(f)
        return _deep_merge(DEFAULT_STRATEGY, saved)
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(DEFAULT_STRATEGY)

def save_strategy_config(config):
    os.makedirs(os.path.dirname(STRATEGY_FILE), exist_ok=True)
    with open(STRATEGY_FILE, "w") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def get_etf_config(symbol, global_config=None):
    if global_config is None:
        global_config = load_strategy_config()
    per_etf = global_config.get("per_etf", {})
    override = per_etf.get(symbol, {})
    if not override:
        return global_config
    return _deep_merge(global_config, override)


# ============================================================
# 趋势适配度校准
# ============================================================

GRADE_THRESHOLDS = {
    "S": 100,
    "A": 50,
    "B": 20,
    "C": 0,
}

def load_calibration():
    try:
        with open(CALIBRATION_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_calibration(data):
    os.makedirs(os.path.dirname(CALIBRATION_FILE), exist_ok=True)
    with open(CALIBRATION_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def calc_grade(total_return):
    if total_return >= 100:
        return "S"
    elif total_return >= 50:
        return "A"
    elif total_return >= 20:
        return "B"
    elif total_return >= 0:
        return "C"
    else:
        return "D"


def is_trend_suitable(symbol):
    cal = load_calibration()
    entry = cal.get(symbol)
    if not entry:
        return True, "未校准", "-"
    grade = entry.get("grade", "-")
    ret = entry.get("total_return", 0)
    return grade in ("S", "A", "B"), grade, ret

# ============================================================
# 系统设置
# ============================================================

def load_etf_settings():
    try:
        with open(SETTINGS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"tushare_token": "", "data_source": "tencent", "fetch_schedule": "16:00"}


def save_etf_settings(settings):
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    allowed = {"tushare_token", "data_source", "fetch_schedule"}
    clean = {k: v for k, v in settings.items() if k in allowed}
    with open(SETTINGS_FILE, "w") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)


# ============================================================
# 自适应ATR追踪止损
# ============================================================

def adaptive_K(gain_pct, chg_5d, base_k=BASE_K, min_k=MIN_K):
    gain_adj = min(gain_pct / 10, 6) * 0.3
    accel_adj = 0.3 if chg_5d > 8 else 0
    return max(min_k, base_k - gain_adj - accel_adj)


def calc_trailing_stop(entry_price, highest_close, atr_val, chg_5d,
                       base_k=BASE_K, min_k=MIN_K, hard_stop_pct=HARD_STOP_PCT):
    if atr_val is None or atr_val <= 0:
        return highest_close * 0.95, base_k
    gain_pct = max(0, (highest_close - entry_price) / entry_price * 100)
    k = adaptive_K(gain_pct, chg_5d, base_k, min_k)
    stop = highest_close - k * atr_val
    hard_stop = entry_price * (1 - hard_stop_pct)
    return max(stop, hard_stop), k


# ============================================================
# 多周期K线聚合
# ============================================================

def aggregate_to_weekly(bars):
    """日线聚合为周线，按自然周分组"""
    if not bars:
        return []
    from datetime import datetime
    weekly = {}
    for b in bars:
        dt = datetime.strptime(b["date"], "%Y-%m-%d")
        key = dt.isocalendar()[:2]  # (year, week)
        if key not in weekly:
            weekly[key] = {"date": b["date"], "open": b["open"],
                           "high": b["high"], "low": b["low"],
                           "close": b["close"], "volume": b["volume"]}
        else:
            w = weekly[key]
            w["high"] = max(w["high"], b["high"])
            w["low"] = min(w["low"], b["low"])
            w["close"] = b["close"]
            w["date"] = b["date"]
            w["volume"] = w["volume"] + b["volume"]
    return [weekly[k] for k in sorted(weekly.keys())]


def aggregate_to_monthly(bars):
    """日线聚合为月线，按自然月分组"""
    if not bars:
        return []
    monthly = {}
    for b in bars:
        key = b["date"][:7]  # "YYYY-MM"
        if key not in monthly:
            monthly[key] = {"date": b["date"], "open": b["open"],
                            "high": b["high"], "low": b["low"],
                            "close": b["close"], "volume": b["volume"]}
        else:
            m = monthly[key]
            m["high"] = max(m["high"], b["high"])
            m["low"] = min(m["low"], b["low"])
            m["close"] = b["close"]
            m["date"] = b["date"]
            m["volume"] = m["volume"] + b["volume"]
    return [monthly[k] for k in sorted(monthly.keys())]


def check_monthly_filter(bars, config=None):
    """月线多头过滤：价格>月MA + 月MA上行，返回(pass, details)"""
    if config is None:
        config = DEFAULT_STRATEGY
    mc = config.get("monthly", DEFAULT_STRATEGY.get("monthly", {}))
    if not mc.get("enabled", True):
        return True, ["月线过滤:关闭"]

    monthly_bars = aggregate_to_monthly(bars)
    if len(monthly_bars) < 3:
        return True, ["月线数据不足"]

    closes = [b["close"] for b in monthly_bars]
    ma_p = mc.get("ma_period", 20)
    ma = sma(closes, ma_p)
    i = len(closes) - 1

    if ma[i] is None:
        return True, ["月MA数据不足"]

    cond_above = closes[i] > ma[i]
    pct = (closes[i] - ma[i]) / ma[i] * 100

    cond_rising = True
    if mc.get("require_ma_rising", True) and i >= 1 and ma[i - 1] is not None:
        cond_rising = ma[i] > ma[i - 1]

    passed = cond_above and cond_rising
    details = []
    if passed:
        details.append(f"月线多头:MA{ma_p}({pct:+.1f}%)")
    else:
        reasons = []
        if not cond_above:
            reasons.append(f"价格低于月MA{ma_p}({pct:+.1f}%)")
        if not cond_rising:
            reasons.append(f"月MA{ma_p}下行")
        details.append("月线空头:" + ",".join(reasons))
    return passed, details


def check_weekly_filter(bars, config=None):
    """周线趋势过滤：价格>周MA + 周MA上行 + 周MACD>0，返回(pass, details)"""
    if config is None:
        config = DEFAULT_STRATEGY
    wc = config.get("weekly", DEFAULT_STRATEGY.get("weekly", {}))
    if not wc.get("enabled", True):
        return True, ["周线过滤:关闭"]

    weekly_bars = aggregate_to_weekly(bars)
    if len(weekly_bars) < 5:
        return True, ["周线数据不足"]

    closes = [b["close"] for b in weekly_bars]
    ma_p = wc.get("ma_period", 10)
    ma = sma(closes, ma_p)
    macd_line, _, macd_hist = calc_macd(closes)
    i = len(closes) - 1

    if ma[i] is None:
        return True, ["周MA数据不足"]

    conds_met = 0
    cond_details = []
    min_conds = wc.get("min_conditions", 2)

    # 条件1: 价格 > 周MA
    if closes[i] > ma[i]:
        conds_met += 1
        pct = (closes[i] - ma[i]) / ma[i] * 100
        cond_details.append(f"价格>周MA{ma_p}({pct:+.1f}%)")
    else:
        pct = (closes[i] - ma[i]) / ma[i] * 100
        cond_details.append(f"价格<周MA{ma_p}({pct:+.1f}%)")

    # 条件2: 周MA上行
    if wc.get("require_ma_rising", True):
        if i >= 1 and ma[i - 1] is not None and ma[i] > ma[i - 1]:
            conds_met += 1
            cond_details.append(f"周MA{ma_p}上行")
        else:
            cond_details.append(f"周MA{ma_p}走平/下行")

    # 条件3: 周MACD > 0
    if wc.get("require_macd_positive", True):
        if macd_hist[i] is not None and macd_hist[i] > 0:
            conds_met += 1
            cond_details.append(f"周MACD>0({macd_hist[i]:.4f})")
        else:
            cond_details.append("周MACD≤0")

    passed = conds_met >= min_conds
    tag = "周线确认" if passed else "周线不足"
    return passed, [f"{tag}({conds_met}/{min_conds}):" + ",".join(cond_details)]


# ============================================================
# 右侧入场信号
# ============================================================

def check_entry(bars, config=None):
    n = len(bars)
    if n < 60:
        return False, [], {}

    if config is None:
        config = DEFAULT_STRATEGY
    ec = config.get("entry", DEFAULT_STRATEGY["entry"])
    bc = config.get("bonus", DEFAULT_STRATEGY["bonus"])

    closes = [b["close"] for b in bars]
    volumes = [b["volume"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    i = n - 1
    price = closes[i]

    # 多周期过滤
    monthly_pass, monthly_details = check_monthly_filter(bars, config)
    weekly_pass, weekly_details = check_weekly_filter(bars, config)

    ma_p = ec.get("ma_period", 20)
    ma_short = sma(closes, ma_p)
    ma_long_p = bc.get("ma_long_period", 60)
    ma_long = sma(closes, ma_long_p) if n >= ma_long_p else [None] * n
    macd_line, macd_signal, macd_hist = calc_macd(
        closes, ec.get("macd_fast", 12), ec.get("macd_slow", 26), ec.get("macd_signal", 9))
    vol_long_p = ec.get("vol_long", 20)
    vol_ratio = calc_volume_ratio(volumes, vol_long_p)
    rsi_p = bc.get("rsi_period", 14)
    rsi = calc_rsi(closes, rsi_p)
    atr = calc_atr(highs, lows, closes, 20)

    conditions = []
    details = []
    extras = {}
    enabled = ec.get("conditions_enabled", [True, True, True, True])

    cond1 = ma_short[i] is not None and price > ma_short[i]
    conditions.append(cond1 if enabled[0] else True)
    if cond1:
        pct = (price - ma_short[i]) / ma_short[i] * 100
        details.append(f"价格>MA{ma_p}({pct:+.1f}%)")

    slope_lb = ec.get("ma_slope_lookback", 5)
    cond2 = False
    if ma_short[i] is not None and i >= ma_p + slope_lb - 1:
        ma_past = sma(closes[:i - slope_lb + 1], ma_p)
        if ma_past and ma_past[-1] is not None:
            cond2 = ma_short[i] > ma_past[-1]
    conditions.append(cond2 if enabled[1] else True)
    if cond2:
        details.append(f"MA{ma_p}上行")

    cond3 = macd_hist[i] is not None and macd_hist[i] > 0
    conditions.append(cond3 if enabled[2] else True)
    if cond3:
        details.append(f"MACD柱>0({macd_hist[i]:.4f})")

    vol_short_p = ec.get("vol_short", 5)
    vol_thresh = ec.get("vol_ratio_threshold", 1.2)
    cond4 = False
    if i >= vol_long_p - 1:
        vol_s = sum(volumes[i - vol_short_p + 1:i + 1]) / vol_short_p
        vol_l = sum(volumes[max(0, i - vol_long_p + 1):i + 1]) / min(vol_long_p, i + 1)
        if vol_l > 0 and vol_s > vol_l:
            cond4 = True
            details.append(f"量能放大({vol_s / vol_l:.1f}x)")
        elif vol_ratio[i] is not None and vol_ratio[i] > vol_thresh:
            chg_today = (closes[i] - closes[i - 1]) / closes[i - 1] * 100
            if chg_today > 0:
                cond4 = True
                details.append(f"放量上涨(量比{vol_ratio[i]:.1f})")
    conditions.append(cond4 if enabled[3] else True)

    if ec.get("use_kdj"):
        k_line, d_line, j_line = calc_kdj(
            highs, lows, closes,
            ec.get("kdj_period", 9), ec.get("kdj_k_smooth", 3), ec.get("kdj_d_smooth", 3))
        kdj_oversold = ec.get("kdj_oversold", 20)
        cond_kdj = j_line[i] is not None and j_line[i] < kdj_oversold
        conditions.append(cond_kdj)
        if cond_kdj:
            details.append(f"KDJ超卖(J={j_line[i]:.1f})")

    bonus = []
    nhp = bc.get("new_high_period", 20)
    if i >= nhp and price >= max(closes[i - nhp + 1:i + 1]):
        bonus.append(f"{nhp}日新高")
    if ma_long[i] is not None and price > ma_long[i]:
        bonus.append(f"站上MA{ma_long_p}")
    rsi_thresh = bc.get("rsi_threshold", 50)
    if rsi[i] is not None and rsi[i] > rsi_thresh:
        bonus.append(f"RSI={rsi[i]:.0f}")
    if macd_line[i] is not None and macd_signal[i] is not None:
        prev = i - 1
        if (macd_line[prev] is not None and macd_signal[prev] is not None and
                macd_line[i] > macd_signal[i] and macd_line[prev] <= macd_signal[prev]):
            bonus.append("MACD金叉")

    if bonus:
        details.append("加分:" + "/".join(bonus))

    extras["price"] = price
    extras["ma20"] = ma_short[i]
    extras["ma60"] = ma_long[i]
    extras["atr"] = atr[i]
    extras["rsi"] = rsi[i]
    extras["macd_hist"] = macd_hist[i]
    extras["vol_ratio"] = vol_ratio[i]
    chg_5d = (price - closes[i - 5]) / closes[i - 5] * 100 if i >= 5 else 0
    extras["chg_5d"] = chg_5d
    extras["chg_1d"] = (price - closes[i - 1]) / closes[i - 1] * 100 if i >= 1 else 0
    extras["chg_20d"] = (price - closes[i - 20]) / closes[i - 20] * 100 if i >= 20 else 0
    extras["monthly_pass"] = monthly_pass
    extras["weekly_pass"] = weekly_pass
    extras["monthly_detail"] = monthly_details[0] if monthly_details else ""
    extras["weekly_detail"] = weekly_details[0] if weekly_details else ""

    daily_pass = all(conditions)
    entry = daily_pass and monthly_pass and weekly_pass
    if monthly_details:
        details = monthly_details + weekly_details + details
    return entry, details, extras


# ============================================================
# 信号分类引擎 — 月线选菜单, 日线自助餐
# ============================================================

SIGNAL_BREAKOUT = "breakout"
SIGNAL_PULLBACK = "pullback"
SIGNAL_OVERBOUGHT = "overbought"
SIGNAL_STRONG = "strong"
SIGNAL_WATCH = "watch"

SIGNAL_LABELS = {
    SIGNAL_BREAKOUT: "突破信号",
    SIGNAL_PULLBACK: "回踩机会",
    SIGNAL_OVERBOUGHT: "超买提醒",
    SIGNAL_STRONG: "强势持仓",
    SIGNAL_WATCH: "观望",
}

SIGNAL_HINTS = {
    SIGNAL_BREAKOUT: "突破建仓 30-50%",
    SIGNAL_PULLBACK: "回踩加仓 50-70%",
    SIGNAL_OVERBOUGHT: "短期超买，谨慎追高",
    SIGNAL_STRONG: "已持仓持有，未持仓轻仓",
    SIGNAL_WATCH: "观望等待",
}

SIGNAL_COLORS = {
    SIGNAL_BREAKOUT: "#e74c3c",
    SIGNAL_PULLBACK: "#27ae60",
    SIGNAL_OVERBOUGHT: "#e67e22",
    SIGNAL_STRONG: "#3498db",
    SIGNAL_WATCH: "#95a5a6",
}


def classify_signal(bars, config=None):
    n = len(bars)
    empty = {
        "signal_type": SIGNAL_WATCH, "label": SIGNAL_LABELS[SIGNAL_WATCH],
        "position_hint": SIGNAL_HINTS[SIGNAL_WATCH], "color": SIGNAL_COLORS[SIGNAL_WATCH],
        "monthly_pass": False, "weekly_pass": False,
        "monthly_detail": "数据不足", "weekly_detail": "",
        "daily_conditions": [False] * 4, "indicators": {}, "details": ["数据不足"],
    }
    if n < 60:
        return empty

    if config is None:
        config = DEFAULT_STRATEGY
    ec = config.get("entry", DEFAULT_STRATEGY["entry"])
    bc = config.get("bonus", DEFAULT_STRATEGY["bonus"])

    closes = [b["close"] for b in bars]
    volumes = [b["volume"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    i = n - 1
    price = closes[i]

    monthly_pass, monthly_details = check_monthly_filter(bars, config)
    weekly_pass, weekly_details = check_weekly_filter(bars, config)

    ma_p = ec.get("ma_period", 20)
    ma_short = sma(closes, ma_p)
    ma_long_p = bc.get("ma_long_period", 60)
    ma_long = sma(closes, ma_long_p) if n >= ma_long_p else [None] * n
    macd_line, macd_signal_line, macd_hist = calc_macd(
        closes, ec.get("macd_fast", 12), ec.get("macd_slow", 26), ec.get("macd_signal", 9))
    rsi_val = calc_rsi(closes, bc.get("rsi_period", 14))
    atr = calc_atr(highs, lows, closes, 20)
    vol_ratio = calc_volume_ratio(volumes, ec.get("vol_long", 20))

    cond1 = ma_short[i] is not None and price > ma_short[i]
    slope_lb = ec.get("ma_slope_lookback", 5)
    cond2 = False
    if ma_short[i] is not None and i >= ma_p + slope_lb - 1:
        ma_past = sma(closes[:i - slope_lb + 1], ma_p)
        if ma_past and ma_past[-1] is not None:
            cond2 = ma_short[i] > ma_past[-1]
    cond3 = macd_hist[i] is not None and macd_hist[i] > 0
    vol_short_p = ec.get("vol_short", 5)
    vol_long_p = ec.get("vol_long", 20)
    cond4 = False
    if i >= vol_long_p - 1:
        vol_s = sum(volumes[i - vol_short_p + 1:i + 1]) / vol_short_p
        vol_l = sum(volumes[max(0, i - vol_long_p + 1):i + 1]) / min(vol_long_p, i + 1)
        if vol_l > 0 and vol_s > vol_l:
            cond4 = True

    daily_conditions = [cond1, cond2, cond3, cond4]

    ma20_dist = 0
    if ma_short[i] is not None and ma_short[i] > 0:
        ma20_dist = (price - ma_short[i]) / ma_short[i] * 100
    rsi_v = rsi_val[i] if rsi_val[i] is not None else 50

    # 近20日最高价回撤
    recent_high = max(closes[max(0, i - 19):i + 1])
    pullback_pct = (price - recent_high) / recent_high * 100 if recent_high > 0 else 0

    indicators = {
        "price": price,
        "ma20": ma_short[i], "ma60": ma_long[i],
        "ma20_dist": round(ma20_dist, 2),
        "rsi": round(rsi_v, 1),
        "macd_hist": macd_hist[i],
        "atr": atr[i],
        "vol_ratio": vol_ratio[i],
        "pullback_pct": round(pullback_pct, 1),
        "chg_1d": round((price - closes[i - 1]) / closes[i - 1] * 100, 2) if i >= 1 else 0,
        "chg_5d": round((price - closes[i - 5]) / closes[i - 5] * 100, 2) if i >= 5 else 0,
        "chg_20d": round((price - closes[i - 20]) / closes[i - 20] * 100, 2) if i >= 20 else 0,
    }

    details = []
    if not monthly_pass:
        sig = SIGNAL_WATCH
        details = monthly_details + ["月线不符，观望"]
    elif not weekly_pass:
        sig = SIGNAL_WATCH
        details = monthly_details + weekly_details + ["周线不足，观望"]
    else:
        details = monthly_details + weekly_details
        if cond1 and cond3 and cond4:
            if ma20_dist > 8 and rsi_v > 70:
                sig = SIGNAL_OVERBOUGHT
                details.append(f"MA偏离{ma20_dist:+.1f}%, RSI={rsi_v:.0f}, 短期超买")
            else:
                sig = SIGNAL_BREAKOUT
                details.append(f"突破确认: 价格>MA{ma_p}, MACD>0, 量能放大")
        elif abs(ma20_dist) <= 3 or (ma20_dist < 0 and rsi_v < 35):
            sig = SIGNAL_PULLBACK
            if ma20_dist < 0 and rsi_v < 35:
                details.append(f"超卖回踩: MA距{ma20_dist:+.1f}%, RSI={rsi_v:.0f}")
            else:
                details.append(f"回踩MA支撑: MA距{ma20_dist:+.1f}%")
        elif cond1:
            sig = SIGNAL_STRONG
            details.append(f"站上MA{ma_p}({ma20_dist:+.1f}%), 部分动量条件未满足")
        else:
            sig = SIGNAL_WATCH
            details.append(f"价格低于MA{ma_p}({ma20_dist:+.1f}%), 观望")

    return {
        "signal_type": sig,
        "label": SIGNAL_LABELS[sig],
        "position_hint": SIGNAL_HINTS[sig],
        "color": SIGNAL_COLORS[sig],
        "monthly_pass": monthly_pass,
        "weekly_pass": weekly_pass,
        "monthly_detail": monthly_details[0] if monthly_details else "",
        "weekly_detail": weekly_details[0] if weekly_details else "",
        "daily_conditions": daily_conditions,
        "indicators": indicators,
        "details": details,
    }


def check_exit(bars, entry_price, highest_close,
               base_k=BASE_K, min_k=MIN_K, hard_stop_pct=HARD_STOP_PCT,
               config=None):
    n = len(bars)
    if n < 20:
        return False, "", 0, base_k

    if config is not None:
        exc = config.get("exit", {})
        base_k = exc.get("base_k", base_k)
        min_k = exc.get("min_k", min_k)
        hard_stop_pct = exc.get("hard_stop_pct", hard_stop_pct)
        atr_p = exc.get("atr_period", 20)
        ma_exit_p = exc.get("ma_exit_period", 20)
        ma_exit_d = exc.get("ma_exit_days", 3)
    else:
        atr_p = 20
        ma_exit_p = 20
        ma_exit_d = 3

    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    i = n - 1
    price = closes[i]

    atr = calc_atr(highs, lows, closes, atr_p)
    ma_exit = sma(closes, ma_exit_p)

    chg_5d = (price - closes[i - 5]) / closes[i - 5] * 100 if i >= 5 else 0
    new_highest = max(highest_close, price)
    stop, k = calc_trailing_stop(entry_price, new_highest, atr[i], chg_5d,
                                 base_k, min_k, hard_stop_pct)

    if price < stop:
        gain = (price - entry_price) / entry_price * 100
        return True, f"追踪止损(K={k:.1f},stop={stop:.3f},gain={gain:+.1f}%)", stop, k

    hard = entry_price * (1 - hard_stop_pct)
    if price < hard:
        return True, f"硬止损({hard_stop_pct * 100:.0f}%,stop={hard:.3f})", stop, k

    if ma_exit[i] is not None and n >= ma_exit_d:
        below_count = sum(1 for j in range(max(0, i - ma_exit_d + 1), i + 1)
                         if closes[j] < ma_exit[j] and ma_exit[j] is not None)
        if below_count >= ma_exit_d:
            return True, f"连续{ma_exit_d}日破MA{ma_exit_p}({ma_exit[i]:.3f})", stop, k

    return False, "", stop, k


# ============================================================
# 持仓管理
# ============================================================

def load_positions():
    try:
        with open(POSITIONS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"positions": {}, "trade_history": []}


def save_positions(data):
    os.makedirs(os.path.dirname(POSITIONS_FILE), exist_ok=True)
    with open(POSITIONS_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============================================================
# 回测引擎
# ============================================================

class RightSideBacktest:
    def __init__(self, bars, benchmark_bars=None, capital=CAPITAL_PER_TRADE,
                 base_k=BASE_K, min_k=MIN_K, hard_stop_pct=HARD_STOP_PCT,
                 config=None):
        self.bars = bars
        self.benchmark_bars = benchmark_bars
        self.init_capital = capital
        self.capital = capital
        self.position = 0
        self.entry_price = 0
        self.entry_date = ""
        self.highest_close = 0
        self.trades = []
        self.equity_curve = []
        self.peak = capital
        self.max_dd = 0
        self.dd_peak_date = ""
        self.dd_trough_date = ""
        self.config = config
        if config is not None:
            exc = config.get("exit", {})
            self.base_k = exc.get("base_k", base_k)
            self.min_k = exc.get("min_k", min_k)
            self.hard_stop_pct = exc.get("hard_stop_pct", hard_stop_pct)
        else:
            self.base_k = base_k
            self.min_k = min_k
            self.hard_stop_pct = hard_stop_pct

    def run(self):
        warmup = 60
        for i in range(warmup, len(self.bars)):
            bar = self.bars[i]
            price = bar["close"]
            window = self.bars[max(0, i - 249):i + 1]

            if self.position > 0:
                self.highest_close = max(self.highest_close, price)
                should_exit, reason, stop, k = check_exit(
                    window, self.entry_price, self.highest_close,
                    self.base_k, self.min_k, self.hard_stop_pct,
                    config=self.config
                )
                if should_exit:
                    pnl_pct = (price - self.entry_price) / self.entry_price * 100
                    hold = self._days(self.entry_date, bar["date"])
                    self.trades.append({
                        "entry_date": self.entry_date,
                        "exit_date": bar["date"],
                        "entry_price": self.entry_price,
                        "exit_price": price,
                        "pnl_pct": pnl_pct,
                        "pnl": self.position * (price - self.entry_price),
                        "hold_days": hold,
                        "exit_reason": reason,
                        "exit_K": k,
                    })
                    self.capital += self.position * price
                    self.position = 0
                    self.entry_price = 0
                    self.highest_close = 0

            if self.position == 0:
                entry, details, extras = check_entry(window, config=self.config)
                if entry:
                    shares = int(self.capital / price)
                    if shares > 0:
                        self.capital -= shares * price
                        self.position = shares
                        self.entry_price = price
                        self.entry_date = bar["date"]
                        self.highest_close = price

            equity = self.capital + self.position * price
            self.equity_curve.append({"date": bar["date"], "equity": equity, "price": price})
            if equity > self.peak:
                self.peak = equity
                self.dd_peak_date = bar["date"]
            dd = (self.peak - equity) / self.peak
            if dd > self.max_dd:
                self.max_dd = dd
                self.dd_trough_date = bar["date"]

        if self.position > 0:
            last = self.bars[-1]
            pnl_pct = (last["close"] - self.entry_price) / self.entry_price * 100
            self.trades.append({
                "entry_date": self.entry_date,
                "exit_date": last["date"] + "(持仓中)",
                "entry_price": self.entry_price,
                "exit_price": last["close"],
                "pnl_pct": pnl_pct,
                "pnl": self.position * (last["close"] - self.entry_price),
                "hold_days": self._days(self.entry_date, last["date"]),
                "exit_reason": "持仓中",
                "exit_K": 0,
            })

    def _days(self, d1, d2):
        try:
            return (datetime.date.fromisoformat(d2[:10]) - datetime.date.fromisoformat(d1[:10])).days
        except (ValueError, TypeError):
            return 0

    def report(self, label=""):
        if not self.equity_curve:
            print("  无数据")
            return {}

        final = self.equity_curve[-1]["equity"]
        total_ret = (final - self.init_capital) / self.init_capital * 100
        days = len(self.equity_curve)
        years = days / 244
        ann_ret = ((final / self.init_capital) ** (1 / years) - 1) * 100 if years > 0.1 else total_ret

        bench_ret = None
        if self.benchmark_bars and len(self.benchmark_bars) >= 2:
            b0 = self.benchmark_bars[0]["close"]
            b1 = self.benchmark_bars[-1]["close"]
            if b0 > 0:
                bench_ret = (b1 - b0) / b0 * 100

        dr = []
        for j in range(1, len(self.equity_curve)):
            p = self.equity_curve[j - 1]["equity"]
            if p > 0:
                dr.append((self.equity_curve[j]["equity"] - p) / p)
        avg = sum(dr) / len(dr) if dr else 0
        std = math.sqrt(sum((r - avg) ** 2 for r in dr) / len(dr)) if len(dr) > 1 else 1
        sharpe = (avg / std) * math.sqrt(244) if std > 0 else 0

        wins = [t for t in self.trades if t["pnl"] > 0]
        losses = [t for t in self.trades if t["pnl"] <= 0]
        wr = len(wins) / len(self.trades) * 100 if self.trades else 0
        avg_w = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
        avg_l = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
        pf = (sum(t["pnl"] for t in wins) / abs(sum(t["pnl"] for t in losses))) if losses and sum(t["pnl"] for t in losses) != 0 else float("inf")
        avg_hold = sum(t["hold_days"] for t in self.trades) / len(self.trades) if self.trades else 0

        header = f"回测: {label}" if label else "回测报告"
        start = self.equity_curve[0]["date"]
        end = self.equity_curve[-1]["date"]

        print(f"\n{'=' * 60}")
        print(f"{header} | {start} ~ {end} ({days}交易日)")
        print(f"{'=' * 60}")
        print(f"  总收益:   {total_ret:+.1f}%")
        print(f"  年化:     {ann_ret:+.1f}%")
        if bench_ret is not None:
            print(f"  基准:     {bench_ret:+.1f}% (上证)")
            print(f"  超额:     {total_ret - bench_ret:+.1f}%")
        print(f"  最大回撤: {self.max_dd * 100:.1f}%")
        print(f"  Sharpe:   {sharpe:.2f}")
        print(f"  交易:     {len(self.trades)}笔 | 胜率{wr:.0f}%({len(wins)}胜/{len(losses)}负)")
        print(f"  平均盈:   {avg_w:+.1f}% | 平均亏: {avg_l:+.1f}%")
        if avg_l != 0:
            print(f"  盈亏比:   {abs(avg_w / avg_l):.2f} | PF: {pf:.2f}")
        print(f"  持仓:     均{avg_hold:.0f}天")
        print(f"  净值:     {final:.0f} (初始{self.init_capital})")

        if self.trades:
            print(f"\n  {'入场':>10s}  {'出场':>10s}  {'入价':>7s}  {'出价':>7s} {'收益':>7s} {'天':>3s}  {'K':>3s}  原因")
            print(f"  {'-' * 72}")
            for t in self.trades:
                k_str = f"{t['exit_K']:.1f}" if t["exit_K"] > 0 else " - "
                print(f"  {t['entry_date']:>10s}  {t['exit_date'][:10]:>10s}  "
                      f"{t['entry_price']:7.3f}  {t['exit_price']:7.3f} "
                      f"{t['pnl_pct']:+6.1f}% {t['hold_days']:3d}  {k_str:>3s}  {t['exit_reason']}")

        return {
            "total_return": total_ret, "annual_return": ann_ret,
            "max_drawdown": self.max_dd * 100, "sharpe": sharpe,
            "trades": len(self.trades), "win_rate": wr,
            "profit_factor": pf, "avg_hold": avg_hold,
            "bench_return": bench_ret,
        }


# ============================================================
# CLI: signal
# ============================================================

def _resolve_name(symbol):
    for n, cfg in ETF_BASKET.items():
        if cfg["symbol"] == symbol:
            return n
    return ""


def cmd_signal(symbol):
    conn = init_db()
    _s = load_etf_settings()
    _ds, _tk = _s.get("data_source", "tencent"), _s.get("tushare_token", "")
    update_cn_ticker(conn, symbol, verbose=False, data_source=_ds, tushare_token=_tk)
    update_cn_ticker(conn, BENCHMARK, verbose=False, data_source=_ds, tushare_token=_tk)
    bars = get_bars(conn, symbol, 750)
    conn.close()

    if len(bars) < 60:
        print(f"{symbol}: 数据不足({len(bars)}条)")
        return

    entry, details, ex = check_entry(bars)
    name = _resolve_name(symbol)
    label = f"{name}({symbol})" if name else symbol

    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    atr = calc_atr(highs, lows, closes, 20)
    i = len(bars) - 1

    hypo_stop, hypo_k = calc_trailing_stop(
        ex["price"], ex["price"], atr[i], ex["chg_5d"]
    )

    print(f"\n{'=' * 55}")
    print(f"  {label}  {ex['price']:.3f}  ({bars[-1]['date']})")
    print(f"{'=' * 55}")
    print(f"  入场信号: {'>>> 触发 <<<' if entry else '未触发'}")
    print()
    cond_labels = ["价格>MA20", "MA20上行", "MACD柱>0", "量价确认"]
    entry_check, _, _ = check_entry(bars)
    closes2 = [b["close"] for b in bars]
    ma20v = sma(closes2, 20)
    macd_l, macd_s, macd_h = calc_macd(closes2)
    vr = calc_volume_ratio([b["volume"] for b in bars], 20)

    c1 = ma20v[i] is not None and closes2[i] > ma20v[i]
    c2 = False
    if ma20v[i] is not None and i >= 24:
        old_ma = sma(closes2[:i - 4], 20)
        if old_ma and old_ma[-1] is not None:
            c2 = ma20v[i] > old_ma[-1]
    c3 = macd_h[i] is not None and macd_h[i] > 0
    vol5 = sum(bars[j]["volume"] for j in range(i - 4, i + 1)) / 5 if i >= 4 else 0
    vol20 = sum(bars[j]["volume"] for j in range(max(0, i - 19), i + 1)) / min(20, i + 1)
    c4 = vol5 > vol20 if vol20 > 0 else False
    checks = [c1, c2, c3, c4]
    for j, (cl, cv) in enumerate(zip(cond_labels, checks)):
        mark = "OK" if cv else "--"
        print(f"    [{mark}] {cl}")

    print()
    print(f"  涨幅: 1日{ex['chg_1d']:+.2f}% | 5日{ex['chg_5d']:+.1f}% | 20日{ex['chg_20d']:+.1f}%")
    print(f"  MA20={ex['ma20']:.3f}  MA60={ex['ma60']:.3f}" if ex.get("ma60") else f"  MA20={ex['ma20']:.3f}")
    print(f"  ATR(20)={atr[i]:.4f}  RSI={ex['rsi']:.0f}" if ex.get("rsi") else f"  ATR(20)={atr[i]:.4f}")
    print(f"  量比={ex['vol_ratio']:.1f}" if ex.get("vol_ratio") else "")
    print()
    print(f"  假设入场 {ex['price']:.3f}:")
    print(f"    追踪止损={hypo_stop:.3f} (K={hypo_k:.1f}, 距入场{(hypo_stop / ex['price'] - 1) * 100:+.1f}%)")
    print(f"    硬止损={ex['price'] * (1 - HARD_STOP_PCT):.3f} (最大亏{HARD_STOP_PCT * 100:.0f}%)")
    if details:
        print(f"\n  {' | '.join(details)}")


# ============================================================
# CLI: scan
# ============================================================

def cmd_scan():
    conn = init_db()
    _s = load_etf_settings()
    _ds, _tk = _s.get("data_source", "tencent"), _s.get("tushare_token", "")
    print("更新基准...", end="", flush=True)
    update_cn_ticker(conn, BENCHMARK, verbose=False, data_source=_ds, tushare_token=_tk)
    print(" OK")

    print("获取份额数据...", end="", flush=True)
    share_data = fetch_all_share_changes()
    print(f" OK ({len(share_data)}个)")

    cal = load_calibration()
    results = []
    symbols = [(n, cfg["symbol"], cfg.get("holding", False)) for n, cfg in ETF_BASKET.items()]

    print(f"扫描{len(symbols)}个行业ETF...\n")
    for name, sym, holding in symbols:
        try:
            update_cn_ticker(conn, sym, verbose=False, data_source=_ds, tushare_token=_tk)
        except Exception as e:
            print(f"  {name}: 失败 {e}")
            continue
        bars = get_bars(conn, sym, 750)
        if len(bars) < 60:
            continue
        entry, details, ex = check_entry(bars)
        c = cal.get(sym, {})
        grade = c.get("grade", "-")
        bt_ret = c.get("total_return", None)
        sd = share_data.get(sym, {})
        results.append({
            "name": name, "symbol": sym, "holding": holding,
            "entry": entry, "details": details,
            "price": ex["price"], "chg_5d": ex["chg_5d"],
            "chg_1d": ex["chg_1d"], "rsi": ex.get("rsi"),
            "atr": ex.get("atr"),
            "grade": grade, "bt_ret": bt_ret,
            "share_chg": sd.get("chg_pct", None),
            "share_latest": sd.get("latest", None),
        })

    conn.close()

    good_entries = [r for r in results if r["entry"] and r["grade"] in ("S", "A", "B", "-")]
    bad_entries = [r for r in results if r["entry"] and r["grade"] in ("C", "D")]
    others = [r for r in results if not r["entry"]]
    good_entries.sort(key=lambda x: -x["chg_5d"])
    others.sort(key=lambda x: -x["chg_5d"])

    if good_entries:
        print(f">>> 入场信号 ({len(good_entries)}个) <<<")
        print(f"{'行业':<8s} {'评级':>2s} {'价格':>7s} {'1日':>6s} {'5日':>6s} {'份额%':>6s}  因子")
        print("-" * 78)
        for r in good_entries:
            m = "*" if r["holding"] else " "
            det = " | ".join(r["details"][:3])
            bt = f"(回测+{r['bt_ret']:.0f}%)" if r["bt_ret"] is not None else ""
            sc = f"{r['share_chg']:+.1f}" if r["share_chg"] is not None else "  -"
            print(f"{m}{r['name']:<7s} [{r['grade']}] {r['price']:7.3f} {r['chg_1d']:+5.1f}% {r['chg_5d']:+5.1f}% {sc:>6s}  {det} {bt}")
        print()

    if bad_entries:
        print(f"--- 信号已过滤 ({len(bad_entries)}个, 回测不适合趋势交易) ---")
        for r in bad_entries:
            bt = f"回测{r['bt_ret']:+.0f}%" if r["bt_ret"] is not None else ""
            print(f"  {r['name']:<7s} [{r['grade']}] {r['price']:7.3f} {bt} — 不推荐")
        print()

    print(f"未触发 ({len(others)}个):")
    print(f"{'行业':<8s} {'评级':>2s} {'价格':>7s} {'1日':>6s} {'5日':>6s} {'份额%':>6s}")
    print("-" * 50)
    for r in others:
        m = "*" if r["holding"] else " "
        sc = f"{r['share_chg']:+.1f}" if r["share_chg"] is not None else "  -"
        print(f"{m}{r['name']:<7s} [{r['grade']}] {r['price']:7.3f} {r['chg_1d']:+5.1f}% {r['chg_5d']:+5.1f}% {sc:>6s}")

    # 份额流向摘要
    inflows = sorted([r for r in results if r["share_chg"] is not None and r["share_chg"] > 3],
                     key=lambda x: -x["share_chg"])
    outflows = sorted([r for r in results if r["share_chg"] is not None and r["share_chg"] < -3],
                      key=lambda x: x["share_chg"])
    if inflows or outflows:
        print(f"\n份额流向 (近1月):")
        if inflows:
            parts = ["%s(%+.0f%%)" % (r["name"], r["share_chg"]) for r in inflows[:5]]
            print(f"  流入: {', '.join(parts)}")
        if outflows:
            parts = ["%s(%+.0f%%)" % (r["name"], r["share_chg"]) for r in outflows[:5]]
            print(f"  流出: {', '.join(parts)}")


# ============================================================
# CLI: positions / check
# ============================================================

def cmd_positions():
    data = load_positions()
    pos = data.get("positions", {})
    if not pos:
        print("当前无持仓")
        hist = data.get("trade_history", [])
        if hist:
            print(f"\n历史交易: {len(hist)}笔")
            wins = sum(1 for t in hist if t.get("pnl_pct", 0) > 0)
            print(f"胜率: {wins}/{len(hist)} = {wins / len(hist) * 100:.0f}%")
        return

    conn = init_db()
    print(f"\n当前持仓 ({len(pos)}个):")
    print(f"{'标的':<10s} {'入场价':>7s} {'现价':>7s} {'盈亏':>7s} {'最高':>7s} {'止损':>7s} {'K':>4s} {'天数':>4s}")
    print("-" * 65)

    for sym, p in pos.items():
        bars = get_bars(conn, sym, 60)
        current = bars[-1]["close"] if bars else 0
        gain = (current - p["entry_price"]) / p["entry_price"] * 100 if p["entry_price"] > 0 else 0
        days = 0
        try:
            days = (datetime.date.today() - datetime.date.fromisoformat(p["entry_date"])).days
        except (ValueError, TypeError):
            pass
        print(f"  {p.get('name', sym):<8s} {p['entry_price']:7.3f} {current:7.3f} {gain:+6.1f}% "
              f"{p['highest_close']:7.3f} {p['trailing_stop']:7.3f} {p['current_K']:4.1f} {days:4d}")

    conn.close()


def cmd_check():
    data = load_positions()
    pos = data.get("positions", {})
    if not pos:
        print("无持仓需检查")
        return

    conn = init_db()
    _s = load_etf_settings()
    _ds, _tk = _s.get("data_source", "tencent"), _s.get("tushare_token", "")
    alerts = []

    for sym, p in pos.items():
        try:
            update_cn_ticker(conn, sym, verbose=False, data_source=_ds, tushare_token=_tk)
        except Exception:
            pass
        bars = get_bars(conn, sym, 60)
        if not bars:
            continue

        current = bars[-1]["close"]
        highest = max(p["highest_close"], current)
        should_exit, reason, new_stop, new_k = check_exit(
            bars, p["entry_price"], highest
        )

        p["highest_close"] = highest
        p["trailing_stop"] = new_stop
        p["current_K"] = new_k

        gain = (current - p["entry_price"]) / p["entry_price"] * 100
        name = p.get("name", sym)

        if should_exit:
            alerts.append(f"!!! {name}: 触发出场 — {reason}")
            print(f"  !!! {name} {current:.3f} 触发出场: {reason}")
        else:
            dist = (current - new_stop) / current * 100
            print(f"  {name} {current:.3f} (盈{gain:+.1f}%) 止损={new_stop:.3f}(距{dist:.1f}%) K={new_k:.1f}")

    save_positions(data)
    conn.close()
    return alerts


# ============================================================
# CLI: daily
# ============================================================

def cmd_daily(push_dingtalk=False):
    now = datetime.datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M")
    print(f"[{now_str}] ETF右侧趋势系统 — 每日例行\n")

    conn = init_db()
    _s = load_etf_settings()
    _ds, _tk = _s.get("data_source", "tencent"), _s.get("tushare_token", "")
    update_cn_ticker(conn, BENCHMARK, verbose=False, data_source=_ds, tushare_token=_tk)

    data = load_positions()
    pos = data.get("positions", {})
    lines = [f"### ETF右侧趋势 ({now.strftime('%m/%d %H:%M')})", ""]

    exit_alerts = []
    for sym in list(pos.keys()):
        p = pos[sym]
        try:
            update_cn_ticker(conn, sym, verbose=False, data_source=_ds, tushare_token=_tk)
        except Exception:
            continue
        bars = get_bars(conn, sym, 60)
        if not bars:
            continue
        current = bars[-1]["close"]
        highest = max(p["highest_close"], current)
        should_exit, reason, new_stop, new_k = check_exit(bars, p["entry_price"], highest)
        p["highest_close"] = highest
        p["trailing_stop"] = new_stop
        p["current_K"] = new_k
        gain = (current - p["entry_price"]) / p["entry_price"] * 100
        name = p.get("name", sym)

        if should_exit:
            exit_alerts.append(f"- **{name}** 出场: {current:.3f}({gain:+.1f}%) {reason}")
            data.setdefault("trade_history", []).append({
                "symbol": sym, "name": name,
                "entry_date": p["entry_date"], "exit_date": now.strftime("%Y-%m-%d"),
                "entry_price": p["entry_price"], "exit_price": current,
                "pnl_pct": gain, "exit_reason": reason,
            })
            del pos[sym]
        else:
            dist = (current - new_stop) / current * 100
            lines.append(f"- **{name}** {current:.3f}({gain:+.1f}%) 止损={new_stop:.3f}(距{dist:.1f}%) K={new_k:.1f}")

    if exit_alerts:
        lines.insert(2, "**出场信号**:")
        lines.insert(3, "")
        for a in exit_alerts:
            lines.insert(4, a)
        lines.insert(4 + len(exit_alerts), "")

    entry_signals = []
    symbols = [(n, cfg["symbol"]) for n, cfg in ETF_BASKET.items()]
    for name, sym in symbols:
        if sym in pos:
            continue
        try:
            update_cn_ticker(conn, sym, verbose=False, data_source=_ds, tushare_token=_tk)
        except Exception:
            continue
        bars = get_bars(conn, sym, 750)
        if len(bars) < 60:
            continue
        entry, details, ex = check_entry(bars)
        if entry:
            entry_signals.append(f"- **{name}** {ex['price']:.3f} 5日{ex['chg_5d']:+.1f}% {' | '.join(details[:2])}")

    if entry_signals:
        lines.append("")
        lines.append("**入场信号**:")
        lines.append("")
        lines.extend(entry_signals)

    # 份额流向摘要
    try:
        share_data = fetch_all_share_changes()
        inflows = sorted(
            [(n, share_data[cfg["symbol"]]) for n, cfg in ETF_BASKET.items()
             if cfg["symbol"] in share_data and share_data[cfg["symbol"]]["chg_pct"] > 3],
            key=lambda x: -x[1]["chg_pct"])
        outflows = sorted(
            [(n, share_data[cfg["symbol"]]) for n, cfg in ETF_BASKET.items()
             if cfg["symbol"] in share_data and share_data[cfg["symbol"]]["chg_pct"] < -3],
            key=lambda x: x[1]["chg_pct"])
        if inflows or outflows:
            lines.append("")
            lines.append("**份额流向(近1月)**:")
            if inflows:
                parts = ["%s(%+.0f%%)" % (n, s["chg_pct"]) for n, s in inflows[:5]]
                lines.append(f"- 流入: {', '.join(parts)}")
            if outflows:
                parts = ["%s(%+.0f%%)" % (n, s["chg_pct"]) for n, s in outflows[:5]]
                lines.append(f"- 流出: {', '.join(parts)}")
    except Exception:
        pass

    lines.append("")
    lines.append("---")
    lines.append("右侧趋势系统 | 自适应ATR追踪止损")

    save_positions(data)
    conn.close()

    md = "\n".join(lines)
    print(md)

    if push_dingtalk and (exit_alerts or entry_signals):
        title = "ETF出场警报" if exit_alerts else "ETF入场信号"
        ok, msg = send_dingtalk(md, title=title)
        print(f"\n钉钉: {'OK' if ok else '失败'} {msg}")


# ============================================================
# CLI: backtest / backtest-all
# ============================================================

def cmd_backtest(symbol, days=500):
    conn = init_db()
    _s = load_etf_settings()
    _ds, _tk = _s.get("data_source", "tencent"), _s.get("tushare_token", "")
    print(f"拉取 {symbol}...", end="", flush=True)
    update_cn_ticker(conn, symbol, verbose=False, data_source=_ds, tushare_token=_tk)
    update_cn_ticker(conn, BENCHMARK, verbose=False, data_source=_ds, tushare_token=_tk)
    bars = get_bars(conn, symbol, days + 100)
    bench = get_bars(conn, BENCHMARK, days + 100)
    conn.close()
    print(f" {len(bars)}条K线")

    if len(bars) < 80:
        print(f"数据不足({len(bars)})")
        return None

    name = _resolve_name(symbol)
    label = f"{name}({symbol})" if name else symbol

    bt = RightSideBacktest(bars, bench)
    bt.run()
    return bt.report(label)


def cmd_backtest_all(days=500):
    conn = init_db()
    _s = load_etf_settings()
    _ds, _tk = _s.get("data_source", "tencent"), _s.get("tushare_token", "")
    print("更新基准...", end="", flush=True)
    update_cn_ticker(conn, BENCHMARK, verbose=False, data_source=_ds, tushare_token=_tk)
    print(" OK\n")

    symbols = [(n, cfg["symbol"], cfg.get("holding", False)) for n, cfg in ETF_BASKET.items()]
    for _, sym, _ in symbols:
        try:
            update_cn_ticker(conn, sym, verbose=False, data_source=_ds, tushare_token=_tk)
        except Exception:
            pass

    bench = get_bars(conn, BENCHMARK, days + 100)
    results = []

    for name, sym, holding in symbols:
        bars = get_bars(conn, sym, days + 100)
        if len(bars) < 80:
            print(f"  {name}: 数据不足({len(bars)})")
            continue

        bt = RightSideBacktest(bars, bench)
        bt.run()

        if not bt.equity_curve:
            continue

        final = bt.equity_curve[-1]["equity"]
        ret = (final - bt.init_capital) / bt.init_capital * 100
        wins = [t for t in bt.trades if t["pnl"] > 0]
        wr = len(wins) / len(bt.trades) * 100 if bt.trades else 0
        avg_k = sum(t["exit_K"] for t in bt.trades if t["exit_K"] > 0) / max(1, sum(1 for t in bt.trades if t["exit_K"] > 0))

        results.append({
            "name": name, "symbol": sym, "holding": holding,
            "total_return": ret, "max_dd": bt.max_dd * 100,
            "trades": len(bt.trades), "win_rate": wr,
            "avg_K": avg_k,
            "wins": len(wins), "losses": len(bt.trades) - len(wins),
        })

    conn.close()
    results.sort(key=lambda x: -x["total_return"])

    print(f"\n{'=' * 75}")
    print(f"批量回测 | 右侧+自适应ATR止损 | 近{days}天 | {len(results)}个ETF")
    print(f"{'=' * 75}")
    print(f"{'行业':<8s} {'评级':>2s} {'总收益':>7s} {'回撤':>6s} {'交易':>4s} {'胜率':>5s} {'均K':>4s}")
    print("-" * 48)
    for r in results:
        m = "*" if r["holding"] else " "
        g = calc_grade(r["total_return"])
        print(f"{m}{r['name']:<7s} [{g}] {r['total_return']:+6.1f}% {r['max_dd']:5.1f}% {r['trades']:4d} {r['win_rate']:4.0f}% {r['avg_K']:4.1f}")

    if results:
        avg_ret = sum(r["total_return"] for r in results) / len(results)
        avg_dd = sum(r["max_dd"] for r in results) / len(results)
        pos = sum(1 for r in results if r["total_return"] > 0)
        total_trades = sum(r["trades"] for r in results)
        total_wins = sum(r["wins"] for r in results)
        print(f"\n平均收益: {avg_ret:+.1f}% | 平均回撤: {avg_dd:.1f}% | 盈利: {pos}/{len(results)}")
        print(f"总交易: {total_trades}笔 | 总胜率: {total_wins / total_trades * 100:.0f}%")
        s_count = sum(1 for r in results if calc_grade(r["total_return"]) == "S")
        a_count = sum(1 for r in results if calc_grade(r["total_return"]) == "A")
        b_count = sum(1 for r in results if calc_grade(r["total_return"]) == "B")
        cd_count = sum(1 for r in results if calc_grade(r["total_return"]) in ("C", "D"))
        print(f"评级: S={s_count} A={a_count} B={b_count} C/D={cd_count}(不推荐)")

    return results


def cmd_calibrate(days=500):
    print("=== 趋势适配度校准 ===\n")
    results = cmd_backtest_all(days)
    if not results:
        print("校准失败: 无回测结果")
        return

    cal = {}
    for r in results:
        grade = calc_grade(r["total_return"])
        cal[r["symbol"]] = {
            "name": r["name"],
            "grade": grade,
            "total_return": round(r["total_return"], 1),
            "max_dd": round(r["max_dd"], 1),
            "trades": r["trades"],
            "win_rate": round(r["win_rate"], 0),
            "avg_K": round(r["avg_K"], 1),
        }

    save_calibration(cal)

    s = [c for c in cal.values() if c["grade"] == "S"]
    a = [c for c in cal.values() if c["grade"] == "A"]
    b = [c for c in cal.values() if c["grade"] == "B"]
    cd = [c for c in cal.values() if c["grade"] in ("C", "D")]

    print(f"\n校准完成，已保存到 {CALIBRATION_FILE}")
    print(f"\n趋势适配度评级 (基于{days}天回测):")
    print(f"  S级(>100%): {', '.join(c['name'] for c in s)}")
    print(f"  A级(50-100%): {', '.join(c['name'] for c in a)}")
    print(f"  B级(20-50%): {', '.join(c['name'] for c in b)}")
    print(f"  C/D级(<20%,不推荐): {', '.join(c['name'] for c in cd)}")
    print(f"\n  扫描时将自动过滤C/D级标的的入场信号")


# ============================================================
# 市场环境分段回测
# ============================================================

MARKET_PHASES = [
    ("熊市阴跌",   "2024-01-02", "2024-09-23", "bear"),
    ("暴涨行情",   "2024-09-24", "2024-10-08", "bull"),
    ("冲高回落",   "2024-10-09", "2024-11-30", "bear"),
    ("震荡筑底",   "2024-12-01", "2025-03-31", "sideways"),
    ("反弹行情",   "2025-04-01", "2025-07-31", "bull"),
    ("震荡盘整",   "2025-08-01", "2025-12-31", "sideways"),
    ("慢牛行情",   "2026-01-01", "2026-06-30", "bull"),
]


def _phase_for_date(date_str):
    for name, start, end, ptype in MARKET_PHASES:
        if start <= date_str <= end:
            return name, ptype
    return "其他", "unknown"


def _calc_bench_phase_return(bench_bars, start, end):
    start_price, end_price = None, None
    for b in bench_bars:
        if b["date"] >= start and start_price is None:
            start_price = b["close"]
        if b["date"] <= end:
            end_price = b["close"]
    if start_price and end_price and start_price > 0:
        return (end_price - start_price) / start_price * 100
    return 0


def cmd_backtest_env(symbol, days=1000):
    conn = init_db()
    _s = load_etf_settings()
    _ds, _tk = _s.get("data_source", "tencent"), _s.get("tushare_token", "")
    print(f"拉取 {symbol} ({days}天)...", end="", flush=True)
    update_cn_ticker(conn, symbol, verbose=False, data_source=_ds, tushare_token=_tk)
    update_cn_ticker(conn, BENCHMARK, verbose=False, data_source=_ds, tushare_token=_tk)
    bars = get_bars(conn, symbol, days + 100)
    bench = get_bars(conn, BENCHMARK, days + 100)
    conn.close()
    print(f" {len(bars)}条K线")

    if len(bars) < 80:
        print(f"数据不足({len(bars)})")
        return None

    name = _resolve_name(symbol)
    label = f"{name}({symbol})" if name else symbol

    bt = RightSideBacktest(bars, bench)
    bt.run()

    if not bt.trades:
        print("无交易记录")
        return None

    phase_stats = {}
    for phase_name, start, end, ptype in MARKET_PHASES:
        phase_stats[phase_name] = {
            "type": ptype, "start": start, "end": end,
            "trades": [], "bench_ret": _calc_bench_phase_return(bench, start, end),
        }

    for t in bt.trades:
        pname, _ = _phase_for_date(t["entry_date"])
        if pname in phase_stats:
            phase_stats[pname]["trades"].append(t)

    print(f"\n{'=' * 72}")
    print(f"  市场环境回测 | {label} | {len(bars)}天")
    print(f"{'=' * 72}")
    print(f"  {'阶段':<8s} {'类型':<8s} {'基准':>6s} {'交易':>4s} {'胜率':>5s} {'平均盈':>7s} {'平均亏':>7s} {'阶段PnL':>8s}")
    print(f"  {'-' * 66}")

    type_agg = {"bull": [], "bear": [], "sideways": []}

    for phase_name, start, end, ptype in MARKET_PHASES:
        ps = phase_stats[phase_name]
        trades = ps["trades"]
        n_trades = len(trades)
        if n_trades == 0:
            print(f"  {phase_name:<8s} {ptype:<8s} {ps['bench_ret']:+5.1f}%    0     -       -       -       -")
            continue

        wins = [t for t in trades if t["pnl_pct"] > 0]
        losses = [t for t in trades if t["pnl_pct"] <= 0]
        wr = len(wins) / n_trades * 100
        avg_w = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
        avg_l = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
        total_pnl = sum(t["pnl_pct"] for t in trades)

        print(f"  {phase_name:<8s} {ptype:<8s} {ps['bench_ret']:+5.1f}% {n_trades:4d} {wr:4.0f}% {avg_w:+6.1f}% {avg_l:+6.1f}% {total_pnl:+7.1f}%")

        for t in trades:
            type_agg[ptype].append(t)

    print(f"\n  {'─' * 50}")
    print(f"  汇总:")
    for ptype, label_cn in [("bull", "牛市"), ("bear", "熊市"), ("sideways", "震荡")]:
        trades = type_agg[ptype]
        if not trades:
            print(f"    {label_cn}: 无交易")
            continue
        wins = [t for t in trades if t["pnl_pct"] > 0]
        wr = len(wins) / len(trades) * 100
        avg_pnl = sum(t["pnl_pct"] for t in trades) / len(trades)
        total_pnl = sum(t["pnl_pct"] for t in trades)
        print(f"    {label_cn}: {len(trades)}笔 胜率{wr:.0f}% 单均{avg_pnl:+.1f}% 合计{total_pnl:+.1f}%")

    bt.report(label)
    return {"trades": bt.trades, "phase_stats": phase_stats, "type_agg": type_agg}


def cmd_backtest_env_all(days=1000):
    conn = init_db()
    _s = load_etf_settings()
    _ds, _tk = _s.get("data_source", "tencent"), _s.get("tushare_token", "")
    print("更新基准...", end="", flush=True)
    update_cn_ticker(conn, BENCHMARK, verbose=False, data_source=_ds, tushare_token=_tk)
    print(" OK")

    symbols = [(n, cfg["symbol"]) for n, cfg in ETF_BASKET.items()]
    for _, sym in symbols:
        try:
            update_cn_ticker(conn, sym, verbose=False, data_source=_ds, tushare_token=_tk)
        except Exception:
            pass

    bench = get_bars(conn, BENCHMARK, days + 100)

    type_all = {"bull": [], "bear": [], "sideways": []}
    per_etf = []

    print(f"\n扫描{len(symbols)}个ETF({days}天)...\n")
    for name, sym in symbols:
        bars = get_bars(conn, sym, days + 100)
        if len(bars) < 80:
            continue

        bt = RightSideBacktest(bars, bench)
        bt.run()
        if not bt.trades:
            continue

        etf_type = {"bull": [], "bear": [], "sideways": []}
        for t in bt.trades:
            _, ptype = _phase_for_date(t["entry_date"])
            if ptype in etf_type:
                etf_type[ptype].append(t)
                type_all[ptype].append(t)

        per_etf.append({"name": name, "symbol": sym, "by_type": etf_type, "trades": bt.trades})

    conn.close()

    print(f"\n{'=' * 78}")
    print(f"  全市场分环境回测 | {len(per_etf)}个ETF | {days}天")
    print(f"{'=' * 78}")

    for ptype, label_cn in [("bull", "牛市"), ("bear", "熊市"), ("sideways", "震荡")]:
        all_trades = type_all[ptype]
        if not all_trades:
            continue
        wins = [t for t in all_trades if t["pnl_pct"] > 0]
        wr = len(wins) / len(all_trades) * 100
        avg_pnl = sum(t["pnl_pct"] for t in all_trades) / len(all_trades)
        total_pnl = sum(t["pnl_pct"] for t in all_trades)
        print(f"\n  【{label_cn}】{len(all_trades)}笔交易 | 胜率{wr:.0f}% | 单均{avg_pnl:+.1f}% | 合计{total_pnl:+.1f}%")

        ranked = []
        for e in per_etf:
            trades = e["by_type"][ptype]
            if not trades:
                continue
            pnl = sum(t["pnl_pct"] for t in trades)
            w = sum(1 for t in trades if t["pnl_pct"] > 0)
            ranked.append({"name": e["name"], "trades": len(trades),
                           "wr": w / len(trades) * 100, "pnl": pnl})

        ranked.sort(key=lambda x: -x["pnl"])
        top = ranked[:8]
        bottom = ranked[-3:] if len(ranked) > 8 else []

        print(f"    {'标的':<8s} {'交易':>4s} {'胜率':>5s} {'合计PnL':>8s}")
        print(f"    {'-' * 35}")
        for r in top:
            print(f"    {r['name']:<8s} {r['trades']:4d} {r['wr']:4.0f}% {r['pnl']:+7.1f}%")
        if bottom and bottom[0] != top[-1]:
            print(f"    ...")
            for r in bottom:
                print(f"    {r['name']:<8s} {r['trades']:4d} {r['wr']:4.0f}% {r['pnl']:+7.1f}%")

    print(f"\n{'─' * 50}")
    print("  结论:")
    for ptype, label_cn in [("bull", "牛市"), ("bear", "熊市"), ("sideways", "震荡")]:
        all_t = type_all[ptype]
        if not all_t:
            continue
        wins = [t for t in all_t if t["pnl_pct"] > 0]
        losses = [t for t in all_t if t["pnl_pct"] <= 0]
        avg_w = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
        avg_l = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
        wr = len(wins) / len(all_t) * 100
        print(f"    {label_cn}: 胜率{wr:.0f}% 平均盈{avg_w:+.1f}% 平均亏{avg_l:+.1f}% 盈亏比{abs(avg_w/avg_l):.1f}" if avg_l != 0 else f"    {label_cn}: 胜率{wr:.0f}% 平均盈{avg_w:+.1f}%")


# ============================================================
# 参数敏感性测试
# ============================================================

def cmd_param_test(symbol, days=1000):
    conn = init_db()
    _s = load_etf_settings()
    _ds, _tk = _s.get("data_source", "tencent"), _s.get("tushare_token", "")
    print(f"拉取 {symbol} ({days}天)...", end="", flush=True)
    update_cn_ticker(conn, symbol, verbose=False, data_source=_ds, tushare_token=_tk)
    update_cn_ticker(conn, BENCHMARK, verbose=False, data_source=_ds, tushare_token=_tk)
    bars = get_bars(conn, symbol, days + 100)
    bench = get_bars(conn, BENCHMARK, days + 100)
    conn.close()
    print(f" {len(bars)}条K线")

    if len(bars) < 80:
        print(f"数据不足({len(bars)})")
        return

    name = _resolve_name(symbol)
    label = f"{name}({symbol})" if name else symbol

    param_sets = [
        (3.0, 1.2, 0.08, "3.0→1.2  8% ← 当前"),
        (2.5, 1.0, 0.08, "2.5→1.0  8%"),
        (2.5, 1.2, 0.08, "2.5→1.2  8%"),
        (3.0, 1.5, 0.08, "3.0→1.5  8%"),
        (3.5, 1.2, 0.08, "3.5→1.2  8%"),
        (3.5, 1.5, 0.08, "3.5→1.5  8%"),
        (3.0, 1.2, 0.06, "3.0→1.2  6%"),
        (3.0, 1.2, 0.10, "3.0→1.2 10%"),
        (3.0, 1.2, 0.12, "3.0→1.2 12%"),
    ]

    print(f"\n{'=' * 72}")
    print(f"  参数敏感性 | {label} | {len(bars)}天")
    print(f"{'=' * 72}")
    print(f"  {'参数':<18s} {'总收益':>7s} {'回撤':>6s} {'交易':>4s} {'胜率':>5s} {'均盈':>6s} {'均亏':>6s} {'Sharpe':>6s}")
    print(f"  {'-' * 65}")

    for base_k, min_k, hard_stop, desc in param_sets:
        bt = RightSideBacktest(bars, bench, base_k=base_k, min_k=min_k, hard_stop_pct=hard_stop)
        bt.run()

        if not bt.equity_curve:
            print(f"  {desc:<18s}   无数据")
            continue

        final = bt.equity_curve[-1]["equity"]
        ret = (final - bt.init_capital) / bt.init_capital * 100
        wins = [t for t in bt.trades if t["pnl_pct"] > 0]
        losses = [t for t in bt.trades if t["pnl_pct"] <= 0]
        wr = len(wins) / len(bt.trades) * 100 if bt.trades else 0
        avg_w = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
        avg_l = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0

        dr = []
        for j in range(1, len(bt.equity_curve)):
            p = bt.equity_curve[j - 1]["equity"]
            if p > 0:
                dr.append((bt.equity_curve[j]["equity"] - p) / p)
        avg_dr = sum(dr) / len(dr) if dr else 0
        std_dr = math.sqrt(sum((r - avg_dr) ** 2 for r in dr) / len(dr)) if len(dr) > 1 else 1
        sharpe = (avg_dr / std_dr) * math.sqrt(244) if std_dr > 0 else 0

        print(f"  {desc:<18s} {ret:+6.1f}% {bt.max_dd*100:5.1f}% {len(bt.trades):4d} {wr:4.0f}% {avg_w:+5.1f}% {avg_l:+5.1f}% {sharpe:5.2f}")

    print(f"\n  注: 当前参数为 Base_K=3.0, Min_K=1.2, 硬止损=8%")


# ============================================================
# ETF份额数据 — 资金流入/流出信号
# ============================================================

SHARE_DB_PATH = os.path.join(SCRIPT_DIR, "state", "etf_shares.db")

# 每个ETF对应的跟踪指数代码（用于找同指数的其他ETF）
ETF_INDEX_MAP = {
    "sh515880": "931160",   # 通信
    "sz159516": "H30184",   # 半导体设备
    "sz159801": "990001",   # 芯片
    "sz159611": "399812",   # 电力
    "sz159326": "931180",   # 电网设备
    "sh512660": "399967",   # 军工
    "sh562500": "930009",   # 机器人
    "sz159732": "931502",   # 消费电子
    "sh515220": "399998",   # 煤炭
    "sh512800": "399986",   # 银行
    "sz159992": "399993",   # 创新药
    "sh515790": "931151",   # 光伏
    "sh515030": "399976",   # 新能车
    "sz161725": "399997",   # 白酒
    "sh515180": "930955",   # 红利
    "sh512010": "399978",   # 医药
    "sh512200": "399393",   # 房地产
    "sh512400": "399395",   # 有色金属
    "sz159852": "932094",   # 软件
    "sz159206": "931616",   # 卫星
    "sz159819": "931580",   # AI算力
    "sz159890": "930851",   # 云计算
    "sz159869": "930902",   # 游戏
    "sz159996": "930697",   # 家电
    "sz159713": "399428",   # 稀土
    "sh516760": "931746",   # 储能
    "sh512880": "399975",   # 证券
    "sz159865": "399959",   # 养殖
    "sh515210": "399440",   # 钢铁
    "sz159870": "931040",   # 化工
    "sz159647": "930641",   # 中药
    "sz159766": "930633",   # 旅游
    "sh512890": "H30269",   # 红利低波
    "sh515900": "931643",   # 央企创新
    "sh513180": "HSTECH",   # 恒生科技
    "sh588200": "931743",   # 科创芯片
}


def init_share_db():
    os.makedirs(os.path.dirname(SHARE_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(SHARE_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS etf_shares (
            fund_code TEXT, date TEXT, shares_yi REAL,
            PRIMARY KEY (fund_code, date)
        )
    """)
    conn.commit()
    return conn


def fetch_fund_shares(fund_code):
    """从东方财富获取近1月每日份额数据 (Data_fundSharesPositions)"""
    url = f"https://fund.eastmoney.com/pingzhongdata/{fund_code}.js"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        content = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return []

    m = re.search(r'Data_fundSharesPositions\s*=\s*(\[.*?\]);', content, re.DOTALL)
    if not m:
        return []

    try:
        data = json.loads(m.group(1))
    except (json.JSONDecodeError, ValueError):
        return []

    result = []
    for ts_ms, shares_yi in data:
        dt = datetime.datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")
        result.append({"date": dt, "shares_yi": shares_yi})
    return result


def fetch_fund_quarterly_shares(fund_code):
    """从东方财富获取季度份额历史 (FundArchivesDatas gmbd)"""
    url = f"https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=gmbd&code={fund_code}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        content = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return []

    m = re.search(r'"data":\[(.*?)\]', content, re.DOTALL)
    if not m:
        return []

    results = []
    for item in re.finditer(r'\{[^}]+\}', m.group(1)):
        try:
            d = json.loads(item.group(0))
            date = d.get("FSRQ", "")
            qmzfe = d.get("QMZFE")
            if date and qmzfe:
                results.append({
                    "date": date,
                    "shares_yi": round(qmzfe / 1e8, 2),
                    "net_asset_yi": round(d.get("QMJZC", 0) / 1e8, 2) if d.get("QMJZC") else None,
                })
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
    return results


def save_shares_to_db(conn, fund_code, share_data):
    for item in share_data:
        conn.execute(
            "INSERT OR REPLACE INTO etf_shares (fund_code, date, shares_yi) VALUES (?, ?, ?)",
            (fund_code, item["date"], item["shares_yi"]),
        )
    conn.commit()


def get_shares_from_db(conn, fund_code, days=60):
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT date, shares_yi FROM etf_shares WHERE fund_code=? AND date>=? ORDER BY date",
        (fund_code, cutoff),
    ).fetchall()
    return [{"date": r[0], "shares_yi": r[1]} for r in rows]


def symbol_to_fund_code(symbol):
    return symbol[2:]


def fetch_all_share_changes():
    """批量获取所有ETF的份额变化，返回 {symbol: {latest, chg, chg_pct, date}} """
    share_conn = init_share_db()
    result = {}
    for name, info in ETF_BASKET.items():
        sym = info["symbol"]
        fund_code = symbol_to_fund_code(sym)
        daily = fetch_fund_shares(fund_code)
        if daily:
            save_shares_to_db(share_conn, fund_code, daily)
        if daily and len(daily) >= 2:
            latest = daily[-1]["shares_yi"]
            earliest = daily[0]["shares_yi"]
            chg = latest - earliest
            chg_pct = chg / earliest * 100 if earliest > 0 else 0
            result[sym] = {
                "latest": latest, "chg": round(chg, 2),
                "chg_pct": round(chg_pct, 1), "date": daily[-1]["date"],
            }
        time.sleep(0.15)
    share_conn.close()
    return result


def cmd_shares(symbol):
    """查看单个ETF的份额变化"""
    fund_code = symbol_to_fund_code(symbol)
    name = None
    for n, info in ETF_BASKET.items():
        if info["symbol"] == symbol:
            name = n
            break
    if not name:
        name = symbol

    print(f"\n  获取 {name}({symbol}) 份额数据...")

    daily = fetch_fund_shares(fund_code)
    quarterly = fetch_fund_quarterly_shares(fund_code)

    conn = init_share_db()
    if daily:
        save_shares_to_db(conn, fund_code, daily)

    print(f"\n{'='*60}")
    print(f"  ETF份额变化 | {name}({symbol})")
    print(f"{'='*60}")

    if daily and len(daily) >= 2:
        latest = daily[-1]
        earliest = daily[0]
        chg = latest["shares_yi"] - earliest["shares_yi"]
        chg_pct = chg / earliest["shares_yi"] * 100 if earliest["shares_yi"] > 0 else 0
        print(f"\n  近期每日份额（亿份）:")
        print(f"  {'日期':12s} {'份额(亿)':>10s} {'日变化':>10s}")
        print(f"  {'─'*36}")
        prev = None
        for item in daily:
            day_chg = ""
            if prev is not None:
                dc = item["shares_yi"] - prev
                if abs(dc) > 0.001:
                    day_chg = f"{dc:+.2f}"
            print(f"  {item['date']:12s} {item['shares_yi']:10.2f} {day_chg:>10s}")
            prev = item["shares_yi"]
        print(f"\n  期间变化: {chg:+.2f}亿份 ({chg_pct:+.1f}%)")
        print(f"  最新: {latest['shares_yi']:.2f}亿份 ({latest['date']})")
    else:
        print("\n  无法获取近期每日份额数据")

    if quarterly:
        print(f"\n  季度份额历史:")
        print(f"  {'日期':12s} {'份额(亿)':>10s} {'净资产(亿)':>12s} {'变化':>10s}")
        print(f"  {'─'*50}")
        prev_q = None
        for item in quarterly[:8]:
            qchg = ""
            if prev_q is not None:
                dc = item["shares_yi"] - prev_q
                qchg = f"{dc:+.1f}"
            na = f"{item['net_asset_yi']:.1f}" if item.get("net_asset_yi") else "-"
            print(f"  {item['date']:12s} {item['shares_yi']:10.2f} {na:>12s} {qchg:>10s}")
            prev_q = item["shares_yi"]

    conn.close()
    print()


def cmd_shares_all():
    """查看全部ETF份额变化概览"""

    print(f"\n  获取全部ETF份额数据...\n")

    conn = init_share_db()
    results = []

    for name, info in ETF_BASKET.items():
        symbol = info["symbol"]
        fund_code = symbol_to_fund_code(symbol)
        daily = fetch_fund_shares(fund_code)
        if daily:
            save_shares_to_db(conn, fund_code, daily)

        if daily and len(daily) >= 2:
            latest = daily[-1]["shares_yi"]
            earliest = daily[0]["shares_yi"]
            chg = latest - earliest
            chg_pct = chg / earliest * 100 if earliest > 0 else 0
            results.append({
                "name": name, "symbol": symbol, "cat": info["cat"],
                "latest": latest, "chg": chg, "chg_pct": chg_pct,
                "date": daily[-1]["date"],
            })
        time.sleep(0.3)

    conn.close()

    results.sort(key=lambda x: x["chg_pct"], reverse=True)

    print(f"{'='*70}")
    print(f"  ETF份额变化排行 | 近1月 | {results[0]['date'] if results else ''}")
    print(f"{'='*70}")
    print(f"  {'标的':8s} {'分类':4s} {'最新(亿)':>10s} {'变化(亿)':>10s} {'变化%':>8s}  方向")
    print(f"  {'─'*64}")

    for r in results:
        arrow = "🔴净流出" if r["chg"] < -0.5 else ("🟢净流入" if r["chg"] > 0.5 else "➖持平")
        print(f"  {r['name']:8s} {r['cat']:4s} {r['latest']:10.2f} {r['chg']:+10.2f} {r['chg_pct']:+7.1f}%  {arrow}")

    inflow = [r for r in results if r["chg"] > 0.5]
    outflow = [r for r in results if r["chg"] < -0.5]
    print(f"\n  净流入: {len(inflow)}个ETF  |  净流出: {len(outflow)}个ETF  |  持平: {len(results)-len(inflow)-len(outflow)}个")

    if inflow:
        top3 = inflow[:3]
        parts = ["%s(%+.1f亿)" % (r["name"], r["chg"]) for r in top3]
        print(f"  TOP流入: {', '.join(parts)}")
    if outflow:
        bot3 = outflow[-3:]
        parts = ["%s(%+.1f亿)" % (r["name"], r["chg"]) for r in bot3]
        print(f"  TOP流出: {', '.join(parts)}")
    print()


# ============================================================
# 回测API / 优化引擎
# ============================================================

def run_backtest_with_config(symbol, config=None, days=500):
    conn = init_db()
    _s = load_etf_settings()
    _ds, _tk = _s.get("data_source", "tencent"), _s.get("tushare_token", "")
    update_cn_ticker(conn, symbol, verbose=False, data_source=_ds, tushare_token=_tk)
    update_cn_ticker(conn, BENCHMARK, verbose=False, data_source=_ds, tushare_token=_tk)
    bars = get_bars(conn, symbol, days + 100)
    bench = get_bars(conn, BENCHMARK, days + 100)
    conn.close()

    if len(bars) < 80:
        return {"error": f"数据不足({len(bars)}条)"}

    bt = RightSideBacktest(bars, bench, config=config)
    bt.run()

    total_ret = (bt.equity_curve[-1]["equity"] / bt.init_capital - 1) * 100 if bt.equity_curve else 0
    n_days = len(bt.equity_curve)
    ann_ret = ((1 + total_ret / 100) ** (244 / max(n_days, 1)) - 1) * 100 if n_days > 0 else 0
    wins = [t for t in bt.trades if t["pnl_pct"] > 0]
    losses = [t for t in bt.trades if t["pnl_pct"] <= 0]
    win_rate = len(wins) / len(bt.trades) * 100 if bt.trades else 0
    avg_win = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0

    bench_ret = 0
    if bench and len(bench) >= 2:
        bench_ret = (bench[-1]["close"] / bench[0]["close"] - 1) * 100

    eq_vals = [e["equity"] for e in bt.equity_curve]
    sharpe = 0
    if len(eq_vals) > 1:
        rets = [(eq_vals[j] - eq_vals[j - 1]) / eq_vals[j - 1] for j in range(1, len(eq_vals))]
        avg_r = sum(rets) / len(rets)
        std_r = math.sqrt(sum((r - avg_r) ** 2 for r in rets) / len(rets)) if len(rets) > 1 else 1
        sharpe = (avg_r / std_r) * math.sqrt(244) if std_r > 0 else 0

    trade_list = []
    for t in bt.trades:
        trade_list.append({
            "entry_date": t["entry_date"], "exit_date": t["exit_date"],
            "entry_price": round(t["entry_price"], 3), "exit_price": round(t["exit_price"], 3),
            "pnl_pct": round(t["pnl_pct"], 1), "hold_days": t["hold_days"],
            "exit_reason": t["exit_reason"],
        })

    return {
        "symbol": symbol, "name": _resolve_name(symbol), "days": days,
        "total_return": round(total_ret, 1), "annual_return": round(ann_ret, 1),
        "max_drawdown": round(bt.max_dd * 100, 1), "sharpe": round(sharpe, 2),
        "trades": len(bt.trades), "win_rate": round(win_rate, 1),
        "avg_win": round(avg_win, 1), "avg_loss": round(avg_loss, 1),
        "bench_return": round(bench_ret, 1),
        "trade_list": trade_list,
    }


OPTIMIZE_GRID = {
    "ma_period": [10, 15, 20, 30],
    "base_k": [2.0, 2.5, 3.0, 3.5],
    "min_k": [0.8, 1.0, 1.2, 1.5],
    "hard_stop_pct": [0.06, 0.08, 0.10, 0.12],
}

def _opt_score(total_ret, max_dd_pct, win_rate, n_trades):
    if n_trades < 3:
        return -999
    return (total_ret * 0.4
            + (100 - max_dd_pct) * 0.3
            + win_rate * 0.2
            + min(n_trades / 5, 10) * 0.1)

def run_optimization(scope="global", days=1000):
    conn = init_db()
    _s = load_etf_settings()
    _ds, _tk = _s.get("data_source", "tencent"), _s.get("tushare_token", "")
    update_cn_ticker(conn, BENCHMARK, verbose=False, data_source=_ds, tushare_token=_tk)

    if scope == "global":
        symbols = [(n, cfg["symbol"]) for n, cfg in ETF_BASKET.items()]
        for _, sym in symbols:
            try:
                update_cn_ticker(conn, sym, verbose=False, data_source=_ds, tushare_token=_tk)
            except Exception:
                pass
    else:
        symbols = [(_resolve_name(scope), scope)]
        try:
            update_cn_ticker(conn, scope, verbose=False, data_source=_ds, tushare_token=_tk)
        except Exception:
            pass

    all_bars = {}
    bench = get_bars(conn, BENCHMARK, days + 100)
    for _, sym in symbols:
        b = get_bars(conn, sym, days + 100)
        if len(b) >= 80:
            all_bars[sym] = b
    conn.close()

    if not all_bars:
        return {"error": "无有效数据"}

    combos = []
    for ma in OPTIMIZE_GRID["ma_period"]:
        for bk in OPTIMIZE_GRID["base_k"]:
            for mk in OPTIMIZE_GRID["min_k"]:
                if mk >= bk:
                    continue
                for hs in OPTIMIZE_GRID["hard_stop_pct"]:
                    combos.append({"ma_period": ma, "base_k": bk, "min_k": mk, "hard_stop_pct": hs})

    results = []
    for combo in combos:
        cfg = _deep_merge(DEFAULT_STRATEGY, {
            "entry": {"ma_period": combo["ma_period"]},
            "exit": {"base_k": combo["base_k"], "min_k": combo["min_k"],
                     "hard_stop_pct": combo["hard_stop_pct"]},
        })
        agg_ret, agg_dd, agg_wr, agg_trades = [], [], [], 0
        for sym, bars in all_bars.items():
            bt = RightSideBacktest(bars, bench, config=cfg)
            bt.run()
            if not bt.equity_curve:
                continue
            ret = (bt.equity_curve[-1]["equity"] / bt.init_capital - 1) * 100
            dd = bt.max_dd * 100
            wr = len([t for t in bt.trades if t["pnl_pct"] > 0]) / len(bt.trades) * 100 if bt.trades else 0
            agg_ret.append(ret)
            agg_dd.append(dd)
            agg_wr.append(wr)
            agg_trades += len(bt.trades)

        if not agg_ret:
            continue
        avg_ret = sum(agg_ret) / len(agg_ret)
        avg_dd = sum(agg_dd) / len(agg_dd)
        avg_wr = sum(agg_wr) / len(agg_wr)
        score = _opt_score(avg_ret, avg_dd, avg_wr, agg_trades)

        results.append({
            "params": combo,
            "score": round(score, 1),
            "avg_return": round(avg_ret, 1),
            "avg_drawdown": round(avg_dd, 1),
            "avg_winrate": round(avg_wr, 1),
            "total_trades": agg_trades,
            "etf_count": len(agg_ret),
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    top20 = results[:20]

    output = {
        "scope": scope,
        "days": days,
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "tested": len(combos),
        "valid": len(results),
        "best": top20[0] if top20 else None,
        "rankings": top20,
    }

    _save_optimize_results(output)

    if scope != "global" and top20:
        best_p = top20[0]["params"]
        cur = load_strategy_config()
        per_etf = cur.get("per_etf", {})
        per_etf[scope] = {
            "entry": {"ma_period": best_p["ma_period"]},
            "exit": {
                "base_k": best_p["base_k"],
                "min_k": best_p["min_k"],
                "hard_stop_pct": best_p["hard_stop_pct"],
            },
        }
        cur["per_etf"] = per_etf
        save_strategy_config(cur)

    return output

def _save_optimize_results(result):
    os.makedirs(os.path.dirname(OPTIMIZE_FILE), exist_ok=True)
    try:
        with open(OPTIMIZE_FILE, "r") as f:
            history = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        history = []
    history.insert(0, result)
    history = history[:10]
    with open(OPTIMIZE_FILE, "w") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

def load_optimize_results():
    try:
        with open(OPTIMIZE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def cmd_optimize(scope="global", days=1000):
    print(f"参数优化 | 范围: {scope} | {days}天\n")
    result = run_optimization(scope, days)
    if "error" in result:
        print(f"错误: {result['error']}")
        return

    print(f"测试 {result['tested']} 个组合, 有效 {result['valid']} 个\n")
    if result["best"]:
        b = result["best"]
        p = b["params"]
        print(f"最优参数:")
        print(f"  MA={p['ma_period']}  Base_K={p['base_k']}  Min_K={p['min_k']}  止损={p['hard_stop_pct']*100:.0f}%")
        print(f"  评分={b['score']}  收益={b['avg_return']:+.1f}%  回撤={b['avg_drawdown']:.1f}%  胜率={b['avg_winrate']:.1f}%\n")

    print(f"{'排名':>4s}  {'MA':>3s}  {'K':>8s}  {'止损':>4s}  {'评分':>5s}  {'收益':>7s}  {'回撤':>5s}  {'胜率':>5s}  {'交易':>4s}")
    print(f"  {'-' * 60}")
    for i, r in enumerate(result["rankings"]):
        p = r["params"]
        print(f"  {i+1:2d}.  {p['ma_period']:3d}  {p['base_k']:.1f}/{p['min_k']:.1f}  {p['hard_stop_pct']*100:3.0f}%"
              f"  {r['score']:5.1f}  {r['avg_return']:+6.1f}%  {r['avg_drawdown']:5.1f}%  {r['avg_winrate']:4.1f}%  {r['total_trades']:4d}")


# ============================================================
# 入口
# ============================================================

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return

    cmd = args[0]

    if cmd == "signal":
        if len(args) < 2:
            print("用法: a_etf_trend.py signal <symbol>")
            return
        cmd_signal(args[1])

    elif cmd == "scan":
        cmd_scan()

    elif cmd == "positions":
        cmd_positions()

    elif cmd == "check":
        cmd_check()

    elif cmd == "daily":
        cmd_daily(push_dingtalk="--dingtalk" in args)

    elif cmd == "backtest":
        if len(args) < 2:
            print("用法: a_etf_trend.py backtest <symbol> [--days N]")
            return
        d = 500
        if "--days" in args:
            idx = args.index("--days")
            if idx + 1 < len(args):
                d = int(args[idx + 1])
        cmd_backtest(args[1], d)

    elif cmd == "backtest-all":
        d = 500
        if "--days" in args:
            idx = args.index("--days")
            if idx + 1 < len(args):
                d = int(args[idx + 1])
        cmd_backtest_all(d)

    elif cmd == "calibrate":
        d = 500
        if "--days" in args:
            idx = args.index("--days")
            if idx + 1 < len(args):
                d = int(args[idx + 1])
        cmd_calibrate(d)

    elif cmd == "backtest-env":
        if len(args) < 2:
            print("用法: a_etf_trend.py backtest-env <symbol> [--days N]")
            return
        d = 1000
        if "--days" in args:
            idx = args.index("--days")
            if idx + 1 < len(args):
                d = int(args[idx + 1])
        cmd_backtest_env(args[1], d)

    elif cmd == "backtest-env-all":
        d = 1000
        if "--days" in args:
            idx = args.index("--days")
            if idx + 1 < len(args):
                d = int(args[idx + 1])
        cmd_backtest_env_all(d)

    elif cmd == "param-test":
        sym = args[1] if len(args) >= 2 else "sz159516"
        d = 1000
        if "--days" in args:
            idx = args.index("--days")
            if idx + 1 < len(args):
                d = int(args[idx + 1])
        cmd_param_test(sym, d)

    elif cmd == "shares":
        if len(args) < 2:
            print("用法: a_etf_trend.py shares <symbol>")
            print("  例: a_etf_trend.py shares sz159516")
            return
        cmd_shares(args[1])

    elif cmd == "shares-all":
        cmd_shares_all()

    elif cmd == "optimize":
        scope = "global"
        d = 1000
        for a in args[1:]:
            if a.startswith("--days"):
                continue
            if a.startswith("s") and len(a) > 3:
                scope = a
        if "--days" in args:
            idx = args.index("--days")
            if idx + 1 < len(args):
                d = int(args[idx + 1])
        cmd_optimize(scope, d)

    else:
        print(f"未知命令: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
