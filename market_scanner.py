#!/usr/bin/env python3
"""
盘中实时扫描器 — 每次只检查关键条件，触发时推送钉钉
配合CloudCLI定时任务使用,建议盘中每30分钟运行一次

与memory_monitor.py的区别:
- 不生成完整报告
- 只检查止损/回补条件
- 只在条件触发时才推钉钉(不触发=不打扰)
- 运行时间<5秒
"""

import sys
import os
import json
import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from memory_monitor import (
    HOLDINGS, WATCHLIST, REBUY_PLAN,
    fetch_all_quotes, check_rebuy_conditions,
    send_dingtalk, evaluate_traffic_light,
)

STATE_FILE = os.path.join(SCRIPT_DIR, "state", "scan_state.json")


def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"alerted": {}, "last_scan": None}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def scan():
    now = datetime.datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    today_str = now.strftime("%Y-%m-%d")

    holdings_data, watchlist_data = fetch_all_quotes()

    if not holdings_data and not watchlist_data:
        print(f"[{now_str}] 数据获取失败，跳过")
        return

    rebuy_signals = check_rebuy_conditions(holdings_data, watchlist_data)
    fired = [r for r in rebuy_signals if r.get("fired")]

    state = load_state()
    if (state.get("last_scan") or "")[:10] != today_str:
        state["alerted"] = {}

    new_alerts = []
    for sig in fired:
        sig_id = sig["id"]
        if sig_id not in state["alerted"]:
            new_alerts.append(sig)
            state["alerted"][sig_id] = now_str

    # 检查持仓是否有新的止损触发
    for symbol, config in HOLDINGS.items():
        data = holdings_data.get(symbol)
        if not data or not data.get("ok"):
            continue
        change = data["change_pct"]
        stop_key = f"stop_{symbol}"
        if change <= config["stop_loss_pct"] and stop_key not in state["alerted"]:
            new_alerts.append({
                "id": stop_key,
                "name": f"止损警报:{symbol}",
                "detail": f"{symbol}跌{change:.1f}%,触及止损线{config['stop_loss_pct']}%",
                "description": f"请检查{symbol}是否需要卖出",
                "urgency": "critical",
            })
            state["alerted"][stop_key] = now_str

    # 检查SOX单日暴跌
    sox_data = watchlist_data.get("SOX")
    if sox_data and sox_data.get("ok") and sox_data["change_pct"] <= -5:
        sox_key = f"sox_crash_{today_str}"
        if sox_key not in state["alerted"]:
            new_alerts.append({
                "id": sox_key,
                "name": "SOX暴跌警报",
                "detail": f"SOX费半指数跌{sox_data['change_pct']:.1f}%，全线警戒",
                "description": "检查所有持仓止损线",
                "urgency": "critical",
            })
            state["alerted"][sox_key] = now_str

    state["last_scan"] = now_str
    save_state(state)

    # 打印状态
    ok_count = sum(1 for d in holdings_data.values() if d.get("ok"))
    print(f"[{now_str}] 扫描完成: {ok_count}个持仓正常, "
          f"{len(fired)}个条件已触发, {len(new_alerts)}个新告警")

    for sig in rebuy_signals:
        icon = "✅" if sig.get("fired") else "⏳"
        print(f"  {icon} {sig.get('name', '?')}: {sig.get('detail', '')}")

    if new_alerts:
        alert_lines = [f"### 🚨 盘中扫描告警 ({now.strftime('%H:%M')})", ""]
        for a in new_alerts:
            icon = "🚨" if a.get("urgency") == "critical" else "⚠️"
            alert_lines.append(f"{icon} **{a['name']}**")
            alert_lines.append(f"> {a['detail']}")
            alert_lines.append(f"> {a.get('description', '')}")
            alert_lines.append("")
        alert_lines.append("---")
        alert_lines.append("⚡ 盘中自动扫描触发,请检查并决定操作")

        alert_md = "\n".join(alert_lines)
        ok, msg = send_dingtalk(alert_md, title="🚨 盘中告警")
        print(f"  📤 钉钉推送: {'✓' if ok else '✗'} {msg}")
    else:
        print(f"  ✅ 无新告警,不打扰")


if __name__ == "__main__":
    scan()
