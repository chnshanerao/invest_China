#!/usr/bin/env python3
"""
A股趋势交易系统 — 信号评分 + 回测引擎

核心理念: A股看趋势/资金/动量/政策, 不看PE估值
5因子评分: 趋势强度(30) + 动量(25) + 量价(20) + 突破(15) + 相对强度(10) = 0~100

用法:
  python3 a_trend_trader.py signal sz159516          # 单标的信号详情
  python3 a_trend_trader.py scan                     # 扫描全部行业ETF
  python3 a_trend_trader.py backtest sz159516        # 回测500天
  python3 a_trend_trader.py backtest sz159516 --days 300
  python3 a_trend_trader.py backtest-all             # 批量回测20个行业ETF
  python3 a_trend_trader.py fetch sz159516           # 仅拉取数据
"""

import urllib.request
import json
import re
import os
import sys
import sqlite3
import ssl
import math
import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from chokepoint_trader import (
    init_db, get_bars,
    sma, ema, calc_rsi, calc_macd, calc_bollinger,
    calc_volume_ratio, calc_atr,
)
from a_sector_scanner import SECTOR_ETFS

BENCHMARK_SYMBOL = "sh000001"

# ============================================================
# 数据获取 — 腾讯财经A股前复权日K
# ============================================================

def _ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def fetch_tencent_cn_kline(symbol, days=500):
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,,,{days},qfq"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    })
    ctx = _ssl_ctx()
    resp = urllib.request.urlopen(req, context=ctx, timeout=15)
    raw = resp.read().decode("utf-8", errors="replace")
    data = json.loads(raw)

    if data.get("code") != 0 or not data.get("data"):
        return []

    stock_data = data["data"].get(symbol, {})
    klines = stock_data.get("day") or stock_data.get("qfqday") or []
    if not klines:
        return []

    bars = []
    for k in klines:
        if len(k) < 5:
            continue
        bars.append({
            "date": k[0],
            "open": float(k[1]),
            "close": float(k[2]),
            "high": float(k[3]),
            "low": float(k[4]),
            "volume": int(float(k[5])) if len(k) > 5 else 0,
        })
    return bars


def update_cn_ticker(conn, symbol, verbose=False):
    cursor = conn.execute(
        "SELECT MAX(date) FROM daily_bars WHERE ticker=?", (symbol,)
    )
    last_date = cursor.fetchone()[0]

    bars = fetch_tencent_cn_kline(symbol, 500)
    if not bars:
        if verbose:
            print(f"  {symbol}: 无数据")
        return 0

    new_count = 0
    for b in bars:
        if last_date and b["date"] <= last_date:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO daily_bars VALUES (?,?,?,?,?,?,?)",
            (symbol, b["date"], b["open"], b["high"], b["low"], b["close"], b["volume"]),
        )
        new_count += 1

    if new_count == 0 and not last_date:
        for b in bars:
            conn.execute(
                "INSERT OR REPLACE INTO daily_bars VALUES (?,?,?,?,?,?,?)",
                (symbol, b["date"], b["open"], b["high"], b["low"], b["close"], b["volume"]),
            )
            new_count += 1

    conn.commit()
    if verbose:
        print(f"  {symbol}: +{new_count} bars (latest {bars[-1]['date']})")
    return new_count


# ============================================================
# 趋势信号评分 — 5因子 0~100
# ============================================================

def compute_trend_score(bars, benchmark_bars=None):
    n = len(bars)
    if n < 60:
        return 0, [], "NO_DATA"

    closes = [b["close"] for b in bars]
    volumes = [b["volume"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]

    ma5 = sma(closes, 5)
    ma10 = sma(closes, 10)
    ma20 = sma(closes, 20)
    ma60 = sma(closes, 60)

    rsi = calc_rsi(closes, 14)
    macd_line, macd_signal, macd_hist = calc_macd(closes)
    bb_upper, bb_lower = calc_bollinger(closes, 20, 2)
    vol_ratio = calc_volume_ratio(volumes, 20)

    i = n - 1
    prev = i - 1
    price = closes[i]

    score = 0
    details = []

    # ---- 趋势强度 (30分) ----
    trend_score = 0

    if all(v is not None for v in [ma5[i], ma10[i], ma20[i], ma60[i]]):
        if ma5[i] > ma10[i] > ma20[i] > ma60[i]:
            trend_score += 15
            details.append("完美多头排列+15")
        elif ma5[i] > ma10[i] > ma20[i]:
            trend_score += 10
            details.append("三线多头+10")
        elif ma5[i] > ma10[i]:
            trend_score += 5
            details.append("短期多头+5")

    if ma20[i] is not None and price > ma20[i]:
        trend_score += 8
    if ma60[i] is not None and price > ma60[i]:
        trend_score += 7

    if ma20[i] is not None and ma20[prev] is not None and i >= 25:
        ma20_5ago = sma(closes[:i-4], 20)
        if ma20_5ago and ma20_5ago[-1] is not None and ma20[i] > ma20_5ago[-1]:
            trend_score += 5
            details.append("MA20上行+5")

    trend_score = min(trend_score, 30)
    score += trend_score

    # ---- 动量加速 (25分) ----
    momentum_score = 0

    if macd_hist[i] is not None:
        if macd_hist[i] > 0:
            if macd_hist[prev] is not None and macd_hist[i] > macd_hist[prev]:
                momentum_score += 8
                details.append("MACD柱放大+8")
            else:
                momentum_score += 4
        if (macd_line[i] is not None and macd_signal[i] is not None and
                macd_line[prev] is not None and macd_signal[prev] is not None):
            if macd_line[i] > macd_signal[i] and macd_line[prev] <= macd_signal[prev]:
                momentum_score += 5
                details.append("MACD金叉+5")

    if rsi[i] is not None:
        if 50 <= rsi[i] < 70:
            momentum_score += 5
        elif 70 <= rsi[i] < 80:
            momentum_score += 4
            details.append(f"RSI超强{rsi[i]:.0f}")
        elif rsi[i] >= 80:
            momentum_score += 3
            details.append(f"RSI过热{rsi[i]:.0f}")

    chg_5d = (price - closes[i-5]) / closes[i-5] * 100 if i >= 5 else 0
    chg_10d = (price - closes[i-10]) / closes[i-10] * 100 if i >= 10 else 0
    if chg_5d > 3:
        momentum_score += 5
        details.append(f"5日+{chg_5d:.1f}%")
    if chg_10d > 5:
        momentum_score += 3

    momentum_score = min(momentum_score, 25)
    score += momentum_score

    # ---- 量价配合 (20分) ----
    volume_score = 0

    if vol_ratio[i] is not None:
        chg_today = (closes[i] - closes[prev]) / closes[prev] * 100
        if vol_ratio[i] > 1.5 and chg_today > 0:
            volume_score += 10
            details.append(f"放量上涨(量比{vol_ratio[i]:.1f})+10")
        elif vol_ratio[i] < 0.8 and -1 < chg_today < 0:
            volume_score += 5
            details.append("缩量回调+5")

    vol5 = sum(volumes[i-4:i+1]) / 5 if i >= 4 else 0
    vol20 = sum(volumes[max(0,i-19):i+1]) / min(20, i+1) if i >= 0 else 0
    if vol20 > 0 and vol5 > vol20:
        volume_score += 5

    volume_score = min(volume_score, 20)
    score += volume_score

    # ---- 突破形态 (15分) ----
    breakout_score = 0

    high_20 = max(closes[max(0,i-19):i+1])
    if price >= high_20:
        breakout_score += 8
        details.append("20日新高+8")

    if i >= 60:
        high_60 = max(closes[i-59:i+1])
        if price >= high_60:
            breakout_score += 7
            details.append("60日新高+7")

    bb_mid = ma20[i]
    if bb_upper[i] is not None and price > bb_upper[i]:
        breakout_score += 5
    elif bb_mid is not None and price > bb_mid:
        breakout_score += 3

    breakout_score = min(breakout_score, 15)
    score += breakout_score

    # ---- 相对强度 (10分) ----
    rs_score = 0

    if benchmark_bars and len(benchmark_bars) >= n:
        bench_closes = [b["close"] for b in benchmark_bars[-n:]]
        if len(bench_closes) >= i + 1:
            bench_i = len(bench_closes) - 1
            if bench_i >= 5 and bench_closes[bench_i-5] > 0 and closes[i-5] > 0:
                stock_5d = (price - closes[i-5]) / closes[i-5] * 100
                bench_5d = (bench_closes[bench_i] - bench_closes[bench_i-5]) / bench_closes[bench_i-5] * 100
                if stock_5d > bench_5d:
                    rs_score += 5
                    details.append(f"5日超额+{stock_5d-bench_5d:.1f}%")

            if bench_i >= 20 and bench_closes[bench_i-20] > 0 and closes[i-20] > 0:
                stock_20d = (price - closes[i-20]) / closes[i-20] * 100
                bench_20d = (bench_closes[bench_i] - bench_closes[bench_i-20]) / bench_closes[bench_i-20] * 100
                if stock_20d > bench_20d:
                    rs_score += 5

    rs_score = min(rs_score, 10)
    score += rs_score

    score = max(0, min(100, score))

    if score >= 80:
        signal = "STRONG_TREND"
    elif score >= 65:
        signal = "TREND_CONFIRMED"
    elif score >= 50:
        signal = "TREND_EMERGING"
    elif score >= 35:
        signal = "NEUTRAL"
    else:
        signal = "TREND_WEAK"

    return score, details, signal


# ============================================================
# 回测引擎
# ============================================================

class BacktestEngine:
    ENTRY_THRESHOLD = 65
    EXIT_THRESHOLD = 35
    STOP_LOSS_PCT = 0.08
    BATCH_COUNT = 2

    def __init__(self, bars, benchmark_bars=None, init_capital=100000):
        self.bars = bars
        self.benchmark_bars = benchmark_bars
        self.init_capital = init_capital
        self.capital = init_capital
        self.position = 0
        self.entry_price = 0
        self.entry_date = ""
        self.batch = 0
        self.trades = []
        self.equity_curve = []
        self.daily_scores = []
        self.peak = init_capital
        self.max_drawdown = 0
        self.dd_peak_date = ""
        self.dd_trough_date = ""
        self.below_ma20_days = 0
        self.prev_score = 0

    def _get_bench_window(self, end_idx, window_size):
        if not self.benchmark_bars:
            return None
        offset = len(self.bars) - len(self.benchmark_bars)
        bench_end = end_idx - offset
        if bench_end < window_size:
            return None
        return self.benchmark_bars[max(0, bench_end - window_size + 1):bench_end + 1]

    def run(self):
        warmup = 60
        for i in range(warmup, len(self.bars)):
            bar = self.bars[i]
            price = bar["close"]
            window = self.bars[max(0, i - 249):i + 1]
            bench_window = self._get_bench_window(i, len(window))
            score, details, signal = compute_trend_score(window, bench_window)

            closes_w = [b["close"] for b in window]
            ma20_val = sma(closes_w, 20)[-1] if len(closes_w) >= 20 else None
            ma5_val = sma(closes_w, 5)[-1] if len(closes_w) >= 5 else None
            ma20_prev = sma(closes_w[:-1], 20)[-1] if len(closes_w) >= 21 else None
            ma5_prev = sma(closes_w[:-1], 5)[-1] if len(closes_w) >= 6 else None

            if self.position == 0:
                if score >= self.ENTRY_THRESHOLD and self.prev_score < self.ENTRY_THRESHOLD:
                    shares_to_buy = int(self.capital * 0.5 / price) if self.batch == 0 else int(self.capital / price)
                    if shares_to_buy > 0:
                        cost = shares_to_buy * price
                        self.capital -= cost
                        self.position = shares_to_buy
                        self.entry_price = price
                        self.entry_date = bar["date"]
                        self.batch = 1
                        self.below_ma20_days = 0

            elif self.position > 0 and self.batch == 1:
                if score >= self.ENTRY_THRESHOLD:
                    shares_to_buy = int(self.capital / price)
                    if shares_to_buy > 0:
                        total_cost = self.entry_price * self.position + shares_to_buy * price
                        self.position += shares_to_buy
                        self.capital -= shares_to_buy * price
                        self.entry_price = total_cost / self.position
                        self.batch = 2

            if self.position > 0:
                should_exit = False
                exit_reason = ""

                if score < self.EXIT_THRESHOLD and self.prev_score >= self.EXIT_THRESHOLD:
                    should_exit = True
                    exit_reason = f"趋势转弱(score={score})"

                if (ma5_val is not None and ma20_val is not None and
                        ma5_prev is not None and ma20_prev is not None):
                    if ma5_val < ma20_val and ma5_prev >= ma20_prev:
                        should_exit = True
                        exit_reason = "MA5死叉MA20"

                if ma20_val is not None:
                    if price < ma20_val:
                        self.below_ma20_days += 1
                    else:
                        self.below_ma20_days = 0
                    if self.below_ma20_days >= 3:
                        should_exit = True
                        exit_reason = "连续3日收于MA20下方"

                drawdown = (price - self.entry_price) / self.entry_price
                if drawdown <= -self.STOP_LOSS_PCT:
                    should_exit = True
                    exit_reason = f"止损({drawdown*100:.1f}%)"

                if should_exit:
                    proceeds = self.position * price
                    pnl = proceeds - self.position * self.entry_price
                    pnl_pct = (price - self.entry_price) / self.entry_price * 100
                    hold_days = self._date_diff(self.entry_date, bar["date"])
                    self.trades.append({
                        "entry_date": self.entry_date,
                        "exit_date": bar["date"],
                        "entry_price": self.entry_price,
                        "exit_price": price,
                        "shares": self.position,
                        "pnl": pnl,
                        "pnl_pct": pnl_pct,
                        "hold_days": hold_days,
                        "exit_reason": exit_reason,
                    })
                    self.capital += proceeds
                    self.position = 0
                    self.entry_price = 0
                    self.batch = 0
                    self.below_ma20_days = 0

            equity = self.capital + self.position * price
            self.equity_curve.append({
                "date": bar["date"],
                "equity": equity,
                "price": price,
                "score": score,
            })
            self.daily_scores.append(score)

            if equity > self.peak:
                self.peak = equity
                self.dd_peak_date = bar["date"]
            dd = (self.peak - equity) / self.peak
            if dd > self.max_drawdown:
                self.max_drawdown = dd
                self.dd_trough_date = bar["date"]

            self.prev_score = score

        if self.position > 0:
            last_price = self.bars[-1]["close"]
            pnl_pct = (last_price - self.entry_price) / self.entry_price * 100
            self.trades.append({
                "entry_date": self.entry_date,
                "exit_date": self.bars[-1]["date"] + "(持仓中)",
                "entry_price": self.entry_price,
                "exit_price": last_price,
                "shares": self.position,
                "pnl": self.position * (last_price - self.entry_price),
                "pnl_pct": pnl_pct,
                "hold_days": self._date_diff(self.entry_date, self.bars[-1]["date"]),
                "exit_reason": "持仓中",
            })

    def _date_diff(self, d1, d2):
        try:
            dt1 = datetime.date.fromisoformat(d1)
            dt2 = datetime.date.fromisoformat(d2[:10])
            return (dt2 - dt1).days
        except (ValueError, TypeError):
            return 0

    def report(self):
        if not self.equity_curve:
            print("  无回测数据")
            return {}

        final_equity = self.equity_curve[-1]["equity"]
        total_return = (final_equity - self.init_capital) / self.init_capital * 100

        start_date = self.equity_curve[0]["date"]
        end_date = self.equity_curve[-1]["date"]
        trading_days = len(self.equity_curve)
        years = trading_days / 244
        annual_return = ((final_equity / self.init_capital) ** (1 / years) - 1) * 100 if years > 0 else 0

        bench_return = None
        if self.benchmark_bars and len(self.benchmark_bars) >= 2:
            b_start = self.benchmark_bars[0]["close"]
            b_end = self.benchmark_bars[-1]["close"]
            if b_start > 0:
                bench_return = (b_end - b_start) / b_start * 100

        daily_returns = []
        for j in range(1, len(self.equity_curve)):
            prev_eq = self.equity_curve[j-1]["equity"]
            if prev_eq > 0:
                daily_returns.append((self.equity_curve[j]["equity"] - prev_eq) / prev_eq)
        avg_dr = sum(daily_returns) / len(daily_returns) if daily_returns else 0
        std_dr = math.sqrt(sum((r - avg_dr) ** 2 for r in daily_returns) / len(daily_returns)) if len(daily_returns) > 1 else 1
        sharpe = (avg_dr / std_dr) * math.sqrt(244) if std_dr > 0 else 0

        wins = [t for t in self.trades if t["pnl"] > 0]
        losses = [t for t in self.trades if t["pnl"] <= 0]
        win_rate = len(wins) / len(self.trades) * 100 if self.trades else 0
        avg_win = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
        profit_factor = (sum(t["pnl"] for t in wins) / abs(sum(t["pnl"] for t in losses))) if losses and sum(t["pnl"] for t in losses) != 0 else float('inf')
        avg_hold = sum(t["hold_days"] for t in self.trades) / len(self.trades) if self.trades else 0

        print(f"\n{'='*60}")
        print(f"回测报告 | {start_date} ~ {end_date} ({trading_days}个交易日)")
        print(f"{'='*60}")
        print(f"  总收益:     {total_return:+.1f}%")
        print(f"  年化收益:   {annual_return:+.1f}%")
        if bench_return is not None:
            print(f"  基准收益:   {bench_return:+.1f}% (上证指数)")
            print(f"  超额收益:   {total_return - bench_return:+.1f}%")
        print(f"  最大回撤:   {self.max_drawdown*100:.1f}%")
        print(f"  Sharpe:     {sharpe:.2f}")
        print()
        print(f"  交易次数:   {len(self.trades)}")
        print(f"  胜率:       {win_rate:.0f}% ({len(wins)}胜/{len(losses)}负)")
        print(f"  平均盈利:   {avg_win:+.1f}%")
        print(f"  平均亏损:   {avg_loss:+.1f}%")
        print(f"  盈亏比:     {abs(avg_win/avg_loss):.2f}" if avg_loss != 0 else "  盈亏比:     N/A")
        print(f"  Profit Factor: {profit_factor:.2f}" if profit_factor != float('inf') else "  Profit Factor: ∞")
        print(f"  平均持仓:   {avg_hold:.0f}天")
        print(f"  最终净值:   {final_equity:.0f} (初始{self.init_capital})")

        if self.trades:
            print(f"\n  {'入场':>10s}  {'出场':>10s}  {'入价':>7s}  {'出价':>7s}  {'收益%':>7s}  {'天数':>4s}  原因")
            print(f"  {'-'*70}")
            for t in self.trades:
                print(f"  {t['entry_date']:>10s}  {t['exit_date'][:10]:>10s}  "
                      f"{t['entry_price']:7.3f}  {t['exit_price']:7.3f}  "
                      f"{t['pnl_pct']:+6.1f}%  {t['hold_days']:4d}  {t['exit_reason']}")

        return {
            "total_return": total_return,
            "annual_return": annual_return,
            "max_drawdown": self.max_drawdown * 100,
            "sharpe": sharpe,
            "trades": len(self.trades),
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "avg_hold_days": avg_hold,
        }


# ============================================================
# CLI命令
# ============================================================

def cmd_fetch(symbol, verbose=True):
    conn = init_db()
    if verbose:
        print(f"拉取 {symbol} K线数据...")
    update_cn_ticker(conn, symbol, verbose=verbose)
    bars = get_bars(conn, symbol, 500)
    conn.close()
    if verbose:
        print(f"  DB中共 {len(bars)} 条记录")
        if bars:
            print(f"  区间: {bars[0]['date']} ~ {bars[-1]['date']}")
    return bars


def cmd_signal(symbol, verbose=True):
    conn = init_db()
    update_cn_ticker(conn, symbol, verbose=False)
    update_cn_ticker(conn, BENCHMARK_SYMBOL, verbose=False)
    bars = get_bars(conn, symbol, 300)
    bench_bars = get_bars(conn, BENCHMARK_SYMBOL, 300)
    conn.close()

    if len(bars) < 60:
        print(f"{symbol}: 数据不足 ({len(bars)}条, 需60+)")
        return

    score, details, signal = compute_trend_score(bars, bench_bars)

    closes = [b["close"] for b in bars]
    price = closes[-1]
    chg_1d = (closes[-1] - closes[-2]) / closes[-2] * 100 if len(closes) >= 2 else 0
    chg_5d = (price - closes[-6]) / closes[-6] * 100 if len(closes) >= 6 else 0
    chg_20d = (price - closes[-21]) / closes[-21] * 100 if len(closes) >= 21 else 0

    name = ""
    for n, cfg in SECTOR_ETFS.items():
        if cfg["symbol"] == symbol:
            name = n
            break

    SIGNAL_LABELS = {
        "STRONG_TREND": "强趋势",
        "TREND_CONFIRMED": "趋势确认",
        "TREND_EMERGING": "趋势初现",
        "NEUTRAL": "震荡",
        "TREND_WEAK": "弱势",
        "NO_DATA": "数据不足",
    }

    if verbose:
        label = f"{name}({symbol})" if name else symbol
        print(f"\n{'='*50}")
        print(f"  {label}  {price:.3f}")
        print(f"{'='*50}")
        print(f"  信号: {SIGNAL_LABELS.get(signal, signal)} (score={score})")
        print(f"  涨幅: 1日{chg_1d:+.2f}% | 5日{chg_5d:+.1f}% | 20日{chg_20d:+.1f}%")
        print(f"  日期: {bars[-1]['date']}")
        if details:
            print(f"  因子: {' | '.join(details)}")
        print()

    return score, signal, details


def cmd_scan(verbose=True):
    conn = init_db()
    if verbose:
        print("拉取基准(上证指数)...")
    update_cn_ticker(conn, BENCHMARK_SYMBOL, verbose=False)
    bench_bars = get_bars(conn, BENCHMARK_SYMBOL, 300)

    results = []
    symbols = [(name, cfg["symbol"]) for name, cfg in SECTOR_ETFS.items()]

    if verbose:
        print(f"拉取{len(symbols)}个行业ETF数据...\n")

    for name, symbol in symbols:
        try:
            update_cn_ticker(conn, symbol, verbose=False)
        except Exception as e:
            if verbose:
                print(f"  {name}({symbol}): 获取失败 {e}")
            continue

        bars = get_bars(conn, symbol, 300)
        if len(bars) < 60:
            if verbose:
                print(f"  {name}: 数据不足({len(bars)})")
            continue

        score, details, signal = compute_trend_score(bars, bench_bars)
        price = bars[-1]["close"]
        closes = [b["close"] for b in bars]
        chg_5d = (price - closes[-6]) / closes[-6] * 100 if len(closes) >= 6 else 0
        is_holding = SECTOR_ETFS[name].get("holding", False)

        results.append({
            "name": name,
            "symbol": symbol,
            "price": price,
            "score": score,
            "signal": signal,
            "chg_5d": chg_5d,
            "details": details,
            "holding": is_holding,
        })

    conn.close()

    results.sort(key=lambda x: -x["score"])

    SIGNAL_LABELS = {
        "STRONG_TREND": "强趋势",
        "TREND_CONFIRMED": "趋势确认",
        "TREND_EMERGING": "趋势初现",
        "NEUTRAL": "震荡",
        "TREND_WEAK": "弱势",
    }

    if verbose:
        print(f"{'行业':<8s} {'价格':>7s} {'分数':>4s} {'信号':<6s} {'5日涨幅':>7s}  因子")
        print("-" * 75)
        for r in results:
            marker = "*" if r["holding"] else " "
            sig_cn = SIGNAL_LABELS.get(r["signal"], r["signal"])
            det = " | ".join(r["details"][:3])
            print(f"{marker}{r['name']:<7s} {r['price']:7.3f} {r['score']:4d} {sig_cn:<6s} {r['chg_5d']:+6.1f}%  {det}")

        strong = [r for r in results if r["signal"] == "STRONG_TREND"]
        confirmed = [r for r in results if r["signal"] == "TREND_CONFIRMED"]
        emerging = [r for r in results if r["signal"] == "TREND_EMERGING"]
        weak = [r for r in results if r["signal"] == "TREND_WEAK"]

        print(f"\n强趋势({len(strong)}): {', '.join(r['name'] for r in strong) or '无'}")
        print(f"趋势确认({len(confirmed)}): {', '.join(r['name'] for r in confirmed) or '无'}")
        print(f"趋势初现({len(emerging)}): {', '.join(r['name'] for r in emerging) or '无'}")
        print(f"弱势({len(weak)}): {', '.join(r['name'] for r in weak) or '无'}")

    return results


def cmd_backtest(symbol, days=500, verbose=True):
    conn = init_db()
    if verbose:
        print(f"拉取 {symbol} 数据...")
    update_cn_ticker(conn, symbol, verbose=verbose)
    update_cn_ticker(conn, BENCHMARK_SYMBOL, verbose=False)

    bars = get_bars(conn, symbol, days + 100)
    bench_bars = get_bars(conn, BENCHMARK_SYMBOL, days + 100)
    conn.close()

    if len(bars) < 80:
        print(f"{symbol}: 数据不足 ({len(bars)}条)")
        return None

    name = ""
    for n, cfg in SECTOR_ETFS.items():
        if cfg["symbol"] == symbol:
            name = n
            break

    if verbose:
        label = f"{name}({symbol})" if name else symbol
        print(f"\n回测: {label} 共{len(bars)}条K线")

    engine = BacktestEngine(bars, bench_bars)
    engine.run()
    metrics = engine.report()
    return metrics


def cmd_backtest_all(days=500, verbose=True):
    conn = init_db()
    print(f"拉取基准(上证指数)...\n")
    update_cn_ticker(conn, BENCHMARK_SYMBOL, verbose=False)

    all_results = []
    symbols = [(name, cfg["symbol"]) for name, cfg in SECTOR_ETFS.items()]

    for name, symbol in symbols:
        try:
            update_cn_ticker(conn, symbol, verbose=False)
        except Exception as e:
            print(f"  {name}: 获取失败 {e}")
            continue

    conn_read = init_db()
    bench_bars = get_bars(conn_read, BENCHMARK_SYMBOL, days + 100)

    for name, symbol in symbols:
        bars = get_bars(conn_read, symbol, days + 100)
        if len(bars) < 80:
            print(f"  {name}({symbol}): 数据不足({len(bars)})")
            continue

        engine = BacktestEngine(bars, bench_bars)
        engine.run()

        if not engine.equity_curve:
            continue

        final_eq = engine.equity_curve[-1]["equity"]
        total_ret = (final_eq - engine.init_capital) / engine.init_capital * 100

        wins = [t for t in engine.trades if t["pnl"] > 0]
        wr = len(wins) / len(engine.trades) * 100 if engine.trades else 0

        all_results.append({
            "name": name,
            "symbol": symbol,
            "total_return": total_ret,
            "max_drawdown": engine.max_drawdown * 100,
            "trades": len(engine.trades),
            "win_rate": wr,
            "holding": SECTOR_ETFS[name].get("holding", False),
        })

    conn.close()
    conn_read.close()

    all_results.sort(key=lambda x: -x["total_return"])

    print(f"\n{'='*70}")
    print(f"批量回测汇总 | 近{days}天 | {len(all_results)}个行业ETF")
    print(f"{'='*70}")
    print(f"{'行业':<8s} {'总收益':>7s} {'最大回撤':>8s} {'交易次数':>6s} {'胜率':>5s}")
    print("-" * 45)
    for r in all_results:
        marker = "*" if r["holding"] else " "
        print(f"{marker}{r['name']:<7s} {r['total_return']:+6.1f}% {r['max_drawdown']:7.1f}% {r['trades']:6d} {r['win_rate']:4.0f}%")

    if all_results:
        avg_ret = sum(r["total_return"] for r in all_results) / len(all_results)
        pos = sum(1 for r in all_results if r["total_return"] > 0)
        print(f"\n平均收益: {avg_ret:+.1f}% | 盈利比例: {pos}/{len(all_results)}")

    return all_results


# ============================================================
# 入口
# ============================================================

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return

    cmd = args[0]

    if cmd == "fetch":
        if len(args) < 2:
            print("用法: python3 a_trend_trader.py fetch <symbol>")
            return
        cmd_fetch(args[1])

    elif cmd == "signal":
        if len(args) < 2:
            print("用法: python3 a_trend_trader.py signal <symbol>")
            return
        cmd_signal(args[1])

    elif cmd == "scan":
        cmd_scan()

    elif cmd == "backtest":
        if len(args) < 2:
            print("用法: python3 a_trend_trader.py backtest <symbol> [--days N]")
            return
        symbol = args[1]
        days = 500
        if "--days" in args:
            idx = args.index("--days")
            if idx + 1 < len(args):
                days = int(args[idx + 1])
        cmd_backtest(symbol, days)

    elif cmd == "backtest-all":
        days = 500
        if "--days" in args:
            idx = args.index("--days")
            if idx + 1 < len(args):
                days = int(args[idx + 1])
        cmd_backtest_all(days)

    else:
        print(f"未知命令: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
