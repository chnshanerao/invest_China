const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

// Tab switching
$$('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    $$('.tab').forEach(t => t.classList.remove('active'));
    $$('.tab-panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    $(`#tab-${btn.dataset.tab}`).classList.add('active');
    if (btn.dataset.tab === 'history') loadHistory();
  });
});

// Stock Analysis
$('#stock-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const ticker = $('#ticker-input').value.trim();
  if (!ticker) return;

  $('#stock-btn').disabled = true;
  $('#stock-progress').classList.remove('hidden');
  $('#stock-result').classList.add('hidden');
  $('#stock-fill').style.width = '5%';
  $('#stock-stage').textContent = '启动分析...';

  try {
    const res = await fetch('/api/analyze/stock', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticker })
    });
    const { jobId, error } = await res.json();
    if (error) throw new Error(error);
    streamJob(jobId, 'stock');
  } catch (err) {
    showError('stock', err.message);
    $('#stock-btn').disabled = false;
  }
});

// Portfolio
$('#add-pos-btn').addEventListener('click', addPositionRow);

$('#portfolio-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const positions = getPositions();
  if (!positions.length) return;

  $('#portfolio-btn').disabled = true;
  $('#portfolio-progress').classList.remove('hidden');
  $('#portfolio-result').classList.add('hidden');
  $('#portfolio-fill').style.width = '5%';
  $('#portfolio-stage').textContent = '启动组合评估...';

  try {
    const res = await fetch('/api/analyze/portfolio', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ positions })
    });
    const { jobId, error } = await res.json();
    if (error) throw new Error(error);
    streamJob(jobId, 'portfolio');
  } catch (err) {
    showError('portfolio', err.message);
    $('#portfolio-btn').disabled = false;
  }
});

// Remove position row
document.addEventListener('click', (e) => {
  if (e.target.classList.contains('pos-remove')) {
    const rows = $$('.pos-row');
    if (rows.length > 1) e.target.closest('.pos-row').remove();
  }
});

function addPositionRow() {
  const row = document.createElement('div');
  row.className = 'pos-row';
  row.innerHTML = `
    <input type="text" placeholder="代码 (e.g. NVDA)" class="pos-ticker">
    <input type="number" placeholder="股数" class="pos-shares" min="1">
    <input type="number" placeholder="成本价" class="pos-cost" step="0.01">
    <select class="pos-horizon">
      <option value="6months">6个月</option>
      <option value="1year" selected>1年</option>
      <option value="3years">3年</option>
      <option value="5years">5年+</option>
    </select>
    <button type="button" class="pos-remove" title="删除">&times;</button>`;
  $('#positions-list').appendChild(row);
}

function getPositions() {
  const rows = $$('.pos-row');
  const positions = [];
  rows.forEach(row => {
    const ticker = row.querySelector('.pos-ticker').value.trim();
    const shares = parseInt(row.querySelector('.pos-shares').value) || 100;
    const costBasis = parseFloat(row.querySelector('.pos-cost').value) || 0;
    const horizon = row.querySelector('.pos-horizon').value;
    if (ticker) positions.push({ ticker, shares, costBasis, horizon });
  });
  return positions;
}

const STAGE_LABELS = {
  checking_cache: '检查缓存...',
  fetching_price: '获取实时行情...',
  fetching_fundamentals: '获取基本面数据...',
  analyzing: 'AI 深度分析中（约30-60秒）...',
  caching: '缓存结果...',
  complete: '分析完成',
  evaluating_portfolio: '评估组合（最终裁决）...',
};

function streamJob(jobId, type) {
  const es = new EventSource(`/api/stream/${jobId}`);

  es.onmessage = (event) => {
    const data = JSON.parse(event.data);

    if (data.type === 'progress') {
      const stage = data.stage || '';
      const pct = data.pct || 0;
      const label = STAGE_LABELS[stage] || parseProgressStage(stage);
      $(`#${type}-fill`).style.width = `${Math.max(pct * 100, 10)}%`;
      $(`#${type}-stage`).textContent = label;
    }

    if (data.type === 'complete') {
      es.close();
      $(`#${type}-fill`).style.width = '100%';
      $(`#${type}-stage`).textContent = '完成！';
      showResult(type, data.result);
      $(`#${type === 'stock' ? 'stock-btn' : 'portfolio-btn'}`).disabled = false;
    }

    if (data.type === 'error') {
      es.close();
      showError(type, data.error);
      $(`#${type === 'stock' ? 'stock-btn' : 'portfolio-btn'}`).disabled = false;
    }
  };

  es.onerror = () => {
    es.close();
    showError(type, '连接中断，请重试');
    $(`#${type === 'stock' ? 'stock-btn' : 'portfolio-btn'}`).disabled = false;
  };
}

function parseProgressStage(stage) {
  const m = stage.match(/analyzed_(\d+)\/(\d+)/);
  if (m) return `分析标的中 (${m[1]}/${m[2]})...`;
  return stage;
}

function showResult(type, result) {
  const box = $(`#${type}-result`);
  box.classList.remove('hidden');

  if (type === 'stock') {
    const color = getVerdictColor(result.verdict);
    box.innerHTML = `
      <div class="result-card">
        <div class="rc-head">
          <span class="rc-ticker">${result.ticker}</span>
          <span class="rc-verdict" style="background:${color}">${result.verdict}</span>
        </div>
        <div class="rc-score">综合评分: <strong>${result.compositeScore}</strong>/10</div>
        <div class="rc-link"><a href="${result.reportUrl}" target="_blank">查看完整报告 &rarr;</a></div>
      </div>`;
  } else {
    const color = getGradeColor(result.grade);
    box.innerHTML = `
      <div class="result-card">
        <div class="rc-head">
          <span class="rc-ticker">组合评估</span>
          <span class="rc-verdict" style="background:${color}">${result.grade}</span>
        </div>
        <div class="rc-score">${result.gradeRationale || ''}</div>
        <div class="rc-link"><a href="${result.reportUrl}" target="_blank">查看完整报告 &rarr;</a></div>
      </div>`;
  }
}

function showError(type, msg) {
  const box = $(`#${type}-result`);
  box.classList.remove('hidden');
  box.innerHTML = `<div class="result-error">分析失败: ${msg}</div>`;
  $(`#${type}-progress`).classList.add('hidden');
}

function getVerdictColor(v) {
  const map = { 'Strong Buy': '#16a34a', 'Buy': '#22c55e', 'Hold': '#ea580c', 'Sell': '#dc2626', 'Strong Sell': '#991b1b' };
  return map[v] || '#6b7280';
}

function getGradeColor(g) {
  const map = { A: '#16a34a', B: '#22c55e', C: '#ea580c', D: '#dc2626', F: '#991b1b' };
  return map[g] || '#6b7280';
}

async function loadHistory() {
  try {
    const res = await fetch('/api/history');
    const rows = await res.json();
    const list = $('#history-list');

    if (!rows.length) {
      list.innerHTML = '<p class="empty">暂无记录</p>';
      return;
    }

    list.innerHTML = rows.map(r => {
      const date = new Date(r.created_at).toLocaleDateString('zh-CN');
      const verdict = r.verdict || '';
      const typeLabel = r.type === 'stock' ? '标的' : '组合';
      return `
        <div class="history-item" data-id="${r.id}">
          <div class="hi-left">
            <span class="hi-type">${typeLabel}</span>
            <span class="hi-name">${r.name}</span>
          </div>
          <div class="hi-right">
            <div class="hi-verdict">${verdict}</div>
            <div class="hi-date">${date}</div>
          </div>
        </div>`;
    }).join('');

    list.querySelectorAll('.history-item').forEach(item => {
      item.addEventListener('click', () => {
        const id = item.dataset.id;
        window.open(`/reports/${id}.html`, '_blank');
      });
    });
  } catch (e) {
    $('#history-list').innerHTML = '<p class="empty">加载失败</p>';
  }
}
