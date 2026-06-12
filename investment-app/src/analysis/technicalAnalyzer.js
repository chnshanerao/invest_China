export function computeIndicators(bars) {
  if (!bars || bars.length < 50) return null;

  const closes = bars.map(b => b.close);
  const volumes = bars.map(b => b.volume);
  const highs = bars.map(b => b.high);
  const lows = bars.map(b => b.low);

  const sma20 = sma(closes, 20);
  const sma50 = sma(closes, 50);
  const sma200 = closes.length >= 200 ? sma(closes, 200) : null;
  const rsi = calcRSI(closes, 14);
  const { macd, signal: macdSignal, histogram: macdHist } = calcMACD(closes);
  const { upper: bbUpper, lower: bbLower } = calcBollinger(closes, 20, 2);
  const volRatio = calcVolumeRatio(volumes, 20);

  const last = closes.length - 1;
  const prev = last - 1;

  return {
    ticker: null,
    date: bars[last].date,
    close: closes[last],
    sma20: sma20[last],
    sma50: sma50[last],
    sma200: sma200 ? sma200[last] : null,
    rsi: rsi[last],
    macd: macd[last],
    macdSignal: macdSignal[last],
    macdHist: macdHist[last],
    bbUpper: bbUpper[last],
    bbLower: bbLower[last],
    volumeRatio: volRatio[last],
    prevMacd: macd[prev],
    prevMacdSignal: macdSignal[prev],
    prevClose: closes[prev],
    high20: Math.max(...closes.slice(-20)),
    low20: Math.min(...closes.slice(-20)),
  };
}

export function generateSignal(ind) {
  if (!ind) return { signal: 'NO_DATA', details: [], action: '数据不足' };

  const buySignals = [];
  const sellSignals = [];

  // MA trend
  if (ind.close > ind.sma20 && ind.sma20 > ind.sma50) {
    buySignals.push('MA多头排列');
  } else if (ind.close < ind.sma20 && ind.sma20 < ind.sma50) {
    sellSignals.push('MA空头排列');
  }

  // RSI
  if (ind.rsi < 30) buySignals.push(`RSI超卖(${ind.rsi.toFixed(1)})`);
  else if (ind.rsi > 70) sellSignals.push(`RSI超买(${ind.rsi.toFixed(1)})`);

  // MACD crossover
  if (ind.macd > ind.macdSignal && ind.prevMacd <= ind.prevMacdSignal) {
    buySignals.push('MACD金叉');
  } else if (ind.macd < ind.macdSignal && ind.prevMacd >= ind.prevMacdSignal) {
    sellSignals.push('MACD死叉');
  }

  // Bollinger + RSI
  if (ind.close <= ind.bbLower && ind.rsi < 35) {
    buySignals.push('触布林下轨+RSI弱');
  } else if (ind.close >= ind.bbUpper && ind.rsi > 65) {
    sellSignals.push('触布林上轨+RSI强');
  }

  // Volume breakout
  if (ind.volumeRatio > 1.5 && ind.close >= ind.high20) {
    buySignals.push('放量突破');
  } else if (ind.volumeRatio > 1.5 && ind.close <= ind.low20) {
    sellSignals.push('放量下跌');
  }

  let signal;
  if (buySignals.length >= 3) signal = 'STRONG_BUY';
  else if (buySignals.length >= 2) signal = 'BUY';
  else if (sellSignals.length >= 3) signal = 'STRONG_SELL';
  else if (sellSignals.length >= 2) signal = 'SELL';
  else signal = 'NEUTRAL';

  const details = [...buySignals.map(s => `+${s}`), ...sellSignals.map(s => `-${s}`)];
  const action = getAction(signal);

  return { signal, details, action, buyCount: buySignals.length, sellCount: sellSignals.length };
}

export function getCombinedAction(aiVerdict, techSignal) {
  const matrix = {
    'Strong Buy': { STRONG_BUY: '立即买入', BUY: '买入', NEUTRAL: '等技术确认', SELL: '观望', STRONG_SELL: '不操作' },
    'Buy':        { STRONG_BUY: '买入', BUY: '分批建仓', NEUTRAL: '小仓试探', SELL: '观望', STRONG_SELL: '不操作' },
    'Hold':       { STRONG_BUY: '加仓', BUY: '持有', NEUTRAL: '持有', SELL: '减仓', STRONG_SELL: '减仓' },
    'Sell':       { STRONG_BUY: '观望', BUY: '观望', NEUTRAL: '减仓', SELL: '卖出', STRONG_SELL: '立即卖出' },
    'Strong Sell':{ STRONG_BUY: '不操作', BUY: '不操作', NEUTRAL: '卖出', SELL: '立即卖出', STRONG_SELL: '清仓' },
  };
  return (matrix[aiVerdict] || {})[techSignal] || '持有';
}

function getAction(signal) {
  const map = { STRONG_BUY: '强烈看多', BUY: '偏多', NEUTRAL: '中性', SELL: '偏空', STRONG_SELL: '强烈看空' };
  return map[signal] || '中性';
}

// --- Indicator calculations ---

function sma(data, period) {
  const result = new Array(data.length).fill(null);
  for (let i = period - 1; i < data.length; i++) {
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) sum += data[j];
    result[i] = sum / period;
  }
  return result;
}

function ema(data, period) {
  const result = new Array(data.length).fill(null);
  const k = 2 / (period + 1);
  let prev = data[0];
  result[0] = data[0];
  for (let i = 1; i < data.length; i++) {
    prev = data[i] * k + prev * (1 - k);
    result[i] = prev;
  }
  return result;
}

function calcRSI(closes, period) {
  const result = new Array(closes.length).fill(null);
  const gains = [];
  const losses = [];

  for (let i = 1; i < closes.length; i++) {
    const diff = closes[i] - closes[i - 1];
    gains.push(diff > 0 ? diff : 0);
    losses.push(diff < 0 ? -diff : 0);
  }

  if (gains.length < period) return result;

  let avgGain = gains.slice(0, period).reduce((a, b) => a + b, 0) / period;
  let avgLoss = losses.slice(0, period).reduce((a, b) => a + b, 0) / period;

  result[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);

  for (let i = period; i < gains.length; i++) {
    avgGain = (avgGain * (period - 1) + gains[i]) / period;
    avgLoss = (avgLoss * (period - 1) + losses[i]) / period;
    result[i + 1] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }
  return result;
}

function calcMACD(closes) {
  const ema12 = ema(closes, 12);
  const ema26 = ema(closes, 26);
  const macd = ema12.map((v, i) => (v != null && ema26[i] != null) ? v - ema26[i] : null);
  const validMacd = macd.filter(v => v != null);
  const signalLine = ema(validMacd, 9);

  const signal = new Array(macd.length).fill(null);
  let si = 0;
  for (let i = 0; i < macd.length; i++) {
    if (macd[i] != null) {
      signal[i] = signalLine[si] || null;
      si++;
    }
  }

  const histogram = macd.map((v, i) => (v != null && signal[i] != null) ? v - signal[i] : null);
  return { macd, signal, histogram };
}

function calcBollinger(closes, period, mult) {
  const mid = sma(closes, period);
  const upper = new Array(closes.length).fill(null);
  const lower = new Array(closes.length).fill(null);

  for (let i = period - 1; i < closes.length; i++) {
    const slice = closes.slice(i - period + 1, i + 1);
    const mean = mid[i];
    const std = Math.sqrt(slice.reduce((s, v) => s + (v - mean) ** 2, 0) / period);
    upper[i] = mean + mult * std;
    lower[i] = mean - mult * std;
  }
  return { upper, lower };
}

function calcVolumeRatio(volumes, period) {
  const avgVol = sma(volumes, period);
  return volumes.map((v, i) => avgVol[i] ? v / avgVol[i] : null);
}
