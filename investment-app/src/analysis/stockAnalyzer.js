import { fetchPrice } from '../data/priceClient.js';
import { fetchFundamentals } from '../data/fundClient.js';
import { getCachedPrice, setCachedPrice, getCachedAnalysis, setCachedAnalysis } from '../data/dataCache.js';
import { callClaude, parseJsonFromClaude } from './claudeClient.js';

const SYSTEM_PROMPT = `You are a disciplined investment research team with THREE adversarial voices:

1. BULL ANALYST: Finds the strongest possible upside case. Looks for moats, growth catalysts, and margin of safety.
2. BEAR ANALYST: Challenges EVERY bull assumption with equal rigor. Finds the fatal flaws, overvaluation signals, and hidden risks. Bear text must be AT LEAST as long and specific as bull text.
3. RISK MANAGER (Final Arbiter): Synthesizes both sides and issues the definitive verdict.

MANDATORY RULES — VIOLATION MEANS ANALYSIS FAILURE:
- Bear case text must be ≥ as long as bull case text for EVERY dimension
- Valuation score > 7 requires explicit peer comparison data
- Risk score must include: regulatory risk, customer/geo concentration, macro sensitivity
- Final verdict MUST name the top 3 bear risks even if overall bullish
- Do NOT use hedging language ("it could", "may potentially") — be DIRECT and BLUNT
- If the stock is overvalued, SAY SO clearly. Do not soften bad news.
- You represent the USER's money. Protecting capital > being polite.

Output strict JSON only. No markdown, no explanation outside the JSON.`;

function buildUserPrompt(ticker, priceData, fundData) {
  let prompt = `Analyze the stock: ${ticker}\n\n`;

  if (priceData) {
    prompt += `MARKET DATA:\n`;
    prompt += `- Current Price: $${priceData.close}\n`;
    prompt += `- Today: Open $${priceData.open}, High $${priceData.high}, Low $${priceData.low}\n`;
    prompt += `- Volume: ${priceData.volume?.toLocaleString() || 'N/A'}\n`;
    prompt += `- Date: ${priceData.date}\n\n`;
  }

  if (fundData) {
    prompt += `FUNDAMENTALS:\n`;
    prompt += `- Company: ${fundData.name}\n`;
    prompt += `- Sector: ${fundData.sector} | Industry: ${fundData.industry}\n`;
    prompt += `- Market Cap: $${fundData.marketCap ? (fundData.marketCap / 1e9).toFixed(1) + 'B' : 'N/A'}\n`;
    prompt += `- P/E Ratio: ${fundData.pe || 'N/A'}\n`;
    prompt += `- PEG Ratio: ${fundData.peg || 'N/A'}\n`;
    prompt += `- Revenue Growth YoY: ${fundData.revenueGrowth ? (fundData.revenueGrowth * 100).toFixed(1) + '%' : 'N/A'}\n`;
    prompt += `- Earnings Growth YoY: ${fundData.earningsGrowth ? (fundData.earningsGrowth * 100).toFixed(1) + '%' : 'N/A'}\n`;
    prompt += `- Gross Margin: ${fundData.grossMargin ? (fundData.grossMargin * 100).toFixed(1) + '%' : 'N/A'}\n`;
    prompt += `- Operating Margin: ${fundData.operatingMargin ? (fundData.operatingMargin * 100).toFixed(1) + '%' : 'N/A'}\n`;
    prompt += `- ROE: ${fundData.returnOnEquity ? (fundData.returnOnEquity * 100).toFixed(1) + '%' : 'N/A'}\n`;
    prompt += `- Beta: ${fundData.beta || 'N/A'}\n`;
    prompt += `- 52-Week High/Low: $${fundData.high52w || 'N/A'} / $${fundData.low52w || 'N/A'}\n`;
    prompt += `- Analyst Target: $${fundData.targetPrice || 'N/A'}\n`;
    prompt += `- Analyst Ratings: Buy ${fundData.analystBuy} | Hold ${fundData.analystHold} | Sell ${fundData.analystSell}\n`;
    prompt += `- Short Interest: ${fundData.shortPercent ? (fundData.shortPercent * 100).toFixed(1) + '%' : 'N/A'}\n\n`;
  }

  if (!priceData && !fundData) {
    prompt += `NOTE: No API data available for this ticker. Use your training knowledge to provide the best analysis possible. Be explicit about data uncertainty.\n\n`;
  } else if (!fundData) {
    prompt += `NOTE: Fundamental API data unavailable. Use your training knowledge for fundamentals.\n\n`;
  }

  prompt += `OUTPUT FORMAT (strict JSON):
{
  "ticker": "${ticker}",
  "companyName": "Full Company Name",
  "sector": "Sector",
  "analysisDate": "${new Date().toISOString().split('T')[0]}",
  "dimensions": {
    "businessModel": { "score": 1-10, "weight": 0.20, "bullCase": "2-3 sentences", "bearCase": "2-3 sentences (must be >= bull length)" },
    "financialHealth": { "score": 1-10, "weight": 0.20, "bullCase": "...", "bearCase": "..." },
    "valuation": { "score": 1-10, "weight": 0.20, "bullCase": "...", "bearCase": "..." },
    "growth": { "score": 1-10, "weight": 0.20, "bullCase": "...", "bearCase": "..." },
    "competitive": { "score": 1-10, "weight": 0.15, "bullCase": "...", "bearCase": "..." },
    "risk": { "score": 1-10 (10=low risk), "weight": 0.05, "bullCase": "...", "bearCase": "..." }
  },
  "compositeScore": weighted average to 1 decimal,
  "verdict": "Strong Buy|Buy|Hold|Sell|Strong Sell",
  "verdictRationale": "3-4 sentences from Risk Manager explaining the final call",
  "timeHorizon": "Specific actionable recommendation",
  "keyRisks": ["risk1", "risk2", "risk3"],
  "keyOpportunities": ["opp1", "opp2", "opp3"],
  "priceTargets": { "bull": number, "base": number, "bear": number },
  "opportunityCost": "What better alternatives exist? Be specific with ticker names."
}`;

  return prompt;
}

export async function analyzeStock(ticker, onProgress) {
  const upperTicker = ticker.toUpperCase().replace(/\s/g, '');

  if (onProgress) onProgress('checking_cache');
  const cached = getCachedAnalysis(upperTicker);
  if (cached) {
    if (onProgress) onProgress('complete');
    return { ...cached, cached: true };
  }

  if (onProgress) onProgress('fetching_price');
  let priceData = getCachedPrice(upperTicker);
  if (!priceData) {
    priceData = await fetchPrice(upperTicker);
    if (priceData) setCachedPrice(upperTicker, priceData);
  }

  if (onProgress) onProgress('fetching_fundamentals');
  const fundData = await fetchFundamentals(upperTicker);

  if (onProgress) onProgress('analyzing');
  const userPrompt = buildUserPrompt(upperTicker, priceData, fundData);
  const rawResponse = await callClaude(SYSTEM_PROMPT, userPrompt);

  let analysis;
  try {
    analysis = parseJsonFromClaude(rawResponse);
  } catch (e) {
    const retryResponse = await callClaude(
      SYSTEM_PROMPT + '\n\nCRITICAL: Output ONLY raw JSON. No markdown code blocks. No text before or after the JSON.',
      userPrompt
    );
    analysis = parseJsonFromClaude(retryResponse);
  }

  analysis.priceData = priceData;
  analysis.fundData = fundData;
  analysis.cached = false;

  if (onProgress) onProgress('caching');
  setCachedAnalysis(upperTicker, analysis);

  if (onProgress) onProgress('complete');
  return analysis;
}
