import { getDb } from '../db/schema.js';

const PRICE_TTL = 30 * 60 * 1000;
const ANALYSIS_TTL = 24 * 60 * 60 * 1000;

export function getCached(key, ttl) {
  const db = getDb();
  const row = db.prepare('SELECT data, created_at FROM data_cache WHERE key = ?').get(key);
  if (!row) return null;
  if (Date.now() - row.created_at > ttl) {
    db.prepare('DELETE FROM data_cache WHERE key = ?').run(key);
    return null;
  }
  return JSON.parse(row.data);
}

export function setCache(key, data) {
  const db = getDb();
  const json = JSON.stringify(data);
  db.prepare('INSERT OR REPLACE INTO data_cache (key, data, created_at) VALUES (?, ?, ?)').run(key, json, Date.now());
}

export function getCachedPrice(ticker) {
  return getCached(`price:${ticker.toUpperCase()}`, PRICE_TTL);
}

export function setCachedPrice(ticker, data) {
  setCache(`price:${ticker.toUpperCase()}`, data);
}

export function getCachedAnalysis(ticker) {
  return getCached(`analysis:${ticker.toUpperCase()}`, ANALYSIS_TTL);
}

export function setCachedAnalysis(ticker, data) {
  setCache(`analysis:${ticker.toUpperCase()}`, data);
}

export function saveAnalysis(id, type, name, verdict, score, analysisJson, ticker) {
  const db = getDb();
  db.prepare('INSERT OR REPLACE INTO analyses (id, type, name, ticker, verdict, composite_score, analysis_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)').run(id, type, name, ticker || null, verdict, score, JSON.stringify(analysisJson), Date.now());
}

export function getHistory(type, limit = 20) {
  const db = getDb();
  const query = type
    ? 'SELECT id, type, name, verdict, composite_score, created_at FROM analyses WHERE type = ? ORDER BY created_at DESC LIMIT ?'
    : 'SELECT id, type, name, verdict, composite_score, created_at FROM analyses ORDER BY created_at DESC LIMIT ?';
  return type ? db.prepare(query).all(type, limit) : db.prepare(query).all(limit);
}

export function getAnalysisById(id) {
  const db = getDb();
  return db.prepare('SELECT * FROM analyses WHERE id = ?').get(id);
}
