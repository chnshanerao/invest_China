#!/usr/bin/env python3
"""
存储芯片监控守护进程 - 定时运行监控并输出报告
可通过 nohup 或 supervisord 运行在后台

运行方式:
  前台: python3 monitor_daemon.py
  后台: nohup python3 monitor_daemon.py > /dev/null 2>&1 &

默认执行时间 (北京时间 UTC+8):
  09:30 - 港股开盘后
  21:30 - 美股开盘后
  05:00 - 美股收盘后
"""

import time
import datetime
import subprocess
import sys
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MONITOR_SCRIPT = os.path.join(SCRIPT_DIR, "memory_monitor.py")
LOG_FILE = os.path.join(SCRIPT_DIR, "monitor.log")

SCHEDULE_HOURS = [5, 9, 21]
SCHEDULE_MINUTES = [0, 30, 30]

def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def run_monitor():
    log("开始执行监控...")
    try:
        result = subprocess.run(
            [sys.executable, MONITOR_SCRIPT, "--quiet"],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            log("监控执行成功，报告已生成")
        else:
            log(f"监控执行失败: {result.stderr[:200]}")
    except Exception as e:
        log(f"监控执行异常: {e}")

def next_run_time():
    now = datetime.datetime.now()
    candidates = []
    for h, m in zip(SCHEDULE_HOURS, SCHEDULE_MINUTES):
        t = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if t <= now:
            t += datetime.timedelta(days=1)
        candidates.append(t)
    return min(candidates)

def main():
    log("监控守护进程启动")
    log(f"计划运行时间: {', '.join(f'{h:02d}:{m:02d}' for h, m in zip(SCHEDULE_HOURS, SCHEDULE_MINUTES))}")

    run_monitor()

    while True:
        nxt = next_run_time()
        wait = (nxt - datetime.datetime.now()).total_seconds()
        log(f"下次运行: {nxt.strftime('%Y-%m-%d %H:%M')} (等待{wait/3600:.1f}小时)")

        while datetime.datetime.now() < nxt:
            time.sleep(60)

        if datetime.datetime.now().weekday() < 5:
            run_monitor()
        else:
            log("周末跳过")

if __name__ == "__main__":
    main()
