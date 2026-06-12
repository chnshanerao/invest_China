import { getCached, setCache } from './dataCache.js';

const HISTORY_TTL = 4 * 60 * 60 * 1000;
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36';

export async function fetchHistory(ticker, days = 200) {
  const key = `history:${ticker.toUpperCase()}`;
  const cached = getCached(key, HISTORY_TTL);
  if (cached) return cached;

  const result = await fetchFromTwelveData(ticker, days);
  if (result && result.length > 0) {
    setCache(key, result);
    return result;
  }

  return null;
}

async function fetchFromTwelveData(ticker, days) {
  const url = `https://api.twelvedata.com/time_series?symbol=${ticker}&interval=1day&outputsize=${days}&apikey=demo`;
  try {
    const res = await fetch(url, {
      headers: { 'User-Agent': UA },
      signal: AbortSignal.timeout(15000),
    });
    if (!res.ok) return null;
    const data = await res.json();
    if (data.code || !data.values?.length) return null;

    const records = data.values.map(v => ({
      date: v.datetime,
      open: parseFloat(v.open),
      high: parseFloat(v.high),
      low: parseFloat(v.low),
      close: parseFloat(v.close),
      volume: parseInt(v.volume) || 0,
    })).reverse();

    return records;
  } catch {
    return null;
  }
}

export function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

