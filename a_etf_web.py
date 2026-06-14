#!/usr/bin/env python3
"""
ETF右侧趋势监控 — Web Dashboard
python3 a_etf_web.py          # 启动后访问 http://localhost:8080
python3 a_etf_web.py --port 9000
"""

import http.server
import json
import os
import sys
import datetime
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from a_etf_trend import (
    ETF_BASKET, BENCHMARK, check_entry,
    HARD_STOP_PCT, adaptive_K,
    load_calibration, calc_grade,
    fetch_all_share_changes,
    load_etf_settings, save_etf_settings,
    load_strategy_config, save_strategy_config, DEFAULT_STRATEGY,
    run_backtest_with_config, run_optimization, load_optimize_results,
    classify_signal, get_etf_config,
    SIGNAL_BREAKOUT, SIGNAL_PULLBACK, SIGNAL_OVERBOUGHT, SIGNAL_STRONG, SIGNAL_WATCH,
    SIGNAL_LABELS, SIGNAL_COLORS,
)
from a_trend_trader import update_cn_ticker, fetch_tushare_cn_kline
from chokepoint_trader import (
    init_db, get_bars, sma, calc_rsi, calc_macd, calc_atr, calc_volume_ratio,
)


def scan_all_data():
    conn = init_db()
    cal = load_calibration()
    settings = load_etf_settings()
    ds = settings.get("data_source", "tencent")
    token = settings.get("tushare_token", "")
    strat_config = load_strategy_config()

    try:
        share_data = fetch_all_share_changes()
    except Exception:
        share_data = {}

    try:
        update_cn_ticker(conn, BENCHMARK, verbose=False, data_source=ds, tushare_token=token)
    except Exception:
        pass

    bench_bars = get_bars(conn, BENCHMARK, 750)
    bench_price = bench_bars[-1]["close"] if bench_bars else 0
    bench_chg = 0
    if len(bench_bars) >= 2:
        bench_chg = (bench_bars[-1]["close"] - bench_bars[-2]["close"]) / bench_bars[-2]["close"] * 100

    results = []
    fetch_errors = []
    for name, cfg in ETF_BASKET.items():
        sym = cfg["symbol"]
        cat = cfg.get("cat", "行业")
        holding = cfg.get("holding", False)

        try:
            update_cn_ticker(conn, sym, verbose=False, data_source=ds, tushare_token=token)
        except Exception as e:
            fetch_errors.append(f"{name}({sym}): {e}")
            continue

        bars = get_bars(conn, sym, 750)
        if len(bars) < 60:
            results.append({
                "name": name, "symbol": sym, "cat": cat, "holding": holding,
                "price": 0, "chg_1d": 0, "chg_5d": 0, "chg_20d": 0,
                "entry": False, "conditions": [False]*4, "details": [],
                "signal_type": "watch", "signal_label": "观望",
                "signal_color": "#95a5a6", "position_hint": "数据不足",
                "signal_details": [],
                "ma20": 0, "ma20_dist": 0, "ma60": 0,
                "rsi": 0, "atr": 0, "vol_ratio": 0,
                "macd_hist": 0, "data_ok": False,
                "share_chg": None, "share_latest": None,
                "monthly_pass": False, "weekly_pass": False,
                "monthly_detail": "", "weekly_detail": "",
                "tier": "none",
            })
            continue

        etf_config = get_etf_config(sym, strat_config)
        entry, details, ex = check_entry(bars, config=etf_config)
        sig = classify_signal(bars, config=etf_config)

        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        volumes = [b["volume"] for b in bars]
        i = len(bars) - 1

        ma20v = sma(closes, 20)
        macd_l, macd_s, macd_h = calc_macd(closes)
        atr_vals = calc_atr(highs, lows, closes, 20)

        c1 = ma20v[i] is not None and closes[i] > ma20v[i]
        c2 = False
        if ma20v[i] is not None and i >= 24:
            old = sma(closes[:i-4], 20)
            if old and old[-1] is not None:
                c2 = ma20v[i] > old[-1]
        c3 = macd_h[i] is not None and macd_h[i] > 0
        vol5 = sum(volumes[i-4:i+1]) / 5 if i >= 4 else 0
        vol20 = sum(volumes[max(0,i-19):i+1]) / min(20, i+1)
        c4 = vol5 > vol20 if vol20 > 0 else False

        ma20_dist = 0
        if ma20v[i] and ma20v[i] > 0:
            ma20_dist = (closes[i] - ma20v[i]) / ma20v[i] * 100

        ma60v = sma(closes, 60) if len(closes) >= 60 else [None]*len(closes)

        stop_price = 0
        k_val = 3.0
        if atr_vals[i] and atr_vals[i] > 0:
            chg5 = ex.get("chg_5d", 0)
            k_val = adaptive_K(0, chg5)
            stop_price = closes[i] - k_val * atr_vals[i]
            hard = closes[i] * (1 - HARD_STOP_PCT)
            stop_price = max(stop_price, hard)

        c_entry = cal.get(sym, {})
        grade = c_entry.get("grade", "-")
        bt_ret = c_entry.get("total_return", None)

        sd = share_data.get(sym, {})

        m_pass = sig["monthly_pass"]
        w_pass = sig["weekly_pass"]
        tier = "all" if (m_pass and w_pass) else ("partial" if (m_pass or w_pass) else "none")

        results.append({
            "name": name, "symbol": sym, "cat": cat, "holding": holding,
            "price": round(closes[i], 3),
            "chg_1d": round(ex.get("chg_1d", 0), 2),
            "chg_5d": round(ex.get("chg_5d", 0), 2),
            "chg_20d": round(ex.get("chg_20d", 0), 2),
            "entry": entry,
            "conditions": sig["daily_conditions"],
            "cond_count": sum(sig["daily_conditions"]),
            "details": details,
            "signal_type": sig["signal_type"],
            "signal_label": sig["label"],
            "signal_color": sig["color"],
            "position_hint": sig["position_hint"],
            "signal_details": sig["details"],
            "ma20": round(ma20v[i], 3) if ma20v[i] else 0,
            "ma20_dist": round(ma20_dist, 1),
            "ma60": round(ma60v[i], 3) if ma60v[i] else 0,
            "rsi": round(ex.get("rsi", 0) or 0, 0),
            "atr": round(atr_vals[i], 4) if atr_vals[i] else 0,
            "vol_ratio": round(ex.get("vol_ratio", 0) or 0, 1),
            "macd_hist": round(macd_h[i], 4) if macd_h[i] else 0,
            "stop_price": round(stop_price, 3),
            "K": round(k_val, 1),
            "date": bars[-1]["date"],
            "data_ok": True,
            "grade": grade,
            "bt_ret": bt_ret,
            "share_chg": sd.get("chg_pct", None),
            "share_latest": sd.get("latest", None),
            "monthly_pass": sig["monthly_pass"],
            "weekly_pass": sig["weekly_pass"],
            "monthly_detail": sig["monthly_detail"],
            "weekly_detail": sig["weekly_detail"],
            "tier": tier,
        })

    conn.close()

    sig_order = {"breakout": 5, "pullback": 4, "overbought": 3, "strong": 2, "watch": 1}
    results.sort(key=lambda x: (-sig_order.get(x["signal_type"], 0), -x["chg_5d"]))

    tier_all = sum(1 for r in results if r["tier"] == "all")
    tier_partial = sum(1 for r in results if r["tier"] == "partial")
    tier_none = sum(1 for r in results if r["tier"] == "none")

    return {
        "scan_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "benchmark": {"price": round(bench_price, 2), "chg": round(bench_chg, 2)},
        "total": len(results),
        "entry_count": sum(1 for r in results if r["entry"]),
        "tier_all": tier_all,
        "tier_partial": tier_partial,
        "tier_none": tier_none,
        "fetch_errors": fetch_errors[:10],
        "results": results,
    }


_cache = {"data": None, "time": None}


def get_cached_data(force=False):
    now = datetime.datetime.now()
    if not force and _cache["data"] and _cache["time"]:
        age = (now - _cache["time"]).total_seconds()
        if age < 300:
            return _cache["data"]
    print(f"[{now.strftime('%H:%M:%S')}] 扫描36个ETF...", flush=True)
    data = scan_all_data()
    errs = data.get("fetch_errors", [])
    print(f"[{now.strftime('%H:%M:%S')}] 扫描完成: {data['total']}个ETF, {len(errs)}个失败", flush=True)
    if errs:
        for e in errs[:5]:
            print(f"  错误: {e}", flush=True)
    _cache["data"] = data
    _cache["time"] = now
    print(f"[{now.strftime('%H:%M:%S')}] 扫描完成", flush=True)
    return data


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ETF右侧趋势监控</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, "Helvetica Neue", "PingFang SC", "Microsoft YaHei", sans-serif; background: #f5f6fa; color: #2d3436; font-size: 14px; }
.header { background: linear-gradient(135deg, #2d3436 0%, #000000 100%); color: #fff; padding: 20px 24px; display: flex; justify-content: space-between; align-items: center; }
.header h1 { font-size: 20px; font-weight: 600; }
.header .sub { color: #b2bec3; font-size: 13px; margin-top: 4px; }
.header .right { text-align: right; }
.header .bench { font-size: 15px; }
.header .time { color: #b2bec3; font-size: 12px; margin-top: 4px; }
.btn { background: #0984e3; color: #fff; border: none; padding: 8px 18px; border-radius: 6px; cursor: pointer; font-size: 13px; }
.btn:hover { background: #0876cc; }
.btn:disabled { background: #636e72; cursor: wait; }

.tab-bar { display:flex; gap:0; background:#fff; border-bottom:2px solid #eee; padding:0 24px; }
.tab-btn { padding:12px 24px; border:none; background:transparent; cursor:pointer; font-size:14px; font-weight:500; color:#636e72; border-bottom:2px solid transparent; margin-bottom:-2px; transition:all 0.15s; }
.tab-btn:hover { color:#2d3436; }
.tab-btn.active { color:#0984e3; border-bottom-color:#0984e3; }
.tab-content { display:none; }
.tab-content.active { display:block; }

.stats { display: flex; gap: 12px; padding: 16px 24px; flex-wrap: wrap; }
.stat-card { background: #fff; border-radius: 10px; padding: 14px 20px; min-width: 140px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
.stat-card .label { font-size: 12px; color: #636e72; }
.stat-card .value { font-size: 22px; font-weight: 700; margin-top: 4px; }

.filters { padding: 8px 24px 12px; display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.filter-group { display: flex; gap: 4px; background: #fff; border-radius: 8px; padding: 3px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
.filter-btn { padding: 6px 14px; border: none; background: transparent; border-radius: 6px; cursor: pointer; font-size: 13px; color: #636e72; transition: all 0.15s; }
.filter-btn.active { background: #0984e3; color: #fff; }
.filter-btn:hover:not(.active) { background: #dfe6e9; }
.search { padding: 7px 14px; border: 1px solid #dfe6e9; border-radius: 8px; font-size: 13px; width: 180px; outline: none; }
.search:focus { border-color: #0984e3; }

.table-wrap { padding: 0 24px 24px; overflow-x: auto; }
table { width: 100%; border-collapse: separate; border-spacing: 0; background: #fff; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); overflow: hidden; }
th { background: #f8f9fa; padding: 10px 12px; text-align: left; font-weight: 600; font-size: 12px; color: #636e72; text-transform: uppercase; cursor: pointer; white-space: nowrap; user-select: none; border-bottom: 2px solid #eee; position: sticky; top: 0; z-index: 10; }
th:hover { background: #e9ecef; }
th .arrow { margin-left: 4px; font-size: 10px; color: #b2bec3; }
th.sorted .arrow { color: #0984e3; }
td { padding: 10px 12px; border-bottom: 1px solid #f1f2f6; white-space: nowrap; }
tr:hover td { background: #f8f9ff; }
tr.entry-row td { background: #f0fff4; }
tr.entry-row:hover td { background: #e6ffe8; }
tr.holding-row td:first-child { border-left: 3px solid #fdcb6e; }

.tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 500; }
.tag-行业 { background: #dfe6e9; color: #2d3436; }
.tag-细分 { background: #e8daef; color: #6c3483; }
.tag-策略 { background: #d5f5e3; color: #1e8449; }

.grade { display: inline-block; padding: 2px 7px; border-radius: 4px; font-size: 11px; font-weight: 700; min-width: 20px; text-align: center; }
.grade-S { background: #e74c3c; color: #fff; }
.grade-A { background: #e67e22; color: #fff; }
.grade-B { background: #3498db; color: #fff; }
.grade-C { background: #bdc3c7; color: #636e72; }
.grade-D { background: #ecf0f1; color: #b2bec3; }
.grade-- { background: #f5f6fa; color: #b2bec3; }

tr.filtered-row td { opacity: 0.4; }
tr.filtered-row:hover td { opacity: 0.7; }

.up { color: #e74c3c; }
.down { color: #27ae60; }
.flat { color: #95a5a6; }

.cond { display: inline-flex; gap: 3px; }
.dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.dot.on { background: #00b894; }
.dot.off { background: #dfe6e9; }

.signal-badge { display: inline-block; padding: 3px 10px; border-radius: 4px; font-size: 11px; font-weight: 600; }
.signal-entry { background: #00b894; color: #fff; }
.signal-none { background: #f1f2f6; color: #b2bec3; }
.signal-close { background: #e74c3c22; color: #e74c3c; }
.signal-breakout { background: #e74c3c; color: #fff; }
.signal-pullback { background: #27ae60; color: #fff; }
.signal-overbought { background: #e67e22; color: #fff; }
.signal-strong { background: #3498db; color: #fff; }
.signal-watch { background: #f1f2f6; color: #95a5a6; }

.tier-section { padding: 4px 24px 0; }
.section-title { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.step-num { width: 24px; height: 24px; border-radius: 50%; background: #0984e3; color: #fff; display: flex; align-items: center; justify-content: center; font-size: 13px; font-weight: 700; flex-shrink: 0; }
.section-title .title-text { font-size: 15px; font-weight: 600; color: #2d3436; }
.section-title .title-sub { font-size: 12px; color: #b2bec3; margin-left: 8px; }
.tier-filter { display: flex; gap: 10px; padding: 0 24px 8px; flex-wrap: wrap; }
.tier-btn { background: #fff; border: 2px solid #eee; border-radius: 10px; padding: 10px 24px; cursor: pointer; text-align: center; min-width: 120px; transition: all 0.15s; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
.tier-btn:hover { border-color: #b2bec3; }
.tier-btn .tier-count { font-size: 24px; font-weight: 700; }
.tier-btn .tier-label { font-size: 12px; color: #636e72; margin-top: 2px; }
.tier-btn.tier-all .tier-count { color: #27ae60; }
.tier-btn.tier-partial .tier-count { color: #e67e22; }
.tier-btn.tier-none .tier-count { color: #95a5a6; }
.tier-btn.active.tier-all { border-color: #27ae60; background: #f0fff4; }
.tier-btn.active.tier-partial { border-color: #e67e22; background: #fef9f0; }
.tier-btn.active.tier-none { border-color: #95a5a6; background: #f8f9fa; }
.step-divider { border: none; border-top: 1px dashed #dfe6e9; margin: 4px 24px 8px; }
.trend-tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
.trend-all { background: #d5f5e3; color: #1e8449; }
.trend-partial { background: #fdebd0; color: #b9770e; }
.trend-none { background: #f2f3f4; color: #95a5a6; }
.fetch-error-bar { display:none; margin:0 24px 8px; padding:10px 16px; background:#fff3cd; color:#856404; border-radius:8px; font-size:13px; }

.holding-star { color: #f39c12; font-size: 12px; }

.detail-row { display: none; }
.detail-row.show { display: table-row; }
.detail-row td { background: #fafbfc; padding: 12px 20px; }
.detail-content { display: flex; gap: 24px; flex-wrap: wrap; }
.detail-block { min-width: 200px; }
.detail-block h4 { font-size: 12px; color: #636e72; margin-bottom: 6px; }
.detail-block .item { display: flex; justify-content: space-between; padding: 3px 0; font-size: 13px; }
.detail-block .item .k { color: #636e72; }

.cond-detail { display: flex; gap: 16px; flex-wrap: wrap; }
.cond-item { display: flex; align-items: center; gap: 6px; font-size: 13px; padding: 4px 10px; border-radius: 6px; }
.cond-item.met { background: #d5f5e3; color: #1e8449; }
.cond-item.unmet { background: #f8f9fa; color: #b2bec3; }

.loading { text-align: center; padding: 60px; color: #636e72; }
.loading .spinner { display: inline-block; width: 32px; height: 32px; border: 3px solid #dfe6e9; border-top-color: #0984e3; border-radius: 50%; animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

@media (max-width: 768px) {
  .header { flex-direction: column; gap: 10px; }
  .stats { padding: 12px 16px; }
  .filters { padding: 8px 16px; }
  .table-wrap { padding: 0 8px 16px; }
  td, th { padding: 8px 6px; font-size: 12px; }
  .tier-section { padding: 4px 16px 0; }
  .tier-filter { padding: 0 16px 8px; }
  .tier-btn { min-width: 90px; padding: 8px 14px; }
  .tier-btn .tier-count { font-size: 20px; }
  .step-divider { margin: 4px 16px 8px; }
}

/* Strategy page */
.strat-wrap { padding: 16px 24px; max-width: 900px; }
.strat-section { background: #fff; border-radius: 10px; padding: 20px 24px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
.strat-section h3 { font-size: 15px; margin-bottom: 12px; color: #2d3436; border-bottom: 1px solid #f1f2f6; padding-bottom: 8px; }
.strat-doc { font-size: 13px; line-height: 1.8; color: #636e72; }
.strat-doc strong { color: #2d3436; }
.strat-doc .formula { background: #f8f9fa; padding: 4px 10px; border-radius: 4px; font-family: monospace; font-size: 12px; display: inline-block; margin: 2px 0; }

.param-row { display: flex; align-items: center; gap: 12px; padding: 8px 0; border-bottom: 1px solid #f8f9fa; flex-wrap: wrap; }
.param-row:last-child { border-bottom: none; }
.param-toggle { width: 36px; height: 20px; background: #dfe6e9; border-radius: 10px; position: relative; cursor: pointer; flex-shrink: 0; }
.param-toggle.on { background: #00b894; }
.param-toggle::after { content: ''; position: absolute; top: 2px; left: 2px; width: 16px; height: 16px; background: #fff; border-radius: 50%; transition: 0.15s; }
.param-toggle.on::after { left: 18px; }
.param-label { font-size: 13px; font-weight: 500; min-width: 80px; }
.param-inputs { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.param-inputs label { font-size: 12px; color: #636e72; }
.param-inputs input[type="number"] { width: 64px; padding: 5px 8px; border: 1px solid #dfe6e9; border-radius: 4px; font-size: 13px; text-align: center; }
.param-inputs input:focus { border-color: #0984e3; outline: none; }

.strat-actions { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 12px; }
.strat-actions button { padding: 10px 20px; border: none; border-radius: 6px; font-size: 13px; cursor: pointer; }
.btn-primary { background: #0984e3; color: #fff; }
.btn-primary:hover { background: #0876cc; }
.btn-primary:disabled { background: #636e72; cursor: wait; }
.btn-secondary { background: #dfe6e9; color: #2d3436; }
.btn-secondary:hover { background: #cfd8dc; }
.btn-success { background: #00b894; color: #fff; }
.btn-success:hover { background: #00a382; }

.bt-result { margin-top: 16px; }
.bt-metrics { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.bt-metric { background: #f8f9fa; border-radius: 8px; padding: 10px 16px; min-width: 100px; text-align: center; }
.bt-metric .v { font-size: 18px; font-weight: 700; }
.bt-metric .l { font-size: 11px; color: #636e72; margin-top: 2px; }
.bt-trades { font-size: 12px; max-height: 200px; overflow-y: auto; }
.bt-trades table { font-size: 12px; }
.bt-trades td, .bt-trades th { padding: 6px 8px; }

.opt-result { margin-top: 16px; }
.opt-best { background: #f0fff4; border: 1px solid #d5f5e3; border-radius: 8px; padding: 14px 18px; margin-bottom: 12px; }
.opt-best .title { font-size: 13px; font-weight: 600; color: #1e8449; margin-bottom: 6px; }
.opt-table { font-size: 12px; }
.opt-table td, .opt-table th { padding: 6px 10px; }

/* Settings tab */
.settings-wrap { padding: 16px 24px; max-width: 600px; }
.settings-section { background: #fff; border-radius: 10px; padding: 20px 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
.settings-section h3 { font-size: 15px; margin-bottom: 16px; color: #2d3436; }
.settings-section label { display: block; font-size: 13px; color: #636e72; margin-bottom: 6px; font-weight: 500; }
.settings-section input[type="text"], .settings-section input[type="time"] { width: 100%; padding: 8px 12px; border: 1px solid #dfe6e9; border-radius: 6px; font-size: 14px; }
.settings-section input:focus { border-color: #0984e3; outline: none; }
.settings-section .field { margin-bottom: 16px; }
.settings-section .radio-group { display: flex; gap: 16px; margin-bottom: 16px; }
.settings-section .radio-group label { display: flex; align-items: center; gap: 6px; cursor: pointer; font-size: 14px; color: #2d3436; }
.btn-test { background: #00b894; color: #fff; padding: 8px 16px; border: none; border-radius: 6px; cursor: pointer; font-size: 13px; margin-left: 8px; }
.test-result { font-size: 13px; margin-top: 8px; padding: 6px 10px; border-radius: 4px; }
.test-ok { background: #d4edda; color: #155724; }
.test-fail { background: #f8d7da; color: #721c24; }
.hint { font-size: 12px; color: #b2bec3; margin-top: 4px; }
</style>
</head>
<body>

<div class="header">
  <div>
    <h1>ETF右侧趋势监控</h1>
    <div class="sub">36个标的 · 自适应ATR追踪止损 · 右侧交易</div>
  </div>
  <div class="right">
    <div class="bench" id="bench"></div>
    <div class="time" id="scan-time"></div>
    <button class="btn" id="refresh-btn" onclick="refresh()" style="margin-top:8px">刷新数据</button>
  </div>
</div>

<div class="tab-bar">
  <button class="tab-btn active" onclick="switchTab('monitor')">监控</button>
  <button class="tab-btn" onclick="switchTab('strategy')">策略</button>
  <button class="tab-btn" onclick="switchTab('settings')">设置</button>
</div>

<!-- ============ 监控 Tab ============ -->
<div class="tab-content active" id="tab-monitor">

<!-- Step 1: 多周期趋势过滤 -->
<div class="tier-section">
  <div class="section-title">
    <span class="step-num">1</span>
    <span class="title-text">多周期趋势过滤</span>
    <span class="title-sub">月线+周线决定可选盘子</span>
  </div>
</div>
<div class="stats" id="stats"></div>
<div class="tier-filter" id="tier-filter">
  <div class="tier-btn tier-all active" data-tier="all">
    <div class="tier-count" id="tc-all">-</div>
    <div class="tier-label">全满足</div>
  </div>
  <div class="tier-btn tier-partial" data-tier="partial">
    <div class="tier-count" id="tc-partial">-</div>
    <div class="tier-label">部分满足</div>
  </div>
  <div class="tier-btn tier-none" data-tier="none">
    <div class="tier-count" id="tc-none">-</div>
    <div class="tier-label">都不满足</div>
  </div>
</div>
<div class="fetch-error-bar" id="fetch-error-bar"></div>

<hr class="step-divider">

<!-- Step 2: 日线信号分类 -->
<div class="tier-section">
  <div class="section-title">
    <span class="step-num">2</span>
    <span class="title-text">日线信号分类</span>
    <span class="title-sub" id="sig-subtitle">在选中趋势等级内查看日线信号</span>
  </div>
</div>
<div class="stats" id="sig-stats" style="padding-top:0"></div>

<div class="filters">
  <div class="filter-group" id="cat-filter">
    <button class="filter-btn active" data-cat="all">全部</button>
    <button class="filter-btn" data-cat="行业">行业</button>
    <button class="filter-btn" data-cat="细分">细分</button>
    <button class="filter-btn" data-cat="策略">策略</button>
  </div>
  <div class="filter-group" id="sig-filter">
    <button class="filter-btn active" data-sig="all">全部</button>
    <button class="filter-btn" data-sig="breakout" style="color:#e74c3c">突破</button>
    <button class="filter-btn" data-sig="pullback" style="color:#27ae60">回踩</button>
    <button class="filter-btn" data-sig="overbought" style="color:#e67e22">超买</button>
    <button class="filter-btn" data-sig="strong" style="color:#3498db">强势</button>
    <button class="filter-btn" data-sig="holding">持仓</button>
    <button class="filter-btn" data-sig="watch">观望</button>
  </div>
  <input class="search" id="search" placeholder="搜索标的..." oninput="applyFilters()">
</div>

<div class="table-wrap">
  <div class="loading" id="loading"><div class="spinner"></div><p style="margin-top:12px">正在扫描36个ETF...</p></div>
  <table id="main-table" style="display:none">
    <thead>
      <tr>
        <th data-sort="name">标的 <span class="arrow">↕</span></th>
        <th data-sort="tier">趋势 <span class="arrow">↕</span></th>
        <th data-sort="grade">评级 <span class="arrow">↕</span></th>
        <th data-sort="cat">分类 <span class="arrow">↕</span></th>
        <th data-sort="price">现价 <span class="arrow">↕</span></th>
        <th data-sort="chg_1d">1日% <span class="arrow">↕</span></th>
        <th data-sort="chg_5d">5日% <span class="arrow">↕</span></th>
        <th data-sort="chg_20d">20日% <span class="arrow">↕</span></th>
        <th data-sort="share_chg">份额% <span class="arrow">↕</span></th>
        <th data-sort="cond_count">日线条件 <span class="arrow">↕</span></th>
        <th data-sort="signal_type">信号 <span class="arrow">↕</span></th>
        <th>建议</th>
        <th data-sort="bt_ret">回测 <span class="arrow">↕</span></th>
        <th data-sort="ma20_dist">MA20距 <span class="arrow">↕</span></th>
        <th data-sort="rsi">RSI <span class="arrow">↕</span></th>
      </tr>
    </thead>
    <tbody id="tbody"></tbody>
  </table>
</div>
</div>

<!-- ============ 策略 Tab ============ -->
<div class="tab-content" id="tab-strategy">
<div class="strat-wrap">

<div class="strat-section">
  <h3>系统说明</h3>
  <div class="strat-doc">
    <p><strong>交易哲学:</strong> 月线定方向，周线确认趋势，日线找时机。只做右侧 — 不追反弹，只在长期多头环境下入场。</p>
    <p style="margin-top:10px"><strong>多周期过滤（自上而下）:</strong></p>
    <p>1. <strong>月线多头（铁律）</strong> — 价格>月MA(N) + 月MA上行 → 确认长期趋势向上，不满足一律不入场</p>
    <p>2. <strong>周线趋势确认</strong> — 价格>周MA(N) / 周MA上行 / 周MACD>0，满足至少M个 → 中期趋势确认</p>
    <p style="margin-top:10px"><strong>日线入场条件（全部满足才入场）:</strong></p>
    <p>3. <strong>价格 > MA(N)</strong> — 价格站上均线，确认趋势向上</p>
    <p>4. <strong>MA(N) 上行</strong> — 均线斜率为正（对比N+M日前），趋势加速确认</p>
    <p>5. <strong>MACD柱 > 0</strong> — MACD(fast,slow,signal)柱状图为正，动量确认</p>
    <p>6. <strong>量价确认</strong> — 短期成交量 > 长期均量，或放量上涨（量比>阈值）</p>
    <p>7. <strong>KDJ超卖（可选）</strong> — J线低于超卖阈值，逆势买入机会</p>
    <p style="margin-top:10px"><strong>出场条件（满足任一即出场）:</strong></p>
    <p>1. <strong>自适应追踪止损</strong> — <span class="formula">stop = 最高价 - K × ATR(N)</span>，K从Base_K随盈利收紧到Min_K</p>
    <p>2. <strong>硬止损</strong> — 跌破入场价 × (1-N%)，绝对风控</p>
    <p>3. <strong>均线破位</strong> — 连续M日收盘低于MA(N)，趋势终结</p>
  </div>
</div>

<div class="strat-section">
  <h3>月线过滤（铁律）</h3>
  <div class="param-row">
    <div class="param-toggle on" id="tog-monthly" onclick="togMonthly(this)"></div>
    <div class="param-label">月线多头</div>
    <div class="param-inputs" id="monthly-params">
      <label>月MA周期</label><input type="number" id="p-monthly-ma" value="20" min="3" max="24">
      <label style="margin-left:12px"><input type="checkbox" id="p-monthly-rising" checked> 要求月MA上行</label>
    </div>
  </div>
</div>

<div class="strat-section">
  <h3>周线过滤</h3>
  <div class="param-row">
    <div class="param-toggle on" id="tog-weekly" onclick="togWeekly(this)"></div>
    <div class="param-label">周线趋势</div>
    <div class="param-inputs" id="weekly-params">
      <label>周MA周期</label><input type="number" id="p-weekly-ma" value="10" min="3" max="30">
      <label style="margin-left:12px">最少满足</label><input type="number" id="p-weekly-min" value="2" min="1" max="3" style="width:40px">个
    </div>
  </div>
  <div class="param-row" style="padding-left:32px;opacity:0.85">
    <div class="param-inputs">
      <label><input type="checkbox" id="p-weekly-rising" checked> 周MA上行</label>
      <label style="margin-left:12px"><input type="checkbox" id="p-weekly-macd" checked> 周MACD>0</label>
    </div>
  </div>
</div>

<div class="strat-section">
  <h3>日线入场参数</h3>
  <div id="entry-params">
    <div class="param-row">
      <div class="param-toggle on" id="tog-c1" onclick="togCond(this,0)"></div>
      <div class="param-label">价格>MA</div>
      <div class="param-inputs">
        <label>MA周期</label><input type="number" id="p-ma-period" value="20" min="5" max="120">
      </div>
    </div>
    <div class="param-row">
      <div class="param-toggle on" id="tog-c2" onclick="togCond(this,1)"></div>
      <div class="param-label">MA上行</div>
      <div class="param-inputs">
        <label>回看</label><input type="number" id="p-ma-slope" value="5" min="1" max="30">
      </div>
    </div>
    <div class="param-row">
      <div class="param-toggle on" id="tog-c3" onclick="togCond(this,2)"></div>
      <div class="param-label">MACD>0</div>
      <div class="param-inputs">
        <label>快线</label><input type="number" id="p-macd-fast" value="12" min="2" max="50">
        <label>慢线</label><input type="number" id="p-macd-slow" value="26" min="5" max="100">
        <label>信号</label><input type="number" id="p-macd-signal" value="9" min="2" max="30">
      </div>
    </div>
    <div class="param-row">
      <div class="param-toggle on" id="tog-c4" onclick="togCond(this,3)"></div>
      <div class="param-label">量价确认</div>
      <div class="param-inputs">
        <label>短期</label><input type="number" id="p-vol-short" value="5" min="1" max="20">日
        <label>长期</label><input type="number" id="p-vol-long" value="20" min="5" max="60">日
        <label>阈值</label><input type="number" id="p-vol-thresh" value="1.2" min="0.5" max="3" step="0.1">
      </div>
    </div>
    <div class="param-row">
      <div class="param-toggle" id="tog-kdj" onclick="togKdj(this)"></div>
      <div class="param-label">KDJ超卖</div>
      <div class="param-inputs" id="kdj-params" style="opacity:0.4">
        <label>周期</label><input type="number" id="p-kdj-period" value="9" min="3" max="30">
        <label>超卖</label><input type="number" id="p-kdj-oversold" value="20" min="0" max="50">
      </div>
    </div>
  </div>
</div>

<div class="strat-section">
  <h3>出场参数</h3>
  <div class="param-row">
    <div class="param-inputs">
      <label>Base K</label><input type="number" id="p-base-k" value="3.0" min="1" max="6" step="0.1">
      <label>Min K</label><input type="number" id="p-min-k" value="1.2" min="0.3" max="4" step="0.1">
      <label>硬止损%</label><input type="number" id="p-hard-stop" value="8" min="2" max="20" step="1">
    </div>
  </div>
  <div class="param-row">
    <div class="param-inputs">
      <label>ATR周期</label><input type="number" id="p-atr-period" value="20" min="5" max="60">
      <label>MA出场</label><input type="number" id="p-ma-exit" value="20" min="5" max="60">
      <label>连跌天数</label><input type="number" id="p-ma-exit-days" value="3" min="1" max="10">
    </div>
  </div>
</div>

<div class="strat-section">
  <h3>回测验证</h3>
  <div class="param-row">
    <div class="param-inputs">
      <label>ETF</label>
      <select id="bt-symbol" style="padding:5px 8px;border:1px solid #dfe6e9;border-radius:4px;font-size:13px;min-width:140px"></select>
      <label>周期</label>
      <select id="bt-days" style="padding:5px 8px;border:1px solid #dfe6e9;border-radius:4px;font-size:13px">
        <option value="500">500天</option><option value="1000" selected>1000天</option><option value="1500">1500天</option>
      </select>
    </div>
  </div>
  <div class="strat-actions">
    <button class="btn-primary" id="btn-bt" onclick="runBacktest()">运行回测</button>
    <button class="btn-secondary" onclick="resetConfig()">恢复默认</button>
    <button class="btn-success" onclick="saveStrategyConfig()">保存配置</button>
  </div>
  <div id="bt-result" class="bt-result"></div>
</div>

<div class="strat-section">
  <h3>自动优化</h3>
  <div class="param-row">
    <div class="param-inputs">
      <label>范围</label>
      <select id="opt-scope" style="padding:5px 8px;border:1px solid #dfe6e9;border-radius:4px;font-size:13px">
        <option value="global">全局(36个ETF)</option>
      </select>
      <label>周期</label>
      <select id="opt-days" style="padding:5px 8px;border:1px solid #dfe6e9;border-radius:4px;font-size:13px">
        <option value="500">500天</option><option value="1000" selected>1000天</option>
      </select>
    </div>
  </div>
  <div class="strat-actions">
    <button class="btn-primary" id="btn-opt" onclick="runOptimize()">开始优化</button>
    <button class="btn-secondary" onclick="loadOptResults()">查看历史</button>
  </div>
  <div class="hint" style="margin-top:8px">单个ETF优化后会自动保存最优参数为该ETF的覆盖配置。全局优化更新全局参数。</div>
  <div id="opt-result" class="opt-result"></div>
</div>

<div class="strat-section">
  <h3>单独ETF参数覆盖</h3>
  <div class="param-row">
    <div class="param-inputs">
      <label>ETF</label>
      <select id="per-etf-symbol" style="padding:5px 8px;border:1px solid #dfe6e9;border-radius:4px;font-size:13px;min-width:140px"></select>
      <button class="btn-secondary" style="font-size:12px;padding:5px 12px" onclick="loadPerEtfConfig()">查看覆盖</button>
      <button class="btn-secondary" style="font-size:12px;padding:5px 12px;color:#e74c3c" onclick="clearPerEtfConfig()">清除覆盖</button>
    </div>
  </div>
  <div id="per-etf-info" style="margin-top:8px;font-size:13px;color:#636e72"></div>
  <div class="hint" style="margin-top:8px">在上方"自动优化"中选择单个ETF运行优化，最优参数会自动保存为该ETF的覆盖。也可CLI: python3 a_etf_trend.py optimize sz159516</div>
</div>

</div>
</div>

<!-- ============ 设置 Tab ============ -->
<div class="tab-content" id="tab-settings">
<div class="settings-wrap">
<div class="settings-section">
  <h3>系统设置</h3>
  <div class="field">
    <label>数据源</label>
    <div class="radio-group">
      <label><input type="radio" name="data_source" value="tencent" checked> 腾讯财经（默认）</label>
      <label><input type="radio" name="data_source" value="tushare"> Tushare Pro</label>
    </div>
  </div>
  <div class="field" id="tushare-fields" style="display:none">
    <label>Tushare Token</label>
    <div style="display:flex;align-items:center">
      <input type="text" id="tushare-token" placeholder="输入你的Tushare Pro Token">
      <button class="btn-test" onclick="testTushare()">测试连接</button>
    </div>
    <div id="test-result"></div>
    <div class="hint">在 tushare.pro 注册后获取Token。</div>
  </div>
  <div class="field">
    <label>建议取数时间</label>
    <input type="time" id="fetch-schedule" value="16:00">
    <div class="hint">仅作参考。实际定时取数通过 cron + python3 a_etf_trend.py daily 实现。</div>
  </div>
  <div class="field" id="settings-status" style="display:none"></div>
  <div class="strat-actions">
    <button class="btn-success" onclick="saveSettings()">保存设置</button>
  </div>
</div>
</div>
</div>

<script>
let DATA = null;
let sortCol = 'signal_type';
let sortDir = -1;
let catFilter = 'all';
let sigFilter = 'all';
let tierFilter = 'all';
let condEnabled = [true, true, true, true];

function switchTab(name) {
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  document.querySelector('.tab-btn[onclick*="' + name + '"]').classList.add('active');
  if (name === 'strategy') loadStrategyConfig();
  if (name === 'settings') loadSettings();
}

async function loadData(force) {
  const btn = document.getElementById('refresh-btn');
  const loading = document.getElementById('loading');
  const table = document.getElementById('main-table');
  btn.disabled = true;
  btn.textContent = '扫描中(首次约30s)...';
  if (!DATA) { loading.style.display = 'block'; table.style.display = 'none'; }
  try {
    const url = force ? '/api/scan?force=1' : '/api/scan';
    const resp = await fetch(url);
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    DATA = await resp.json();
    if (DATA.error) { alert('扫描出错: ' + DATA.error); return; }
    populateEtfSelect();
    renderAll();
    if (DATA.fetch_errors && DATA.fetch_errors.length > 0) {
      const errDiv = document.getElementById('fetch-error-bar');
      errDiv.innerHTML = '<strong>' + DATA.fetch_errors.length + '个标的拉取失败:</strong> ' + DATA.fetch_errors.slice(0,3).join('; ') + (DATA.fetch_errors.length > 3 ? ' ...' : '');
      errDiv.style.display = 'block';
    } else {
      document.getElementById('fetch-error-bar').style.display = 'none';
    }
  } catch(e) {
    document.getElementById('loading').innerHTML = '<p style="color:#e74c3c;font-size:14px">数据加载失败: ' + e.message + '</p><p style="margin-top:8px;color:#636e72;font-size:13px">请检查网络连接，确保可访问 web.ifzq.gtimg.cn</p><button class="btn" onclick="loadData(true)" style="margin-top:12px">重试</button>';
    return;
  } finally {
    btn.disabled = false;
    btn.textContent = '刷新数据';
    if (DATA && DATA.results) { loading.style.display = 'none'; table.style.display = ''; }
  }
}

function refresh() { loadData(true); }

function chgClass(v) { return v > 0.01 ? 'up' : v < -0.01 ? 'down' : 'flat'; }
function chgStr(v) { return (v > 0 ? '+' : '') + v.toFixed(2) + '%'; }

function renderAll() {
  if (!DATA) return;
  const b = DATA.benchmark;
  document.getElementById('bench').innerHTML =
    '上证 ' + b.price + ' <span class="' + chgClass(b.chg) + '">' + chgStr(b.chg) + '</span>';
  document.getElementById('scan-time').textContent = DATA.scan_time;

  const tAll = DATA.results.filter(r => r.tier === 'all').length;
  const tPartial = DATA.results.filter(r => r.tier === 'partial').length;
  const tNone = DATA.results.filter(r => r.tier === 'none').length;
  const holdN = DATA.results.filter(r => r.holding).length;
  document.getElementById('tc-all').textContent = tAll;
  document.getElementById('tc-partial').textContent = tPartial;
  document.getElementById('tc-none').textContent = tNone;

  document.getElementById('stats').innerHTML =
    '<div class="stat-card"><div class="label">监测标的</div><div class="value">' + DATA.total + '</div></div>' +
    '<div class="stat-card"><div class="label">趋势全满足</div><div class="value" style="color:#27ae60">' + tAll + '</div></div>' +
    '<div class="stat-card"><div class="label">部分满足</div><div class="value" style="color:#e67e22">' + tPartial + '</div></div>' +
    '<div class="stat-card"><div class="label">都不满足</div><div class="value" style="color:#95a5a6">' + tNone + '</div></div>' +
    '<div class="stat-card"><div class="label">持仓标的</div><div class="value" style="color:#0984e3">' + holdN + '</div></div>';

  renderSigStats();
  renderTable();
}

function renderSigStats() {
  if (!DATA) return;
  const tierRows = DATA.results.filter(r => r.tier === tierFilter);
  const breakoutN = tierRows.filter(r => r.signal_type === 'breakout').length;
  const pullbackN = tierRows.filter(r => r.signal_type === 'pullback').length;
  const overboughtN = tierRows.filter(r => r.signal_type === 'overbought').length;
  const strongN = tierRows.filter(r => r.signal_type === 'strong').length;
  const watchN = tierRows.filter(r => r.signal_type === 'watch').length;
  const tierNames = {all:'全满足', partial:'部分满足', none:'都不满足'};
  document.getElementById('sig-subtitle').textContent = tierNames[tierFilter] + '等级内 ' + tierRows.length + ' 个标的的日线信号分布';
  document.getElementById('sig-stats').innerHTML =
    '<div class="stat-card"><div class="label">当前等级</div><div class="value">' + tierRows.length + '</div></div>' +
    '<div class="stat-card"><div class="label">突破信号</div><div class="value" style="color:#e74c3c">' + breakoutN + '</div></div>' +
    '<div class="stat-card"><div class="label">回踩机会</div><div class="value" style="color:#27ae60">' + pullbackN + '</div></div>' +
    '<div class="stat-card"><div class="label">超买提醒</div><div class="value" style="color:#e67e22">' + overboughtN + '</div></div>' +
    '<div class="stat-card"><div class="label">强势持仓</div><div class="value" style="color:#3498db">' + strongN + '</div></div>' +
    '<div class="stat-card"><div class="label">观望</div><div class="value" style="color:#95a5a6">' + watchN + '</div></div>';
}

function renderTable() {
  const tbody = document.getElementById('tbody');
  const search = document.getElementById('search').value.toLowerCase();
  let rows = DATA.results.filter(r => {
    if (r.tier !== tierFilter) return false;
    if (catFilter !== 'all' && r.cat !== catFilter) return false;
    if (sigFilter === 'holding') { if (!r.holding) return false; }
    else if (sigFilter !== 'all') { if (r.signal_type !== sigFilter) return false; }
    if (search && !r.name.toLowerCase().includes(search) && !r.symbol.includes(search)) return false;
    return true;
  });

  rows.sort((a, b) => {
    let va = a[sortCol], vb = b[sortCol];
    if (sortCol === 'name') { va = a.name; vb = b.name; }
    if (sortCol === 'signal_type') { const so = {breakout:5,pullback:4,overbought:3,strong:2,watch:1}; va = so[a.signal_type]||0; vb = so[b.signal_type]||0; }
    if (sortCol === 'tier') { const to = {all:3,partial:2,none:1}; va = to[a.tier]||0; vb = to[b.tier]||0; }
    if (sortCol === 'grade') { const go = {S:5,A:4,B:3,C:2,D:1,'-':0}; va = go[a.grade]||0; vb = go[b.grade]||0; }
    if (sortCol === 'bt_ret') { va = a.bt_ret != null ? a.bt_ret : -999; vb = b.bt_ret != null ? b.bt_ret : -999; }
    if (sortCol === 'share_chg') { va = a.share_chg != null ? a.share_chg : -999; vb = b.share_chg != null ? b.share_chg : -999; }
    if (typeof va === 'string') return va.localeCompare(vb) * sortDir;
    return ((va || 0) - (vb || 0)) * sortDir;
  });

  let html = '';
  const condLabels = ['价格>MA20', 'MA20上行', 'MACD>0', '量价确认'];
  const tierLabels = {all:'月+周', partial:'部分', none:'不满足'};
  for (const r of rows) {
    const g = r.grade || '-';
    const rowClass = r.signal_type === 'breakout' ? 'entry-row' : (r.holding ? 'holding-row' : '');
    const star = r.holding ? '<span class="holding-star">★</span> ' : '';
    const dots = r.conditions.map(c => '<span class="dot ' + (c ? 'on' : 'off') + '"></span>').join('');
    const sigBadge = '<span class="signal-badge signal-' + r.signal_type + '">' + r.signal_label + '</span>';
    const hintStr = r.signal_type !== 'watch' ? '<span style="font-size:11px;color:' + r.signal_color + '">' + r.position_hint + '</span>' : '<span style="font-size:11px;color:#95a5a6">-</span>';
    const btStr = r.bt_ret != null ? '<span class="' + chgClass(r.bt_ret) + '">' + (r.bt_ret > 0 ? '+' : '') + r.bt_ret.toFixed(1) + '%</span>' : '-';
    const shareStr = r.share_chg != null ? '<span class="' + chgClass(r.share_chg) + '">' + (r.share_chg > 0 ? '+' : '') + r.share_chg.toFixed(1) + '%</span>' : '-';

    html += '<tr class="' + rowClass + '" data-sym="' + r.symbol + '" onclick="toggleDetail(this)">' +
      '<td>' + star + r.name + '</td>' +
      '<td><span class="trend-tag trend-' + r.tier + '">' + tierLabels[r.tier] + '</span></td>' +
      '<td><span class="grade grade-' + g + '">' + g + '</span></td>' +
      '<td><span class="tag tag-' + r.cat + '">' + r.cat + '</span></td>' +
      '<td>' + r.price.toFixed(3) + '</td>' +
      '<td class="' + chgClass(r.chg_1d) + '">' + chgStr(r.chg_1d) + '</td>' +
      '<td class="' + chgClass(r.chg_5d) + '">' + chgStr(r.chg_5d) + '</td>' +
      '<td class="' + chgClass(r.chg_20d) + '">' + chgStr(r.chg_20d) + '</td>' +
      '<td>' + shareStr + '</td>' +
      '<td><span class="cond">' + dots + '</span></td>' +
      '<td>' + sigBadge + '</td>' +
      '<td>' + hintStr + '</td>' +
      '<td>' + btStr + '</td>' +
      '<td class="' + chgClass(r.ma20_dist) + '">' + (r.ma20_dist > 0 ? '+' : '') + r.ma20_dist.toFixed(1) + '%</td>' +
      '<td>' + (r.rsi || '-') + '</td>' +
      '</tr>';

    const condHtml = condLabels.map((l, idx) =>
      '<span class="cond-item ' + (r.conditions[idx] ? 'met' : 'unmet') + '">' +
      (r.conditions[idx] ? '✓' : '✗') + ' ' + l + '</span>'
    ).join('');

    html += '<tr class="detail-row" data-detail="' + r.symbol + '">' +
      '<td colspan="15"><div class="detail-content">' +
      '<div class="detail-block"><h4>信号分类</h4>' +
        '<div class="item"><span class="k">类型</span><span style="color:' + r.signal_color + ';font-weight:700">' + r.signal_label + '</span></div>' +
        '<div class="item"><span class="k">仓位建议</span><span>' + r.position_hint + '</span></div>' +
        '<div class="item"><span class="k">判断依据</span><span style="font-size:12px">' + (r.signal_details||[]).join(' | ') + '</span></div>' +
      '</div>' +
      '<div class="detail-block"><h4>多周期过滤</h4>' +
        '<div class="cond-detail">' +
          '<span class="cond-item ' + (r.monthly_pass ? 'met' : 'unmet') + '">' + (r.monthly_pass ? '✓' : '✗') + ' ' + (r.monthly_detail || '月线') + '</span>' +
          '<span class="cond-item ' + (r.weekly_pass ? 'met' : 'unmet') + '">' + (r.weekly_pass ? '✓' : '✗') + ' ' + (r.weekly_detail || '周线') + '</span>' +
        '</div></div>' +
      '<div class="detail-block"><h4>日线条件</h4><div class="cond-detail">' + condHtml + '</div></div>' +
      '<div class="detail-block"><h4>技术指标</h4>' +
        '<div class="item"><span class="k">MA20</span><span>' + r.ma20.toFixed(3) + '</span></div>' +
        '<div class="item"><span class="k">MA60</span><span>' + (r.ma60 ? r.ma60.toFixed(3) : '-') + '</span></div>' +
        '<div class="item"><span class="k">ATR(20)</span><span>' + r.atr.toFixed(4) + '</span></div>' +
        '<div class="item"><span class="k">MACD柱</span><span class="' + chgClass(r.macd_hist) + '">' + r.macd_hist.toFixed(4) + '</span></div>' +
        '<div class="item"><span class="k">RSI</span><span>' + (r.rsi || '-') + '</span></div>' +
        '<div class="item"><span class="k">量比</span><span>' + (r.vol_ratio || '-') + '</span></div>' +
      '</div>' +
      '<div class="detail-block"><h4>假设入场</h4>' +
        '<div class="item"><span class="k">追踪止损</span><span>' + r.stop_price.toFixed(3) + '</span></div>' +
        '<div class="item"><span class="k">硬止损(8%)</span><span>' + (r.price * 0.92).toFixed(3) + '</span></div>' +
        '<div class="item"><span class="k">止损系数K</span><span>' + r.K.toFixed(1) + '</span></div>' +
        '<div class="item"><span class="k">止损距离</span><span>' + ((r.stop_price / r.price - 1) * 100).toFixed(1) + '%</span></div>' +
      '</div>' +
      '<div class="detail-block"><h4>份额流向</h4>' +
        '<div class="item"><span class="k">近1月变化</span><span class="' + (r.share_chg != null ? chgClass(r.share_chg) : '') + '">' + (r.share_chg != null ? (r.share_chg > 0 ? '+' : '') + r.share_chg.toFixed(1) + '%' : '-') + '</span></div>' +
        '<div class="item"><span class="k">最新份额</span><span>' + (r.share_latest != null ? r.share_latest.toFixed(1) + '亿' : '-') + '</span></div>' +
      '</div>' +
      '</div></td></tr>';
  }
  tbody.innerHTML = html;
}

function toggleDetail(tr) {
  const sym = tr.dataset.sym;
  const detail = document.querySelector('tr[data-detail="' + sym + '"]');
  if (detail) detail.classList.toggle('show');
}

document.getElementById('cat-filter').addEventListener('click', e => {
  if (!e.target.dataset.cat) return;
  catFilter = e.target.dataset.cat;
  document.querySelectorAll('#cat-filter .filter-btn').forEach(b => b.classList.remove('active'));
  e.target.classList.add('active');
  applyFilters();
});
document.getElementById('sig-filter').addEventListener('click', e => {
  if (!e.target.dataset.sig) return;
  sigFilter = e.target.dataset.sig;
  document.querySelectorAll('#sig-filter .filter-btn').forEach(b => b.classList.remove('active'));
  e.target.classList.add('active');
  applyFilters();
});

function applyFilters() { if (DATA) { renderSigStats(); renderTable(); } }

document.getElementById('tier-filter').addEventListener('click', e => {
  const btn = e.target.closest('.tier-btn');
  if (!btn || !btn.dataset.tier) return;
  tierFilter = btn.dataset.tier;
  document.querySelectorAll('#tier-filter .tier-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  sigFilter = 'all';
  document.querySelectorAll('#sig-filter .filter-btn').forEach(b => b.classList.remove('active'));
  document.querySelector('#sig-filter .filter-btn[data-sig="all"]').classList.add('active');
  renderSigStats();
  renderTable();
});

document.querySelectorAll('th[data-sort]').forEach(th => {
  th.addEventListener('click', () => {
    const col = th.dataset.sort;
    if (sortCol === col) { sortDir *= -1; } else { sortCol = col; sortDir = -1; }
    document.querySelectorAll('th').forEach(h => h.classList.remove('sorted'));
    th.classList.add('sorted');
    renderTable();
  });
});

/* ---- Strategy tab ---- */

function populateEtfSelect() {
  const sel = document.getElementById('bt-symbol');
  const optSel = document.getElementById('opt-scope');
  if (!DATA || sel.options.length > 1) return;
  sel.innerHTML = '';
  let optHtml = '<option value="global">全局(36个ETF)</option>';
  for (const r of DATA.results) {
    sel.innerHTML += '<option value="' + r.symbol + '">' + r.name + '</option>';
    optHtml += '<option value="' + r.symbol + '">' + r.name + '</option>';
  }
  optSel.innerHTML = optHtml;
  populatePerEtfSelect();
}

function togCond(el, idx) {
  condEnabled[idx] = !condEnabled[idx];
  el.classList.toggle('on');
}

function togKdj(el) {
  el.classList.toggle('on');
  const p = document.getElementById('kdj-params');
  p.style.opacity = el.classList.contains('on') ? '1' : '0.4';
}

function togMonthly(el) {
  el.classList.toggle('on');
  const p = document.getElementById('monthly-params');
  p.style.opacity = el.classList.contains('on') ? '1' : '0.4';
}

function togWeekly(el) {
  el.classList.toggle('on');
  const p = document.getElementById('weekly-params');
  p.style.opacity = el.classList.contains('on') ? '1' : '0.4';
}

function collectConfig() {
  return {
    monthly: {
      enabled: document.getElementById('tog-monthly').classList.contains('on'),
      ma_period: parseInt(document.getElementById('p-monthly-ma').value),
      require_ma_rising: document.getElementById('p-monthly-rising').checked,
    },
    weekly: {
      enabled: document.getElementById('tog-weekly').classList.contains('on'),
      ma_period: parseInt(document.getElementById('p-weekly-ma').value),
      require_ma_rising: document.getElementById('p-weekly-rising').checked,
      require_macd_positive: document.getElementById('p-weekly-macd').checked,
      min_conditions: parseInt(document.getElementById('p-weekly-min').value),
    },
    entry: {
      ma_period: parseInt(document.getElementById('p-ma-period').value),
      ma_slope_lookback: parseInt(document.getElementById('p-ma-slope').value),
      macd_fast: parseInt(document.getElementById('p-macd-fast').value),
      macd_slow: parseInt(document.getElementById('p-macd-slow').value),
      macd_signal: parseInt(document.getElementById('p-macd-signal').value),
      vol_short: parseInt(document.getElementById('p-vol-short').value),
      vol_long: parseInt(document.getElementById('p-vol-long').value),
      vol_ratio_threshold: parseFloat(document.getElementById('p-vol-thresh').value),
      conditions_enabled: [...condEnabled],
      use_kdj: document.getElementById('tog-kdj').classList.contains('on'),
      kdj_period: parseInt(document.getElementById('p-kdj-period').value),
      kdj_k_smooth: 3, kdj_d_smooth: 3,
      kdj_oversold: parseInt(document.getElementById('p-kdj-oversold').value),
    },
    exit: {
      base_k: parseFloat(document.getElementById('p-base-k').value),
      min_k: parseFloat(document.getElementById('p-min-k').value),
      hard_stop_pct: parseInt(document.getElementById('p-hard-stop').value) / 100,
      atr_period: parseInt(document.getElementById('p-atr-period').value),
      ma_exit_period: parseInt(document.getElementById('p-ma-exit').value),
      ma_exit_days: parseInt(document.getElementById('p-ma-exit-days').value),
    },
    bonus: { new_high_period: 20, ma_long_period: 60, rsi_period: 14, rsi_threshold: 50 }
  };
}

function populateForm(cfg) {
  const m = cfg.monthly || {};
  const w = cfg.weekly || {};
  const e = cfg.entry || {};
  const x = cfg.exit || {};

  // Monthly
  const mTog = document.getElementById('tog-monthly');
  if (m.enabled !== false) { mTog.classList.add('on'); document.getElementById('monthly-params').style.opacity='1'; }
  else { mTog.classList.remove('on'); document.getElementById('monthly-params').style.opacity='0.4'; }
  document.getElementById('p-monthly-ma').value = m.ma_period || 20;
  document.getElementById('p-monthly-rising').checked = m.require_ma_rising !== false;

  // Weekly
  const wTog = document.getElementById('tog-weekly');
  if (w.enabled !== false) { wTog.classList.add('on'); document.getElementById('weekly-params').style.opacity='1'; }
  else { wTog.classList.remove('on'); document.getElementById('weekly-params').style.opacity='0.4'; }
  document.getElementById('p-weekly-ma').value = w.ma_period || 10;
  document.getElementById('p-weekly-min').value = w.min_conditions || 2;
  document.getElementById('p-weekly-rising').checked = w.require_ma_rising !== false;
  document.getElementById('p-weekly-macd').checked = w.require_macd_positive !== false;

  // Daily entry
  document.getElementById('p-ma-period').value = e.ma_period || 20;
  document.getElementById('p-ma-slope').value = e.ma_slope_lookback || 5;
  document.getElementById('p-macd-fast').value = e.macd_fast || 12;
  document.getElementById('p-macd-slow').value = e.macd_slow || 26;
  document.getElementById('p-macd-signal').value = e.macd_signal || 9;
  document.getElementById('p-vol-short').value = e.vol_short || 5;
  document.getElementById('p-vol-long').value = e.vol_long || 20;
  document.getElementById('p-vol-thresh').value = e.vol_ratio_threshold || 1.2;
  document.getElementById('p-base-k').value = x.base_k || 3.0;
  document.getElementById('p-min-k').value = x.min_k || 1.2;
  document.getElementById('p-hard-stop').value = Math.round((x.hard_stop_pct || 0.08) * 100);
  document.getElementById('p-atr-period').value = x.atr_period || 20;
  document.getElementById('p-ma-exit').value = x.ma_exit_period || 20;
  document.getElementById('p-ma-exit-days').value = x.ma_exit_days || 3;
  document.getElementById('p-kdj-period').value = e.kdj_period || 9;
  document.getElementById('p-kdj-oversold').value = e.kdj_oversold || 20;

  condEnabled = (e.conditions_enabled || [true,true,true,true]).slice();
  for (let i = 0; i < 4; i++) {
    const t = document.getElementById('tog-c' + (i+1));
    if (condEnabled[i]) t.classList.add('on'); else t.classList.remove('on');
  }
  const kdjTog = document.getElementById('tog-kdj');
  if (e.use_kdj) { kdjTog.classList.add('on'); document.getElementById('kdj-params').style.opacity='1'; }
  else { kdjTog.classList.remove('on'); document.getElementById('kdj-params').style.opacity='0.4'; }
}

function loadStrategyConfig() {
  fetch('/api/strategy').then(r=>r.json()).then(cfg => populateForm(cfg));
}

function resetConfig() {
  fetch('/api/strategy?default=1').then(r=>r.json()).then(cfg => {
    populateForm(cfg);
    document.getElementById('bt-result').innerHTML = '<div class="test-result test-ok" style="margin-top:8px">已恢复默认参数</div>';
  });
}

function saveStrategyConfig() {
  const cfg = collectConfig();
  fetch('/api/strategy', {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(cfg)
  }).then(r=>r.json()).then(d => {
    document.getElementById('bt-result').innerHTML =
      '<div class="test-result test-ok" style="margin-top:8px">配置已保存 — <a href="javascript:void(0)" onclick="switchTab(\'monitor\');loadData(true)" style="color:#0984e3;text-decoration:underline">刷新监控数据</a></div>';
  });
}

async function runBacktest() {
  const btn = document.getElementById('btn-bt');
  btn.disabled = true; btn.textContent = '回测中...';
  const cfg = collectConfig();
  const sym = document.getElementById('bt-symbol').value;
  const days = parseInt(document.getElementById('bt-days').value);
  try {
    const resp = await fetch('/api/backtest', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({symbol:sym, days:days, config:cfg})
    });
    const r = await resp.json();
    renderBtResult(r);
  } catch(e) {
    document.getElementById('bt-result').innerHTML = '<div class="test-result test-fail">回测失败: '+e+'</div>';
  }
  btn.disabled = false; btn.textContent = '运行回测';
}

function renderBtResult(r) {
  const el = document.getElementById('bt-result');
  if (r.error) { el.innerHTML = '<div class="test-result test-fail">' + r.error + '</div>'; return; }
  const rc = r.total_return > 0 ? 'up' : 'down';
  let h = '<div class="bt-metrics">' +
    '<div class="bt-metric"><div class="v ' + rc + '">' + (r.total_return>0?'+':'') + r.total_return + '%</div><div class="l">总收益</div></div>' +
    '<div class="bt-metric"><div class="v">' + (r.annual_return>0?'+':'') + r.annual_return + '%</div><div class="l">年化</div></div>' +
    '<div class="bt-metric"><div class="v down">-' + r.max_drawdown + '%</div><div class="l">最大回撤</div></div>' +
    '<div class="bt-metric"><div class="v">' + r.sharpe + '</div><div class="l">Sharpe</div></div>' +
    '<div class="bt-metric"><div class="v">' + r.trades + '</div><div class="l">交易笔数</div></div>' +
    '<div class="bt-metric"><div class="v">' + r.win_rate + '%</div><div class="l">胜率</div></div>' +
    '<div class="bt-metric"><div class="v up">+' + r.avg_win + '%</div><div class="l">平均盈</div></div>' +
    '<div class="bt-metric"><div class="v down">' + r.avg_loss + '%</div><div class="l">平均亏</div></div>' +
    '</div>';
  if (r.trade_list && r.trade_list.length) {
    h += '<div class="bt-trades"><table><thead><tr><th>入场</th><th>出场</th><th>入价</th><th>出价</th><th>收益</th><th>天数</th><th>原因</th></tr></thead><tbody>';
    for (const t of r.trade_list) {
      h += '<tr><td>'+t.entry_date+'</td><td>'+t.exit_date+'</td><td>'+t.entry_price+'</td><td>'+t.exit_price+'</td><td class="'+(t.pnl_pct>0?'up':'down')+'">'+(t.pnl_pct>0?'+':'')+t.pnl_pct+'%</td><td>'+t.hold_days+'</td><td>'+t.exit_reason+'</td></tr>';
    }
    h += '</tbody></table></div>';
  }
  el.innerHTML = h;
}

async function runOptimize() {
  const btn = document.getElementById('btn-opt');
  btn.disabled = true; btn.textContent = '优化中(约30s)...';
  const scope = document.getElementById('opt-scope').value;
  const days = parseInt(document.getElementById('opt-days').value);
  try {
    const resp = await fetch('/api/optimize', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({scope:scope, days:days})
    });
    const r = await resp.json();
    renderOptResult(r);
  } catch(e) {
    document.getElementById('opt-result').innerHTML = '<div class="test-result test-fail">优化失败: '+e+'</div>';
  }
  btn.disabled = false; btn.textContent = '开始优化';
}

function loadOptResults() {
  fetch('/api/optimize/results').then(r=>r.json()).then(list => {
    if (!list || !list.length) { document.getElementById('opt-result').innerHTML = '<div class="hint">暂无优化历史</div>'; return; }
    renderOptResult(list[0]);
  });
}

function renderOptResult(r) {
  const el = document.getElementById('opt-result');
  if (r.error) { el.innerHTML = '<div class="test-result test-fail">' + r.error + '</div>'; return; }
  let h = '';
  if (r.best) {
    const p = r.best.params;
    h += '<div class="opt-best"><div class="title">最优参数 (评分 ' + r.best.score + ')</div>' +
      '<div>MA=' + p.ma_period + '  Base_K=' + p.base_k + '  Min_K=' + p.min_k + '  止损=' + (p.hard_stop_pct*100).toFixed(0) + '%</div>' +
      '<div style="margin-top:4px;font-size:13px;color:#636e72">收益' + (r.best.avg_return>0?'+':'') + r.best.avg_return + '% | 回撤' + r.best.avg_drawdown + '% | 胜率' + r.best.avg_winrate + '% | ' + r.best.total_trades + '笔</div>' +
      '<button class="btn-secondary" style="margin-top:8px;font-size:12px;padding:6px 14px" onclick="applyOptResult()">应用此参数</button></div>';
  }
  if (r.rankings && r.rankings.length > 1) {
    h += '<div class="opt-table"><table><thead><tr><th>#</th><th>MA</th><th>K</th><th>止损</th><th>评分</th><th>收益</th><th>回撤</th><th>胜率</th><th>交易</th></tr></thead><tbody>';
    for (let i = 0; i < r.rankings.length; i++) {
      const rk = r.rankings[i];
      const p = rk.params;
      h += '<tr'+(i===0?' style="font-weight:700"':'')+'><td>'+(i+1)+'</td><td>'+p.ma_period+'</td><td>'+p.base_k+'/'+p.min_k+'</td><td>'+(p.hard_stop_pct*100).toFixed(0)+'%</td><td>'+rk.score+'</td><td class="'+(rk.avg_return>0?'up':'down')+'">'+(rk.avg_return>0?'+':'')+rk.avg_return+'%</td><td>'+rk.avg_drawdown+'%</td><td>'+rk.avg_winrate+'%</td><td>'+rk.total_trades+'</td></tr>';
    }
    h += '</tbody></table></div>';
  }
  h += '<div class="hint" style="margin-top:8px">' + (r.timestamp||'') + ' | 测试' + (r.tested||0) + '组合 | 范围:' + (r.scope||'global') + ' | ' + (r.days||0) + '天</div>';
  el.innerHTML = h;
  if (r.best) window._lastOptBest = r.best.params;
}

function applyOptResult() {
  if (!window._lastOptBest) return;
  const p = window._lastOptBest;
  document.getElementById('p-ma-period').value = p.ma_period;
  document.getElementById('p-base-k').value = p.base_k;
  document.getElementById('p-min-k').value = p.min_k;
  document.getElementById('p-hard-stop').value = (p.hard_stop_pct*100).toFixed(0);
  document.getElementById('bt-result').innerHTML = '<div class="test-result test-ok" style="margin-top:8px">已应用最优参数，可运行回测验证</div>';
}

function populatePerEtfSelect() {
  const sel = document.getElementById('per-etf-symbol');
  if (!DATA || sel.options.length > 1) return;
  sel.innerHTML = '';
  for (const r of DATA.results) {
    sel.innerHTML += '<option value="' + r.symbol + '">' + r.name + '</option>';
  }
}

function loadPerEtfConfig() {
  const sym = document.getElementById('per-etf-symbol').value;
  fetch('/api/strategy/per-etf/' + sym).then(r=>r.json()).then(cfg => {
    const el = document.getElementById('per-etf-info');
    if (!cfg || Object.keys(cfg).length === 0) {
      el.innerHTML = '<span style="color:#95a5a6">该ETF使用全局参数，无个别覆盖</span>';
    } else {
      el.innerHTML = '<pre style="background:#f8f9fa;padding:8px;border-radius:4px;font-size:12px;max-width:400px;overflow:auto">' + JSON.stringify(cfg, null, 2) + '</pre>';
    }
  });
}

function clearPerEtfConfig() {
  const sym = document.getElementById('per-etf-symbol').value;
  const name = document.getElementById('per-etf-symbol').selectedOptions[0].text;
  if (!confirm('确定清除 ' + name + ' 的个别参数？将恢复使用全局参数。')) return;
  fetch('/api/strategy/per-etf/' + sym, { method: 'DELETE' })
    .then(r => r.json()).then(d => {
      document.getElementById('per-etf-info').innerHTML = '<span style="color:#27ae60">已清除，恢复全局参数</span>';
    });
}

/* ---- Settings tab ---- */

function loadSettings() {
  fetch('/api/settings').then(r => r.json()).then(s => {
    document.querySelector('input[name=data_source][value="' + (s.data_source || 'tencent') + '"]').checked = true;
    document.getElementById('tushare-token').value = s.tushare_token || '';
    document.getElementById('fetch-schedule').value = s.fetch_schedule || '16:00';
    toggleTushareFields();
    document.getElementById('test-result').innerHTML = '';
    document.getElementById('settings-status').style.display = 'none';
  });
}

document.querySelectorAll('input[name=data_source]').forEach(r => {
  r.addEventListener('change', toggleTushareFields);
});

function toggleTushareFields() {
  const isTushare = document.querySelector('input[name=data_source]:checked').value === 'tushare';
  document.getElementById('tushare-fields').style.display = isTushare ? 'block' : 'none';
}

function testTushare() {
  const token = document.getElementById('tushare-token').value.trim();
  const el = document.getElementById('test-result');
  el.innerHTML = '<div class="test-result" style="background:#fff3cd;color:#856404">测试中...</div>';
  fetch('/api/test-tushare', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({token: token})
  }).then(r => r.json()).then(d => {
    if (d.ok) { el.innerHTML = '<div class="test-result test-ok">' + d.msg + '</div>'; }
    else { el.innerHTML = '<div class="test-result test-fail">' + d.msg + '</div>'; }
  }).catch(e => {
    el.innerHTML = '<div class="test-result test-fail">请求失败: ' + e + '</div>';
  });
}

function saveSettings() {
  const ds = document.querySelector('input[name=data_source]:checked').value;
  const token = document.getElementById('tushare-token').value.trim();
  const schedule = document.getElementById('fetch-schedule').value;
  const el = document.getElementById('settings-status');
  fetch('/api/settings', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({data_source: ds, tushare_token: token, fetch_schedule: schedule})
  }).then(r => r.json()).then(d => {
    el.style.display = 'block';
    el.innerHTML = '<div class="test-result test-ok">' + (d.msg || '已保存') + '</div>';
    setTimeout(() => el.style.display = 'none', 2000);
  }).catch(e => {
    el.style.display = 'block';
    el.innerHTML = '<div class="test-result test-fail">保存失败: ' + e + '</div>';
  });
}

loadData(false);
</script>
</body>
</html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))

        elif self.path.startswith("/api/scan"):
            force = "force=1" in self.path
            try:
                data = get_cached_data(force=force)
                self._send_json(data)
            except Exception as e:
                traceback.print_exc()
                self._send_json({"error": str(e)}, 500)

        elif self.path == "/api/settings":
            self._send_json(load_etf_settings())

        elif self.path.startswith("/api/strategy/per-etf/"):
            sym = self.path.split("/")[-1]
            cfg = load_strategy_config()
            per_etf = cfg.get("per_etf", {}).get(sym, {})
            self._send_json(per_etf)

        elif self.path.startswith("/api/strategy"):
            if "default=1" in self.path:
                self._send_json(DEFAULT_STRATEGY)
            else:
                self._send_json(load_strategy_config())

        elif self.path == "/api/optimize/results":
            self._send_json(load_optimize_results())

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body) if body else {}
        except (json.JSONDecodeError, ValueError):
            self._send_json({"ok": False, "msg": "Invalid JSON"}, 400)
            return

        if self.path == "/api/settings":
            save_etf_settings(data)
            self._send_json({"ok": True, "msg": "设置已保存"})

        elif self.path == "/api/test-tushare":
            token = data.get("token", "")
            if not token:
                self._send_json({"ok": False, "msg": "Token不能为空"})
                return
            try:
                bars = fetch_tushare_cn_kline("sz159516", 5, token)
                if bars:
                    self._send_json({"ok": True, "msg": f"连接成功，获取{len(bars)}条数据"})
                else:
                    self._send_json({"ok": False, "msg": "连接成功但未返回数据，请检查Token权限(需要fund_daily接口)"})
            except Exception as e:
                err = str(e)
                if "SSL" in err or "certificate" in err.lower():
                    hint = "SSL证书错误 — 请尝试: pip install certifi"
                elif "IP" in err and "超限" in err:
                    hint = "Tushare IP限制 — 请升级积分或稍后重试"
                elif "timed out" in err.lower() or "timeout" in err.lower():
                    hint = "连接超时 — 请检查网络是否可访问 api.tushare.pro"
                elif "权限" in err or "permission" in err.lower():
                    hint = "Token无权限 — 请在tushare.pro确认已开通fund_daily接口"
                else:
                    hint = err
                self._send_json({"ok": False, "msg": f"连接失败: {hint}"})

        elif self.path == "/api/strategy":
            try:
                save_strategy_config(data)
                self._send_json({"ok": True, "msg": "策略配置已保存"})
            except Exception as e:
                self._send_json({"ok": False, "msg": str(e)}, 500)

        elif self.path == "/api/backtest":
            try:
                symbol = data.get("symbol", "sz159516")
                days = data.get("days", 500)
                config = data.get("config")
                result = run_backtest_with_config(symbol, config, days)
                self._send_json(result)
            except Exception as e:
                traceback.print_exc()
                self._send_json({"error": str(e)}, 500)

        elif self.path == "/api/optimize":
            try:
                scope = data.get("scope", "global")
                days = data.get("days", 1000)
                result = run_optimization(scope, days)
                self._send_json(result)
            except Exception as e:
                traceback.print_exc()
                self._send_json({"error": str(e)}, 500)

        else:
            self._send_json({"error": "Not found"}, 404)

    def do_DELETE(self):
        if self.path.startswith("/api/strategy/per-etf/"):
            sym = self.path.split("/")[-1]
            cfg = load_strategy_config()
            per_etf = cfg.get("per_etf", {})
            if sym in per_etf:
                del per_etf[sym]
                cfg["per_etf"] = per_etf
                save_strategy_config(cfg)
            self._send_json({"ok": True, "msg": f"已清除 {sym} 覆盖参数"})
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        pass


def main():
    port = 8080
    args = sys.argv[1:]
    if "--port" in args:
        idx = args.index("--port")
        if idx + 1 < len(args):
            port = int(args[idx + 1])

    server = http.server.HTTPServer(("0.0.0.0", port), Handler)
    print(f"ETF趋势监控 Dashboard 启动")
    print(f"  http://localhost:{port}")
    print(f"  首次加载会拉取36个ETF数据，约30秒...")
    print(f"  Ctrl+C 退出\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        server.server_close()


if __name__ == "__main__":
    main()
