import { analyzeStock } from './stockAnalyzer.js';
import { callClaude, parseJsonFromClaude } from './claudeClient.js';

const PORTFOLIO_SYSTEM = `You are a ruthless portfolio risk manager. Your job is to evaluate an investment portfolio with ZERO bias toward the investor's ego.

You MUST:
- Point out concentration risks bluntly
- Calculate opportunity cost honestly — name specific BETTER alternatives
- Never say "this is a good portfolio" unless the numbers truly support it
- Grade harshly: most retail portfolios deserve C or D
- Provide specific, actionable recommendations to improve the portfolio

Output strict JSON only.`;

function buildPortfolioPrompt(positions, stockAnalyses) {
  let prompt = `PORTFOLIO EVALUATION REQUEST\n\n`;
  prompt += `POSITIONS:\n`;

  for (const pos of positions) {
    const analysis = stockAnalyses[pos.ticker];
    prompt += `\n--- ${pos.ticker} ---\n`;
    prompt += `  Shares: ${pos.shares} | Cost Basis: $${pos.costBasis} | Horizon: ${pos.horizon}\n`;
    if (analysis) {
      prompt += `  Current Price: $${analysis.priceData?.close || 'N/A'}\n`;
      prompt += `  Composite Score: ${analysis.compositeScore}/10 | Verdict: ${analysis.verdict}\n`;
      prompt += `  Key Risks: ${analysis.keyRisks?.join('; ') || 'N/A'}\n`;
      prompt += `  Price Targets: Bull $${analysis.priceTargets?.bull} | Base $${analysis.priceTargets?.base} | Bear $${analysis.priceTargets?.bear}\n`;
      prompt += `  Sector: ${analysis.sector || 'N/A'}\n`;
    }
  }

  prompt += `\n\nEVALUATE THIS PORTFOLIO ON FIVE DIMENSIONS. Output JSON:
{
  "portfolioName": "string",
  "grade": "A|B|C|D|F",
  "gradeRationale": "2-3 blunt sentences explaining the grade",
  "winRate": {
    "value": 0-100,
    "breakdown": [{ "ticker": "X", "probability": 0-100, "rationale": "1 sentence" }],
    "assessment": "1-2 sentences"
  },
  "riskReward": {
    "ratio": number (>2 good, <1 bad),
    "breakdown": [{ "ticker": "X", "expectedUpside": "%", "maxDrawdown": "%", "ratio": number }],
    "assessment": "1-2 sentences"
  },
  "timeCost": {
    "annualizedReturn": "%",
    "vsSP500": "above|below|inline",
    "assessment": "1-2 sentences — is this a good use of capital time?"
  },
  "opportunityCost": {
    "betterAlternatives": [
      { "ticker": "X", "rationale": "why this is better", "expectedReturn": "%" }
    ],
    "assessment": "2-3 sentences — what is the investor MISSING?"
  },
  "concentration": {
    "sectorExposure": { "sector1": "%", "sector2": "%" },
    "topRisk": "biggest concentration issue",
    "singlePositionWarnings": ["any position >25% of portfolio"],
    "correlationWarning": "are positions too correlated?"
  },
  "recommendations": ["specific action 1", "specific action 2", "specific action 3"],
  "positionActions": [{ "ticker": "X", "action": "hold|trim|add|sell", "rationale": "1 sentence" }]
}`;

  return prompt;
}

export async function analyzePortfolio(positions, onProgress) {
  const stockAnalyses = {};
  const total = positions.length;
  let completed = 0;

  const CONCURRENCY = 3;
  for (let i = 0; i < positions.length; i += CONCURRENCY) {
    const batch = positions.slice(i, i + CONCURRENCY);
    const results = await Promise.allSettled(
      batch.map(pos => analyzeStock(pos.ticker))
    );
    for (let j = 0; j < batch.length; j++) {
      completed++;
      if (results[j].status === 'fulfilled') {
        stockAnalyses[batch[j].ticker] = results[j].value;
      }
      if (onProgress) onProgress(`analyzed_${completed}/${total}`, completed / total);
    }
  }

  if (onProgress) onProgress('evaluating_portfolio', 0.9);

  const userPrompt = buildPortfolioPrompt(positions, stockAnalyses);
  const rawResponse = await callClaude(PORTFOLIO_SYSTEM, userPrompt);

  let portfolioAnalysis;
  try {
    portfolioAnalysis = parseJsonFromClaude(rawResponse);
  } catch {
    const retryResponse = await callClaude(
      PORTFOLIO_SYSTEM + '\n\nOutput ONLY raw JSON. No markdown.',
      userPrompt
    );
    portfolioAnalysis = parseJsonFromClaude(retryResponse);
  }

  portfolioAnalysis.stockAnalyses = stockAnalyses;
  portfolioAnalysis.positions = positions;

  if (onProgress) onProgress('complete', 1);
  return portfolioAnalysis;
}
