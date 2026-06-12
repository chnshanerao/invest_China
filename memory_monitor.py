#!/usr/bin/env python3
"""
存储芯片投资组合每日监控系统
Daily Memory/Semiconductor Portfolio Monitoring System

持仓: MU (Micron), MRVL (Marvell), DRAM ETF (Roundhill) + 待建仓: TSM, ASML, ANET, VRT
数据源: 新浪财经 API (无需第三方依赖)
"""

import urllib.request
import urllib.error
import json
import datetime
import ssl
import sys
import os
import time
import re
import hmac
import hashlib
import base64
import http.cookiejar

# ============================================================
# 配置区 CONFIG
# ============================================================

# 总资产约9.5万元(07709亏损后)，现金约6.0万元(63%)
TOTAL_ASSET = 95000
CASH_AMOUNT = 60000

HOLDINGS = {
    "MU": {
        "name": "美光科技",
        "sina_symbol": "gb_mu",
        "market": "us",
        "amount": 27000,
        "weight_current": 0.284,
        "weight_target": 0.337,
        "leverage": 1.0,
        "stop_loss_pct": -20.0,
        "take_profit_pct": 30.0,
        "notes": "核心主力，SOX企稳后回补至3.2万",
    },
    "MRVL": {
        "name": "迈威尔科技",
        "sina_symbol": "gb_mrvl",
        "market": "us",
        "amount": 4000,
        "weight_current": 0.042,
        "weight_target": 0.042,
        "leverage": 1.0,
        "stop_loss_pct": -20.0,
        "take_profit_pct": 30.0,
        "notes": "持有等6/22标普纳入催化剂",
    },
    "DRAM": {
        "name": "Roundhill DRAM ETF",
        "sina_symbol": "gb_dram",
        "market": "us",
        "amount": 4000,
        "weight_current": 0.042,
        "weight_target": 0.00,
        "leverage": 1.0,
        "stop_loss_pct": -15.0,
        "take_profit_pct": 20.0,
        "notes": "择机清仓，资金转入VRT",
    },
}

# 待建仓标的 — 目标组合新成员
PENDING_POSITIONS = {
    "TSM": {
        "name": "台积电",
        "sina_symbol": "gb_tsm",
        "market": "us",
        "target_amount": 10000,
        "target_weight": 0.102,
        "trigger": "SOX企稳后(07709已清仓，资金已释放)",
        "batch": 1,
        "notes": "CoWoS封装垄断，AI芯片物理瓶颈",
    },
    "ASML": {
        "name": "阿斯麦",
        "sina_symbol": "gb_asml",
        "market": "us",
        "target_amount": 5000,
        "target_weight": 0.051,
        "trigger": "FOMC后利率明朗",
        "batch": 2,
        "notes": "全球唯一EUV光刻机，海力士扩产直接受益",
    },
    "ANET": {
        "name": "Arista网络",
        "sina_symbol": "gb_anet",
        "market": "us",
        "target_amount": 5000,
        "target_weight": 0.051,
        "trigger": "FOMC后利率明朗",
        "batch": 2,
        "notes": "AI数据中心800G网络龙头，80%份额",
    },
    "VRT": {
        "name": "Vertiv电力散热",
        "sina_symbol": "gb_vrt",
        "market": "us",
        "target_amount": 5000,
        "target_weight": 0.051,
        "trigger": "MU财报6/24确认后",
        "batch": 3,
        "notes": "AI数据中心电力/液冷，低相关性对冲",
    },
}

# 回补/建仓计划 REBUY & BUILD PLAN
# 当条件满足时自动推送钉钉告警
REBUY_PLAN = [
    {
        "id": "sox_stabilize",
        "name": "第1批:SOX企稳→回补MU+建仓TSM",
        "description": "SOX连续2日不创新低 → 回补MU+0.5万,建仓TSM 1.0万",
        "target": "MU,TSM",
        "action_amount": 15000,
        "check": "sox_no_new_low_2d",
        "priority": 1,
    },
    {
        "id": "mrvl_sp500",
        "name": "MRVL标普纳入倒计时",
        "description": "6/22纳入标普500前5个交易日+SOX企稳 → 持有MRVL不动",
        "target": "MRVL",
        "action_amount": 0,
        "check": "mrvl_sp500_window",
        "priority": 2,
    },
    {
        "id": "fomc_clear",
        "name": "第2批:FOMC后→建仓ASML+ANET",
        "description": "6/17 FOMC结束+非鹰派 → 建仓ASML 0.5万,ANET 0.5万",
        "target": "ASML,ANET",
        "action_amount": 10000,
        "check": "post_fomc",
        "priority": 3,
    },
    {
        "id": "mu_earnings",
        "name": "第3批:MU财报后→建仓VRT",
        "description": "6/24 MU财报确认基本面 → 建仓VRT 0.5万(来源:DRAM清仓+现金)",
        "target": "VRT",
        "action_amount": 5000,
        "check": "post_mu_earnings",
        "priority": 4,
    },
    {
        "id": "vixy_cooldown",
        "name": "VIX回落确认",
        "description": "VIXY回落至22以下 → 恐慌消退，可加速建仓节奏",
        "target": "ALL",
        "action_amount": 0,
        "check": "vixy_below_22",
        "priority": 5,
    },
]

# SOX历史低点跟踪文件
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SOX_LOW_TRACKER = os.path.join(SCRIPT_DIR, "state", "sox_tracker.json")

WATCHLIST = {
    "SOX": {"name": "费城半导体指数", "sina_symbol": "gb_sox", "market": "index"},
    "SOXX": {"name": "半导体ETF", "sina_symbol": "gb_soxx", "market": "us"},
    "VIXY": {"name": "恐慌指数ETF(VIX代理)", "sina_symbol": "gb_vixy", "market": "us"},
    "TLT": {"name": "20年+美债ETF(利率反指)", "sina_symbol": "gb_tlt", "market": "us"},
    "NVDA": {"name": "英伟达", "sina_symbol": "gb_nvda", "market": "us"},
    "AVGO": {"name": "博通", "sina_symbol": "gb_avgo", "market": "us"},
    "TSM": {"name": "台积电(待建仓)", "sina_symbol": "gb_tsm", "market": "us"},
    "ASML": {"name": "阿斯麦(待建仓)", "sina_symbol": "gb_asml", "market": "us"},
    "ANET": {"name": "Arista网络(待建仓)", "sina_symbol": "gb_anet", "market": "us"},
    "VRT": {"name": "Vertiv电力(待建仓)", "sina_symbol": "gb_vrt", "market": "us"},
}

# VIX估算参数: VIXY价格与VIX的近似关系
# VIXY在VIX=15时约$15-20, VIX=25时约$25-35, VIX=35时约$40+
# 这只是粗略估算，实际VIX需从CBOE获取
VIX_VIXY_APPROX = {
    "low_threshold": 20.0,    # VIXY<20 ≈ VIX偏低
    "mid_threshold": 30.0,    # VIXY 20-30 ≈ VIX中等
    "high_threshold": 40.0,   # VIXY>40 ≈ VIX偏高
}

# 10Y收益率估算: TLT反向代理
# TLT价格↓ = 利率↑，TLT≈$90→4.0%, TLT≈$85→4.5%, TLT≈$80→5.0%
TLT_YIELD_APPROX = {
    90.0: 4.0,
    85.0: 4.5,
    80.0: 5.0,
    75.0: 5.5,
}

# 钉钉推送配置 DingTalk Push Config
DINGTALK_WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=61fded0e8252140ae2fcc761ff20f8cf6df9f75d1623c3be6a4cfadb6d9dc586"
DINGTALK_SECRET = "SECd92d215a87b1a45bd6dd94ed0699bcefa5fc7b98e4ffc29226bf0fbf5a25ae71"

# 红绿灯阈值 Traffic Light Thresholds
TRAFFIC_LIGHT = {
    "green": {
        "vixy_below": 20.0,
        "tlt_above": 90.0,
        "sox_change_healthy": (-3, 3),
        "description": "全面看多 — 可持有或加仓",
    },
    "yellow": {
        "vixy_range": (20.0, 35.0),
        "tlt_range": (82.0, 90.0),
        "description": "谨慎持有 — 严格止损，不加仓",
    },
    "red": {
        "vixy_above": 35.0,
        "tlt_below": 82.0,
        "description": "高危 — 立即减仓至安全水位",
    },
}

# 关键事件日历 Key Event Calendar
KEY_EVENTS = [
    {"date": "2026-06-12", "event": "SpaceX IPO上市", "impact": "虹吸效应，流动性抽取$150-300B被动再平衡", "action": "IPO前减仓至60%以下"},
    {"date": "2026-06-16", "event": "FOMC会议开始", "impact": "利率决议+点阵图，决定下半年流动性", "action": "会前不加仓，观望"},
    {"date": "2026-06-17", "event": "FOMC会议结束+鲍威尔发言", "impact": "鸽派=利好，鹰派=继续承压", "action": "根据措辞调整仓位"},
    {"date": "2026-06-22", "event": "MRVL纳入标普500生效", "impact": "被动基金$8-12B买入", "action": "MRVL可持有等纳入利好"},
    {"date": "2026-06-24", "event": "美光Q3财报", "impact": "HBM收入指引是关键", "action": "财报前可小幅减MU仓位对冲"},
    {"date": "2026-07-01", "event": "DRAM 7月合约价发布", "impact": "决定Q3存储趋势", "action": "价格环比+5%以上=加仓信号"},
    {"date": "2026-07-15", "event": "三星电子Q2财报", "impact": "HBM良率+DDR5出货量指引", "action": "关注HBM良率改善速度"},
    {"date": "2026-07-24", "event": "SK海力士Q2财报", "impact": "HBM3E/HBM4量产进度", "action": "如超预期可考虑回补7709"},
    {"date": "2026-08-05", "event": "台积电月度营收", "impact": "CoWoS产能利用率", "action": "产能持续满载=存储需求确认"},
    {"date": "2026-09-17", "event": "FOMC 9月会议", "impact": "潜在降息窗口", "action": "降息=半导体板块催化剂"},
]

# ============================================================
# 数据获取 DATA FETCHING (新浪财经 API)
# ============================================================

def create_ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def fetch_sina_batch(sina_symbols):
    """
    从新浪财经批量获取行情数据
    sina_symbols: list of sina symbol strings, e.g. ["gb_mu", "rt_hk07709"]
    """
    url = "https://hq.sinajs.cn/list=" + ",".join(sina_symbols)
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": "https://finance.sina.com.cn",
    }
    req = urllib.request.Request(url, headers=headers)
    ctx = create_ssl_context()

    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            raw = resp.read().decode("gbk", errors="replace")
        return raw
    except Exception as e:
        return None

def parse_sina_us(raw_line):
    """解析新浪美股数据行"""
    match = re.search(r'var hq_str_gb_\w+="(.+)"', raw_line)
    if not match or not match.group(1):
        return None
    parts = match.group(1).split(",")
    if len(parts) < 27:
        return None

    price = float(parts[1])
    change_pct = float(parts[2])
    prev_close = float(parts[26]) if parts[26] and parts[26] != "--" else None
    after_hours_pct = float(parts[22]) if parts[22] and parts[22] != "--" else None

    return {
        "name": parts[0],
        "price": price,
        "change_pct": change_pct,
        "prev_close": prev_close,
        "open": float(parts[5]) if parts[5] else None,
        "high": float(parts[6]) if parts[6] else None,
        "low": float(parts[7]) if parts[7] else None,
        "high_52w": float(parts[8]) if parts[8] else None,
        "low_52w": float(parts[9]) if parts[9] else None,
        "volume": int(parts[10]) if parts[10] else None,
        "market_cap": float(parts[12]) if parts[12] else None,
        "pe": float(parts[13]) if parts[13] and parts[13] != "0.00" else None,
        "after_hours_pct": after_hours_pct,
        "timestamp": parts[3],
        "ok": True,
    }

def parse_sina_hk(raw_line):
    """解析新浪港股数据行"""
    match = re.search(r'var hq_str_rt_hk\w+="(.+)"', raw_line)
    if not match or not match.group(1):
        return None
    parts = match.group(1).split(",")
    if len(parts) < 18:
        return None

    return {
        "name": parts[1],
        "price": float(parts[6]),
        "change_pct": float(parts[8]),
        "prev_close": float(parts[2]),
        "open": float(parts[3]),
        "high": float(parts[4]),
        "low": float(parts[5]),
        "high_52w": float(parts[15]) if parts[15] else None,
        "low_52w": float(parts[16]) if parts[16] else None,
        "volume": int(float(parts[12])) if parts[12] else None,
        "turnover": float(parts[11]) if parts[11] else None,
        "timestamp": f"{parts[17]} {parts[18]}",
        "currency": "HKD",
        "ok": True,
    }

def parse_sina_index(raw_line):
    """解析新浪指数数据行 (gb_sox等)"""
    match = re.search(r'var hq_str_gb_\w+="(.+)"', raw_line)
    if not match or not match.group(1):
        return None
    parts = match.group(1).split(",")
    if len(parts) < 8:
        return None

    return {
        "name": parts[0],
        "price": float(parts[1]),
        "change_pct": float(parts[2]),
        "open": float(parts[5]) if parts[5] else None,
        "high": float(parts[6]) if parts[6] else None,
        "low": float(parts[7]) if parts[7] else None,
        "timestamp": parts[3],
        "ok": True,
    }

def fetch_all_quotes():
    """获取所有持仓和关注列表的实时行情"""
    all_symbols = []
    symbol_map = {}

    for ticker, config in HOLDINGS.items():
        sina_sym = config["sina_symbol"]
        all_symbols.append(sina_sym)
        symbol_map[sina_sym] = {"ticker": ticker, "type": "holding", "market": config["market"]}

    for ticker, config in WATCHLIST.items():
        sina_sym = config["sina_symbol"]
        all_symbols.append(sina_sym)
        symbol_map[sina_sym] = {"ticker": ticker, "type": "watchlist", "market": config["market"]}

    raw = fetch_sina_batch(all_symbols)
    if not raw:
        return {}, {}

    holdings_data = {}
    watchlist_data = {}

    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line or '=""' in line:
            continue

        sina_sym_match = re.search(r'var hq_str_(\w+)=', line)
        if not sina_sym_match:
            continue
        sina_sym = sina_sym_match.group(1)

        info = symbol_map.get(sina_sym)
        if not info:
            continue

        parsed = None
        if sina_sym.startswith("rt_hk"):
            parsed = parse_sina_hk(line)
        elif info["market"] == "index":
            parsed = parse_sina_index(line)
        else:
            parsed = parse_sina_us(line)

        if parsed:
            parsed["symbol"] = info["ticker"]
            if info["type"] == "holding":
                holdings_data[info["ticker"]] = parsed
            else:
                watchlist_data[info["ticker"]] = parsed

    return holdings_data, watchlist_data

def estimate_vix_from_vixy(vixy_price):
    """从VIXY ETF价格粗略估算VIX水平"""
    if vixy_price is None:
        return None
    if vixy_price < VIX_VIXY_APPROX["low_threshold"]:
        return "low"
    elif vixy_price < VIX_VIXY_APPROX["mid_threshold"]:
        return "medium"
    elif vixy_price < VIX_VIXY_APPROX["high_threshold"]:
        return "high"
    else:
        return "extreme"

def estimate_yield_from_tlt(tlt_price):
    """从TLT价格粗略估算10年期收益率"""
    if tlt_price is None:
        return None
    sorted_prices = sorted(TLT_YIELD_APPROX.keys(), reverse=True)
    for p in sorted_prices:
        if tlt_price >= p:
            return TLT_YIELD_APPROX[p]
    return 5.5

# ============================================================
# 信号生成 SIGNAL GENERATION
# ============================================================

def evaluate_traffic_light(vixy_data, tlt_data, sox_data):
    """基于VIXY/TLT/SOX评估市场红绿灯"""
    signals = {"green": 0, "yellow": 0, "red": 0, "details": []}

    # VIXY (VIX代理) 评估
    if vixy_data and vixy_data.get("ok"):
        vixy_price = vixy_data["price"]
        vixy_chg = vixy_data["change_pct"]
        vix_level = estimate_vix_from_vixy(vixy_price)

        if vix_level == "low":
            signals["green"] += 1
            signals["details"].append(f"✅ VIXY=${vixy_price:.1f}({vixy_chg:+.1f}%) → VIX偏低，市场平静")
        elif vix_level == "medium":
            signals["yellow"] += 1
            signals["details"].append(f"🟡 VIXY=${vixy_price:.1f}({vixy_chg:+.1f}%) → VIX中等，需警惕")
        elif vix_level == "high":
            signals["red"] += 1
            signals["details"].append(f"🔴 VIXY=${vixy_price:.1f}({vixy_chg:+.1f}%) → VIX偏高，恐慌升温")
        else:
            signals["red"] += 1
            signals["details"].append(f"‼️ VIXY=${vixy_price:.1f}({vixy_chg:+.1f}%) → VIX极高，极度恐慌!")

        if vixy_chg > 15:
            signals["details"].append(f"  ⚠️ VIXY单日暴涨{vixy_chg:.1f}%，恐慌急剧升温")
    else:
        signals["details"].append("  ⚠️ VIXY数据获取失败，无法评估VIX")

    # TLT (利率反向代理) 评估
    if tlt_data and tlt_data.get("ok"):
        tlt_price = tlt_data["price"]
        tlt_chg = tlt_data["change_pct"]
        est_yield = estimate_yield_from_tlt(tlt_price)

        if tlt_price > TRAFFIC_LIGHT["green"]["tlt_above"]:
            signals["green"] += 1
            signals["details"].append(f"✅ TLT=${tlt_price:.1f}({tlt_chg:+.1f}%) → 估算10Y≈{est_yield:.1f}%，利率友好")
        elif tlt_price < TRAFFIC_LIGHT["red"]["tlt_below"]:
            signals["red"] += 1
            signals["details"].append(f"🔴 TLT=${tlt_price:.1f}({tlt_chg:+.1f}%) → 估算10Y≈{est_yield:.1f}%，利率压制严重")
        else:
            signals["yellow"] += 1
            signals["details"].append(f"🟡 TLT=${tlt_price:.1f}({tlt_chg:+.1f}%) → 估算10Y≈{est_yield:.1f}%，利率偏高")
    else:
        signals["details"].append("  ⚠️ TLT数据获取失败，无法评估利率")

    # SOX 评估
    if sox_data and sox_data.get("ok"):
        sox_price = sox_data["price"]
        sox_chg = sox_data["change_pct"]

        if sox_chg < -5:
            signals["red"] += 1
            signals["details"].append(f"🔴 SOX={sox_price:.0f}({sox_chg:+.1f}%) → 半导体板块暴跌!")
        elif sox_chg < -2:
            signals["yellow"] += 1
            signals["details"].append(f"🟡 SOX={sox_price:.0f}({sox_chg:+.1f}%) → 半导体承压")
        elif sox_chg > 3:
            signals["green"] += 1
            signals["details"].append(f"✅ SOX={sox_price:.0f}({sox_chg:+.1f}%) → 半导体强势反弹")
        else:
            signals["green"] += 1
            signals["details"].append(f"✅ SOX={sox_price:.0f}({sox_chg:+.1f}%) → 半导体正常波动")

        if sox_data.get("high_52w"):
            dist_from_high = (sox_price - sox_data["high_52w"]) / sox_data["high_52w"] * 100
            signals["details"].append(f"  📏 SOX距52周高点: {dist_from_high:+.1f}%")
    else:
        signals["details"].append("  ⚠️ SOX数据获取失败")

    # Determine overall
    if signals["red"] >= 2:
        overall = "RED"
        overall_cn = "🔴 红灯 — 高危，立即减仓"
    elif signals["red"] >= 1 or signals["yellow"] >= 2:
        overall = "YELLOW"
        overall_cn = "🟡 黄灯 — 谨慎，严格止损"
    else:
        overall = "GREEN"
        overall_cn = "✅ 绿灯 — 可持有/择机加仓"

    return overall, overall_cn, signals["details"]

def generate_holding_signals(holdings_data):
    """Generate action signals for each holding."""
    signals = []

    for symbol, config in HOLDINGS.items():
        data = holdings_data.get(symbol)
        if not data or not data.get("ok"):
            signals.append({
                "symbol": symbol,
                "name": config["name"],
                "signal": "⚠️ 数据获取失败，请手动检查",
                "urgency": "low",
            })
            continue

        price = data["price"]
        change = data["change_pct"]
        effective_change = change * config["leverage"]

        actions = []
        urgency = "low"

        # Check stop loss
        if change <= config["stop_loss_pct"]:
            actions.append(
                f"‼️ 触发止损线! 跌幅{change:.1f}%(有效{effective_change:.1f}%)，"
                f"建议立即卖出至目标仓位{config['weight_target']*100:.0f}%"
            )
            urgency = "critical"

        # 2x ETF specific warnings
        if config["leverage"] > 1:
            if abs(change) > 5:
                actions.append(
                    f"⚠️ 2倍杠杆ETF单日波动{change:.1f}%，有效波动{effective_change:.1f}%，波动衰减加剧"
                )
                if urgency in ("low", "medium"):
                    urgency = "high"

        # Position too high vs target
        if config["weight_current"] > config["weight_target"] * 1.5 and config["weight_target"] > 0:
            excess = config["weight_current"] - config["weight_target"]
            actions.append(
                f"📉 当前仓位{config['weight_current']*100:.0f}%高于目标{config['weight_target']*100:.0f}%，"
                f"建议减仓{excess*100:.0f}%"
            )
            if urgency == "low":
                urgency = "medium"
        elif config["weight_target"] == 0 and config["weight_current"] > 0:
            actions.append(
                f"📉 目标仓位为0%，当前仍持有{config['weight_current']*100:.0f}%，建议清仓"
            )
            if urgency == "low":
                urgency = "medium"

        # Strong daily move
        if change > 5:
            actions.append(f"📈 大涨{change:.1f}%，可考虑逢高减仓锁定利润")
        elif change < -5:
            actions.append(f"📉 大跌{change:.1f}%，评估是否触及止损或为加仓机会")
            if urgency in ("low", "medium"):
                urgency = "high"

        # After-hours alert (US stocks)
        if data.get("after_hours_pct") and abs(data["after_hours_pct"]) > 3:
            ah = data["after_hours_pct"]
            direction = "继续下跌" if ah < 0 else "盘后反弹"
            actions.append(f"🌙 盘后{direction}{ah:+.1f}%，关注明日开盘")

        # 52-week range context
        if data.get("high_52w") and data.get("low_52w"):
            range_pos = (price - data["low_52w"]) / (data["high_52w"] - data["low_52w"]) * 100
            if range_pos < 20:
                actions.append(f"📊 处于52周区间底部{range_pos:.0f}%位置")
            elif range_pos > 90:
                actions.append(f"📊 接近52周高点，位于区间{range_pos:.0f}%位置")

        if not actions:
            actions.append("无异常信号，维持当前策略")

        signals.append({
            "symbol": symbol,
            "name": config["name"],
            "price": price,
            "change_pct": change,
            "effective_change": effective_change,
            "signal": "\n       ".join(actions),
            "urgency": urgency,
            "currency": data.get("currency", "USD"),
        })

    return signals

def check_upcoming_events(days_ahead=7):
    """Check for events within the next N days."""
    today = datetime.date.today()
    upcoming = []
    past_recent = []

    for event in KEY_EVENTS:
        event_date = datetime.date.fromisoformat(event["date"])
        delta = (event_date - today).days

        if 0 <= delta <= days_ahead:
            upcoming.append({**event, "days_until": delta})
        elif -3 <= delta < 0:
            past_recent.append({**event, "days_ago": abs(delta)})

    return upcoming, past_recent


def load_sox_tracker():
    """加载SOX低点跟踪数据"""
    try:
        with open(SOX_LOW_TRACKER, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"low": None, "low_date": None, "no_new_low_days": 0, "history": []}


def save_sox_tracker(tracker):
    with open(SOX_LOW_TRACKER, "w") as f:
        json.dump(tracker, f, ensure_ascii=False, indent=2)


def update_sox_tracker(sox_price):
    """更新SOX追踪器，返回连续未创新低天数"""
    tracker = load_sox_tracker()
    today_str = datetime.date.today().isoformat()

    if tracker["history"] and tracker["history"][-1].get("date") == today_str:
        return tracker

    if tracker["low"] is None or sox_price < tracker["low"]:
        tracker["low"] = sox_price
        tracker["low_date"] = today_str
        tracker["no_new_low_days"] = 0
    else:
        tracker["no_new_low_days"] += 1

    tracker["history"].append({"date": today_str, "price": sox_price})
    tracker["history"] = tracker["history"][-30:]

    save_sox_tracker(tracker)
    return tracker


def check_rebuy_conditions(holdings_data, watchlist_data):
    """检查回补条件，返回已触发的信号列表"""
    triggered = []
    today = datetime.date.today()

    sox_data = watchlist_data.get("SOX")
    vixy_data = watchlist_data.get("VIXY")

    sox_tracker = None
    if sox_data and sox_data.get("ok"):
        sox_tracker = update_sox_tracker(sox_data["price"])

    for plan in sorted(REBUY_PLAN, key=lambda p: p["priority"]):
        check = plan["check"]

        if check == "sox_no_new_low_2d" and sox_tracker:
            if sox_tracker["no_new_low_days"] >= 2:
                triggered.append({
                    **plan,
                    "fired": True,
                    "detail": f"SOX连续{sox_tracker['no_new_low_days']}日未创新低"
                             f"(低点{sox_tracker['low']:.1f}@{sox_tracker['low_date']})"
                             f" → 回补MU+0.5万，建仓TSM 1.0万",
                    "urgency": "medium",
                })
            else:
                triggered.append({
                    **plan,
                    "fired": False,
                    "detail": f"SOX未创新低{sox_tracker['no_new_low_days']}天(需≥2天)",
                    "urgency": "low",
                })

        elif check == "mrvl_sp500_window":
            sp500_date = datetime.date(2026, 6, 22)
            days_until = (sp500_date - today).days
            sox_stable = sox_tracker and sox_tracker["no_new_low_days"] >= 2 if sox_tracker else False
            if 0 < days_until <= 5 and sox_stable:
                triggered.append({
                    **plan,
                    "fired": True,
                    "detail": f"距MRVL纳入标普500还有{days_until}天+SOX已企稳，持有MRVL 0.4万",
                    "urgency": "medium",
                })
            elif 0 < days_until <= 14:
                triggered.append({
                    **plan,
                    "fired": False,
                    "detail": f"距标普纳入{days_until}天，SOX{'已企稳' if sox_stable else '未企稳'}",
                    "urgency": "low",
                })

        elif check == "post_fomc":
            fomc_end = datetime.date(2026, 6, 17)
            days_until = (fomc_end - today).days
            if days_until < 0 and abs(days_until) <= 5:
                vixy_ok = vixy_data and vixy_data.get("ok") and vixy_data["price"] < 28.0
                if vixy_ok:
                    triggered.append({
                        **plan,
                        "fired": True,
                        "detail": f"FOMC已结束{abs(days_until)}天+VIXY={vixy_data['price']:.1f}<28"
                                 f" → 建仓ASML 0.5万+ANET 0.5万",
                        "urgency": "medium",
                    })
                else:
                    triggered.append({
                        **plan,
                        "fired": False,
                        "detail": f"FOMC已结束{abs(days_until)}天，但VIXY仍偏高",
                        "urgency": "low",
                    })
            elif days_until >= 0:
                triggered.append({
                    **plan,
                    "fired": False,
                    "detail": f"FOMC还有{days_until}天(6/17结束)，等待中",
                    "urgency": "low",
                })

        elif check == "post_mu_earnings":
            mu_date = datetime.date(2026, 6, 24)
            days_until = (mu_date - today).days
            if days_until < 0 and abs(days_until) <= 5:
                triggered.append({
                    **plan,
                    "fired": True,
                    "detail": f"MU财报已发布{abs(days_until)}天 → 确认基本面后建仓VRT 0.5万",
                    "urgency": "medium",
                })
            elif days_until >= 0:
                triggered.append({
                    **plan,
                    "fired": False,
                    "detail": f"MU财报还有{days_until}天(6/24)，等待中",
                    "urgency": "low",
                })

        elif check == "vixy_below_22" and vixy_data and vixy_data.get("ok"):
            if vixy_data["price"] < 22.0:
                triggered.append({
                    **plan,
                    "fired": True,
                    "detail": f"VIXY已回落至{vixy_data['price']:.1f}(低于22)，恐慌消退，可加速建仓",
                    "urgency": "medium",
                })
            else:
                triggered.append({
                    **plan,
                    "fired": False,
                    "detail": f"VIXY={vixy_data['price']:.1f}(需<22)",
                    "urgency": "low",
                })

    return triggered


def determine_phase(today=None):
    """Determine which phase of the 5-phase battle plan we're in."""
    if today is None:
        today = datetime.date.today()

    phases = [
        {
            "phase": 1, "name": "震荡筑底期",
            "start": "2026-06-07", "end": "2026-06-11",
            "target_position": "37%(当前)+待建仓",
            "key_action": "07709已清仓✅ DRAM择机清仓,等SOX企稳信号启动第1批建仓(MU+TSM)",
            "reasoning": "SpaceX IPO前保持低仓位，观察SOX是否筑底",
        },
        {
            "phase": 2, "name": "IPO冲击观察期",
            "start": "2026-06-12", "end": "2026-06-17",
            "target_position": "40-50%",
            "key_action": "观察SpaceX上市冲击+FOMC会议结果",
            "reasoning": "双重事件叠加，流动性风险最高",
        },
        {
            "phase": 3, "name": "信号确认期",
            "start": "2026-06-18", "end": "2026-06-30",
            "target_position": "50-70%",
            "key_action": "MRVL纳入S&P500(6/22)，MU财报(6/24)，根据结果调仓",
            "reasoning": "两大催化剂验证基本面",
        },
        {
            "phase": 4, "name": "趋势跟随期",
            "start": "2026-07-01", "end": "2026-08-31",
            "target_position": "60-80%",
            "key_action": "Q3 DRAM合约价确认后择机加仓",
            "reasoning": "存储超级周期验证窗口",
        },
        {
            "phase": 5, "name": "收获/防守期",
            "start": "2026-09-01", "end": "2026-09-30",
            "target_position": "根据趋势判断",
            "key_action": "9月FOMC降息预期交易",
            "reasoning": "年度收官布局",
        },
    ]

    for p in phases:
        start = datetime.date.fromisoformat(p["start"])
        end = datetime.date.fromisoformat(p["end"])
        if start <= today <= end:
            return p

    if today < datetime.date.fromisoformat(phases[0]["start"]):
        return phases[0]
    return phases[-1]

# ============================================================
# 报告生成 REPORT GENERATION
# ============================================================

def format_report(holdings_data, watchlist_data, traffic_light, holding_signals,
                  upcoming_events, past_events, phase, timestamp, rebuy_signals=None):
    """Generate formatted daily monitoring report."""

    L = []
    W = 72

    L.append("=" * W)
    L.append("  📊 存储芯片投资组合 — 每日监控报告")
    L.append(f"  生成时间: {timestamp}")
    L.append(f"  数据源: 新浪财经实时行情")
    L.append("=" * W)

    # ── Phase ──
    L.append("")
    L.append(f"📅 当前阶段: 第{phase['phase']}阶段 — {phase['name']}")
    L.append(f"   时间窗口: {phase['start']} ~ {phase['end']}")
    L.append(f"   目标仓位: {phase['target_position']}")
    L.append(f"   核心操作: {phase['key_action']}")
    L.append(f"   操作逻辑: {phase['reasoning']}")

    # ── Traffic Light ──
    overall, overall_cn, details = traffic_light
    L.append("")
    L.append("─" * W)
    L.append(f"🚦 市场信号灯: {overall_cn}")
    L.append("─" * W)
    for d in details:
        L.append(f"   {d}")

    # ── Holdings Table ──
    L.append("")
    L.append("─" * W)
    L.append("💼 持仓监控")
    L.append("─" * W)

    header = f"  {'标的':<22}{'价格':>10}{'日涨跌':>9}{'有效波动':>9}{'仓位→目标':>12}"
    L.append(header)
    L.append("  " + "─" * 62)

    for sig in holding_signals:
        if "price" in sig:
            config = HOLDINGS[sig["symbol"]]
            sym_display = f"{sig['symbol']}({sig['name'][:6]})"
            cur = f"{config['weight_current']*100:.0f}%"
            tgt = f"{config['weight_target']*100:.0f}%"
            L.append(
                f"  {sym_display:<22}{sig['price']:>10.2f}"
                f"{sig['change_pct']:>+8.1f}%{sig['effective_change']:>+8.1f}%"
                f"  {cur}→{tgt}"
            )
        else:
            L.append(f"  {sig['symbol']:<22}{'数据获取失败':>10}")

    # ── Action Signals ──
    L.append("")
    L.append("─" * W)
    L.append("⚡ 操作信号（按紧急程度排序）")
    L.append("─" * W)

    urgency_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    sorted_sigs = sorted(holding_signals, key=lambda x: urgency_order.get(x.get("urgency", "low"), 3))

    for sig in sorted_sigs:
        icon = {"critical": "‼️", "high": "⚠️", "medium": "📋", "low": "ℹ️"}.get(sig.get("urgency", "low"), "ℹ️")
        L.append(f"  {icon} [{sig.get('urgency','low').upper()}] {sig['symbol']} ({sig['name']})")
        L.append(f"     → {sig['signal']}")
        L.append("")

    # ── Watchlist ──
    L.append("─" * W)
    L.append("👁️ 关注列表")
    L.append("─" * W)
    L.append(f"  {'标的':<10}{'名称':<20}{'价格':>10}{'日涨跌':>9}")
    L.append("  " + "─" * 49)

    for symbol, config in WATCHLIST.items():
        data = watchlist_data.get(symbol)
        if data and data.get("ok"):
            L.append(
                f"  {symbol:<10}{config['name']:<20}{data['price']:>10.2f}{data['change_pct']:>+8.1f}%"
            )
        else:
            L.append(f"  {symbol:<10}{config['name']:<20}{'--':>10}{'--':>9}")

    # ── Upcoming Events ──
    if upcoming_events:
        L.append("")
        L.append("─" * W)
        L.append("📆 未来7天关键事件")
        L.append("─" * W)
        for evt in sorted(upcoming_events, key=lambda x: x["days_until"]):
            if evt["days_until"] == 0:
                prefix = "🔥 今天"
            elif evt["days_until"] == 1:
                prefix = "⏰ 明天"
            else:
                prefix = f"📌 {evt['days_until']}天后"
            L.append(f"  {prefix} ({evt['date']}): {evt['event']}")
            L.append(f"     影响: {evt['impact']}")
            L.append(f"     建议: {evt['action']}")
            L.append("")

    if past_events:
        L.append("─" * W)
        L.append("🔙 近期已发生事件（注意影响延续）")
        L.append("─" * W)
        for evt in past_events:
            L.append(f"  {evt['days_ago']}天前 ({evt['date']}): {evt['event']}")
            L.append(f"     后续关注: {evt['impact']}")
            L.append("")

    # ── Fundamentals Reminder ──
    L.append("─" * W)
    L.append("📊 存储行业基本面提醒")
    L.append("─" * W)
    L.append("  • DRAM合约价: Q1'26涨93-98% QoQ → 关注Q2环比趋势")
    L.append("  • HBM产能: 24%晶圆转向HBM → 标准DRAM供给结构性紧张")
    L.append("  • CoWoS瓶颈: TSMC 150K片/月仍短缺25-30%，制约AI芯片出货")
    L.append("  • 中国扩产: CXMT DRAM 8%份额, YMTC NAND 13%份额，持续跟踪")
    L.append("  • Memflation: 手机出货-6.1%, 笔记本-14.8%，消费需求被价格挤压")
    L.append("  • Agentic AI: KV-cache持久化驱动结构性新增DRAM需求")
    L.append("  • DDR5利润反转: DDR5每片晶圆收入已超HBM (年度vs季度定价)")

    # ── Verdict ──
    L.append("")
    L.append("=" * W)
    L.append("📝 今日总结与操作建议")
    L.append("=" * W)

    critical_count = sum(1 for s in holding_signals if s.get("urgency") == "critical")
    high_count = sum(1 for s in holding_signals if s.get("urgency") == "high")

    if critical_count > 0:
        L.append("  ‼️ 有紧急信号！请立即处理以上标记为 ‼️ 的持仓。")
    elif high_count > 0:
        L.append("  ⚠️ 有高优先级信号，请在今日交易时段内处理。")
    elif overall == "RED":
        L.append("  🔴 市场处于红灯状态，建议减仓至安全水位。")
    elif overall == "YELLOW":
        L.append("  🟡 市场谨慎，维持当前仓位，严格执行止损。")
    else:
        L.append("  ✅ 市场正常，按计划执行当前阶段策略。")

    L.append(f"  📌 当前阶段重点: {phase['key_action']}")

    # ── Rebuy Plan Status ──
    if rebuy_signals:
        L.append("")
        L.append("─" * W)
        L.append("🔄 回补计划监控")
        L.append("─" * W)
        for rb in rebuy_signals:
            icon = "✅" if rb.get("fired") else "⏳"
            L.append(f"  {icon} [{rb['name']}] {rb['detail']}")

    today_events = [e for e in upcoming_events if e["days_until"] == 0]
    if today_events:
        L.append(f"  🔥 今日事件: {', '.join(e['event'] for e in today_events)}")

    tomorrow_events = [e for e in upcoming_events if e["days_until"] == 1]
    if tomorrow_events:
        L.append(f"  ⏰ 明日事件: {', '.join(e['event'] for e in tomorrow_events)}")

    L.append("")
    L.append("─" * W)
    L.append("  💡 本报告基于公开数据自动生成，仅供参考，不构成投资建议")
    L.append("  📂 最新报告: /home/admin/workspace/daily_report.txt")
    L.append("  📂 历史报告: /home/admin/workspace/reports/")
    L.append("  🔧 运行命令: python3 /home/admin/workspace/memory_monitor.py")
    L.append("=" * W)

    return "\n".join(L)

# ============================================================
# 钉钉推送 DINGTALK PUSH
# ============================================================

def dingtalk_sign():
    """生成钉钉加签参数"""
    timestamp = str(round(time.time() * 1000))
    string_to_sign = f"{timestamp}\n{DINGTALK_SECRET}"
    hmac_code = hmac.new(
        DINGTALK_SECRET.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    sign = urllib.request.quote(base64.b64encode(hmac_code).decode("utf-8"))
    return timestamp, sign

def format_dingtalk_markdown(holdings_data, watchlist_data, traffic_light,
                              holding_signals, upcoming_events, phase, timestamp,
                              rebuy_signals=None):
    """生成钉钉 Markdown 格式的精简报告"""
    overall, overall_cn, details = traffic_light

    lines = []
    lines.append(f"### 📊 存储芯片监控 {timestamp[:10]}")
    lines.append("")
    lines.append(f"**阶段{phase['phase']}: {phase['name']}** | 目标仓位: {phase['target_position']}")
    lines.append("")

    # Traffic light
    lines.append(f"**{overall_cn}**")
    lines.append("")
    for d in details:
        lines.append(f"> {d}")
    lines.append("")

    # Holdings
    lines.append("---")
    lines.append("#### 💼 持仓行情")
    lines.append("")
    for sig in holding_signals:
        if "price" in sig:
            config = HOLDINGS[sig["symbol"]]
            icon = {"critical": "🔴", "high": "🟡", "medium": "🟠", "low": "🟢"}.get(sig.get("urgency", "low"), "⚪")
            lines.append(
                f"{icon} **{sig['symbol']}** {sig['price']:.2f} "
                f"({sig['change_pct']:+.1f}%) "
                f"仓位{config['weight_current']*100:.0f}%→{config['weight_target']*100:.0f}%"
            )
        else:
            lines.append(f"⚠️ **{sig['symbol']}** 数据获取失败")
    lines.append("")

    # Signals
    urgency_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    important = [s for s in holding_signals if s.get("urgency") in ("critical", "high")]
    if important:
        lines.append("---")
        lines.append("#### ⚡ 紧急操作信号")
        lines.append("")
        for sig in sorted(important, key=lambda x: urgency_order.get(x.get("urgency", "low"), 3)):
            icon = "‼️" if sig.get("urgency") == "critical" else "⚠️"
            lines.append(f"{icon} **{sig['symbol']}**: {sig['signal']}")
            lines.append("")

    # Watchlist compact
    lines.append("---")
    lines.append("#### 👁️ 关注")
    lines.append("")
    watch_parts = []
    for symbol, config in WATCHLIST.items():
        data = watchlist_data.get(symbol)
        if data and data.get("ok"):
            watch_parts.append(f"{symbol} {data['price']:.1f}({data['change_pct']:+.1f}%)")
    lines.append(" | ".join(watch_parts))
    lines.append("")

    # Events
    if upcoming_events:
        near = [e for e in upcoming_events if e["days_until"] <= 3]
        if near:
            lines.append("---")
            lines.append("#### 📆 近期事件")
            lines.append("")
            for evt in sorted(near, key=lambda x: x["days_until"]):
                if evt["days_until"] == 0:
                    prefix = "🔥今天"
                elif evt["days_until"] == 1:
                    prefix = "⏰明天"
                else:
                    prefix = f"📌{evt['days_until']}天后"
                lines.append(f"- {prefix}: **{evt['event']}** → {evt['action']}")
            lines.append("")

    # Verdict
    lines.append("---")
    critical_count = sum(1 for s in holding_signals if s.get("urgency") == "critical")
    high_count = sum(1 for s in holding_signals if s.get("urgency") == "high")
    if critical_count > 0:
        lines.append("**‼️ 有紧急信号！请立即处理！**")
    elif high_count > 0:
        lines.append("**⚠️ 有高优先级信号，今日交易时段内处理。**")
    elif overall == "RED":
        lines.append("**🔴 红灯，建议减仓至安全水位。**")
    elif overall == "YELLOW":
        lines.append("**🟡 黄灯，严格执行止损。**")
    else:
        lines.append("**✅ 正常，按计划执行。**")

    lines.append(f"  \n📌 重点: {phase['key_action']}")

    # Rebuy plan
    if rebuy_signals:
        fired = [r for r in rebuy_signals if r.get("fired")]
        pending = [r for r in rebuy_signals if not r.get("fired")]
        lines.append("")
        lines.append("---")
        lines.append("#### 🔄 回补计划")
        lines.append("")
        if fired:
            for r in fired:
                icon = "🚨" if r.get("urgency") == "critical" else "✅"
                lines.append(f"{icon} **{r['name']}**: {r['detail']}")
        if pending:
            status_parts = [f"⏳{r['name']}" for r in pending]
            lines.append(f"等待中: {' | '.join(status_parts)}")

    return "\n".join(lines)

def send_dingtalk(report_md, title="存储芯片监控"):
    """发送钉钉 Markdown 消息"""
    timestamp, sign = dingtalk_sign()
    url = f"{DINGTALK_WEBHOOK}&timestamp={timestamp}&sign={sign}"

    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": report_md,
        },
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    ctx = create_ssl_context()

    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            result = json.loads(resp.read().decode())
        if result.get("errcode") == 0:
            return True, "发送成功"
        else:
            return False, f"钉钉返回错误: {result}"
    except Exception as e:
        return False, f"发送失败: {e}"

# ============================================================
# 主函数 MAIN
# ============================================================

def run_monitor(save_report=True, verbose=True, dingtalk=False):
    """Run the complete monitoring cycle."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today = datetime.date.today()

    if verbose:
        print(f"\n🔄 正在从新浪财经获取市场数据... ({timestamp})")

    holdings_data, watchlist_data = fetch_all_quotes()

    if verbose:
        ok_count = sum(1 for d in {**holdings_data, **watchlist_data}.values() if d.get("ok"))
        total = len(HOLDINGS) + len(WATCHLIST)
        print(f"   ✓ 成功获取 {ok_count}/{total} 个标的数据")

        for sym, data in holdings_data.items():
            if data.get("ok"):
                print(f"   💰 {sym}: {data['price']:.2f} ({data['change_pct']:+.1f}%)")

    # Evaluate traffic light
    vixy_data = watchlist_data.get("VIXY")
    tlt_data = watchlist_data.get("TLT")
    sox_data = watchlist_data.get("SOX")

    traffic_light = evaluate_traffic_light(vixy_data, tlt_data, sox_data)
    holding_signals = generate_holding_signals(holdings_data)
    upcoming_events, past_events = check_upcoming_events(days_ahead=7)
    phase = determine_phase(today)
    rebuy_signals = check_rebuy_conditions(holdings_data, watchlist_data)

    report = format_report(
        holdings_data, watchlist_data, traffic_light, holding_signals,
        upcoming_events, past_events, phase, timestamp, rebuy_signals
    )

    if verbose:
        print(report)

    if save_report:
        report_path = "/home/admin/workspace/daily_report.txt"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)

        date_str = today.strftime("%Y%m%d")
        history_dir = "/home/admin/workspace/reports"
        os.makedirs(history_dir, exist_ok=True)
        history_path = f"{history_dir}/report_{date_str}.txt"
        with open(history_path, "w", encoding="utf-8") as f:
            f.write(report)

        if verbose:
            print(f"\n📁 报告已保存: {report_path}")
            print(f"📁 历史记录: {history_path}")

    # DingTalk push
    if dingtalk:
        if verbose:
            print("\n📤 正在推送到钉钉...", end=" ", flush=True)
        md = format_dingtalk_markdown(
            holdings_data, watchlist_data, traffic_light,
            holding_signals, upcoming_events, phase, timestamp, rebuy_signals
        )
        ok, msg = send_dingtalk(md)
        if verbose:
            print(f"{'✓' if ok else '✗'} {msg}")

        # 回补条件触发时额外发一条独立告警
        critical_rebuy = [r for r in rebuy_signals if r.get("fired") and r.get("urgency") in ("critical", "high", "medium")]
        if critical_rebuy:
            alert_lines = ["### 🚨 回补条件触发！", ""]
            for r in critical_rebuy:
                icon = "🚨" if r.get("urgency") == "critical" else "✅"
                alert_lines.append(f"{icon} **{r['name']}**")
                alert_lines.append(f"> {r['detail']}")
                alert_lines.append(f"> 操作: {r['description']}")
                alert_lines.append("")
            alert_lines.append("---")
            alert_lines.append("⚡ 请立即检查并决定是否执行回补操作")
            alert_md = "\n".join(alert_lines)
            send_dingtalk(alert_md, title="🚨 回补条件触发")

    return report, traffic_light, holding_signals

# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="存储芯片投资组合每日监控系统")
    parser.add_argument("--quiet", "-q", action="store_true", help="静默模式，只输出报告")
    parser.add_argument("--no-save", action="store_true", help="不保存报告文件")
    parser.add_argument("--json", action="store_true", help="输出JSON格式")
    parser.add_argument("--dingtalk", "-d", action="store_true", help="推送到钉钉")
    args = parser.parse_args()

    report, traffic_light, signals = run_monitor(
        save_report=not args.no_save,
        verbose=not args.quiet,
        dingtalk=args.dingtalk,
    )

    if args.json:
        output = {
            "timestamp": datetime.datetime.now().isoformat(),
            "traffic_light": traffic_light[0],
            "signals": [
                {k: v for k, v in s.items() if k != "signal"}
                for s in signals
            ],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
