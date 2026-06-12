#!/usr/bin/env python3
"""
A股盘中实时扫描器 — 每次只检查关键条件，触发时推送钉钉
配合CloudCLI定时任务，盘中每30分钟运行一次

与a_stock_monitor.py的区别:
- 不生成完整报告
- 只检查止损/买卖区/大盘暴跌/事件
- 只在条件触发时才推钉钉(不触发=不打扰)
- 运行时间<5秒
"""

import sys
import os
import json
import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from a_stock_monitor import (
    HOLDINGS, PRICE_TARGETS, MARKET_INDICES, KEY_EVENTS,
    fetch_all_quotes, check_price_signals, check_market_crash,
    send_dingtalk,
)

STATE_FILE = os.path.join(SCRIPT_DIR, "state", "a_scan_state.json")


def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"alerted": {}, "last_scan": None}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def check_event_proximity():
    today = datetime.date.today()
    alerts = []
    for ev in KEY_EVENTS:
        try:
            ev_date = datetime.date.fromisoformat(ev["date"])
        except ValueError:
            continue
        delta = (ev_date - today).days
        if delta == 0:
            alerts.append({
                "id": f"event_{ev['date']}",
                "type": "info",
                "name": f"今日事件: {ev['event']}",
                "detail": ev["action"],
            })
        elif delta == 1:
            alerts.append({
                "id": f"event_{ev['date']}",
                "type": "info",
                "name": f"明日事件: {ev['event']}",
                "detail": ev["action"],
            })
    return alerts


def scan():
    now = datetime.datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    today_str = now.strftime("%Y-%m-%d")

    holdings_data, index_data = fetch_all_quotes()

    if not holdings_data:
        print(f"[{now_str}] 数据获取失败，跳过")
        return

    all_signals = []
    all_signals.extend(check_price_signals(holdings_data))
    all_signals.extend(check_market_crash(index_data))
    all_signals.extend(check_event_proximity())

    state = load_state()
    if (state.get("last_scan") or "")[:10] != today_str:
        state["alerted"] = {}

    new_alerts = []
    for sig in all_signals:
        sig_id = sig["id"]
        if sig_id not in state["alerted"]:
            new_alerts.append(sig)
            state["alerted"][sig_id] = now_str

    state["last_scan"] = now_str
    save_state(state)

    ok_count = sum(1 for d in holdings_data.values() if d.get("ok"))
    print(f"[{now_str}] 扫描完成: {ok_count}个持仓, "
          f"{len(all_signals)}个条件触发, {len(new_alerts)}个新告警")

    for sig in all_signals:
        deduped = "NEW" if sig["id"] in [a["id"] for a in new_alerts] else "dup"
        icon = {"critical": "!!!", "warning": "!", "info": ">"}.get(sig["type"], ">")
        print(f"  [{deduped}] {icon} {sig['name']}: {sig.get('detail', '')}")

    if new_alerts:
        has_critical = any(s["type"] == "critical" for s in new_alerts)
        title = "A股止损警报" if has_critical else "A股盘中告警"

        alert_lines = [f"### {title} ({now.strftime('%H:%M')})", ""]
        for a in new_alerts:
            icon = {"critical": "!!!", "warning": "!", "info": ">"}.get(a["type"], ">")
            alert_lines.append(f"{icon} **{a['name']}**")
            alert_lines.append(f"> {a.get('detail', '')}")
            if a.get("action"):
                alert_lines.append(f"> {a['action']}")
            alert_lines.append("")
        alert_lines.append("---")
        alert_lines.append("A股盘中自动扫描触发")

        alert_md = "\n".join(alert_lines)
        ok, msg = send_dingtalk(alert_md, title=title)
        print(f"  钉钉推送: {'成功' if ok else '失败'} {msg}")
    else:
        print(f"  无新告警,不打扰")


if __name__ == "__main__":
    scan()
