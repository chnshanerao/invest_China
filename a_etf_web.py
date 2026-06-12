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
)
from a_trend_trader import update_cn_ticker
from chokepoint_trader import (
    init_db, get_bars, sma, calc_rsi, calc_macd, calc_atr, calc_volume_ratio,
)


def scan_all_data():
    conn = init_db()
    cal = load_calibration()
    try:
        update_cn_ticker(conn, BENCHMARK, verbose=False)
    except Exception:
        pass

    bench_bars = get_bars(conn, BENCHMARK, 300)
    bench_price = bench_bars[-1]["close"] if bench_bars else 0
    bench_chg = 0
    if len(bench_bars) >= 2:
        bench_chg = (bench_bars[-1]["close"] - bench_bars[-2]["close"]) / bench_bars[-2]["close"] * 100

    results = []
    for name, cfg in ETF_BASKET.items():
        sym = cfg["symbol"]
        cat = cfg.get("cat", "行业")
        holding = cfg.get("holding", False)

        try:
            update_cn_ticker(conn, sym, verbose=False)
        except Exception:
            continue

        bars = get_bars(conn, sym, 300)
        if len(bars) < 60:
            results.append({
                "name": name, "symbol": sym, "cat": cat, "holding": holding,
                "price": 0, "chg_1d": 0, "chg_5d": 0, "chg_20d": 0,
                "entry": False, "conditions": [False]*4, "details": [],
                "ma20": 0, "ma20_dist": 0, "ma60": 0,
                "rsi": 0, "atr": 0, "vol_ratio": 0,
                "macd_hist": 0, "data_ok": False,
            })
            continue

        entry, details, ex = check_entry(bars)

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

        results.append({
            "name": name, "symbol": sym, "cat": cat, "holding": holding,
            "price": round(closes[i], 3),
            "chg_1d": round(ex.get("chg_1d", 0), 2),
            "chg_5d": round(ex.get("chg_5d", 0), 2),
            "chg_20d": round(ex.get("chg_20d", 0), 2),
            "entry": entry,
            "conditions": [c1, c2, c3, c4],
            "cond_count": sum([c1, c2, c3, c4]),
            "details": details,
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
        })

    conn.close()

    results.sort(key=lambda x: (-x["cond_count"], -x["chg_5d"]))

    return {
        "scan_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "benchmark": {"price": round(bench_price, 2), "chg": round(bench_chg, 2)},
        "total": len(results),
        "entry_count": sum(1 for r in results if r["entry"]),
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
}
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

<div class="stats" id="stats"></div>

<div class="filters">
  <div class="filter-group" id="cat-filter">
    <button class="filter-btn active" data-cat="all">全部</button>
    <button class="filter-btn" data-cat="行业">行业(20)</button>
    <button class="filter-btn" data-cat="细分">细分(12)</button>
    <button class="filter-btn" data-cat="策略">策略(4)</button>
  </div>
  <div class="filter-group" id="sig-filter">
    <button class="filter-btn active" data-sig="all">全部</button>
    <button class="filter-btn" data-sig="entry">入场信号</button>
    <button class="filter-btn" data-sig="holding">持仓</button>
    <button class="filter-btn" data-sig="close">接近入场(3/4)</button>
  </div>
  <input class="search" id="search" placeholder="搜索标的..." oninput="applyFilters()">
</div>

<div class="table-wrap">
  <div class="loading" id="loading"><div class="spinner"></div><p style="margin-top:12px">正在扫描36个ETF...</p></div>
  <table id="main-table" style="display:none">
    <thead>
      <tr>
        <th data-sort="name">标的 <span class="arrow">↕</span></th>
        <th data-sort="grade">评级 <span class="arrow">↕</span></th>
        <th data-sort="cat">分类 <span class="arrow">↕</span></th>
        <th data-sort="price">现价 <span class="arrow">↕</span></th>
        <th data-sort="chg_1d">1日% <span class="arrow">↕</span></th>
        <th data-sort="chg_5d">5日% <span class="arrow">↕</span></th>
        <th data-sort="chg_20d">20日% <span class="arrow">↕</span></th>
        <th data-sort="cond_count">条件 <span class="arrow">↕</span></th>
        <th data-sort="entry">信号 <span class="arrow">↕</span></th>
        <th data-sort="bt_ret">回测 <span class="arrow">↕</span></th>
        <th data-sort="ma20_dist">MA20距 <span class="arrow">↕</span></th>
        <th data-sort="rsi">RSI <span class="arrow">↕</span></th>
      </tr>
    </thead>
    <tbody id="tbody"></tbody>
  </table>
</div>

<script>
let DATA = null;
let sortCol = 'cond_count';
let sortDir = -1;
let catFilter = 'all';
let sigFilter = 'all';

async function loadData(force) {
  const btn = document.getElementById('refresh-btn');
  const loading = document.getElementById('loading');
  const table = document.getElementById('main-table');
  btn.disabled = true;
  btn.textContent = '扫描中...';
  if (!DATA) { loading.style.display = 'block'; table.style.display = 'none'; }
  try {
    const url = force ? '/api/scan?force=1' : '/api/scan';
    const resp = await fetch(url);
    DATA = await resp.json();
    renderAll();
  } catch(e) {
    alert('数据加载失败: ' + e.message);
  }
  btn.disabled = false;
  btn.textContent = '刷新数据';
  loading.style.display = 'none';
  table.style.display = '';
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

  const realEntry = DATA.results.filter(r => r.entry && r.grade !== 'C' && r.grade !== 'D').length;
  const filteredEntry = DATA.results.filter(r => r.entry && (r.grade === 'C' || r.grade === 'D')).length;
  const holdN = DATA.results.filter(r => r.holding).length;
  const closeN = DATA.results.filter(r => r.cond_count >= 3 && !r.entry).length;
  const entryLabel = filteredEntry > 0 ? realEntry + ' <span style="font-size:12px;color:#b2bec3">(' + filteredEntry + '过滤)</span>' : '' + realEntry;
  document.getElementById('stats').innerHTML =
    '<div class="stat-card"><div class="label">监测标的</div><div class="value">' + DATA.total + '</div></div>' +
    '<div class="stat-card"><div class="label">入场信号</div><div class="value" style="color:#00b894">' + entryLabel + '</div></div>' +
    '<div class="stat-card"><div class="label">接近入场(3/4)</div><div class="value" style="color:#fdcb6e">' + closeN + '</div></div>' +
    '<div class="stat-card"><div class="label">持仓标的</div><div class="value" style="color:#0984e3">' + holdN + '</div></div>';

  renderTable();
}

function renderTable() {
  const tbody = document.getElementById('tbody');
  const search = document.getElementById('search').value.toLowerCase();
  let rows = DATA.results.filter(r => {
    if (catFilter !== 'all' && r.cat !== catFilter) return false;
    if (sigFilter === 'entry' && !r.entry) return false;
    if (sigFilter === 'holding' && !r.holding) return false;
    if (sigFilter === 'close' && (r.cond_count < 3 || r.entry)) return false;
    if (search && !r.name.toLowerCase().includes(search) && !r.symbol.includes(search)) return false;
    return true;
  });

  rows.sort((a, b) => {
    let va = a[sortCol], vb = b[sortCol];
    if (sortCol === 'name') { va = a.name; vb = b.name; }
    if (sortCol === 'entry') { va = a.entry ? 1 : 0; vb = b.entry ? 1 : 0; }
    if (sortCol === 'grade') { const go = {S:5,A:4,B:3,C:2,D:1,'-':0}; va = go[a.grade]||0; vb = go[b.grade]||0; }
    if (sortCol === 'bt_ret') { va = a.bt_ret != null ? a.bt_ret : -999; vb = b.bt_ret != null ? b.bt_ret : -999; }
    if (typeof va === 'string') return va.localeCompare(vb) * sortDir;
    return ((va || 0) - (vb || 0)) * sortDir;
  });

  let html = '';
  const condLabels = ['价格>MA20', 'MA20上行', 'MACD>0', '量价确认'];
  for (const r of rows) {
    const g = r.grade || '-';
    const isFiltered = (g === 'C' || g === 'D') && r.entry;
    const rowClass = isFiltered ? 'filtered-row' : (r.entry ? 'entry-row' : (r.holding ? 'holding-row' : ''));
    const star = r.holding ? '<span class="holding-star">★</span> ' : '';
    const dots = r.conditions.map(c => '<span class="dot ' + (c ? 'on' : 'off') + '"></span>').join('');
    let sigBadge;
    if (r.entry && isFiltered) {
      sigBadge = '<span class="signal-badge" style="background:#dfe6e9;color:#636e72">已过滤</span>';
    } else if (r.entry) {
      sigBadge = '<span class="signal-badge signal-entry">入场</span>';
    } else if (r.cond_count >= 3) {
      sigBadge = '<span class="signal-badge" style="background:#fff3cd;color:#856404">' + r.cond_count + '/4</span>';
    } else {
      sigBadge = '<span class="signal-badge signal-none">' + r.cond_count + '/4</span>';
    }
    const btStr = r.bt_ret != null ? '<span class="' + chgClass(r.bt_ret) + '">' + (r.bt_ret > 0 ? '+' : '') + r.bt_ret.toFixed(1) + '%</span>' : '-';

    html += '<tr class="' + rowClass + '" data-sym="' + r.symbol + '" onclick="toggleDetail(this)">' +
      '<td>' + star + r.name + '</td>' +
      '<td><span class="grade grade-' + g + '">' + g + '</span></td>' +
      '<td><span class="tag tag-' + r.cat + '">' + r.cat + '</span></td>' +
      '<td>' + r.price.toFixed(3) + '</td>' +
      '<td class="' + chgClass(r.chg_1d) + '">' + chgStr(r.chg_1d) + '</td>' +
      '<td class="' + chgClass(r.chg_5d) + '">' + chgStr(r.chg_5d) + '</td>' +
      '<td class="' + chgClass(r.chg_20d) + '">' + chgStr(r.chg_20d) + '</td>' +
      '<td><span class="cond">' + dots + '</span></td>' +
      '<td>' + sigBadge + '</td>' +
      '<td>' + btStr + '</td>' +
      '<td class="' + chgClass(r.ma20_dist) + '">' + (r.ma20_dist > 0 ? '+' : '') + r.ma20_dist.toFixed(1) + '%</td>' +
      '<td>' + (r.rsi || '-') + '</td>' +
      '</tr>';

    // detail row
    const condHtml = condLabels.map((l, idx) =>
      '<span class="cond-item ' + (r.conditions[idx] ? 'met' : 'unmet') + '">' +
      (r.conditions[idx] ? '✓' : '✗') + ' ' + l + '</span>'
    ).join('');

    html += '<tr class="detail-row" data-detail="' + r.symbol + '">' +
      '<td colspan="12"><div class="detail-content">' +
      '<div class="detail-block"><h4>入场条件</h4><div class="cond-detail">' + condHtml + '</div></div>' +
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
      (r.details.length ? '<div class="detail-block"><h4>信号因子</h4><div style="font-size:13px;color:#2d3436">' + r.details.join(' | ') + '</div></div>' : '') +
      '</div></td></tr>';
  }
  tbody.innerHTML = html;
}

function toggleDetail(tr) {
  const sym = tr.dataset.sym;
  const detail = document.querySelector('tr[data-detail="' + sym + '"]');
  if (detail) detail.classList.toggle('show');
}

// filter buttons
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

function applyFilters() { if (DATA) renderTable(); }

// sorting
document.querySelectorAll('th[data-sort]').forEach(th => {
  th.addEventListener('click', () => {
    const col = th.dataset.sort;
    if (sortCol === col) { sortDir *= -1; } else { sortCol = col; sortDir = -1; }
    document.querySelectorAll('th').forEach(h => h.classList.remove('sorted'));
    th.classList.add('sorted');
    renderTable();
  });
});

loadData(false);
</script>
</body>
</html>"""


class Handler(http.server.BaseHTTPRequestHandler):
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
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                traceback.print_exc()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
        else:
            self.send_response(404)
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
