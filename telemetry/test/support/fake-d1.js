// A small in-memory stand-in for the part of the D1 API the Worker uses: prepare(sql).bind(...).run() / .first() /
// .all() and batch([...]). It runs on any Node 20+, with nothing installed.
//
// It does not parse SQL. Each statement the Worker owns (the exported SQL object) has a hand-written equivalent below,
// and a statement it does not know is an error, so changing the SQL forces a change here. That this equivalent
// really behaves like the SQL is checked by running the same tests on real SQLite (sqlite-d1.js) whenever
// Node has `node:sqlite` (22.5 or newer).

import { SQL } from '../../src/db.js';

const cmpText = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

export class FakeD1 {
  constructor() {
    this.name = 'fake D1';
    this.installs = new Map();     // id -> row
    this.meta = new Map();         // key -> {value, updated}
    this.broken = null;            // an Error: every call rejects with it
    this.calls = [];               // [sql, params] of everything that ran
    this.handlers = new Map([
      [SQL.upsertPing, (p) => this.#upsert(p)],
      [SQL.counts, (p) => this.#counts(p)],
      [SQL.versions, (p) => this.#versions(p)],
      [SQL.hardware, (p) => this.#hardware(p)],
      [SQL.countries, (p) => this.#countries(p)],
      [SQL.purge, (p) => this.#purge(p)],
      [SQL.forget, (p) => this.#forget(p)],
      [SQL.dropSnapshot, (p) => this.#metaDelete(p)],
      [SQL.metaGet, (p) => this.#metaGet(p)],
      [SQL.metaSet, (p) => this.#metaSet(p)],
    ]);
  }

  prepare(sql) {
    const handler = this.handlers.get(sql);
    if (!handler) throw new Error(`fake D1 does not know this statement: ${sql}`);
    return new FakeStatement(this, sql, handler, []);
  }

  async batch(statements) {
    const results = [];
    for (const statement of statements) results.push(await statement.run());
    return results;
  }

  // Test helpers (the same names exist on the SQLite backend).
  async dump() {
    return [...this.installs.values()].map((r) => ({ ...r })).sort((a, b) => cmpText(a.id, b.id));
  }
  async dumpMeta() {
    return [...this.meta.entries()].map(([key, v]) => ({ key, ...v })).sort((a, b) => cmpText(a.key, b.key));
  }
  async insertRaw(row) {
    this.installs.set(row.id, { country: null, ...row });
  }

  #upsert([id, now, version, hw, country, cutoff]) {
    const row = this.installs.get(id);
    if (!row) {
      this.installs.set(id, { id, first_seen: now, last_seen: now, version, hw, country });
      return { changes: 1 };
    }
    if (row.last_seen > cutoff) return { changes: 0 };
    Object.assign(row, { last_seen: now, version, hw, country: country === null ? row.country : country });
    return { changes: 1 };
  }

  #counts([cut180, cut12h, cut7d]) {
    const rows = [...this.installs.values()].filter((r) => r.last_seen >= cut180);
    return {
      rows: [{
        total: rows.length,
        online: rows.filter((r) => r.last_seen >= cut12h).length,
        active7d: rows.filter((r) => r.last_seen >= cut7d).length,
      }],
    };
  }

  #group([cut7d], field) {
    const counts = new Map();
    for (const r of this.installs.values()) {
      if (r.last_seen >= cut7d) counts.set(r[field], (counts.get(r[field]) || 0) + 1);
    }
    return [...counts.entries()].map(([value, n]) => ({ [field]: value, n }));
  }

  #versions(p) {
    const rows = this.#group(p, 'version').sort((a, b) => b.n - a.n || cmpText(a.version, b.version));
    return { rows: rows.slice(0, 20) };
  }

  #hardware(p) {
    return { rows: this.#group(p, 'hw').sort((a, b) => cmpText(a.hw, b.hw)) };
  }

  #countries([cut7d]) {
    const seen = new Set();
    for (const r of this.installs.values()) {
      if (r.last_seen >= cut7d && r.country !== null) seen.add(r.country);
    }
    return { rows: [{ n: seen.size }] };
  }

  #purge([cutoff]) {
    let changes = 0;
    for (const [id, r] of this.installs) {
      if (r.last_seen < cutoff) {
        this.installs.delete(id);
        changes += 1;
      }
    }
    return { changes };
  }

  #forget([id]) {
    return { changes: this.installs.delete(id) ? 1 : 0 };
  }

  #metaDelete([key]) {
    return { changes: this.meta.delete(key) ? 1 : 0 };
  }

  #metaGet([key]) {
    const row = this.meta.get(key);
    return { rows: row ? [{ value: row.value, updated: row.updated }] : [] };
  }

  #metaSet([key, value, updated]) {
    this.meta.set(key, { value, updated });
    return { changes: 1 };
  }
}

class FakeStatement {
  constructor(db, sql, handler, params) {
    this.db = db;
    this.sql = sql;
    this.handler = handler;
    this.params = params;
  }

  bind(...params) {
    for (const p of params) {
      // What D1 accepts. undefined is an error there too, and a typo here should not pass silently.
      if (!(p === null || typeof p === 'string' || typeof p === 'number')) throw new TypeError(`fake D1 cannot bind ${typeof p}`);
    }
    return new FakeStatement(this.db, this.sql, this.handler, params);
  }

  async #run() {
    if (this.db.broken) throw this.db.broken;
    this.db.calls.push([this.sql, this.params]);
    const out = this.handler(this.params);
    return { success: true, results: out.rows || [], meta: { changes: out.changes || 0 } };
  }

  run() { return this.#run(); }
  all() { return this.#run(); }
  async first() {
    const { results } = await this.#run();
    return results.length ? results[0] : null;
  }
}
