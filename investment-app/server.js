import express from 'express';
import cors from 'cors';
import { randomUUID } from 'node:crypto';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { mkdirSync, writeFileSync } from 'node:fs';

import { analyzeStock } from './src/analysis/stockAnalyzer.js';
import { analyzePortfolio } from './src/analysis/portfolioAnalyzer.js';
import { renderStockReport, renderPortfolioReport } from './src/analysis/reportRenderer.js';
import { saveAnalysis, getHistory, getAnalysisById } from './src/data/dataCache.js';
import { runBatchAnalysis, getDashboardSummary, getBatchStatus, getSignalForTicker } from './src/batch/batchRunner.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPORTS_DIR = join(__dirname, 'reports');
mkdirSync(REPORTS_DIR, { recursive: true });

const app = express();
app.use(cors());
app.use(express.json());
app.use(express.static(join(__dirname, 'public')));

const jobs = new Map();

app.post('/api/analyze/stock', (req, res) => {
  const { ticker } = req.body;
  if (!ticker) return res.status(400).json({ error: 'ticker is required' });

  const jobId = randomUUID();
  jobs.set(jobId, { status: 'running', progress: [], result: null, error: null });

  runStockAnalysis(jobId, ticker.trim());
  res.json({ jobId });
});

app.post('/api/analyze/portfolio', (req, res) => {
  const { positions } = req.body;
  if (!positions?.length) return res.status(400).json({ error: 'positions array is required' });

  const jobId = randomUUID();
  jobs.set(jobId, { status: 'running', progress: [], result: null, error: null });

  runPortfolioAnalysis(jobId, positions);
  res.json({ jobId });
});

app.get('/api/stream/:jobId', (req, res) => {
  const { jobId } = req.params;
  const job = jobs.get(jobId);
  if (!job) return res.status(404).json({ error: 'job not found' });

  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.flushHeaders();

  for (const msg of job.progress) {
    res.write(`data: ${JSON.stringify(msg)}\n\n`);
  }

  if (job.status === 'done') {
    res.write(`data: ${JSON.stringify({ type: 'complete', result: job.result })}\n\n`);
    res.end();
    return;
  }
  if (job.status === 'error') {
    res.write(`data: ${JSON.stringify({ type: 'error', error: job.error })}\n\n`);
    res.end();
    return;
  }

  const interval = setInterval(() => {
    const current = jobs.get(jobId);
    if (!current) { clearInterval(interval); res.end(); return; }

    const sent = res._sentCount || 0;
    for (let i = sent; i < current.progress.length; i++) {
      res.write(`data: ${JSON.stringify(current.progress[i])}\n\n`);
    }
    res._sentCount = current.progress.length;

    if (current.status === 'done') {
      res.write(`data: ${JSON.stringify({ type: 'complete', result: current.result })}\n\n`);
      clearInterval(interval);
      res.end();
    } else if (current.status === 'error') {
      res.write(`data: ${JSON.stringify({ type: 'error', error: current.error })}\n\n`);
      clearInterval(interval);
      res.end();
    }
  }, 500);

  req.on('close', () => clearInterval(interval));
});

app.get('/api/history', (req, res) => {
  const { type, limit } = req.query;
  const rows = getHistory(type || null, parseInt(limit) || 20);
  res.json(rows);
});

app.get('/api/report/:id', (req, res) => {
  const row = getAnalysisById(req.params.id);
  if (!row) return res.status(404).json({ error: 'not found' });
  res.json({ ...row, analysis_json: JSON.parse(row.analysis_json) });
});

async function runStockAnalysis(jobId, ticker) {
  const job = jobs.get(jobId);
  try {
    const analysis = await analyzeStock(ticker, (stage, pct) => {
      const msg = { type: 'progress', stage, pct: pct || 0 };
      job.progress.push(msg);
    });

    const html = renderStockReport(analysis);
    const id = randomUUID();
    const filename = `${id}.html`;
    writeFileSync(join(REPORTS_DIR, filename), html);
    saveAnalysis(id, 'stock', `${analysis.ticker} - ${analysis.companyName || ''}`, analysis.verdict, analysis.compositeScore, analysis);

    job.result = { id, ticker: analysis.ticker, verdict: analysis.verdict, compositeScore: analysis.compositeScore, reportUrl: `/reports/${filename}` };
    job.status = 'done';
  } catch (e) {
    job.error = e.message;
    job.status = 'error';
  }
}

async function runPortfolioAnalysis(jobId, positions) {
  const job = jobs.get(jobId);
  try {
    const analysis = await analyzePortfolio(positions, (stage, pct) => {
      const msg = { type: 'progress', stage, pct: pct || 0 };
      job.progress.push(msg);
    });

    const html = renderPortfolioReport(analysis);
    const id = randomUUID();
    const filename = `${id}.html`;
    writeFileSync(join(REPORTS_DIR, filename), html);
    saveAnalysis(id, 'portfolio', analysis.portfolioName || 'Portfolio', analysis.grade, null, analysis);

    job.result = { id, grade: analysis.grade, gradeRationale: analysis.gradeRationale, reportUrl: `/reports/${filename}` };
    job.status = 'done';
  } catch (e) {
    job.error = e.message;
    job.status = 'error';
  }
}

app.use('/reports', express.static(REPORTS_DIR));

// --- Batch & Dashboard Routes ---

app.post('/api/batch/start', (req, res) => {
  const batchId = randomUUID();
  jobs.set(batchId, { status: 'running', progress: [], result: null, error: null });

  runBatchAnalysis(batchId, (prog) => {
    const job = jobs.get(batchId);
    if (job) job.progress.push({ type: 'progress', ...prog });
  }).then(() => {
    const job = jobs.get(batchId);
    if (job) { job.status = 'done'; job.result = { batchId }; }
  }).catch((e) => {
    const job = jobs.get(batchId);
    if (job) { job.status = 'error'; job.error = e.message; }
  });

  res.json({ batchId });
});

app.get('/api/batch/:id/status', (req, res) => {
  const status = getBatchStatus(req.params.id);
  if (!status) return res.status(404).json({ error: 'batch not found' });
  res.json(status);
});

app.get('/api/stream/batch/:id', (req, res) => {
  const { id } = req.params;
  const job = jobs.get(id);
  if (!job) return res.status(404).json({ error: 'batch not found' });

  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.flushHeaders();

  for (const msg of job.progress) {
    res.write(`data: ${JSON.stringify(msg)}\n\n`);
  }

  if (job.status === 'done') {
    res.write(`data: ${JSON.stringify({ type: 'complete', result: job.result })}\n\n`);
    res.end();
    return;
  }

  const interval = setInterval(() => {
    const current = jobs.get(id);
    if (!current) { clearInterval(interval); res.end(); return; }

    const sent = res._batchSent || 0;
    for (let i = sent; i < current.progress.length; i++) {
      res.write(`data: ${JSON.stringify(current.progress[i])}\n\n`);
    }
    res._batchSent = current.progress.length;

    if (current.status === 'done') {
      res.write(`data: ${JSON.stringify({ type: 'complete', result: current.result })}\n\n`);
      clearInterval(interval);
      res.end();
    } else if (current.status === 'error') {
      res.write(`data: ${JSON.stringify({ type: 'error', error: current.error })}\n\n`);
      clearInterval(interval);
      res.end();
    }
  }, 1000);

  req.on('close', () => clearInterval(interval));
});

app.get('/api/dashboard/summary', (req, res) => {
  const summary = getDashboardSummary();
  res.json(summary);
});

app.get('/api/technical/:ticker', (req, res) => {
  const sig = getSignalForTicker(req.params.ticker);
  if (!sig) return res.status(404).json({ error: 'no technical data' });
  res.json(sig);
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`Investment Research System running on http://localhost:${PORT}`);
});
