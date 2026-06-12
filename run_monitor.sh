#!/bin/bash
# 存储芯片监控 - 每日定时运行脚本
cd /home/admin/workspace
python3 memory_monitor.py --quiet 2>&1

# 输出最新报告到日志
echo "================================================"
echo "监控报告生成完成: $(date '+%Y-%m-%d %H:%M:%S')"
echo "报告路径: /home/admin/workspace/daily_report.txt"
echo "================================================"
