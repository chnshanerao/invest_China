import { randomUUID } from 'node:crypto';
import { NDX100 } from './ndx100.js';
import { fetchHistory, sleep } from '../data/historyClient.js';
import { computeIndicators, generateSignal, getCombinedAction } from '../analysis/technicalAnalyzer.js';
import { analyzeStock } from '../analysis/stockAnalyzer.js';
import { getCachedAnalysis, saveAnalysis } from '../data/dataCache.js';
import { callClaude, parseJsonFromClaude } from '../analysis/claudeClient.js';
import { getDb } from '../db/schema.js';
import { renderStockReport } from '../analysis/reportRenderer.js';
import { writeFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPORTS_DIR = join(__dirname, '..', '..', 'reports');

const TECH_PROMPT = `You are a quantitative technical analyst. Output ONLY raw JSON. No text before or after. No explanation. No markdown.

Given a stock ticker, output this exact JSON structure based on current market conditions (${new Date().toISOString().split('T')[0]}):
{"ticker":"XXX","date":"YYYY-MM-DD","close":number,"sma20":number,"sma50":number,"sma200":number,"rsi":number,"macdHist":number,"volumeSignal":"high_bullish|high_bearish|normal","overallSignal":"STRONG_BUY|BUY|NEUTRAL|SELL|STRONG_SELL","signalDetails":["reason1","reason2"]}

CRITICAL: Output raw JSON only. First character must be {.`;

export function saveTechnicalSignal(ticker, indicators, signalResult) {
  const db = getDb();
  db.prepare(`INSERT OR REPLACE INTO technical_signals
    (ticker, date, close, sma20, sma50, sma200, rsi, macd, macd_signal, macd_hist, bb_upper, bb_lower, volume_ratio, signal, signal_details, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`).run(
    ticker, indicators.date, indicators.close,
    indicators.sma20, indicators.sma50, indicators.sma200, indicators.rsi,
    indicators.macd, indicators.macdSignal, indicators.macdHist,
    indicators.bbUpper, indicators.bbLower, indicators.volumeRatio,
    signalResult.signal, JSON.stringify(signalResult.details), Date.now()
  );
}

export function getLatestSignals() {
  const db = getDb();
  return db.prepare(`SELECT * FROM technical_signals WHERE (ticker, date) IN
    (SELECT ticker, MAX(date) FROM technical_signals GROUP BY ticker) ORDER BY ticker`).all();
}

export function getSignalForTicker(ticker) {
  const db = getDb();
  return db.prepare('SELECT * FROM technical_signals WHERE ticker = ? ORDER BY date DESC LIMIT 1').get(ticker.toUpperCase());
}

export function saveBatchRun(id, total) {
  const db = getDb();
  db.prepare('INSERT INTO batch_runs (id, status, total, created_at) VALUES (?, ?, ?, ?)').run(id, 'running', total, Date.now());
}

export function updateBatchRun(id, completed, failed, status) {
  const db = getDb();
  const updates = ['completed = ?', 'failed = ?'];
  const params = [completed, failed];
  if (status === 'done' || status === 'error') {
    updates.push('status = ?', 'finished_at = ?');
    params.push(status, Date.now());
  } else {
    updates.push('status = ?');
    params.push(status);
  }
  params.push(id);
  db.prepare(`UPDATE batch_runs SET ${updates.join(', ')} WHERE id = ?`).run(...params);
}

export function getBatchStatus(id) {
  const db = getDb();
  return db.prepare('SELECT * FROM batch_runs WHERE id = ?').get(id);
}

async function runWithConcurrency(items, fn, concurrency) {
  const results = [];
  for (let i = 0; i < items.length; i += concurrency) {
    const batch = items.slice(i, i + concurrency);
    const batchResults = await Promise.allSettled(batch.map(fn));
    results.push(...batchResults);
  }
  return results;
}

export async function runBatchAnalysis(batchId, onProgress) {
  const tickers = [...NDX100];
  const total = tickers.length;
  saveBatchRun(batchId, total);

  let completed = 0;
  let failed = 0;

  // Phase 1: Technical analysis (try real data first, fallback to Claude)
  if (onProgress) onProgress({ phase: 'technical', ticker: '', completed: 0, total, pct: 0 });

  for (let i = 0; i < tickers.length; i += 3) {
    const batch = tickers.slice(i, i + 3);
    await Promise.allSettled(batch.map(async (ticker) => {
      try {
        // Try to compute from real historical data first
        const bars = await fetchHistory(ticker);
        if (bars && bars.length >= 50) {
          const indicators = computeIndicators(bars);
          if (indicators) {
            indicators.ticker = ticker;
            const signalResult = generateSignal(indicators);
            saveTechnicalSignal(ticker, indicators, signalResult);
            return;
          }
        }
        // Fallback: use Claude for technical assessment
        await claudeTechnicalAnalysis(ticker);
      } catch (e) {
        // non-fatal
      }
    }));
    await sleep(800);
    if (onProgress) onProgress({ phase: 'technical', ticker: batch[batch.length - 1], completed: Math.min(i + 3, total), total, pct: Math.min(i + 3, total) / total * 0.3 });
  }

  // Phase 2: AI fundamental analysis (skip cached)
  if (onProgress) onProgress({ phase: 'fundamental', ticker: '', completed: 0, total, pct: 0.3 });

  for (let i = 0; i < tickers.length; i += 3) {
    const batch = tickers.slice(i, i + 3);
    const results = await Promise.allSettled(batch.map(async (ticker) => {
      const cached = getCachedAnalysis(ticker);
      if (cached) return { ticker, analysis: cached, fromCache: true };

      const analysis = await analyzeStock(ticker);
      const id = randomUUID();
      const html = renderStockReport(analysis);
      writeFileSync(join(REPORTS_DIR, `${id}.html`), html);
      saveAnalysis(id, 'stock', `${analysis.ticker} - ${analysis.companyName || ''}`, analysis.verdict, analysis.compositeScore, analysis, ticker);
      return { ticker, analysis, fromCache: false };
    }));

    for (const r of results) {
      if (r.status === 'fulfilled') completed++;
      else failed++;
    }

    updateBatchRun(batchId, completed, failed, 'running');
    if (onProgress) onProgress({ phase: 'fundamental', ticker: batch[batch.length - 1], completed, failed, total, pct: 0.3 + (completed + failed) / total * 0.7 });
  }

  updateBatchRun(batchId, completed, failed, 'done');
  if (onProgress) onProgress({ phase: 'done', completed, failed, total, pct: 1 });
}

async function claudeTechnicalAnalysis(ticker) {
  let data;
  try {
    const raw = await callClaude(TECH_PROMPT, `${ticker}`);
    data = parseJsonFromClaude(raw);
  } catch {
    const raw2 = await callClaude(TECH_PROMPT + '\n\nCRITICAL: First character of response must be {. No other text.', `${ticker}`);
    data = parseJsonFromClaude(raw2);
  }

  const indicators = {
    ticker,
    date: data.date || new Date().toISOString().split('T')[0],
    close: data.close || 0,
    sma20: data.sma20 || null,
    sma50: data.sma50 || null,
    sma200: data.sma200 || null,
    rsi: data.rsi || 50,
    macd: 0,
    macdSignal: 0,
    macdHist: data.macdHist || 0,
    bbUpper: null,
    bbLower: null,
    volumeRatio: data.volumeSignal === 'high_bullish' || data.volumeSignal === 'high_bearish' ? 1.8 : 1.0,
  };

  const signalResult = {
    signal: data.overallSignal || 'NEUTRAL',
    details: data.signalDetails || [],
    action: '',
    buyCount: 0,
    sellCount: 0,
  };

  saveTechnicalSignal(ticker, indicators, signalResult);
}

export function getDashboardSummary() {
  const db = getDb();

  const signals = db.prepare(`SELECT * FROM technical_signals WHERE (ticker, date) IN
    (SELECT ticker, MAX(date) FROM technical_signals GROUP BY ticker)`).all();

  const analyses = db.prepare(`SELECT ticker, verdict, composite_score, created_at FROM analyses
    WHERE type = 'stock' AND ticker IS NOT NULL
    AND (ticker, created_at) IN (SELECT ticker, MAX(created_at) FROM analyses WHERE type='stock' AND ticker IS NOT NULL GROUP BY ticker)`).all();

  const signalMap = {};
  for (const s of signals) signalMap[s.ticker] = s;

  const analysisMap = {};
  for (const a of analyses) analysisMap[a.ticker] = a;

  const summary = NDX100.map(ticker => {
    const sig = signalMap[ticker] || null;
    const ana = analysisMap[ticker] || null;
    const techSignal = sig?.signal || 'NO_DATA';
    const aiVerdict = ana?.verdict || null;
    const action = aiVerdict ? getCombinedAction(aiVerdict, techSignal) : null;

    return {
      ticker,
      close: sig?.close || null,
      compositeScore: ana?.composite_score || null,
      verdict: aiVerdict,
      signal: techSignal,
      signalDetails: sig?.signal_details ? JSON.parse(sig.signal_details) : [],
      rsi: sig?.rsi || null,
      sma20: sig?.sma20 || null,
      sma50: sig?.sma50 || null,
      sma200: sig?.sma200 || null,
      macdHist: sig?.macd_hist || null,
      volumeRatio: sig?.volume_ratio || null,
      action,
      analysisDate: ana ? new Date(ana.created_at).toISOString().split('T')[0] : null,
    };
  });

  return summary;
}
