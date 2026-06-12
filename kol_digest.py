#!/usr/bin/env python3
"""
KOL观点自动摘报系统
每周采集AI半导体KOL的最新观点，交叉验证，推送钉钉

数据源:
  1. RSS: SemiAnalysis, PhotonCap
  2. Baidu搜索: 中文覆盖 (Serenity瓶颈理论, 光通信CPO等)
  3. Bing搜索: 英文覆盖 (Fabricated Knowledge, CrackTheMarket等)

配合CloudCLI定时任务使用，每周日10:00运行
"""

import sys
import os
import json
import re
import datetime
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import html
import argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from memory_monitor import send_dingtalk, create_ssl_context

STATE_FILE = os.path.join(SCRIPT_DIR, "state", "kol_digest_state.json")

# ============================================================
# KOL配置
# ============================================================

RSS_SOURCES = {
    "SemiAnalysis": {
        "url": "https://semianalysis.com/feed",
        "short": "SA",
        "focus": "AI芯片架构/数据中心/全栈分析",
        "days": 30,
    },
    "PhotonCap": {
        "url": "https://photoncap.net/feed",
        "short": "PC",
        "focus": "光通信/CPO/硅光/核电/防务光学",
        "days": 7,
    },
}

SEARCH_QUERIES = {
    "bing": [
        "Serenity chokepoint AI photonics stocks 2026",
        "Fabricated Knowledge semiconductor 2026",
        "NINGI Research short report semiconductor 2026",
        "PhotonCap photonics CPO nuclear supply chain",
        "SemiAnalysis Dylan Patel AI infrastructure 2026",
    ],
}

KNOWN_TICKERS_DB = {
    "NVDA": "英伟达", "COHR": "Coherent光模块", "LITE": "Lumentum激光器",
    "MRVL": "Marvell定制芯片", "AXTI": "AXT InP衬底", "SIVE": "Sivers DML激光器",
    "AAOI": "AAOI光模块IDM", "VICR": "Vicor功率", "LPTH": "LightPath防务光学",
    "TSM": "台积电", "ASML": "阿斯麦光刻", "MU": "美光存储",
    "AEHR": "AEHR测试设备", "IQE": "IQE InP外延", "CRDO": "Credo高速连接",
    "GLW": "康宁光纤", "ANET": "Arista网络", "VRT": "Vertiv电力散热",
    "AVGO": "博通", "AMD": "AMD", "INTC": "英特尔",
    "GFS": "GlobalFoundries", "FN": "Fabrinet", "POET": "POET光子",
    "LWLG": "Lightwave Logic", "TSEM": "Tower Semi",
    "MPWR": "Monolithic Power", "ALAB": "Astera Labs",
    "CEG": "Constellation能源", "CCJ": "Cameco铀矿", "LEU": "Centrus铀浓缩",
    "IONQ": "IonQ量子", "RGTI": "Rigetti量子", "RKLB": "Rocket Lab",
    "NVTS": "Navitas GaN功率",
}

EXCLUDE_TICKERS = {"CEO", "IPO", "ETF", "LLC", "INC", "USA", "GDP", "PMI",
                   "THE", "FOR", "AND", "NOT", "ALL", "NEW", "TOP", "BIG",
                   "LOW", "HIGH", "NYSE", "SEC", "DOE", "FAQ", "RSS"}

# ============================================================
# 状态管理
# ============================================================

def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"last_run": None, "seen_articles": [], "known_tickers": []}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ============================================================
# RSS采集
# ============================================================

def fetch_rss(source_name, config, days=None):
    url = config["url"]
    if days is None:
        days = config.get("days", 7)
    ctx = create_ssl_context()
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    })

    try:
        resp = urllib.request.urlopen(req, timeout=20, context=ctx)
        data = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  [WARN] {source_name} RSS获取失败: {e}")
        return []

    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        print(f"  [WARN] {source_name} RSS解析失败: {e}")
        return []

    channel = root.find("channel")
    if channel is None:
        return []

    cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
    articles = []

    for item in channel.findall("item"):
        title = item.find("title")
        title = title.text.strip() if title is not None and title.text else ""
        link = item.find("link")
        link = link.text.strip() if link is not None and link.text else ""
        pub_date = item.find("pubDate")
        pub_str = pub_date.text.strip() if pub_date is not None and pub_date.text else ""
        desc_el = item.find("description")
        desc = desc_el.text.strip() if desc_el is not None and desc_el.text else ""
        desc = html.unescape(desc)

        parsed_date = parse_rss_date(pub_str)
        if parsed_date and parsed_date < cutoff:
            continue

        tickers = extract_tickers(title + " " + desc)

        articles.append({
            "source": source_name,
            "source_short": config["short"],
            "title": title,
            "link": link,
            "date": pub_str[:16] if pub_str else "",
            "date_parsed": parsed_date,
            "tickers": tickers,
            "summary": clean_html(desc)[:200],
            "id": f"{source_name}:{title[:50]}",
        })

    return articles


def parse_rss_date(date_str):
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
        "%Y-%m-%dT%H:%M:%S%z",
    ]
    for fmt in formats:
        try:
            dt = datetime.datetime.strptime(date_str, fmt)
            return dt.replace(tzinfo=None) if dt.tzinfo else dt
        except ValueError:
            continue
    return None


def clean_html(text):
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_tickers(text):
    dollar_tickers = re.findall(r"\$([A-Z]{2,6})\b", text)
    word_tickers = []
    for ticker in KNOWN_TICKERS_DB:
        if len(ticker) >= 3 and re.search(r'\b' + ticker + r'\b', text):
            word_tickers.append(ticker)
    combined = list(dict.fromkeys(dollar_tickers + word_tickers))
    return [t for t in combined if t not in EXCLUDE_TICKERS]


# ============================================================
# 搜索引擎采集
# ============================================================

def search_bing(query, max_results=5):
    ctx = create_ssl_context()
    encoded = urllib.parse.quote(query)
    url = f"https://www.bing.com/search?q={encoded}&count={max_results}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    })

    try:
        resp = urllib.request.urlopen(req, timeout=15, context=ctx)
        data = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  [WARN] Bing搜索失败 ({query[:20]}...): {e}")
        return []

    results = []
    h2_matches = re.findall(r"<h2[^>]*>(.*?)</h2>", data, re.DOTALL)
    cite_matches = re.findall(r"<cite>(.*?)</cite>", data, re.DOTALL)

    for i, h2 in enumerate(h2_matches[:max_results]):
        title = clean_html(h2)
        if not title or len(title) < 5:
            continue
        if "翻译" in title or "词典" in title or "dictionary" in title.lower():
            continue

        cite = clean_html(cite_matches[i]) if i < len(cite_matches) else ""
        tickers = extract_tickers(title)

        results.append({
            "source": "Bing",
            "source_short": "BG",
            "title": title[:100],
            "link": cite[:200] if cite else "",
            "date": "",
            "date_parsed": None,
            "tickers": tickers,
            "summary": title[:200],
            "id": f"bing:{query[:20]}:{title[:30]}",
        })

    return results


# ============================================================
# 交叉验证
# ============================================================

def cross_validate(all_articles, state):
    ticker_map = {}

    for art in all_articles:
        for ticker in art["tickers"]:
            if ticker not in ticker_map:
                ticker_map[ticker] = {"sources": set(), "articles": [], "name": ""}
            ticker_map[ticker]["sources"].add(art["source"])
            ticker_map[ticker]["articles"].append(art)
            if ticker in KNOWN_TICKERS_DB:
                ticker_map[ticker]["name"] = KNOWN_TICKERS_DB[ticker]

    prev_known = set(state.get("known_tickers", []))
    hot = []  # >=3 sources
    warm = []  # 2 sources
    single = []  # 1 source

    for ticker, info in sorted(ticker_map.items(), key=lambda x: len(x[1]["sources"]), reverse=True):
        is_new = ticker not in prev_known
        entry = {
            "ticker": ticker,
            "name": info["name"],
            "source_count": len(info["sources"]),
            "sources": sorted(info["sources"]),
            "articles": info["articles"],
            "is_new": is_new,
        }

        if len(info["sources"]) >= 3:
            hot.append(entry)
        elif len(info["sources"]) >= 2:
            warm.append(entry)
        else:
            if is_new or ticker in KNOWN_TICKERS_DB:
                single.append(entry)

    return hot, warm, single[:10], list(ticker_map.keys())


# ============================================================
# 报告生成
# ============================================================

def format_report(hot, warm, single, rss_articles, now):
    date_str = now.strftime("%m/%d")
    lines = [f"### 📡 KOL观点周报 ({date_str})", ""]

    if hot:
        lines.append("#### 🔥 高共识标的（≥3个来源）")
        for entry in hot:
            new_tag = "🆕 " if entry["is_new"] else ""
            name_tag = f" ({entry['name']})" if entry["name"] else ""
            sources_str = " + ".join(entry["sources"])
            lines.append(f"**{new_tag}{entry['ticker']}**{name_tag} — {sources_str}")
            for art in entry["articles"][:3]:
                lines.append(f"> {art['source_short']}: {art['title'][:60]}")
            lines.append("")

    if warm:
        lines.append("#### ⚡ 中共识标的（2个来源）")
        for entry in warm:
            new_tag = "🆕 " if entry["is_new"] else ""
            name_tag = f" ({entry['name']})" if entry["name"] else ""
            sources_str = " + ".join(entry["sources"])
            lines.append(f"**{new_tag}{entry['ticker']}**{name_tag} — {sources_str}")
            art = entry["articles"][0]
            lines.append(f"> {art['summary'][:80]}")
            lines.append("")

    new_singles = [s for s in single if s["is_new"]]
    if new_singles:
        lines.append("#### 💡 新出现标的")
        for entry in new_singles[:5]:
            name_tag = f" ({entry['name']})" if entry["name"] else ""
            lines.append(f"**🆕 {entry['ticker']}**{name_tag} — {entry['sources'][0]}")
            art = entry["articles"][0]
            lines.append(f"> {art['title'][:60]}")
        lines.append("")

    rss_by_source = {}
    for art in rss_articles:
        src = art["source"]
        if src not in rss_by_source:
            rss_by_source[src] = []
        rss_by_source[src].append(art)

    if rss_by_source:
        lines.append("#### 📊 本周KOL文章")
        for src, arts in rss_by_source.items():
            for art in arts[:3]:
                link_tag = f"[{art['title'][:50]}]({art['link']})" if art["link"] else art["title"][:50]
                lines.append(f"- **{src}**: {link_tag}")
                if art["tickers"]:
                    lines.append(f"  提及: {', '.join(['$'+t for t in art['tickers'][:8]])}")
        lines.append("")

    lines.append("---")
    lines.append(f"📡 自动KOL追踪 | {now.strftime('%Y-%m-%d %H:%M')} | 每周日推送")

    return "\n".join(lines)


# ============================================================
# 主流程
# ============================================================

def run_digest(push_dingtalk=False, quiet=False, force=False):
    now = datetime.datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    state = load_state()

    if not quiet:
        print(f"[{now_str}] KOL观点摘报开始采集...")

    all_articles = []

    if not quiet:
        print("\n📡 RSS采集...")
    for name, config in RSS_SOURCES.items():
        if not quiet:
            print(f"  获取 {name}...")
        articles = fetch_rss(name, config, days=7)
        if not quiet:
            print(f"    → {len(articles)} 篇文章")
        all_articles.extend(articles)

    rss_articles = list(all_articles)

    if not quiet:
        print("\n🔍 Bing搜索...")
    for query in SEARCH_QUERIES["bing"]:
        results = search_bing(query)
        if not quiet:
            print(f"  '{query[:30]}...' → {len(results)} 条结果")
        all_articles.extend(results)

    if not force:
        seen = set(state.get("seen_articles", []))
        new_articles = [a for a in all_articles if a["id"] not in seen]
        if not quiet:
            print(f"\n📊 总共 {len(all_articles)} 条，其中 {len(new_articles)} 条新内容")
    else:
        new_articles = all_articles
        if not quiet:
            print(f"\n📊 强制模式: 处理全部 {len(all_articles)} 条")

    hot, warm, single, all_tickers = cross_validate(all_articles, state)

    if not quiet:
        print(f"\n📈 交叉验证结果:")
        print(f"  🔥 高共识(≥3源): {len(hot)} 个标的")
        print(f"  ⚡ 中共识(2源): {len(warm)} 个标的")
        print(f"  💡 单一来源: {len(single)} 个标的")
        print()
        for h in hot:
            print(f"  🔥 {h['ticker']} ({h['name']}) — {', '.join(h['sources'])}")
        for w in warm:
            print(f"  ⚡ {w['ticker']} ({w['name']}) — {', '.join(w['sources'])}")

    report = format_report(hot, warm, single, rss_articles, now)

    if not quiet:
        print(f"\n{'='*60}")
        print(report)
        print(f"{'='*60}")

    state["last_run"] = now_str
    state["seen_articles"] = [a["id"] for a in all_articles][-200:]
    state["known_tickers"] = list(set(state.get("known_tickers", []) + all_tickers))
    save_state(state)

    if push_dingtalk:
        has_content = hot or warm or [s for s in single if s["is_new"]] or rss_articles
        if has_content:
            ok, msg = send_dingtalk(report, title="📡 KOL周报")
            if not quiet:
                print(f"\n📤 钉钉推送: {'✓' if ok else '✗'} {msg}")
        else:
            if not quiet:
                print("\n✅ 无新内容，不推送")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KOL观点自动摘报")
    parser.add_argument("--dingtalk", action="store_true", help="推送到钉钉")
    parser.add_argument("--quiet", action="store_true", help="静默模式")
    parser.add_argument("--force", action="store_true", help="忽略去重")
    args = parser.parse_args()

    run_digest(push_dingtalk=args.dingtalk, quiet=args.quiet, force=args.force)
