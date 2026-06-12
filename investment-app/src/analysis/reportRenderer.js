export function renderStockReport(analysis) {
  const a = analysis;
  const dims = a.dimensions || {};
  const verdictColor = getVerdictColor(a.verdict);
  const price = a.priceData?.close;
  const fund = a.fundData;

  return `<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${a.ticker} 投研分析 | ${a.verdict}</title>
<style>${CSS}</style></head><body>
<header class="hdr">
  <div class="ctr">
    <div class="hdr-top">
      <div>
        <h1>${a.ticker} <span class="cname">${a.companyName || ''}</span></h1>
        <div class="sub">${a.sector || ''} &mdash; 对抗式深度研判</div>
      </div>
      <div class="tbox">
        <div class="tsym">分析日期 ${a.analysisDate || new Date().toISOString().split('T')[0]}</div>
        ${price ? `<div class="tprice">$${price}</div>` : ''}
        ${fund?.high52w ? `<div class="tchg">52W: $${fund.low52w} - $${fund.high52w}</div>` : ''}
      </div>
    </div>
  </div>
</header>

<div class="verdict-bar" style="background:${verdictColor}15;border-bottom:3px solid ${verdictColor}">
  <div class="ctr vb-inner">
    <div class="vb-badge" style="background:${verdictColor}">${a.verdict || 'N/A'}</div>
    <div class="vb-score">综合评分 <strong>${a.compositeScore || 'N/A'}</strong>/10</div>
    <div class="vb-horizon">${a.timeHorizon || ''}</div>
  </div>
</div>

<main class="ctr">
${renderKeyRisks(a.keyRisks)}
${renderPriceTargets(a.priceTargets, price)}
${renderDimensions(dims)}
${renderFinancials(fund)}
${renderVerdictRationale(a)}
${renderOpportunityCost(a.opportunityCost)}
<div class="disclaimer">免责声明：本报告由 AI 系统自动生成，仅供研究参考，不构成投资建议。投资有风险，决策需谨慎。</div>
</main>
<footer class="ftr">Investment Research System &copy; 2026 | Adversarial Analysis Framework</footer>
</body></html>`;
}

export function renderPortfolioReport(analysis) {
  const a = analysis;
  const gradeColor = getGradeColor(a.grade);

  return `<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>投资组合评估 | Grade ${a.grade}</title>
<style>${CSS}</style></head><body>
<header class="hdr">
  <div class="ctr">
    <h1>投资组合评估</h1>
    <div class="sub">${a.portfolioName || '用户组合'} &mdash; 五维犀利评估</div>
  </div>
</header>

<div class="verdict-bar" style="background:${gradeColor}15;border-bottom:3px solid ${gradeColor}">
  <div class="ctr vb-inner">
    <div class="vb-badge" style="background:${gradeColor};font-size:28px">${a.grade}</div>
    <div class="vb-score">${a.gradeRationale || ''}</div>
  </div>
</div>

<main class="ctr">
${renderPortfolioMetrics(a)}
${renderPositionActions(a.positionActions)}
${renderPortfolioOpportunity(a.opportunityCost)}
${renderConcentration(a.concentration)}
${renderRecommendations(a.recommendations)}
<div class="disclaimer">免责声明：本报告由 AI 系统自动生成，仅供研究参考，不构成投资建议。</div>
</main>
<footer class="ftr">Investment Research System &copy; 2026 | Portfolio Evaluation Framework</footer>
</body></html>`;
}

function getVerdictColor(v) {
  const map = { 'Strong Buy': '#16a34a', 'Buy': '#22c55e', 'Hold': '#ea580c', 'Sell': '#dc2626', 'Strong Sell': '#991b1b' };
  return map[v] || '#6b7280';
}

function getGradeColor(g) {
  const map = { A: '#16a34a', B: '#22c55e', C: '#ea580c', D: '#dc2626', F: '#991b1b' };
  return map[g] || '#6b7280';
}

function renderKeyRisks(risks) {
  if (!risks?.length) return '';
  return `<section class="sec"><div class="stitle danger-title">&#9888; 核心风险（优先阅读）</div>
    <div class="risk-grid">${risks.map(r => `<div class="risk-card">${r}</div>`).join('')}</div></section>`;
}

function renderPriceTargets(targets, currentPrice) {
  if (!targets) return '';
  const { bull, base, bear } = targets;
  const max = Math.max(bull, currentPrice || bull);
  const min = Math.min(bear, currentPrice || bear);
  const range = max - min || 1;
  const bearPct = ((bear - min) / range * 100).toFixed(1);
  const basePct = ((base - min) / range * 100).toFixed(1);
  const bullPct = ((bull - min) / range * 100).toFixed(1);
  const curPct = currentPrice ? ((currentPrice - min) / range * 100).toFixed(1) : null;

  return `<section class="sec"><div class="stitle">&#127919; 目标价区间</div>
    <div class="pt-bar">
      <div class="pt-track">
        <div class="pt-mark bear" style="left:${bearPct}%"><span>$${bear}</span><em>悲观</em></div>
        <div class="pt-mark base" style="left:${basePct}%"><span>$${base}</span><em>基准</em></div>
        <div class="pt-mark bull" style="left:${bullPct}%"><span>$${bull}</span><em>乐观</em></div>
        ${curPct !== null ? `<div class="pt-cur" style="left:${curPct}%"><span>$${currentPrice}</span><em>当前</em></div>` : ''}
      </div>
    </div></section>`;
}

function renderDimensions(dims) {
  const order = ['businessModel', 'financialHealth', 'valuation', 'growth', 'competitive', 'risk'];
  const labels = { businessModel: '商业模式/壁垒', financialHealth: '财务健康', valuation: '估值', growth: '成长性', competitive: '竞争地位', risk: '风险（10=低风险）' };

  let html = `<section class="sec"><div class="stitle">&#128202; 六维评分 — 牛熊对抗</div><div class="dim-grid">`;
  for (const key of order) {
    const d = dims[key];
    if (!d) continue;
    const pct = (d.score / 10 * 100).toFixed(0);
    const scoreColor = d.score >= 7 ? '#16a34a' : d.score >= 5 ? '#ea580c' : '#dc2626';
    html += `<div class="dim-card">
      <div class="dim-hdr"><span class="dim-name">${labels[key] || key}</span><span class="dim-score" style="color:${scoreColor}">${d.score}/10</span></div>
      <div class="dim-bar"><div class="dim-fill" style="width:${pct}%;background:${scoreColor}"></div></div>
      <div class="dim-cases">
        <div class="case bull-case"><strong>&#128994; 牛方：</strong>${d.bullCase}</div>
        <div class="case bear-case"><strong>&#128308; 熊方：</strong>${d.bearCase}</div>
      </div>
    </div>`;
  }
  html += `</div></section>`;
  return html;
}

function renderFinancials(fund) {
  if (!fund) return '';
  return `<section class="sec"><div class="stitle">&#128200; 关键财务指标</div>
    <div class="kpi-grid">
      ${kpi('P/E', fund.pe)} ${kpi('PEG', fund.peg)} ${kpi('毛利率', fund.grossMargin ? (fund.grossMargin * 100).toFixed(1) + '%' : 'N/A')}
      ${kpi('营收增速', fund.revenueGrowth ? (fund.revenueGrowth * 100).toFixed(1) + '%' : 'N/A')}
      ${kpi('Beta', fund.beta)} ${kpi('分析师目标', fund.targetPrice ? '$' + fund.targetPrice : 'N/A')}
    </div></section>`;
}

function kpi(label, value) {
  return `<div class="kpi"><div class="kpi-v">${value ?? 'N/A'}</div><div class="kpi-l">${label}</div></div>`;
}

function renderVerdictRationale(a) {
  return `<section class="sec"><div class="stitle">&#128161; 风险官裁决</div>
    <div class="rationale">${a.verdictRationale || ''}</div>
    ${a.keyOpportunities ? `<div class="opps"><strong>核心机会：</strong>${a.keyOpportunities.join(' | ')}</div>` : ''}
  </section>`;
}

function renderOpportunityCost(oc) {
  if (!oc) return '';
  return `<section class="sec"><div class="stitle">&#128260; 机会成本提示</div>
    <div class="oc-box">${oc}</div></section>`;
}

function renderPortfolioMetrics(a) {
  const wr = a.winRate || {};
  const rr = a.riskReward || {};
  const tc = a.timeCost || {};
  return `<section class="sec"><div class="stitle">&#128202; 核心指标</div>
    <div class="kpi-grid">
      ${kpi('胜率', wr.value != null ? wr.value + '%' : 'N/A')}
      ${kpi('赔率 (R/R)', rr.ratio != null ? rr.ratio.toFixed(2) + 'x' : 'N/A')}
      ${kpi('年化预期', tc.annualizedReturn || 'N/A')}
      ${kpi('vs 标普500', tc.vsSP500 || 'N/A')}
    </div>
    ${wr.assessment ? `<p class="assess">${wr.assessment}</p>` : ''}
    ${rr.assessment ? `<p class="assess">${rr.assessment}</p>` : ''}
    ${tc.assessment ? `<p class="assess">${tc.assessment}</p>` : ''}
  </section>`;
}

function renderPositionActions(actions) {
  if (!actions?.length) return '';
  let html = `<section class="sec"><div class="stitle">&#9876; 逐笔持仓建议</div><table><thead><tr><th>标的</th><th>操作</th><th>理由</th></tr></thead><tbody>`;
  for (const a of actions) {
    const color = a.action === 'sell' ? '#dc2626' : a.action === 'add' ? '#16a34a' : a.action === 'trim' ? '#ea580c' : '#6b7280';
    html += `<tr><td><strong>${a.ticker}</strong></td><td style="color:${color};font-weight:700">${a.action.toUpperCase()}</td><td>${a.rationale}</td></tr>`;
  }
  html += `</tbody></table></section>`;
  return html;
}

function renderPortfolioOpportunity(oc) {
  if (!oc) return '';
  let html = `<section class="sec"><div class="stitle danger-title">&#128260; 机会成本 — 你正在错过什么？</div>`;
  if (oc.betterAlternatives?.length) {
    html += `<div class="alt-grid">`;
    for (const alt of oc.betterAlternatives) {
      html += `<div class="alt-card"><strong>${alt.ticker}</strong><p>${alt.rationale}</p><span class="alt-ret">预期: ${alt.expectedReturn}</span></div>`;
    }
    html += `</div>`;
  }
  if (oc.assessment) html += `<p class="assess">${oc.assessment}</p>`;
  html += `</section>`;
  return html;
}

function renderConcentration(c) {
  if (!c) return '';
  let html = `<section class="sec"><div class="stitle">&#128678; 集中度分析</div>`;
  if (c.sectorExposure) {
    html += `<div class="sector-bars">`;
    for (const [sector, pct] of Object.entries(c.sectorExposure)) {
      html += `<div class="sb-row"><span class="sb-lbl">${sector}</span><div class="sb-track"><div class="sb-fill" style="width:${pct}">${pct}</div></div></div>`;
    }
    html += `</div>`;
  }
  if (c.topRisk) html += `<div class="conc-warn">${c.topRisk}</div>`;
  if (c.correlationWarning) html += `<p class="assess">${c.correlationWarning}</p>`;
  html += `</section>`;
  return html;
}

function renderRecommendations(recs) {
  if (!recs?.length) return '';
  return `<section class="sec"><div class="stitle">&#9989; 优化建议</div>
    <ul class="rec-list">${recs.map(r => `<li>${r}</li>`).join('')}</ul></section>`;
}

const CSS = `
:root{--dk:#0f172a;--ac:#2563eb;--gn:#16a34a;--rd:#dc2626;--og:#ea580c;--bg:#f1f5f9;--card:#fff;--bdr:#e2e8f0;--t1:#1e293b;--t2:#64748b}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--t1);line-height:1.75}
.ctr{max-width:1100px;margin:0 auto;padding:0 24px}
.hdr{background:linear-gradient(135deg,#0f172a,#1e3a5f,#1e40af);color:#fff;padding:40px 0 32px}
.hdr h1{font-size:30px;font-weight:800} .hdr .cname{font-weight:400;opacity:.7;font-size:20px}
.hdr .sub{font-size:14px;opacity:.65;margin-top:4px}
.hdr-top{display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:16px}
.tbox{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);border-radius:12px;padding:14px 22px;text-align:right}
.tsym{font-size:12px;opacity:.5} .tprice{font-size:32px;font-weight:800} .tchg{font-size:12px;opacity:.7}
.verdict-bar{padding:16px 0}
.vb-inner{display:flex;align-items:center;gap:16px;flex-wrap:wrap}
.vb-badge{color:#fff;padding:8px 20px;border-radius:8px;font-weight:700;font-size:18px}
.vb-score{font-size:15px;color:var(--t2)} .vb-score strong{color:var(--t1);font-size:20px}
.vb-horizon{font-size:13px;color:var(--t2);margin-left:auto;max-width:350px}
main{padding:28px 0 50px}
.sec{background:var(--card);border-radius:12px;border:1px solid var(--bdr);padding:24px 28px;margin-bottom:20px;box-shadow:0 1px 3px rgba(0,0,0,.03)}
.stitle{font-size:18px;font-weight:700;color:var(--ac);margin-bottom:14px;padding-bottom:8px;border-bottom:2px solid var(--ac)}
.danger-title{color:var(--rd);border-color:var(--rd)}
.risk-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px}
.risk-card{background:#fef2f2;border:1px solid #fecaca;border-left:4px solid var(--rd);border-radius:8px;padding:12px 16px;font-size:13px;font-weight:500}
.pt-bar{padding:30px 0 20px} .pt-track{position:relative;height:8px;background:linear-gradient(90deg,#dc2626,#f59e0b,#16a34a);border-radius:4px;margin:0 20px}
.pt-mark,.pt-cur{position:absolute;top:-24px;transform:translateX(-50%);text-align:center;font-size:11px}
.pt-mark span,.pt-cur span{display:block;font-weight:700;font-size:13px}
.pt-mark em,.pt-cur em{font-style:normal;color:var(--t2)}
.pt-mark.bear{color:var(--rd)} .pt-mark.base{color:var(--og)} .pt-mark.bull{color:var(--gn)}
.pt-cur{top:14px;color:var(--ac);font-weight:700}
.dim-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:700px){.dim-grid{grid-template-columns:1fr}}
.dim-card{border:1px solid var(--bdr);border-radius:10px;padding:16px;background:#fafbff}
.dim-hdr{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.dim-name{font-weight:600;font-size:14px} .dim-score{font-weight:800;font-size:18px}
.dim-bar{height:6px;background:#e2e8f0;border-radius:3px;overflow:hidden;margin-bottom:10px}
.dim-fill{height:100%;border-radius:3px;transition:width .3s}
.dim-cases{font-size:12px;line-height:1.6}
.case{padding:8px 12px;border-radius:6px;margin-top:6px}
.bull-case{background:#f0fdf4;border:1px solid #bbf7d0} .bear-case{background:#fef2f2;border:1px solid #fecaca}
.kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px}
.kpi{background:#f8fafc;border:1px solid var(--bdr);border-radius:8px;padding:14px;text-align:center}
.kpi-v{font-size:20px;font-weight:800;color:var(--ac)} .kpi-l{font-size:11px;color:var(--t2);margin-top:2px}
.rationale{font-size:14px;padding:16px;background:#f8fafc;border-radius:8px;border-left:4px solid var(--ac)}
.opps{margin-top:12px;font-size:13px;color:var(--gn)}
.oc-box{font-size:14px;padding:14px;background:#fffbeb;border:1px solid #fde68a;border-radius:8px}
.assess{font-size:13px;color:var(--t2);margin-top:10px;padding:10px;background:#f8fafc;border-radius:6px}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:10px}
thead th{background:#f1f5f9;padding:10px 12px;text-align:left;font-weight:600;color:var(--t2);border-bottom:2px solid var(--bdr)}
tbody td{padding:9px 12px;border-bottom:1px solid #f1f5f9}
.alt-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-top:10px}
.alt-card{background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:14px}
.alt-card strong{color:var(--gn);font-size:15px} .alt-card p{font-size:12px;margin-top:4px} .alt-ret{font-size:11px;color:var(--gn);font-weight:600}
.sector-bars{margin-top:8px} .sb-row{display:flex;align-items:center;gap:10px;margin-bottom:6px}
.sb-lbl{width:100px;font-size:12px;text-align:right;flex-shrink:0}
.sb-track{flex:1;height:22px;background:#f1f5f9;border-radius:4px;overflow:hidden}
.sb-fill{height:100%;background:linear-gradient(90deg,var(--ac),#60a5fa);border-radius:4px;display:flex;align-items:center;padding-left:8px;font-size:10px;color:#fff;font-weight:700}
.conc-warn{margin-top:10px;padding:12px;background:#fef2f2;border:1px solid #fecaca;border-radius:8px;font-size:13px;color:var(--rd);font-weight:500}
.rec-list{padding-left:20px;font-size:14px} .rec-list li{margin-bottom:8px}
.disclaimer{background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:12px 16px;font-size:11px;color:#92400e;margin-top:24px}
.ftr{text-align:center;padding:20px 0;font-size:11px;color:var(--t2);border-top:1px solid var(--bdr);margin-top:30px}
`;
