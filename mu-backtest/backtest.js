const fs = require('fs');

// =====================================================
// MU Trailing Stop Backtest Engine
// Tests multiple trailing stop percentages on MU data
// =====================================================

function loadData(filepath) {
  const raw = fs.readFileSync(filepath, 'utf8');
  const lines = raw.trim().split('\n');
  const header = lines[0].split(',');
  const data = [];

  for (let i = 1; i < lines.length; i++) {
    const cols = lines[i].split(',');
    if (cols.length < 5) continue;
    data.push({
      date: cols[0],
      open: parseFloat(cols[1]),
      high: parseFloat(cols[2]),
      low: parseFloat(cols[3]),
      close: parseFloat(cols[4]),
      volume: parseInt(cols[5]) || 0
    });
  }

  // Ensure chronological order
  data.sort((a, b) => a.date.localeCompare(b.date));
  return data;
}

function computeATR(data, period = 20) {
  const atr = new Array(data.length).fill(0);
  for (let i = 1; i < data.length; i++) {
    const tr = Math.max(
      data[i].high - data[i].low,
      Math.abs(data[i].high - data[i - 1].close),
      Math.abs(data[i].low - data[i - 1].close)
    );
    if (i < period) {
      atr[i] = tr;
    } else if (i === period) {
      let sum = 0;
      for (let j = 1; j <= period; j++) {
        const tr2 = Math.max(
          data[j].high - data[j].low,
          Math.abs(data[j].high - data[j - 1].close),
          Math.abs(data[j].low - data[j - 1].close)
        );
        sum += tr2;
      }
      atr[i] = sum / period;
    } else {
      atr[i] = (atr[i - 1] * (period - 1) + tr) / period;
    }
  }
  return atr;
}

// =====================================================
// Strategy 1: Simple Trailing Stop (fixed percentage)
// =====================================================
function simpleTrailingStop(data, stopPct) {
  const trades = [];
  let inPosition = false;
  let entryPrice = 0;
  let entryDate = '';
  let peakPrice = 0;
  let stopPrice = 0;

  // Start in position from day 1
  inPosition = true;
  entryPrice = data[0].close;
  entryDate = data[0].date;
  peakPrice = data[0].close;
  stopPrice = peakPrice * (1 - stopPct / 100);

  const equity = [{ date: data[0].date, value: 100 }]; // normalized to 100
  let shares = 100 / entryPrice;

  for (let i = 1; i < data.length; i++) {
    const d = data[i];

    if (inPosition) {
      // Check if low hit stop
      if (d.low <= stopPrice) {
        // Stopped out - sell at stop price (or open if gap down)
        const sellPrice = Math.min(d.open, stopPrice);
        const pnl = ((sellPrice - entryPrice) / entryPrice) * 100;
        trades.push({
          type: 'STOP_OUT',
          entryDate,
          exitDate: d.date,
          entryPrice,
          exitPrice: sellPrice,
          peakPrice,
          drawdownFromPeak: ((sellPrice - peakPrice) / peakPrice) * 100,
          pnl,
          holdDays: Math.round((new Date(d.date) - new Date(entryDate)) / 86400000)
        });

        const cash = shares * sellPrice;
        inPosition = false;

        // Re-enter after 5 trading days (cooling period)
        const reEntryIdx = Math.min(i + 5, data.length - 1);
        if (reEntryIdx < data.length - 1) {
          // Fill equity for gap days
          for (let j = i; j <= reEntryIdx; j++) {
            equity.push({ date: data[j].date, value: cash / (100 / entryPrice) * (100 / data[0].close) });
          }

          entryPrice = data[reEntryIdx].close;
          entryDate = data[reEntryIdx].date;
          peakPrice = entryPrice;
          stopPrice = peakPrice * (1 - stopPct / 100);
          shares = cash / entryPrice;
          inPosition = true;
          i = reEntryIdx;
        }
        continue;
      }

      // Update peak and trailing stop
      if (d.high > peakPrice) {
        peakPrice = d.high;
        stopPrice = peakPrice * (1 - stopPct / 100);
      }

      equity.push({ date: d.date, value: shares * d.close / (100 / data[0].close) * (100 / data[0].close) });
    }
  }

  // Final position value
  const finalValue = inPosition ? shares * data[data.length - 1].close : shares * trades[trades.length - 1]?.exitPrice || 100;

  return {
    stopPct,
    trades,
    totalTrades: trades.length,
    finalValue: (finalValue / (100 / data[0].close)) * (100 / data[0].close),
    buyAndHoldReturn: ((data[data.length - 1].close - data[0].close) / data[0].close) * 100,
    equity
  };
}

// =====================================================
// Strategy 2: Tiered Trailing Stop (our recommended approach)
// =====================================================
function tieredTrailingStop(data, tier1Pct, tier2Pct, tier3Pct) {
  let position = 1.0; // 100% position
  let cash = 0;
  let entryPrice = data[0].close;
  let peakPrice = data[0].close;
  const initialInvestment = 100000;
  let shares = initialInvestment / entryPrice;
  let tier1Triggered = false;
  let tier2Triggered = false;
  let tier3Triggered = false;
  const events = [];
  const equityCurve = [];

  for (let i = 0; i < data.length; i++) {
    const d = data[i];

    // Update peak
    if (d.high > peakPrice && position > 0) {
      peakPrice = d.high;
      tier1Triggered = false;
      tier2Triggered = false;
      tier3Triggered = false;
    }

    const drawdownPct = ((d.low - peakPrice) / peakPrice) * 100;
    const closeDrawdown = ((d.close - peakPrice) / peakPrice) * 100;

    // Check tiers
    if (!tier3Triggered && drawdownPct <= -tier3Pct && position > 0) {
      const sellPrice = peakPrice * (1 - tier3Pct / 100);
      const sellShares = shares;
      cash += sellShares * sellPrice;
      shares = 0;
      position = 0;
      tier3Triggered = true;
      events.push({
        date: d.date, tier: 3, action: 'CLEAR ALL',
        price: sellPrice, drawdown: -tier3Pct,
        peakPrice, remaining: '0%'
      });
    } else if (!tier2Triggered && drawdownPct <= -tier2Pct && position > 0.34) {
      const sellPrice = peakPrice * (1 - tier2Pct / 100);
      const sellShares = shares * (1 / 3);
      cash += sellShares * sellPrice;
      shares -= sellShares;
      position = position * (2 / 3);
      tier2Triggered = true;
      events.push({
        date: d.date, tier: 2, action: 'SELL 1/3',
        price: sellPrice, drawdown: -tier2Pct,
        peakPrice, remaining: `${(position * 100).toFixed(0)}%`
      });
    } else if (!tier1Triggered && drawdownPct <= -tier1Pct && position > 0.67) {
      const sellPrice = peakPrice * (1 - tier1Pct / 100);
      const sellShares = shares * (1 / 3);
      cash += sellShares * sellPrice;
      shares -= sellShares;
      position = position * (2 / 3);
      tier1Triggered = true;
      events.push({
        date: d.date, tier: 1, action: 'SELL 1/3',
        price: sellPrice, drawdown: -tier1Pct,
        peakPrice, remaining: `${(position * 100).toFixed(0)}%`
      });
    }

    // Re-enter if all sold and price recovers above peak * 0.95
    if (position === 0 && cash > 0 && d.close > peakPrice * 0.95) {
      shares = cash / d.close;
      cash = 0;
      position = 1.0;
      entryPrice = d.close;
      peakPrice = d.close;
      tier1Triggered = false;
      tier2Triggered = false;
      tier3Triggered = false;
      events.push({
        date: d.date, tier: 0, action: 'RE-ENTER',
        price: d.close, drawdown: 0,
        peakPrice: d.close, remaining: '100%'
      });
    }

    const totalValue = shares * d.close + cash;
    equityCurve.push({ date: d.date, value: totalValue, position });
  }

  const finalValue = shares * data[data.length - 1].close + cash;

  return {
    tiers: [tier1Pct, tier2Pct, tier3Pct],
    events,
    finalValue,
    totalReturn: ((finalValue - initialInvestment) / initialInvestment) * 100,
    buyAndHoldReturn: ((data[data.length - 1].close - data[0].close) / data[0].close) * 100,
    maxDrawdown: computeMaxDrawdown(equityCurve),
    equityCurve
  };
}

function computeMaxDrawdown(equity) {
  let peak = 0;
  let maxDD = 0;
  for (const e of equity) {
    if (e.value > peak) peak = e.value;
    const dd = (e.value - peak) / peak;
    if (dd < maxDD) maxDD = dd;
  }
  return maxDD * 100;
}

// =====================================================
// Run analysis
// =====================================================
function analyzeDrawdowns(data) {
  let peak = data[0].close;
  let peakDate = data[0].date;
  const drawdowns = [];
  let inDrawdown = false;
  let ddStart = '';
  let ddPeak = 0;
  let ddLow = Infinity;
  let ddLowDate = '';

  for (const d of data) {
    if (d.close > peak) {
      if (inDrawdown && ddLow < ddPeak) {
        const ddPct = ((ddLow - ddPeak) / ddPeak) * 100;
        if (ddPct < -5) { // Only record >5% drawdowns
          drawdowns.push({
            peakDate, peakPrice: ddPeak,
            lowDate: ddLowDate, lowPrice: ddLow,
            drawdownPct: ddPct,
            recoveryDate: d.date
          });
        }
      }
      peak = d.close;
      peakDate = d.date;
      inDrawdown = false;
      ddLow = Infinity;
    } else {
      inDrawdown = true;
      ddPeak = peak;
      if (d.low < ddLow) {
        ddLow = d.low;
        ddLowDate = d.date;
      }
    }
  }

  // If still in drawdown at end
  if (inDrawdown && ddLow < ddPeak) {
    const ddPct = ((ddLow - ddPeak) / ddPeak) * 100;
    if (ddPct < -5) {
      drawdowns.push({
        peakDate, peakPrice: ddPeak,
        lowDate: ddLowDate, lowPrice: ddLow,
        drawdownPct: ddPct,
        recoveryDate: 'N/A'
      });
    }
  }

  return drawdowns;
}

// =====================================================
// Main execution
// =====================================================
function main() {
  const data = loadData('/home/admin/workspace/mu-backtest/mu_daily.csv');
  console.log(`Loaded ${data.length} trading days`);
  console.log(`Range: ${data[0].date} to ${data[data.length - 1].date}`);
  console.log(`Price: $${data[0].close.toFixed(2)} → $${data[data.length - 1].close.toFixed(2)}`);
  console.log(`Buy & Hold: +${(((data[data.length - 1].close - data[0].close) / data[0].close) * 100).toFixed(1)}%\n`);

  // ATR analysis
  const atr = computeATR(data);
  const recentATR = atr[atr.length - 1];
  const recentATRPct = (recentATR / data[data.length - 1].close) * 100;
  console.log(`Current ATR(20): $${recentATR.toFixed(2)} (${recentATRPct.toFixed(2)}%)\n`);

  // Historical drawdown analysis
  console.log('=== HISTORICAL DRAWDOWNS > 5% ===');
  const drawdowns = analyzeDrawdowns(data);
  for (const dd of drawdowns) {
    console.log(`  ${dd.peakDate} $${dd.peakPrice.toFixed(0)} → ${dd.lowDate} $${dd.lowPrice.toFixed(0)} (${dd.drawdownPct.toFixed(1)}%) → Recovered: ${dd.recoveryDate}`);
  }

  // Test simple trailing stops
  console.log('\n=== SIMPLE TRAILING STOP BACKTEST ===');
  console.log('Stop% | #Trades | Final($100K) | B&H($100K) | vs B&H | MaxDD');
  console.log('------|---------|--------------|------------|--------|------');

  const stopResults = [];
  for (const pct of [8, 10, 12, 15, 18, 20, 22, 25, 28, 30, 35]) {
    const result = simpleTrailingStop(data, pct);
    const finalVal = result.finalValue;
    const bhVal = 100 + result.buyAndHoldReturn;

    stopResults.push({
      pct,
      trades: result.totalTrades,
      finalValue: finalVal,
      bhReturn: result.buyAndHoldReturn,
      vsHold: finalVal - bhVal
    });

    console.log(
      `  ${String(pct).padStart(3)}%  |   ${String(result.totalTrades).padStart(3)}   | $${(finalVal / 100 * 100000).toFixed(0).padStart(10)} | $${((100 + result.buyAndHoldReturn) / 100 * 100000).toFixed(0).padStart(10)} | ${(finalVal - bhVal > 0 ? '+' : '') + (finalVal - bhVal).toFixed(1).padStart(5)}% | ...`
    );
  }

  // Test tiered approach
  console.log('\n=== TIERED TRAILING STOP BACKTEST ===');
  const tieredConfigs = [
    [10, 18, 25],
    [12, 20, 28],
    [15, 22, 30],  // Our recommendation
    [15, 25, 35],
    [18, 25, 32],
    [20, 28, 35],
  ];

  const tieredResults = [];
  for (const config of tieredConfigs) {
    const result = tieredTrailingStop(data, ...config);
    tieredResults.push(result);
    console.log(`\nTiers: -${config[0]}% / -${config[1]}% / -${config[2]}%`);
    console.log(`  Final: $${result.finalValue.toFixed(0)} (${result.totalReturn > 0 ? '+' : ''}${result.totalReturn.toFixed(1)}%)`);
    console.log(`  B&H:   $${(100000 * (1 + result.buyAndHoldReturn / 100)).toFixed(0)} (${result.buyAndHoldReturn > 0 ? '+' : ''}${result.buyAndHoldReturn.toFixed(1)}%)`);
    console.log(`  MaxDD: ${result.maxDrawdown.toFixed(1)}%`);
    console.log(`  Events: ${result.events.length}`);
    for (const e of result.events) {
      console.log(`    ${e.date}: Tier${e.tier} ${e.action} @ $${e.price.toFixed(0)} (peak=$${e.peakPrice.toFixed(0)}, ${e.drawdown.toFixed(1)}%) → ${e.remaining}`);
    }
  }

  // Save results for HTML visualization
  const output = {
    data: data.map(d => ({ date: d.date, close: d.close, high: d.high, low: d.low })),
    atr: atr.map((v, i) => ({ date: data[i].date, atr: v, atrPct: data[i].close > 0 ? (v / data[i].close * 100) : 0 })),
    drawdowns,
    stopResults,
    tieredResults: tieredResults.map(r => ({
      tiers: r.tiers,
      events: r.events,
      totalReturn: r.totalReturn,
      buyAndHoldReturn: r.buyAndHoldReturn,
      maxDrawdown: r.maxDrawdown,
      finalValue: r.finalValue,
      equityCurve: r.equityCurve.filter((_, i) => i % 5 === 0 || i === r.equityCurve.length - 1) // downsample
    }))
  };

  fs.writeFileSync('/home/admin/workspace/mu-backtest/results.json', JSON.stringify(output));
  console.log('\n✅ Results saved to results.json');
}

main();
