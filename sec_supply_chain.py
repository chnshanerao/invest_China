#!/usr/bin/env python3
"""
SEC 10-K Supply Chain Scanner — 从SEC EDGAR直接读取10-K原文，
提取"sole source"/"single source"/"limited suppliers"等供应链瓶颈关键词，
发现隐藏的供应链依赖关系。

用法：
  python3 sec_supply_chain.py NVDA              # 扫描单个公司
  python3 sec_supply_chain.py NVDA AMD INTC      # 扫描多个公司
  python3 sec_supply_chain.py --all              # 扫描预设的AI供应链核心公司
  python3 sec_supply_chain.py --deep NVDA        # 深度模式：提取完整供应链段落
"""

import argparse
import html
import json
import os
import re
import ssl
import sys
import time
import urllib.request

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "10k_cache")

AI_SUPPLY_CHAIN_TICKERS = [
    "NVDA", "AMD", "INTC", "ASML", "TSM",
    "VRT", "ETN", "GLW", "APH", "ANET", "SMCI",
    "PLAB", "CAMT", "COHR", "LITE", "MU",
    "CRDO", "AXTI", "VICR", "LEU",
]

SUPPLY_CHAIN_KEYWORDS = [
    (r'sole[\s\-]{0,5}source', "SOLE_SOURCE"),
    (r'single[\s\-]{0,5}source', "SINGLE_SOURCE"),
    (r'limited[\s\-]{0,15}(?:number of\s+)?supplier', "LIMITED_SUPPLIERS"),
    (r'limited[\s\-]{0,15}(?:number of\s+)?source', "LIMITED_SOURCES"),
    (r'sole[\s\-]{0,5}supplier', "SOLE_SUPPLIER"),
    (r'single[\s\-]{0,5}supplier', "SINGLE_SUPPLIER"),
    (r'(?:only|exclusive)\s+(?:supplier|source|vendor|manufacturer)', "EXCLUSIVE"),
    (r'cannot[\s\-]{0,10}(?:easily|readily|quickly)?\s*(?:be replaced|substitute)', "IRREPLACEABLE"),
    (r'long[\s\-]{0,5}lead[\s\-]{0,5}time', "LONG_LEAD_TIME"),
    (r'supply[\s\-]{0,5}(?:constraint|shortage|disruption)', "SUPPLY_RISK"),
]

NAMED_ENTITIES = [
    r'(TSMC|Taiwan Semiconductor(?:\s+Manufacturing)?)',
    r'(Samsung\s+Electr\w*)',
    r'(SK\s+[Hh]ynix)',
    r'(Micron\s+Tech\w*)',
    r'(Coherent(?:\s+Corp)?)',
    r'(Lumentum)',
    r'(Photronics)',
    r'(Corning(?:\s+Inc)?)',
    r'(Amphenol)',
    r'(TE\s+Connectivity)',
    r'(Fabrinet)',
    r'(Broadcom)',
    r'(Marvell)',
    r'(Carl\s+Zeiss)',
    r'(Ajinomoto)',
    r'(Ibiden)',
    r'(Shinko\s+Electric)',
    r'(Lasertec)',
    r'(HOYA|Hoya\s+Corp\w*)',
    r'(?<![a-z])(DISCO)(?![a-z])',
    r'(Applied\s+Materials)',
    r'(Lam\s+Research)',
    r'(KLA(?:\s+Corp)?)',
    r'(Tokyo\s+Electron)',
    r'(Entegris)',
    r'(ASM\s+International)',
    r'(Onto\s+Innovation)',
    r'(Camtek)',
    r'(Kulicke\s+(?:&|and)\s*Soffa)',
    r'(BE\s+Semiconductor|BESI)',
    r'(Vicor)',
    r'(Vertiv)',
    r'(Eaton(?:\s+Corp)?)',
    r'(Schneider\s+Electric)',
    r'(Credo\s+Tech)',
    r'(AXT(?:\s+Inc)?)',
    r'(Hon\s+Hai|Foxconn)',
    r'(Wistron)',
    r'(Ablecom)',
    r'(Compuware)',
    r'(NuScale)',
    r'(Centrus)',
    r'(Ferroglobe)',
    r'(Willdan)',
    r'(Adeia|Xperi)',
    r'(Arista)',
    r'(Ciena)',
    r'(Infinera)',
]

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


def _http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={
        "User-Agent": "keji.rx research@example.com",
        "Accept-Encoding": "identity",
    })
    resp = urllib.request.urlopen(req, context=_ctx, timeout=timeout)
    return resp.read()


def get_cik(ticker):
    """Lookup CIK from SEC company_tickers.json (cached)."""
    cache_file = os.path.join(CACHE_DIR, "_cik_map.json")
    if os.path.exists(cache_file):
        with open(cache_file) as f:
            cik_map = json.load(f)
        if ticker.upper() in cik_map:
            return cik_map[ticker.upper()]

    data = _http_get("https://www.sec.gov/files/company_tickers.json")
    tickers_data = json.loads(data)
    cik_map = {}
    for v in tickers_data.values():
        cik_map[v["ticker"].upper()] = str(v["cik_str"])

    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cache_file, "w") as f:
        json.dump(cik_map, f)

    return cik_map.get(ticker.upper())


def get_latest_10k_url(ticker):
    """Get the URL of the latest 10-K (or 20-F for foreign filers) from EDGAR."""
    cik = get_cik(ticker)
    if not cik:
        print(f"  [ERROR] CIK not found for {ticker}")
        return None, None

    cik_padded = cik.zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    data = json.loads(_http_get(url))

    filings = data.get("filings", {}).get("recent", {})
    forms = filings.get("form", [])
    dates = filings.get("filingDate", [])
    accessions = filings.get("accessionNumber", [])
    docs = filings.get("primaryDocument", [])

    for target_form in ["10-K", "20-F"]:
        for i in range(len(forms)):
            if forms[i] == target_form:
                acc_nodash = accessions[i].replace("-", "")
                doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{docs[i]}"
                return doc_url, dates[i]

    print(f"  [ERROR] No 10-K/20-F found for {ticker}")
    return None, None


def fetch_10k(ticker, force=False):
    """Download and cache the 10-K HTML file."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(CACHE_DIR, f"{ticker}.htm")

    if not force and os.path.exists(cache_file) and os.path.getsize(cache_file) > 10000:
        mtime = os.path.getmtime(cache_file)
        age_days = (time.time() - mtime) / 86400
        if age_days < 90:
            return cache_file

    url, filing_date = get_latest_10k_url(ticker)
    if not url:
        return None

    print(f"  Downloading {ticker} 10-K ({filing_date})...")
    data = _http_get(url)
    with open(cache_file, "wb") as f:
        f.write(data)
    print(f"  Saved {len(data)//1024}KB → {cache_file}")
    time.sleep(0.3)
    return cache_file


def extract_text(filepath):
    """Strip HTML tags, decode entities, normalize whitespace."""
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', raw, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<script[^>]*>.*?</script>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html.unescape(text)
    text = re.sub(r'\s+', ' ', text)
    return text


def scan_supply_chain(ticker, deep=False):
    """Scan a company's 10-K for supply chain chokepoint mentions."""
    filepath = fetch_10k(ticker)
    if not filepath:
        return None

    text = extract_text(filepath)
    context_radius = 500 if deep else 200

    result = {
        "ticker": ticker,
        "text_length": len(text),
        "mentions": [],
        "named_suppliers": set(),
        "named_competitors": set(),
    }

    for pattern, label in SUPPLY_CHAIN_KEYWORDS:
        matches = list(re.finditer(pattern, text, re.IGNORECASE))
        for m in matches:
            start = max(0, m.start() - context_radius)
            end = min(len(text), m.end() + context_radius)
            ctx = text[start:end].strip()
            result["mentions"].append({
                "type": label,
                "context": ctx,
                "position": m.start(),
            })

    for pattern in NAMED_ENTITIES:
        matches = list(re.finditer(pattern, text))
        for m in matches:
            name = m.group(1).strip()
            name_lower = name.lower()
            if name_lower == ticker.lower():
                continue
            if name_lower in ("disco", "xperi", "tel", "leu", "besi"):
                continue
            if len(name) > 3:
                result["named_suppliers"].add(name)

    result["named_suppliers"] = sorted(result["named_suppliers"])
    result["named_competitors"] = sorted(result["named_competitors"])

    return result


def format_report(result):
    """Format a scan result into readable text."""
    if not result:
        return ""

    lines = []
    ticker = result["ticker"]
    lines.append(f"\n{'='*70}")
    lines.append(f"  {ticker} — {result['text_length']//1000}K chars | "
                 f"{len(result['mentions'])} supply chain mentions")
    lines.append(f"{'='*70}")

    by_type = {}
    for m in result["mentions"]:
        by_type.setdefault(m["type"], []).append(m)

    for label, mentions in sorted(by_type.items()):
        lines.append(f"\n  [{label}] × {len(mentions)}")
        for m in mentions[:3]:
            ctx = m["context"]
            if len(ctx) > 300:
                ctx = ctx[:300] + "..."
            lines.append(f"    ...{ctx}")

    if result["named_suppliers"]:
        lines.append(f"\n  [NAMED ENTITIES]: {', '.join(result['named_suppliers'])}")

    return "\n".join(lines)


def build_supply_chain_map(results):
    """Cross-reference: who supplies whom."""
    lines = []
    lines.append(f"\n{'='*70}")
    lines.append("  SUPPLY CHAIN DEPENDENCY MAP")
    lines.append(f"{'='*70}")

    all_suppliers = {}
    for r in results:
        if not r:
            continue
        ticker = r["ticker"]
        for m in r["mentions"]:
            if m["type"] in ("SOLE_SOURCE", "SOLE_SUPPLIER", "SINGLE_SOURCE",
                             "SINGLE_SUPPLIER", "EXCLUSIVE"):
                for ent_pattern in NAMED_ENTITIES:
                    for match in re.finditer(ent_pattern, m["context"], re.IGNORECASE):
                        supplier = match.group(1).strip()
                        if supplier.lower() != ticker.lower() and len(supplier) > 3:
                            key = f"{ticker} → {supplier}"
                            all_suppliers[key] = m["type"]

    if all_suppliers:
        lines.append("\n  Critical Dependencies (sole/single source):")
        for dep, dep_type in sorted(all_suppliers.items()):
            lines.append(f"    {dep}  [{dep_type}]")
    else:
        lines.append("\n  (No explicitly named sole/single source dependencies found)")
        lines.append("  Note: Most companies use generic language to avoid naming small suppliers")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="SEC 10-K Supply Chain Scanner")
    parser.add_argument("tickers", nargs="*", help="Ticker symbols to scan")
    parser.add_argument("--all", action="store_true", help="Scan all AI supply chain companies")
    parser.add_argument("--deep", action="store_true", help="Deep mode: more context per match")
    parser.add_argument("--force", action="store_true", help="Force re-download cached filings")
    parser.add_argument("--map", action="store_true", help="Build supply chain dependency map")
    args = parser.parse_args()

    if args.all:
        tickers = AI_SUPPLY_CHAIN_TICKERS
    elif args.tickers:
        tickers = [t.upper() for t in args.tickers]
    else:
        parser.print_help()
        return

    print(f"SEC 10-K Supply Chain Scanner")
    print(f"Scanning {len(tickers)} companies: {', '.join(tickers)}")

    results = []
    for ticker in tickers:
        print(f"\n--- {ticker} ---")
        try:
            result = scan_supply_chain(ticker, deep=args.deep)
            if result:
                print(format_report(result))
                results.append(result)
        except Exception as e:
            print(f"  [ERROR] {ticker}: {e}")
        time.sleep(0.3)

    if args.map and results:
        print(build_supply_chain_map(results))

    summary = {}
    for r in results:
        if not r:
            continue
        by_type = {}
        for m in r["mentions"]:
            by_type[m["type"]] = by_type.get(m["type"], 0) + 1
        summary[r["ticker"]] = {
            "total_mentions": len(r["mentions"]),
            "by_type": by_type,
            "entities": r["named_suppliers"],
        }

    out_file = os.path.join(CACHE_DIR, "scan_results.json")
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    with open(out_file, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to {out_file}")


if __name__ == "__main__":
    main()
