const EODHD_KEY = 'demo';

export async function fetchFundamentals(ticker) {
  const symbol = ticker.toUpperCase().replace(/\.US$/i, '') + '.US';
  const url = `https://eodhd.com/api/fundamentals/${symbol}?api_token=${EODHD_KEY}&fmt=json`;
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(10000) });
    if (!res.ok) return null;
    const data = await res.json();
    if (!data || !data.Highlights) return null;
    const h = data.Highlights;
    const v = data.Valuation || {};
    const ar = data.AnalystRatings || {};
    const g = data.General || {};
    return {
      ticker: ticker.toUpperCase(),
      name: g.Name || ticker,
      sector: g.GicSector || g.Sector || 'Unknown',
      industry: g.GicGroup || g.Industry || 'Unknown',
      marketCap: h.MarketCapitalization,
      pe: h.PERatio,
      peg: h.PEGRatio,
      eps: h.EarningsShare,
      revenueGrowth: h.QuarterlyRevenueGrowthYOY,
      earningsGrowth: h.QuarterlyEarningsGrowthYOY,
      grossMargin: h.ProfitMargin,
      operatingMargin: h.OperatingMarginTTM,
      returnOnEquity: h.ReturnOnEquityTTM,
      beta: h.Beta,
      high52w: h.YearHigh,
      low52w: h.YearLow,
      targetPrice: h.WallStreetTargetPrice,
      analystRating: ar.Rating,
      analystBuy: ar.Buy || 0,
      analystHold: ar.Hold || 0,
      analystSell: ar.Sell || 0,
      shortPercent: h.ShortPercentFloat,
      dividendYield: h.DividendYield,
    };
  } catch {
    return null;
  }
}
