export async function fetchPrice(ticker) {
  const symbol = ticker.toLowerCase().replace(/\.us$/i, '') + '.us';
  const url = `https://stooq.com/q/l/?s=${symbol}&f=sd2t2ohlcvn&h&e=json`;
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(8000) });
    if (!res.ok) return null;
    const json = await res.json();
    const row = json?.symbols?.[0];
    if (!row || row.close === undefined) return null;
    return {
      ticker: ticker.toUpperCase(),
      name: row.name || ticker.toUpperCase(),
      open: row.open,
      high: row.high,
      low: row.low,
      close: row.close,
      volume: row.volume,
      date: row.date,
    };
  } catch {
    return null;
  }
}
