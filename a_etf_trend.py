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
"""

import json
import os
import sys
import math
import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from chokepoint_trader import (
    init_db, get_bars,
    sma, ema, calc_rsi, calc_macd, calc_bollinger,
    calc_volume_ratio, calc_atr,
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
HARD_STOP_PCT = 0.08
BASE_K = 3.0
MIN_K = 1.2
CAPITAL_PER_TRADE = 100000

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
# 自适应ATR追踪止损
# ============================================================

def adaptive_K(gain_pct, chg_5d):
    gain_adj = min(gain_pct / 10, 6) * 0.3
    accel_adj = 0.3 if chg_5d > 8 else 0
    return max(MIN_K, BASE_K - gain_adj - accel_adj)


def calc_trailing_stop(entry_price, highest_close, atr_val, chg_5d):
    if atr_val is None or atr_val <= 0:
        return highest_close * 0.95, BASE_K
    gain_pct = max(0, (highest_close - entry_price) / entry_price * 100)
    k = adaptive_K(gain_pct, chg_5d)
    stop = highest_close - k * atr_val
    hard_stop = entry_price * (1 - HARD_STOP_PCT)
    return max(stop, hard_stop), k


# ============================================================
# 右侧入场信号
# ============================================================

def check_entry(bars):
    n = len(bars)
    if n < 60:
        return False, [], {}

    closes = [b["close"] for b in bars]
    volumes = [b["volume"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    i = n - 1
    price = closes[i]

    ma20 = sma(closes, 20)
    ma60 = sma(closes, 60) if n >= 60 else [None] * n
    ma5 = sma(closes, 5)
    macd_line, macd_signal, macd_hist = calc_macd(closes)
    vol_ratio = calc_volume_ratio(volumes, 20)
    rsi = calc_rsi(closes, 14)
    atr = calc_atr(highs, lows, closes, 20)

    conditions = []
    details = []
    extras = {}

    cond1 = ma20[i] is not None and price > ma20[i]
    conditions.append(cond1)
    if cond1:
        pct = (price - ma20[i]) / ma20[i] * 100
        details.append(f"价格>MA20({pct:+.1f}%)")

    cond2 = False
    if ma20[i] is not None and i >= 24:
        ma20_5ago_vals = sma(closes[:i - 4], 20)
        if ma20_5ago_vals and ma20_5ago_vals[-1] is not None:
            cond2 = ma20[i] > ma20_5ago_vals[-1]
    conditions.append(cond2)
    if cond2:
        details.append("MA20上行")

    cond3 = macd_hist[i] is not None and macd_hist[i] > 0
    conditions.append(cond3)
    if cond3:
        details.append(f"MACD柱>0({macd_hist[i]:.4f})")

    cond4 = False
    if i >= 19:
        vol5 = sum(volumes[i - 4:i + 1]) / 5
        vol20 = sum(volumes[max(0, i - 19):i + 1]) / min(20, i + 1)
        if vol20 > 0 and vol5 > vol20:
            cond4 = True
            details.append(f"量能放大({vol5 / vol20:.1f}x)")
        elif vol_ratio[i] is not None and vol_ratio[i] > 1.2:
            chg_today = (closes[i] - closes[i - 1]) / closes[i - 1] * 100
            if chg_today > 0:
                cond4 = True
                details.append(f"放量上涨(量比{vol_ratio[i]:.1f})")
    conditions.append(cond4)

    bonus = []
    if i >= 20 and price >= max(closes[i - 19:i + 1]):
        bonus.append("20日新高")
    if ma60[i] is not None and price > ma60[i]:
        bonus.append("站上MA60")
    if rsi[i] is not None and rsi[i] > 50:
        bonus.append(f"RSI={rsi[i]:.0f}")
    if macd_line[i] is not None and macd_signal[i] is not None:
        prev = i - 1
        if (macd_line[prev] is not None and macd_signal[prev] is not None and
                macd_line[i] > macd_signal[i] and macd_line[prev] <= macd_signal[prev]):
            bonus.append("MACD金叉")

    if bonus:
        details.append("加分:" + "/".join(bonus))

    extras["price"] = price
    extras["ma20"] = ma20[i]
    extras["ma60"] = ma60[i]
    extras["atr"] = atr[i]
    extras["rsi"] = rsi[i]
    extras["macd_hist"] = macd_hist[i]
    extras["vol_ratio"] = vol_ratio[i]
    chg_5d = (price - closes[i - 5]) / closes[i - 5] * 100 if i >= 5 else 0
    extras["chg_5d"] = chg_5d
    extras["chg_1d"] = (price - closes[i - 1]) / closes[i - 1] * 100 if i >= 1 else 0
    extras["chg_20d"] = (price - closes[i - 20]) / closes[i - 20] * 100 if i >= 20 else 0

    entry = all(conditions)
    return entry, details, extras


def check_exit(bars, entry_price, highest_close):
    n = len(bars)
    if n < 20:
        return False, "", 0, BASE_K

    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    i = n - 1
    price = closes[i]

    atr = calc_atr(highs, lows, closes, 20)
    ma20 = sma(closes, 20)

    chg_5d = (price - closes[i - 5]) / closes[i - 5] * 100 if i >= 5 else 0
    new_highest = max(highest_close, price)
    stop, k = calc_trailing_stop(entry_price, new_highest, atr[i], chg_5d)

    if price < stop:
        gain = (price - entry_price) / entry_price * 100
        return True, f"追踪止损(K={k:.1f},stop={stop:.3f},gain={gain:+.1f}%)", stop, k

    hard = entry_price * (1 - HARD_STOP_PCT)
    if price < hard:
        return True, f"硬止损({HARD_STOP_PCT * 100:.0f}%,stop={hard:.3f})", stop, k

    if ma20[i] is not None and n >= 3:
        below_count = sum(1 for j in range(max(0, i - 2), i + 1) if closes[j] < ma20[j] and ma20[j] is not None)
        if below_count >= 3:
            return True, f"连续3日破MA20({ma20[i]:.3f})", stop, k

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
    def __init__(self, bars, benchmark_bars=None, capital=CAPITAL_PER_TRADE):
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

    def run(self):
        warmup = 60
        for i in range(warmup, len(self.bars)):
            bar = self.bars[i]
            price = bar["close"]
            window = self.bars[max(0, i - 249):i + 1]

            if self.position > 0:
                self.highest_close = max(self.highest_close, price)
                should_exit, reason, stop, k = check_exit(
                    window, self.entry_price, self.highest_close
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
                entry, details, extras = check_entry(window)
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
    update_cn_ticker(conn, symbol, verbose=False)
    update_cn_ticker(conn, BENCHMARK, verbose=False)
    bars = get_bars(conn, symbol, 300)
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
    print("更新基准...", end="", flush=True)
    update_cn_ticker(conn, BENCHMARK, verbose=False)
    print(" OK")

    cal = load_calibration()
    results = []
    symbols = [(n, cfg["symbol"], cfg.get("holding", False)) for n, cfg in ETF_BASKET.items()]

    print(f"扫描{len(symbols)}个行业ETF...\n")
    for name, sym, holding in symbols:
        try:
            update_cn_ticker(conn, sym, verbose=False)
        except Exception as e:
            print(f"  {name}: 失败 {e}")
            continue
        bars = get_bars(conn, sym, 300)
        if len(bars) < 60:
            continue
        entry, details, ex = check_entry(bars)
        c = cal.get(sym, {})
        grade = c.get("grade", "-")
        bt_ret = c.get("total_return", None)
        results.append({
            "name": name, "symbol": sym, "holding": holding,
            "entry": entry, "details": details,
            "price": ex["price"], "chg_5d": ex["chg_5d"],
            "chg_1d": ex["chg_1d"], "rsi": ex.get("rsi"),
            "atr": ex.get("atr"),
            "grade": grade, "bt_ret": bt_ret,
        })

    conn.close()

    good_entries = [r for r in results if r["entry"] and r["grade"] in ("S", "A", "B", "-")]
    bad_entries = [r for r in results if r["entry"] and r["grade"] in ("C", "D")]
    others = [r for r in results if not r["entry"]]
    good_entries.sort(key=lambda x: -x["chg_5d"])
    others.sort(key=lambda x: -x["chg_5d"])

    if good_entries:
        print(f">>> 入场信号 ({len(good_entries)}个) <<<")
        print(f"{'行业':<8s} {'评级':>2s} {'价格':>7s} {'1日':>6s} {'5日':>6s}  因子")
        print("-" * 70)
        for r in good_entries:
            m = "*" if r["holding"] else " "
            det = " | ".join(r["details"][:3])
            bt = f"(回测+{r['bt_ret']:.0f}%)" if r["bt_ret"] is not None else ""
            print(f"{m}{r['name']:<7s} [{r['grade']}] {r['price']:7.3f} {r['chg_1d']:+5.1f}% {r['chg_5d']:+5.1f}%  {det} {bt}")
        print()

    if bad_entries:
        print(f"--- 信号已过滤 ({len(bad_entries)}个, 回测不适合趋势交易) ---")
        for r in bad_entries:
            bt = f"回测{r['bt_ret']:+.0f}%" if r["bt_ret"] is not None else ""
            print(f"  {r['name']:<7s} [{r['grade']}] {r['price']:7.3f} {bt} — 不推荐")
        print()

    print(f"未触发 ({len(others)}个):")
    print(f"{'行业':<8s} {'评级':>2s} {'价格':>7s} {'1日':>6s} {'5日':>6s}")
    print("-" * 45)
    for r in others:
        m = "*" if r["holding"] else " "
        print(f"{m}{r['name']:<7s} [{r['grade']}] {r['price']:7.3f} {r['chg_1d']:+5.1f}% {r['chg_5d']:+5.1f}%")


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
    alerts = []

    for sym, p in pos.items():
        try:
            update_cn_ticker(conn, sym, verbose=False)
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
    update_cn_ticker(conn, BENCHMARK, verbose=False)

    data = load_positions()
    pos = data.get("positions", {})
    lines = [f"### ETF右侧趋势 ({now.strftime('%m/%d %H:%M')})", ""]

    exit_alerts = []
    for sym in list(pos.keys()):
        p = pos[sym]
        try:
            update_cn_ticker(conn, sym, verbose=False)
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
            update_cn_ticker(conn, sym, verbose=False)
        except Exception:
            continue
        bars = get_bars(conn, sym, 300)
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
    print(f"拉取 {symbol}...", end="", flush=True)
    update_cn_ticker(conn, symbol, verbose=False)
    update_cn_ticker(conn, BENCHMARK, verbose=False)
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
    print("更新基准...", end="", flush=True)
    update_cn_ticker(conn, BENCHMARK, verbose=False)
    print(" OK\n")

    symbols = [(n, cfg["symbol"], cfg.get("holding", False)) for n, cfg in ETF_BASKET.items()]
    for _, sym, _ in symbols:
        try:
            update_cn_ticker(conn, sym, verbose=False)
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

    else:
        print(f"未知命令: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
