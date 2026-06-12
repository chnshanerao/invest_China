(function() {
let dashData = [];

const d$ = (sel) => document.querySelector(sel);

document.getElementById('batch-start-btn').addEventListener('click', startBatch);
document.getElementById('filter-verdict')?.addEventListener('change', renderTable);
document.getElementById('filter-signal')?.addEventListener('change', renderTable);
document.getElementById('sort-by')?.addEventListener('change', renderTable);

loadDashboard();

async function loadDashboard() {
  try {
    const res = await fetch('/api/dashboard/summary');
    dashData = await res.json();
    const hasData = dashData.some(d => d.verdict || d.signal !== 'NO_DATA');
    if (hasData) {
      showDashboard();
      const doneCount = dashData.filter(d => d.verdict).length;
      if (doneCount >= 100) {
        d$('#batch-start-btn').textContent = '重新分析';
      } else if (doneCount > 0) {
        d$('#batch-start-btn').textContent = `继续分析 (${doneCount}/100)`;
      }
    }
  } catch (e) {}
}

function showDashboard() {
  d$('#dash-empty').classList.add('hidden');
  d$('#dash-stats').classList.remove('hidden');
  d$('#dash-filters').classList.remove('hidden');
  d$('#dash-table-wrap').classList.remove('hidden');
  renderStats();
  renderTable();
}

function renderStats() {
  const verdicts = { 'Strong Buy': 0, Buy: 0, Hold: 0, Sell: 0, 'Strong Sell': 0 };
  const signals = { STRONG_BUY: 0, BUY: 0, NEUTRAL: 0, SELL: 0, STRONG_SELL: 0 };
  let analyzed = 0;

  for (const d of dashData) {
    if (d.verdict) { verdicts[d.verdict] = (verdicts[d.verdict] || 0) + 1; analyzed++; }
    if (d.signal && d.signal !== 'NO_DATA') signals[d.signal] = (signals[d.signal] || 0) + 1;
  }

  const vColors = { 'Strong Buy': '#16a34a', Buy: '#22c55e', Hold: '#ea580c', Sell: '#dc2626', 'Strong Sell': '#991b1b' };
  const sColors = { STRONG_BUY: '#16a34a', BUY: '#22c55e', NEUTRAL: '#6b7280', SELL: '#dc2626', STRONG_SELL: '#991b1b' };

  let html = `<div class="stats-row">
    <div class="stat-group"><h4>AI 裁决分布 (${analyzed}/100)</h4><div class="stat-bars">`;
  for (const [k, v] of Object.entries(verdicts)) {
    if (v > 0) html += `<span class="stat-chip" style="background:${vColors[k]}15;color:${vColors[k]};border:1px solid ${vColors[k]}40">${k}: ${v}</span>`;
  }
  html += `</div></div><div class="stat-group"><h4>技术信号分布</h4><div class="stat-bars">`;
  for (const [k, v] of Object.entries(signals)) {
    if (v > 0) html += `<span class="stat-chip" style="background:${sColors[k]}15;color:${sColors[k]};border:1px solid ${sColors[k]}40">${signalLabel(k)}: ${v}</span>`;
  }
  html += `</div></div></div>`;
  d$('#dash-stats').innerHTML = html;
}

function renderTable() {
  const fVerdict = d$('#filter-verdict').value;
  const fSignal = d$('#filter-signal').value;
  const sortBy = d$('#sort-by').value;

  let filtered = dashData.filter(d => {
    if (fVerdict && d.verdict !== fVerdict) return false;
    if (fSignal && d.signal !== fSignal) return false;
    return true;
  });

  filtered.sort((a, b) => {
    switch (sortBy) {
      case 'score-desc': return (b.compositeScore || 0) - (a.compositeScore || 0);
      case 'score-asc': return (a.compositeScore || 0) - (b.compositeScore || 0);
      case 'rsi-asc': return (a.rsi || 50) - (b.rsi || 50);
      case 'rsi-desc': return (b.rsi || 50) - (a.rsi || 50);
      case 'ticker': return a.ticker.localeCompare(b.ticker);
      default: return 0;
    }
  });

  const tbody = d$('#dash-tbody');
  tbody.innerHTML = filtered.map(d => {
    const vColor = getVerdictColor(d.verdict);
    const sColor = getSignalColor(d.signal);
    const aColor = getActionColor(d.action);
    const rsiColor = d.rsi ? (d.rsi > 70 ? '#dc2626' : d.rsi < 30 ? '#16a34a' : '#6b7280') : '#6b7280';
    const macdDir = d.macdHist > 0 ? '▲' : d.macdHist < 0 ? '▼' : '—';
    const macdColor = d.macdHist > 0 ? '#16a34a' : d.macdHist < 0 ? '#dc2626' : '#6b7280';

    return `<tr>
      <td><strong>${d.ticker}</strong></td>
      <td>${d.close ? '$' + d.close.toFixed(2) : '—'}</td>
      <td><span class="score-badge" style="color:${vColor}">${d.compositeScore ? d.compositeScore.toFixed(1) : '—'}</span></td>
      <td><span class="verdict-chip" style="background:${vColor}18;color:${vColor}">${d.verdict || '—'}</span></td>
      <td><span class="signal-chip" style="background:${sColor}18;color:${sColor}">${signalLabel(d.signal)}</span></td>
      <td style="color:${rsiColor};font-weight:600">${d.rsi ? d.rsi.toFixed(0) : '—'}</td>
      <td style="color:${macdColor};font-weight:700">${macdDir}</td>
      <td>${d.volumeRatio ? d.volumeRatio.toFixed(1) + 'x' : '—'}</td>
      <td><span class="action-chip" style="background:${aColor}18;color:${aColor};border:1px solid ${aColor}40">${d.action || '—'}</span></td>
    </tr>`;
  }).join('');
}

async function startBatch() {
  const btn = d$('#batch-start-btn');
  btn.disabled = true;
  btn.textContent = '分析进行中...';
  d$('#batch-progress').classList.remove('hidden');
  d$('#batch-fill').style.width = '2%';
  d$('#batch-stage').textContent = '启动中...';

  try {
    const res = await fetch('/api/batch/start', { method: 'POST' });
    const { batchId } = await res.json();
    streamBatch(batchId);
  } catch (e) {
    btn.disabled = false;
    btn.textContent = '启动全量分析';
    d$('#batch-stage').textContent = '启动失败: ' + e.message;
  }
}

function streamBatch(batchId) {
  const es = new EventSource(`/api/stream/batch/${batchId}`);

  es.onmessage = (event) => {
    const data = JSON.parse(event.data);

    if (data.type === 'progress') {
      const pct = data.pct || 0;
      d$('#batch-fill').style.width = `${Math.max(pct * 100, 2)}%`;
      const phaseLabel = data.phase === 'technical' ? '技术面分析' : data.phase === 'fundamental' ? 'AI深度分析' : '完成';
      const detail = data.ticker ? ` — ${data.ticker}` : '';
      const count = data.completed != null ? ` (${data.completed}/${data.total})` : '';
      d$('#batch-stage').textContent = `${phaseLabel}${detail}${count}`;
    }

    if (data.type === 'complete') {
      es.close();
      d$('#batch-fill').style.width = '100%';
      d$('#batch-stage').textContent = '全量分析完成！';
      d$('#batch-start-btn').disabled = false;
      d$('#batch-start-btn').textContent = '重新分析';
      loadDashboard();
    }

    if (data.type === 'error') {
      es.close();
      d$('#batch-stage').textContent = '分析出错: ' + data.error;
      d$('#batch-start-btn').disabled = false;
      d$('#batch-start-btn').textContent = '重试';
    }
  };

  es.onerror = () => {
    es.close();
    d$('#batch-stage').textContent = '连接中断，刷新页面查看已完成的结果';
    d$('#batch-start-btn').disabled = false;
    d$('#batch-start-btn').textContent = '重试';
  };
}

function signalLabel(s) {
  const map = { STRONG_BUY: '强买', BUY: '买入', NEUTRAL: '中性', SELL: '卖出', STRONG_SELL: '强卖', NO_DATA: '—' };
  return map[s] || s;
}

function getVerdictColor(v) {
  const map = { 'Strong Buy': '#16a34a', Buy: '#22c55e', Hold: '#ea580c', Sell: '#dc2626', 'Strong Sell': '#991b1b' };
  return map[v] || '#6b7280';
}

function getSignalColor(s) {
  const map = { STRONG_BUY: '#16a34a', BUY: '#22c55e', NEUTRAL: '#6b7280', SELL: '#dc2626', STRONG_SELL: '#991b1b' };
  return map[s] || '#6b7280';
}

function getActionColor(a) {
  if (!a) return '#6b7280';
  if (a.includes('买入') || a.includes('加仓') || a === '立即买入') return '#16a34a';
  if (a.includes('建仓') || a.includes('试探')) return '#22c55e';
  if (a.includes('卖出') || a.includes('清仓') || a === '立即卖出') return '#dc2626';
  if (a.includes('减仓')) return '#ea580c';
  return '#6b7280';
}
})();
