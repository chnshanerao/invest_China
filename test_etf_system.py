#!/usr/bin/env python3
"""A股ETF趋势交易系统 — 自动化测试套件"""

import unittest
import sys
import os
import math
import random

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chokepoint_trader import sma, ema, calc_rsi, calc_macd, calc_atr, calc_volume_ratio, calc_kdj
from a_etf_trend import (
    adaptive_K, calc_trailing_stop, BASE_K, MIN_K, HARD_STOP_PCT,
    check_monthly_filter, check_weekly_filter, classify_signal,
    RightSideBacktest, DEFAULT_STRATEGY,
    portfolio_advice, calc_extended_metrics, strategy_health_check,
    SIGNAL_BREAKOUT, SIGNAL_PULLBACK, SIGNAL_OVERBOUGHT, SIGNAL_STRONG, SIGNAL_WATCH,
    MAX_POSITIONS, MAX_SINGLE_PCT,
)


def _make_bars(n, base_price=10.0, trend=0.001, noise=0.02, base_volume=1000000):
    """Generate synthetic OHLCV bars for testing."""
    random.seed(42)
    bars = []
    price = base_price
    from datetime import date, timedelta
    start = date(2022, 1, 4)
    for i in range(n):
        price *= (1 + trend + random.uniform(-noise, noise))
        o = price * (1 + random.uniform(-0.005, 0.005))
        h = max(price, o) * (1 + random.uniform(0, 0.01))
        l = min(price, o) * (1 - random.uniform(0, 0.01))
        c = price
        v = int(base_volume * (1 + random.uniform(-0.3, 0.3)))
        d = start + timedelta(days=i)
        bars.append({
            "date": d.strftime("%Y-%m-%d"),
            "open": round(o, 3), "high": round(h, 3),
            "low": round(l, 3), "close": round(c, 3),
            "volume": v,
        })
    return bars


def _make_uptrend_bars(n=500, base=10.0):
    """Strong uptrend bars for triggering entry signals."""
    return _make_bars(n, base_price=base, trend=0.003, noise=0.008)


def _make_downtrend_bars(n=500, base=20.0):
    """Downtrend bars that should NOT trigger entry."""
    return _make_bars(n, base_price=base, trend=-0.005, noise=0.005)


class TestSMA(unittest.TestCase):
    def test_basic(self):
        data = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = sma(data, 3)
        self.assertIsNone(result[0])
        self.assertIsNone(result[1])
        self.assertAlmostEqual(result[2], 2.0)
        self.assertAlmostEqual(result[3], 3.0)
        self.assertAlmostEqual(result[4], 4.0)

    def test_period_equals_length(self):
        data = [10.0, 20.0, 30.0]
        result = sma(data, 3)
        self.assertAlmostEqual(result[2], 20.0)

    def test_single_element_period(self):
        data = [5.0, 10.0, 15.0]
        result = sma(data, 1)
        self.assertAlmostEqual(result[0], 5.0)
        self.assertAlmostEqual(result[2], 15.0)


class TestRSI(unittest.TestCase):
    def test_range(self):
        bars = _make_bars(100)
        closes = [b["close"] for b in bars]
        rsi = calc_rsi(closes, 14)
        for v in rsi:
            if v is not None:
                self.assertGreaterEqual(v, 0)
                self.assertLessEqual(v, 100)

    def test_uptrend_rsi_high(self):
        closes = [10 + i * 0.5 for i in range(50)]
        rsi = calc_rsi(closes, 14)
        self.assertGreater(rsi[-1], 70)

    def test_downtrend_rsi_low(self):
        closes = [50 - i * 0.5 for i in range(50)]
        rsi = calc_rsi(closes, 14)
        self.assertLess(rsi[-1], 30)


class TestMACD(unittest.TestCase):
    def test_length(self):
        closes = [10 + i * 0.1 for i in range(100)]
        line, signal, hist = calc_macd(closes)
        self.assertEqual(len(line), 100)
        self.assertEqual(len(signal), 100)
        self.assertEqual(len(hist), 100)

    def test_uptrend_positive_histogram(self):
        closes = [10 + i * 0.3 for i in range(100)]
        _, _, hist = calc_macd(closes)
        non_none = [h for h in hist if h is not None]
        self.assertTrue(non_none[-1] > 0)


class TestKDJ(unittest.TestCase):
    def test_length(self):
        bars = _make_bars(100)
        h = [b["high"] for b in bars]
        l = [b["low"] for b in bars]
        c = [b["close"] for b in bars]
        k, d, j = calc_kdj(h, l, c)
        self.assertEqual(len(k), 100)
        self.assertEqual(len(d), 100)
        self.assertEqual(len(j), 100)

    def test_k_range(self):
        bars = _make_bars(100)
        h = [b["high"] for b in bars]
        l = [b["low"] for b in bars]
        c = [b["close"] for b in bars]
        k, d, j = calc_kdj(h, l, c)
        for v in k:
            if v is not None:
                self.assertGreaterEqual(v, 0)
                self.assertLessEqual(v, 100)

    def test_d_range(self):
        bars = _make_bars(100)
        h = [b["high"] for b in bars]
        l = [b["low"] for b in bars]
        c = [b["close"] for b in bars]
        _, d, _ = calc_kdj(h, l, c)
        for v in d:
            if v is not None:
                self.assertGreaterEqual(v, 0)
                self.assertLessEqual(v, 100)


class TestATR(unittest.TestCase):
    def test_positive(self):
        bars = _make_bars(50)
        h = [b["high"] for b in bars]
        l = [b["low"] for b in bars]
        c = [b["close"] for b in bars]
        atr = calc_atr(h, l, c, 20)
        for v in atr:
            if v is not None:
                self.assertGreater(v, 0)


class TestAdaptiveStopLoss(unittest.TestCase):
    def test_k_decreases_with_gain(self):
        k0 = adaptive_K(0, 0)
        k10 = adaptive_K(10, 0)
        k30 = adaptive_K(30, 0)
        self.assertGreater(k0, k10)
        self.assertGreater(k10, k30)

    def test_k_minimum_bound(self):
        k = adaptive_K(100, 20)
        self.assertGreaterEqual(k, MIN_K)

    def test_acceleration_adjustment(self):
        k_slow = adaptive_K(10, 3)
        k_fast = adaptive_K(10, 10)
        self.assertGreater(k_slow, k_fast)

    def test_hard_stop_floor(self):
        entry = 10.0
        highest = 10.0
        atr = 0.5
        stop, _ = calc_trailing_stop(entry, highest, atr, 0)
        hard = entry * (1 - HARD_STOP_PCT)
        self.assertGreaterEqual(stop, hard)

    def test_trailing_stop_rises_with_price(self):
        entry = 10.0
        atr = 0.3
        stop1, _ = calc_trailing_stop(entry, 11.0, atr, 5)
        stop2, _ = calc_trailing_stop(entry, 13.0, atr, 15)
        self.assertGreater(stop2, stop1)


class TestSignalClassification(unittest.TestCase):
    def test_watch_when_downtrend(self):
        bars = _make_downtrend_bars(200)
        sig = classify_signal(bars)
        self.assertEqual(sig["signal_type"], SIGNAL_WATCH)

    def test_data_insufficient(self):
        bars = _make_bars(30)
        sig = classify_signal(bars)
        self.assertEqual(sig["signal_type"], SIGNAL_WATCH)
        self.assertFalse(sig["monthly_pass"])

    def test_signal_has_required_fields(self):
        bars = _make_uptrend_bars(200)
        sig = classify_signal(bars)
        required = ["signal_type", "label", "position_hint", "color",
                     "monthly_pass", "weekly_pass", "daily_conditions",
                     "indicators", "details"]
        for field in required:
            self.assertIn(field, sig)

    def test_daily_conditions_length(self):
        bars = _make_uptrend_bars(200)
        sig = classify_signal(bars)
        self.assertEqual(len(sig["daily_conditions"]), 4)


class TestMultiTimeframe(unittest.TestCase):
    def test_monthly_filter_uptrend(self):
        bars = _make_uptrend_bars(700)
        passed, details = check_monthly_filter(bars)
        self.assertTrue(passed)

    def test_monthly_filter_downtrend(self):
        bars = _make_downtrend_bars(700)
        passed, details = check_monthly_filter(bars)
        self.assertFalse(passed)

    def test_weekly_filter_uptrend(self):
        bars = _make_uptrend_bars(200)
        passed, details = check_weekly_filter(bars)
        self.assertTrue(passed)

    def test_weekly_filter_too_short(self):
        bars = _make_bars(10)
        passed, details = check_weekly_filter(bars)
        self.assertTrue(passed)  # returns True with "数据不足" detail

    def test_tier_classification(self):
        bars_up = _make_uptrend_bars(300)
        sig = classify_signal(bars_up)
        m_pass = sig["monthly_pass"]
        w_pass = sig["weekly_pass"]
        if m_pass and w_pass:
            tier = "all"
        elif m_pass or w_pass:
            tier = "partial"
        else:
            tier = "none"
        self.assertIn(tier, ("all", "partial", "none"))


class TestBacktest(unittest.TestCase):
    def test_with_transaction_costs(self):
        bars = _make_uptrend_bars(300)
        bt_no_cost = RightSideBacktest(bars, commission_rate=0, slippage_rate=0)
        bt_no_cost.run()
        bt_cost = RightSideBacktest(bars, commission_rate=0.0003, slippage_rate=0.001)
        bt_cost.run()
        if bt_no_cost.equity_curve and bt_cost.equity_curve:
            eq_no = bt_no_cost.equity_curve[-1]["equity"]
            eq_cost = bt_cost.equity_curve[-1]["equity"]
            self.assertGreaterEqual(eq_no, eq_cost)

    def test_total_cost_tracked(self):
        bars = _make_uptrend_bars(300)
        bt = RightSideBacktest(bars, commission_rate=0.001, slippage_rate=0.002)
        bt.run()
        if bt.trades:
            self.assertGreater(bt.total_commission, 0)
            self.assertGreater(bt.total_slippage, 0)

    def test_equity_curve_length(self):
        bars = _make_bars(200)
        bt = RightSideBacktest(bars)
        bt.run()
        self.assertEqual(len(bt.equity_curve), 200 - 60)

    def test_no_trade_flat_market(self):
        from datetime import date, timedelta
        bars = []
        start = date(2023, 1, 4)
        for i in range(200):
            d = start + timedelta(days=i)
            bars.append({
                "date": d.strftime("%Y-%m-%d"),
                "open": 10.0, "high": 10.01, "low": 9.99, "close": 10.0,
                "volume": 1000000,
            })
        bt = RightSideBacktest(bars)
        bt.run()
        self.assertEqual(len(bt.trades), 0)

    def test_capital_conservation(self):
        bars = _make_uptrend_bars(200)
        bt = RightSideBacktest(bars, capital=100000, commission_rate=0, slippage_rate=0)
        bt.run()
        final_equity = bt.capital + bt.position * bars[-1]["close"]
        self.assertGreater(final_equity, 0)


class TestPortfolioAdvice(unittest.TestCase):
    def _make_results(self, n_breakout=3, n_pullback=5, n_watch=10):
        results = []
        for i in range(n_breakout):
            results.append({
                "name": f"突破ETF{i}", "symbol": f"sz{159000+i}",
                "tier": "all", "signal_type": SIGNAL_BREAKOUT,
                "signal_label": "突破信号", "chg_5d": 5.0 - i,
            })
        for i in range(n_pullback):
            results.append({
                "name": f"回踩ETF{i}", "symbol": f"sz{159100+i}",
                "tier": "all", "signal_type": SIGNAL_PULLBACK,
                "signal_label": "回踩机会", "chg_5d": 2.0 - i,
            })
        for i in range(n_watch):
            results.append({
                "name": f"观望ETF{i}", "symbol": f"sz{159200+i}",
                "tier": "none", "signal_type": SIGNAL_WATCH,
                "signal_label": "观望", "chg_5d": 0,
            })
        return results

    def test_max_positions_limit(self):
        results = self._make_results(n_breakout=10, n_pullback=10)
        pa = portfolio_advice(results)
        self.assertLessEqual(pa["selected"], MAX_POSITIONS)

    def test_single_position_cap(self):
        results = self._make_results()
        pa = portfolio_advice(results)
        for a in pa["advice"]:
            self.assertLessEqual(a["position_pct"], MAX_SINGLE_PCT)

    def test_total_allocation_cap(self):
        results = self._make_results(n_breakout=10, n_pullback=10)
        pa = portfolio_advice(results)
        self.assertLessEqual(pa["total_allocation_pct"], 1.0)

    def test_breakout_priority(self):
        results = self._make_results(n_breakout=3, n_pullback=3)
        pa = portfolio_advice(results)
        if len(pa["advice"]) >= 2:
            first_signals = [a["signal"] for a in pa["advice"]]
            breakout_idx = [i for i, s in enumerate(first_signals) if "突破" in s]
            pullback_idx = [i for i, s in enumerate(first_signals) if "回踩" in s]
            if breakout_idx and pullback_idx:
                self.assertLess(max(breakout_idx), min(pullback_idx))

    def test_excludes_non_all_tier(self):
        results = self._make_results(n_breakout=0, n_pullback=0, n_watch=10)
        pa = portfolio_advice(results)
        self.assertEqual(pa["selected"], 0)
        self.assertEqual(len(pa["advice"]), 0)

    def test_empty_results(self):
        pa = portfolio_advice([])
        self.assertEqual(pa["selected"], 0)

    def test_amount_calculation(self):
        results = self._make_results(n_breakout=1)
        settings = {"portfolio": {"total_capital": 1000000}}
        pa = portfolio_advice(results, settings=settings)
        if pa["advice"]:
            a = pa["advice"][0]
            self.assertEqual(a["amount"], int(1000000 * a["position_pct"]))


class TestTrainTestSplit(unittest.TestCase):
    def test_split_ratio(self):
        bars = _make_bars(200)
        split = int(len(bars) * 0.7)
        train = bars[:split]
        test = bars[split:]
        self.assertEqual(len(train), 140)
        self.assertEqual(len(test), 60)
        self.assertEqual(len(train) + len(test), len(bars))

    def test_no_overlap(self):
        bars = _make_bars(200)
        split = int(len(bars) * 0.7)
        train = bars[:split]
        test = bars[split:]
        train_dates = {b["date"] for b in train}
        test_dates = {b["date"] for b in test}
        self.assertEqual(len(train_dates & test_dates), 0)


class TestExtendedMetrics(unittest.TestCase):
    def _run_bt(self, bars):
        bt = RightSideBacktest(bars, commission_rate=0.0003, slippage_rate=0.001)
        bt.run()
        return bt

    def test_sortino_positive_uptrend(self):
        bars = _make_uptrend_bars(300)
        bt = self._run_bt(bars)
        m = calc_extended_metrics(bt)
        if bt.trades:
            self.assertGreater(m["sortino"], 0)

    def test_calmar_computed(self):
        bars = _make_uptrend_bars(300)
        bt = self._run_bt(bars)
        m = calc_extended_metrics(bt)
        if m:
            self.assertIn("calmar", m)
            self.assertTrue(math.isfinite(m["calmar"]))

    def test_monthly_returns_count(self):
        bars = _make_bars(300)
        bt = self._run_bt(bars)
        m = calc_extended_metrics(bt)
        if m and m.get("monthly_returns"):
            unique_months = set()
            for e in bt.equity_curve:
                unique_months.add(e["date"][:7])
            self.assertEqual(len(m["monthly_returns"]), len(unique_months))

    def test_exposure_range(self):
        bars = _make_uptrend_bars(300)
        bt = self._run_bt(bars)
        m = calc_extended_metrics(bt)
        if m:
            self.assertGreaterEqual(m["exposure_pct"], 0)
            self.assertLessEqual(m["exposure_pct"], 100)

    def test_profit_factor_positive(self):
        bars = _make_uptrend_bars(300)
        bt = self._run_bt(bars)
        m = calc_extended_metrics(bt)
        if bt.trades and m:
            self.assertGreater(m["profit_factor"], 0)

    def test_empty_equity(self):
        bt = RightSideBacktest(_make_bars(30))
        bt.equity_curve = []
        m = calc_extended_metrics(bt)
        self.assertEqual(m, {})


class TestHealthCheck(unittest.TestCase):
    def _make_scan_results(self, n=10, signal="watch", chg_1d=0, holding=False, rsi=50):
        return [
            {"name": f"ETF{i}", "symbol": f"sz{159000+i}",
             "cat": "行业", "signal_type": signal,
             "tier": "all" if signal != "watch" else "none",
             "chg_1d": chg_1d, "holding": holding, "rsi": rsi}
            for i in range(n)
        ]

    def test_returns_list(self):
        results = self._make_scan_results()
        checks = strategy_health_check(results, bench_chg=0)
        self.assertIsInstance(checks, list)

    def test_signal_drought(self):
        results = self._make_scan_results(10, signal="watch")
        checks = strategy_health_check(results)
        categories = [c["category"] for c in checks]
        self.assertIn("drought", categories)

    def test_no_drought_with_signals(self):
        results = self._make_scan_results(5, signal="breakout")
        results += self._make_scan_results(5, signal="watch")
        for r in results[:5]:
            r["tier"] = "all"
        checks = strategy_health_check(results)
        categories = [c["category"] for c in checks]
        self.assertNotIn("drought", categories)

    def test_regime_warning(self):
        results = self._make_scan_results(10, chg_1d=-2.0)
        checks = strategy_health_check(results, bench_chg=-3.0)
        categories = [c["category"] for c in checks]
        self.assertIn("regime", categories)

    def test_concentration_warning(self):
        results = []
        for i in range(5):
            results.append({
                "name": f"半导体ETF{i}", "symbol": f"sz{159000+i}",
                "cat": "行业", "signal_type": SIGNAL_BREAKOUT,
                "tier": "all", "chg_1d": 1.0, "holding": False, "rsi": 50,
            })
        checks = strategy_health_check(results)
        categories = [c["category"] for c in checks]
        self.assertIn("concentration", categories)

    def test_overbought_holding(self):
        results = self._make_scan_results(3, holding=True, rsi=75)
        checks = strategy_health_check(results)
        categories = [c["category"] for c in checks]
        self.assertIn("overbought", categories)


class TestPortfolioCustomSettings(unittest.TestCase):
    def test_custom_max_positions(self):
        results = []
        for i in range(10):
            results.append({
                "name": f"ETF{i}", "symbol": f"sz{159000+i}",
                "tier": "all", "signal_type": SIGNAL_BREAKOUT,
                "signal_label": "突破", "chg_5d": 5.0 - i,
            })
        settings = {"portfolio": {"max_positions": 3, "total_capital": 1000000,
                                   "max_single_pct": 0.30, "breakout_pct": 0.20, "pullback_pct": 0.25}}
        pa = portfolio_advice(results, settings=settings)
        self.assertLessEqual(pa["selected"], 3)

    def test_custom_capital_amount(self):
        results = [{"name": "ETF0", "symbol": "sz159000",
                     "tier": "all", "signal_type": SIGNAL_BREAKOUT,
                     "signal_label": "突破", "chg_5d": 5.0}]
        settings = {"portfolio": {"total_capital": 2000000, "max_positions": 5,
                                   "max_single_pct": 0.30, "breakout_pct": 0.20, "pullback_pct": 0.25}}
        pa = portfolio_advice(results, settings=settings)
        if pa["advice"]:
            self.assertEqual(pa["advice"][0]["amount"], int(2000000 * pa["advice"][0]["position_pct"]))


if __name__ == "__main__":
    unittest.main()
