// The same small D1 surface on top of a real SQLite (Node's built-in `node:sqlite`, Node 22.5 or newer), with
// schema.sql applied exactly as `wrangler d1 execute --file` would apply it. Running the whole suite on this proves
// that the Worker's SQL is valid SQLite and that FakeD1 means the same thing. `load()` is null where node:sqlite
// does not exist (Node 20), and the tests then run on the fake alone.

import { readFileSync } from 'node:fs';

const SCHEMA = readFileSync(new URL('../../schema.sql', import.meta.url), 'utf8');

export async function loadSqliteBackend() {
  let DatabaseSync;
  try {
    ({ DatabaseSync } = await import('node:sqlite'));
  } catch {
    return null;
  }
  return () => new SqliteD1(DatabaseSync);
}

export class SqliteD1 {
  constructor(DatabaseSync) {
    this.name = 'real SQLite';
    this.sqlite = new DatabaseSync(':memory:');
    this.sqlite.exec(SCHEMA);
    this.sqlite.exec(SCHEMA);       // the deploy workflow applies it on every deploy: it must be idempotent
    this.broken = null;
    this.calls = [];
  }

  prepare(sql) {
    return new SqliteStatement(this, sql, this.sqlite.prepare(sql), []);
  }

  // D1 runs a batch as one transaction, and one at a time. SQLite here is synchronous, so doing the whole batch
  // without an await in between is what keeps two batches from interleaving.
  async batch(statements) {
    this.sqlite.exec('BEGIN');
    try {
      const results = statements.map((statement) => statement.runNow());
      this.sqlite.exec('COMMIT');
      return results;
    } catch (err) {
      this.sqlite.exec('ROLLBACK');
      throw err;
    }
  }

  async dump() {
    return this.sqlite.prepare('SELECT id, first_seen, last_seen, version, hw, country FROM installs ORDER BY id').all().map((r) => ({ ...r }));
  }
  async dumpMeta() {
    return this.sqlite.prepare('SELECT key, value, updated FROM meta ORDER BY key').all().map((r) => ({ ...r }));
  }
  async insertRaw(row) {
    const r = { country: null, ...row };
    this.sqlite.prepare('INSERT INTO installs (id, first_seen, last_seen, version, hw, country) VALUES (?, ?, ?, ?, ?, ?)')
      .run(r.id, r.first_seen, r.last_seen, r.version, r.hw, r.country);
  }
}

class SqliteStatement {
  constructor(db, sql, statement, params) {
    this.db = db;
    this.sql = sql;
    this.statement = statement;
    this.params = params;
  }

  bind(...params) {
    for (const p of params) {
      if (!(p === null || typeof p === 'string' || typeof p === 'number')) throw new TypeError(`D1 cannot bind ${typeof p}`);
    }
    return new SqliteStatement(this.db, this.sql, this.statement, params);
  }

  runNow() {
    if (this.db.broken) throw this.db.broken;
    this.db.calls.push([this.sql, this.params]);
    if (/^\s*SELECT\b/i.test(this.sql)) {
      return { success: true, results: this.statement.all(...this.params).map((r) => ({ ...r })), meta: { changes: 0 } };
    }
    const info = this.statement.run(...this.params);
    return { success: true, results: [], meta: { changes: Number(info.changes) } };
  }

  async run() { return this.runNow(); }
  async all() { return this.runNow(); }
  async first() {
    const { results } = this.runNow();
    return results.length ? results[0] : null;
  }
}
