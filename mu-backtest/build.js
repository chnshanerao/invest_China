const fs = require('fs');

// Read template and data
const html = fs.readFileSync('/home/admin/workspace/mu-backtest/index.html', 'utf8');
const results = fs.readFileSync('/home/admin/workspace/mu-backtest/results.json', 'utf8');

// Inject data
const output = html.replace('RESULTS_PLACEHOLDER', results);

fs.writeFileSync('/home/admin/workspace/mu-backtest/dashboard.html', output);
console.log('Dashboard built: dashboard.html');
console.log(`Size: ${(output.length / 1024).toFixed(0)} KB`);
