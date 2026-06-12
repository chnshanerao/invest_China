import { DatabaseSync } from 'node:sqlite';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { mkdirSync } from 'node:fs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const DB_PATH = join(__dirname, '..', '..', 'data.db');

let db;

export function getDb() {
  if (!db) {
    db = new DatabaseSync(DB_PATH);
    db.exec(`
      CREATE TABLE IF NOT EXISTS data_cache (
        key TEXT PRIMARY KEY,
        data TEXT NOT NULL,
        created_at INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS analyses (
        id TEXT PRIMARY KEY,
        type TEXT NOT NULL,
        name TEXT NOT NULL,
        ticker TEXT,
        verdict TEXT,
        composite_score REAL,
        analysis_json TEXT NOT NULL,
        created_at INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS batch_runs (
        id TEXT PRIMARY KEY,
        status TEXT NOT NULL DEFAULT 'pending',
        total INTEGER,
        completed INTEGER DEFAULT 0,
        failed INTEGER DEFAULT 0,
        created_at INTEGER NOT NULL,
        finished_at INTEGER
      );
      CREATE TABLE IF NOT EXISTS technical_signals (
        ticker TEXT NOT NULL,
        date TEXT NOT NULL,
        close REAL,
        sma20 REAL, sma50 REAL, sma200 REAL,
        rsi REAL,
        macd REAL, macd_signal REAL, macd_hist REAL,
        bb_upper REAL, bb_lower REAL,
        volume_ratio REAL,
        signal TEXT,
        signal_details TEXT,
        created_at INTEGER NOT NULL,
        PRIMARY KEY (ticker, date)
      );
    `);
  }
  return db;
}
