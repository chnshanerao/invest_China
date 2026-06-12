#!/usr/bin/env python3
"""
钉钉交互式机器人 — 支持群内@对话
使用 DingTalk Stream 模式（无需公网IP）

配置步骤:
1. 打开 https://open-dev.dingtalk.com/ → 应用开发 → 创建应用
2. 在应用内添加"机器人"能力
3. 机器人配置中选择 Stream 模式
4. 获取 AppKey 和 AppSecret
5. 设置下方 APP_KEY 和 APP_SECRET
6. 将机器人添加到你的钉钉群

运行: python3 dingtalk_bot.py
后台: nohup python3 dingtalk_bot.py > dingtalk_bot.log 2>&1 &
"""

import os
import sys
import json
import subprocess
import datetime
import logging

# ============================================================
# 配置 — 替换为你的应用凭证
# ============================================================
APP_KEY = os.environ.get("DINGTALK_APP_KEY", "YOUR_APP_KEY_HERE")
APP_SECRET = os.environ.get("DINGTALK_APP_SECRET", "YOUR_APP_SECRET_HERE")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MONITOR_SCRIPT = os.path.join(SCRIPT_DIR, "memory_monitor.py")
REPORT_FILE = os.path.join(SCRIPT_DIR, "daily_report.txt")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("dingtalk_bot")


# ============================================================
# 命令处理
# ============================================================

COMMANDS = {
    "报告": "运行监控并返回最新报告",
    "行情": "获取当前持仓行情",
    "回补": "查看回补计划状态",
    "帮助": "显示可用命令列表",
    "状态": "查看系统运行状态",
}


def handle_command(text):
    """解析用户消息并执行对应命令，返回Markdown回复"""
    text = text.strip()

    if "报告" in text or "监控" in text or "report" in text.lower():
        return cmd_report()
    elif "行情" in text or "价格" in text or "quote" in text.lower():
        return cmd_quote()
    elif "回补" in text or "计划" in text or "rebuy" in text.lower():
        return cmd_rebuy()
    elif "状态" in text or "status" in text.lower():
        return cmd_status()
    elif "帮助" in text or "help" in text.lower():
        return cmd_help()
    else:
        return cmd_smart_reply(text)


def cmd_report():
    """运行完整监控并返回报告"""
    try:
        result = subprocess.run(
            [sys.executable, MONITOR_SCRIPT, "--quiet", "--dingtalk"],
            capture_output=True, text=True, timeout=60,
            cwd=SCRIPT_DIR,
        )
        if result.returncode == 0:
            return "### ✅ 监控已执行\n\n完整报告已推送到群聊，请查看上方消息。"
        else:
            return f"### ❌ 执行失败\n\n```\n{result.stderr[:500]}\n```"
    except subprocess.TimeoutExpired:
        return "### ⏰ 执行超时\n\n监控脚本运行超过60秒，请稍后重试。"
    except Exception as e:
        return f"### ❌ 异常\n\n{str(e)}"


def cmd_quote():
    """快速获取行情"""
    try:
        result = subprocess.run(
            [sys.executable, MONITOR_SCRIPT, "--json", "--no-save"],
            capture_output=True, text=True, timeout=30,
            cwd=SCRIPT_DIR,
        )
        lines = result.stdout.strip().split("\n")
        json_start = None
        for i, line in enumerate(lines):
            if line.strip().startswith("{"):
                json_start = i
                break

        if json_start is not None:
            json_str = "\n".join(lines[json_start:])
            data = json.loads(json_str)
            md = ["### 📊 实时行情\n"]
            md.append(f"**信号灯**: {data.get('traffic_light', '?')}\n")
            for sig in data.get("signals", []):
                icon = {"critical": "🔴", "high": "🟡", "medium": "🟠", "low": "🟢"}.get(
                    sig.get("urgency", "low"), "⚪"
                )
                price = sig.get("price", 0)
                chg = sig.get("change_pct", 0)
                md.append(f"{icon} **{sig['symbol']}** {price:.2f} ({chg:+.1f}%)")
            return "\n".join(md)

        price_lines = [l for l in lines if "💰" in l]
        if price_lines:
            return "### 📊 实时行情\n\n" + "\n".join(price_lines)

        return "### 📊 行情获取中...\n\n请稍后查看推送的完整报告。"
    except Exception as e:
        return f"### ❌ 获取失败: {str(e)}"


def cmd_rebuy():
    """查看回补计划"""
    try:
        sox_tracker_file = os.path.join(SCRIPT_DIR, "state", "sox_tracker.json")
        if os.path.exists(sox_tracker_file):
            with open(sox_tracker_file, "r") as f:
                tracker = json.load(f)
        else:
            tracker = None

        md = ["### 🔄 回补计划状态\n"]

        if tracker:
            md.append(f"**SOX追踪**: 低点 {tracker.get('low', '?')} "
                      f"@ {tracker.get('low_date', '?')}")
            md.append(f"**未创新低天数**: {tracker.get('no_new_low_days', 0)} (需≥2天触发)\n")

        md.append("**回补条件清单:**")
        md.append("1. ⏳ SOX连续2日不创新低 → 回补MU")
        md.append("2. ⏳ 标普纳入前5天+SOX企稳 → 回补MRVL")
        md.append("3. ⏳ VIXY回落至22以下 → 恢复6成仓")
        md.append("4. ⏳ 07709跌至HK$85以下 → 小仓位抄底")
        md.append("5. 🚨 07709跌破HK$90 → 全清07709")

        sox_stable = tracker and tracker.get("no_new_low_days", 0) >= 2 if tracker else False
        if sox_stable:
            md.append("\n**✅ SOX已企稳，MU回补条件已满足！**")

        return "\n".join(md)
    except Exception as e:
        return f"### ❌ 查询失败: {str(e)}"


def cmd_status():
    """系统状态"""
    md = ["### 🖥️ 系统状态\n"]

    now = datetime.datetime.now()
    md.append(f"**当前时间**: {now.strftime('%Y-%m-%d %H:%M:%S')}")

    if os.path.exists(REPORT_FILE):
        mtime = os.path.getmtime(REPORT_FILE)
        report_time = datetime.datetime.fromtimestamp(mtime)
        delta = (now - report_time).total_seconds() / 3600
        md.append(f"**最近报告**: {report_time.strftime('%Y-%m-%d %H:%M')} ({delta:.1f}小时前)")

    try:
        result = subprocess.run(["pgrep", "-f", "monitor_daemon.py"],
                                capture_output=True, text=True)
        if result.stdout.strip():
            md.append(f"**守护进程**: ✅ 运行中 (PID: {result.stdout.strip()})")
        else:
            md.append("**守护进程**: ❌ 未运行")
    except Exception:
        md.append("**守护进程**: ⚠️ 无法检查")

    md.append("\n**定时推送**: 05:00 / 09:30 / 21:30")
    return "\n".join(md)


def cmd_help():
    md = ["### 💡 可用命令\n"]
    md.append("在群内 @我 + 以下关键词:\n")
    for cmd, desc in COMMANDS.items():
        md.append(f"- **{cmd}** — {desc}")
    md.append("\n也可以直接用自然语言提问，如:")
    md.append("- 「现在该不该买」「行情怎么样」「MU现在多少钱」")
    return "\n".join(md)


def cmd_smart_reply(text):
    """对无法识别的消息给出引导"""
    md = [f"### 🤖 收到消息\n"]
    md.append(f"> {text}\n")
    md.append("我暂时无法理解这个指令。请尝试以下命令:\n")
    for cmd, desc in COMMANDS.items():
        md.append(f"- **{cmd}** — {desc}")
    return "\n".join(md)


# ============================================================
# DingTalk Stream 机器人主程序
# ============================================================

def start_bot():
    """启动 Stream 模式机器人"""
    if APP_KEY == "YOUR_APP_KEY_HERE":
        print("=" * 60)
        print("❌ 请先配置 APP_KEY 和 APP_SECRET!")
        print()
        print("配置方法 (任选其一):")
        print()
        print("方法1: 设置环境变量")
        print("  export DINGTALK_APP_KEY='你的AppKey'")
        print("  export DINGTALK_APP_SECRET='你的AppSecret'")
        print()
        print("方法2: 直接修改本文件顶部的 APP_KEY 和 APP_SECRET")
        print()
        print("获取凭证步骤:")
        print("  1. 打开 https://open-dev.dingtalk.com/")
        print("  2. 应用开发 → 企业内部开发 → 创建应用")
        print("  3. 添加「机器人」能力 → 选择 Stream 模式")
        print("  4. 复制 AppKey 和 AppSecret")
        print("  5. 发布应用 → 将机器人添加到群聊")
        print("=" * 60)
        sys.exit(1)

    try:
        import dingtalk_stream
        from dingtalk_stream import AckMessage
    except ImportError:
        print("❌ 缺少 dingtalk-stream 包")
        print("安装: python3 -m pip install --user --break-system-packages dingtalk-stream")
        sys.exit(1)

    class BotHandler(dingtalk_stream.ChatbotHandler):
        async def process(self, callback):
            incoming = callback.data
            text = incoming.get("text", {}).get("content", "").strip()
            sender = incoming.get("senderNick", "用户")

            log.info(f"收到消息 from {sender}: {text}")

            reply_md = handle_command(text)

            log.info(f"回复: {reply_md[:100]}...")

            self.reply_markdown("监控助手", reply_md, incoming)

            return AckMessage.STATUS_OK, "OK"

    credential = dingtalk_stream.Credential(APP_KEY, APP_SECRET)
    client = dingtalk_stream.DingTalkStreamClient(credential)
    client.register_callback_handler(
        dingtalk_stream.ChatbotMessage.TOPIC,
        BotHandler()
    )

    log.info("🤖 钉钉交互式机器人启动中...")
    log.info(f"   AppKey: {APP_KEY[:8]}...")
    log.info("   等待消息...")

    client.start_forever()


if __name__ == "__main__":
    start_bot()
