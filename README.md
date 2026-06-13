# AI供应链瓶颈投研系统 + A股ETF趋势交易系统

两套独立的量化投资工具，纯Python实现，零第三方依赖（仅stdlib）。

---

## 一、Chokepoint 投研系统（美股）

基于 **Serenity瓶颈投资法（Chokepoint Theory）** 的AI供应链研究驱动型投资监测系统。

### 投资方法论

核心理念：在AI基础设施供应链中找到 **物理瓶颈** — 不可绕过、不可替代、供不应求的环节，投资其中的小盘隐形冠军。

**决策链：**
```
产业链研究(为什么买) → 估值(贵不贵) → 技术面(什么时候买) → 交易指令
```

**Serenity五问筛选法：**
1. 这个组件是否在物理上不可跳过？（光掩模→芯片制造必须经过）
2. 是否只有1-2家供应商？（寡头/垄断=定价权）
3. 下游客户是否有替代选择？（锁定效应）
4. 产能是否能快速扩张？（不能=瓶颈持续）
5. 管理层是否在买入自家股票？（仅CEO买入为弱正信号，卖出不说明问题）

### 产业链拆解

```
AI数据中心
├── GPU计算集群
│   ├── HBM高带宽内存 ─────── MU(Micron) / HYNIX(SK海力士)
│   ├── 先进封装(CoWoS)
│   │   ├── 封装检测 ──────── CAMT(Camtek)
│   │   └── 光掩模 ─────────── PLAB(Photronics)
│   ├── SerDes连接芯片 ────── CRDO(Credo)
│   ├── GPU功率模块 ────────── VICR(Vicor)
│   └── 封装IP ──────────── ADEA(Adeia)
├── 光互联网络
│   ├── 800G/1.6T光模块 ───── COHR(Coherent)
│   ├── EML激光器 ──────────── LITE(Lumentum)
│   └── InP衬底 ────────────── AXTI(AXT)
├── 电力(核能)
│   ├── 小型模块堆SMR ────── SMR(NuScale)
│   ├── 核燃料(LEU) ────────── LEU(Centrus)
│   ├── 核电服务 ────────────── NNE(Nano Nuclear)
│   └── 电网接入 ────────────── WLDN(Willdan)
└── 基础材料
    ├── 硅金属 ──────────────── GSM(Ferroglobe)
    └── MOSFET ─────────────── MX(Magnachip)
```

### 数据源

| 数据类型 | API来源 | 具体字段 | 更新频率 |
|---------|--------|---------|---------|
| 日K线(OHLCV) | Sina Finance `stock.finance.sina.com.cn` | 开高低收量 | 每日 |
| 实时行情/估值 | Sina Finance `hq.sinajs.cn/list=gb_{ticker}` | fields[1]=价格, [12]=市值, [13]=PE, [8]=52周高, [9]=52周低 | 实时 |
| 季度基本面 | SEC EDGAR XBRL `data.sec.gov/api/xbrl/companyfacts/` | Revenue, EPS, GrossProfit, NetIncome, CostOfRevenue | 季度 |
| CIK映射 | SEC `sec.gov/files/company_tickers.json` | ticker→CIK编号 | 缓存 |
| 供应链关系 | SEC 10-K全文 `efts.sec.gov` | sole-source/single-source关键词扫描 | 年度 |
| 宏观指标 | Sina Finance `hq.sinajs.cn` | SOX(费城半导体), VXX(恐慌), USD/JPY | 每日 |
| 港股(HYNIX) | 腾讯财经 `web.ifzq.gtimg.cn` | HK ETF 07709 价格 | 每日 |

### 技术分析信号评分（0-100分）

| 因子 | 分值 | 计算方法 |
|------|------|---------|
| 趋势(SMA) | 0-30 | 多头排列(20>50>200)=30分, 站上20MA=10分 |
| 动量(MACD/RSI) | 0-25 | MACD金叉+15, RSI超卖反弹+15, 超买-10 |
| 波动(Bollinger) | 0-20 | 触下轨反弹+15, 中轨上方+10 |
| 量价 | 0-15 | 放量上涨+15, 缩量回调+10, 放量下跌-5 |
| 入场区间 | 0-10 | 区间内+10, 低于区间+8 |

| 总分 | 信号 | 含义 |
|------|------|------|
| ≥75 | STRONG_BUY | 多因子共振，建议入场 |
| 60-74 | BUY | 偏多，可分批建仓 |
| 40-59 | HOLD | 中性，持有观望 |
| 25-39 | CAUTION | 偏空，谨慎 |
| <25 | SELL | 多空转向，回避 |

### 估值指标

- **P/E(TTM)** — 从Sina实时获取
- **P/S(TTM)** — 市值 ÷ 最近4季度Revenue合计（SEC XBRL）
- **距52周高点%** — 跌幅越大=越便宜（<-30%为绿色=cheap）
- **毛利率趋势** — SEC XBRL季度GrossProfit/Revenue

### 使用方法

```bash
# 1. 数据采集

# 更新历史K线数据
python3 chokepoint_trader.py --update-history

# 采集SEC季度基本面 + Sina实时估值
python3 fundamentals_fetcher.py

# 扫描技术面信号（每日运行）
python3 chokepoint_trader.py

# 扫描SEC Filing事件
python3 sec_filing_monitor.py

# 2. Web系统

# 启动Web服务器
python3 chokepoint_web.py
# 浏览器访问 http://localhost:8088

# 3. 单个标的分析
python3 chokepoint_trader.py --ticker COHR

# 4. 数据初始化（首次使用）
python3 migrate_to_db.py
```

### Web系统功能（6个Tab）

| Tab | 功能 |
|-----|------|
| 投资总览 | 宏观指标(SOX/VXX/JPY) + 全标的卡片(论点+估值+信号) |
| 研究详情 | 投资逻辑全文 + 季度Revenue/毛利率图表 + 估值面板 |
| 交易策略 | K线图(SMA20/50+成交量) + 信号Score折线 + 技术指标 + 交易建议 |
| 产业链 | 交互式SVG拆解图 — GPU/光互联/核电/材料全链路可视化 |
| 需求侧 | 超大客户CapEx追踪(MSFT/GOOG/META/AMZN) |
| 管理 | 标的CRUD + 手动采集触发 |

---

## 二、A股ETF趋势交易系统

基于 **多周期趋势过滤 + 信号分类** 的右侧趋势跟踪系统，覆盖36个A股行业/策略ETF。

### 核心理念

- **月线定方向（铁律）** — 月线不是多头，一律不入场，不追周度小反弹
- **周线确认趋势** — 月线+周线过滤出"值得关注的盘子"
- **日线分类信号** — 在趋势确认的标的中，按交易风格分类信号，用户自主决策
- **自适应止损** — 涨得越多止损越紧，自动锁住利润

### 系统架构：选菜单 → 自助餐

```
Step 1: 多周期趋势过滤（选菜单 — 系统决定可选盘子）
┌─────────────────────────────────────────────┐
│  月线多头（铁律）        周线趋势确认         │
│  价格>月MA20 + MA上行   价格>周MA10/MA上行/MACD>0 │
│                                             │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐    │
│  │ 全满足    │ │ 部分满足  │ │ 都不满足  │    │
│  │ 月+周通过 │ │ 仅一个过  │ │ 都不过   │    │
│  │ → 可交易  │ │ → 观察   │ │ → 回避   │    │
│  └──────────┘ └──────────┘ └──────────┘    │
└─────────────────────────────────────────────┘
                    ↓
Step 2: 日线信号分类（自助餐 — 用户选择交易风格）
┌─────────────────────────────────────────────┐
│  在趋势确认的标的中，按日线指标分5类信号       │
│                                             │
│  🔴 突破信号   价格站上MA+MACD正+放量 → 建仓30-50% │
│  🟢 回踩机会   回踩MA附近或RSI超卖   → 加仓50-70% │
│  🟠 超买提醒   偏离>8%且RSI>70     → 谨慎追高    │
│  🔵 强势持仓   站上MA但缺部分动量   → 持有/轻仓   │
│  ⚪ 观望       趋势不满足          → 等待        │
└─────────────────────────────────────────────┘
```

### 多周期过滤条件

| 周期 | 条件 | 含义 | 默认参数 |
|------|------|------|---------|
| **月线（铁律）** | 价格>月MA(N) + 月MA上行 | 长期趋势向上才入场 | MA20 |
| **周线确认** | 价格>周MA(N) / 周MA上行 / 周MACD>0，满足≥M个 | 中期趋势确认 | MA10, 2/3 |
| **日线分类** | 价格vs MA20 / MA上行 / MACD / 量价 | 分类信号供决策 | MA20 |

### 信号分类逻辑

| 类型 | 触发条件 | 仓位建议 | 适合风格 |
|------|---------|---------|---------|
| **突破信号** | 月周过+价>MA+MACD正+放量 | 建仓 30-50% | 右侧动量交易 |
| **回踩机会** | 月周过+价格回踩MA附近或RSI超卖 | 加仓 50-70% | 左侧回踩买入 |
| **超买提醒** | 月周过+偏离MA>8%且RSI>70 | 谨慎追高 | 提醒调仓 |
| **强势持仓** | 月周过+站上MA但缺部分动量 | 持有/轻仓 | 已持仓管理 |
| **观望** | 月线或周线不满足 | 等待 | 不操作 |

### 自适应ATR止损

```
trailing_stop = highest_close - K × ATR(20)
K = max(1.2, 3.0 - gain_adj - accel_adj)
```
刚入场K=3.0（宽松），涨10%→K=2.7，涨30%→K=2.1，暴涨→K=1.2（最紧）。

### Per-ETF参数配置

不同ETF特性差异大，系统支持per-ETF参数覆盖：
- 自动优化单个ETF后，最优参数自动保存为该ETF的覆盖配置
- Web策略Tab可查看/清除每个ETF的覆盖参数
- 未设置覆盖的ETF使用全局参数

### 回测结果（500天）

| 指标 | 数值 |
|------|------|
| 平均收益 | **+47.1%**（同期上证+26.3%） |
| 盈利比例 | 26/36（72%的ETF盈利） |
| 总交易 | 355笔，胜率48% |
| 头部标的 | 芯片+178%, 通信+155%, 半导体+153% |

| 市场环境 | 交易笔数 | 胜率 | 盈亏比 | 合计PnL |
|---------|---------|------|--------|---------|
| **牛市** | 167笔 | 48% | 4.4 | +854% |
| **熊市** | 55笔 | 62% | 2.4 | +313% |
| **震荡** | 133笔 | 42% | 2.7 | +238% |

### 数据源

| 数据 | API | 用途 |
|------|-----|------|
| 历史日K线 | 腾讯财经 `web.ifzq.gtimg.cn`（默认） | 技术指标、回测，免费无限制 |
| 历史日K线 | Tushare Pro `api.tushare.pro`（可选） | 更长历史数据，需Token |
| 实时行情 | 新浪财经 `hq.sinajs.cn` | 盘中监测 |

### Web Dashboard

启动命令：`python3 a_etf_web.py --port 8888`

**监控页（2步骤布局）：**
- **Step 1: 多周期趋势过滤** — 趋势等级卡片（全满足/部分满足/都不满足），一键筛选可交易盘子
- **Step 2: 日线信号分类** — 在选中等级内显示突破/回踩/超买/强势/观望信号分布，含仓位建议
- 支持分类筛选（行业/细分/策略）、信号筛选、搜索、多列排序
- 点击展开详情：信号判断依据 + 月周日三层指标 + 技术面 + 止损计算 + 份额流向

**策略页：**
- 月线/周线/日线参数可视化调整（Toggle开关 + 滑块）
- 回测验证（选ETF+周期，运行后显示收益/胜率/交易明细）
- 自动优化（Grid Search 256组参数，自动选最优）
- Per-ETF参数覆盖管理

**设置页：**
- 数据源切换（腾讯/Tushare）+ Token配置
- Tushare连接测试

### CLI命令

```bash
# 信号扫描
python3 a_etf_trend.py scan

# 单个ETF详情（含月线/周线/日线全指标）
python3 a_etf_trend.py signal sz159516

# 每日例行（更新数据+扫描+推送钉钉）
python3 a_etf_trend.py daily --dingtalk

# 回测
python3 a_etf_trend.py backtest sz159516 --days 1000
python3 a_etf_trend.py backtest-all

# 自动优化
python3 a_etf_trend.py optimize sz159516
python3 a_etf_trend.py optimize          # 全局

# Web Dashboard
python3 a_etf_web.py --port 8888
```

---

## 项目结构

```
├── chokepoint_trader.py        # US: 技术指标引擎 + K线采集 + 信号评分
├── fundamentals_fetcher.py     # US: SEC XBRL基本面 + Sina估值采集
├── monitor_db.py               # US: SQLite统一数据库层(13张表)
├── chokepoint_web.py           # US: aiohttp Web服务器(port 8088)
├── migrate_to_db.py            # US: 数据迁移 + 初始化
├── insider_monitor.py          # US: 内部人交易监测(SEC Form 4)
├── sec_filing_monitor.py       # US: SEC Filing事件监测
├── web/                        # US: 前端
│   ├── index.html              #   6-Tab布局
│   ├── app.js                  #   研究驱动型交互逻辑
│   └── style.css               #   暗色主题
├── a_etf_trend.py              # A股: 36个ETF核心引擎 — 多周期过滤+信号5分类+回测+优化
├── a_etf_web.py                # A股: Web Dashboard — 2步骤布局(趋势过滤→信号分类)
├── a_trend_trader.py           # A股: 数据采集层(腾讯/Tushare) + 5因子评分
├── a_sector_scanner.py         # A股: 行业轮动扫描器
├── a_stock_monitor.py          # A股: 持仓监控 + 钉钉推送
└── state/                      # 运行时数据（自动创建）
    ├── chokepoint.db           #   US投研数据库
    ├── price_history.db        #   K线数据库(US+A股共用)
    └── etf_strategy.json       #   A股策略参数(含per-ETF覆盖)
```

## 前置条件

- **Python 3.8+**（无需安装任何第三方库）
- **SQLite**（Python自带）
- 网络可访问Sina/SEC API
- Web系统需要 `aiohttp`（`pip install aiohttp`）
- 钉钉推送需配置 Webhook（可选）

## License

MIT
