// Everything that touches D1. The SQL lives in one exported object so that the tests can run the very same
// statements on a real SQLite and on the small fake that Node 20 can run (see test/support).
//
// All times are epoch seconds. Windows (a box is "seen" at `last_seen`):
//   dedupe   a ping is ignored while the last accepted one is younger than 10 minutes
//   online   seen in the last 12 hours (boxes ping every 6 hours, so one missed ping does not drop a box)
//   active   seen in the last 7 days
//   total    seen in the last 180 days = what is kept (the daily cron deletes the rest, and a box that asks to be
//            forgotten is deleted at once)
// Every window is inclusive at its edge except the purge, which deletes strictly older than 180 days.

export const DEDUPE_SECONDS = 10 * 60;
export const ONLINE_SECONDS = 12 * 60 * 60;
export const ACTIVE_SECONDS = 7 * 24 * 60 * 60;
export const RETENTION_SECONDS = 180 * 24 * 60 * 60;
export const MAX_VERSIONS = 20;

export const SQL = Object.freeze({
  // One atomic statement, so two pings of the same box cannot both pass the dedupe check. When the row exists and is
  // younger than the window, the WHERE makes the update a no-op. A missing country (Cloudflare did not know it)
  // never erases one that was known.
  //   ?1 id  ?2 now  ?3 version  ?4 hw  ?5 country  ?6 now - dedupe window
  upsertPing: `INSERT INTO installs (id, first_seen, last_seen, version, hw, country)
VALUES (?1, ?2, ?2, ?3, ?4, ?5)
ON CONFLICT(id) DO UPDATE SET
  last_seen = excluded.last_seen,
  version = excluded.version,
  hw = excluded.hw,
  country = COALESCE(excluded.country, installs.country)
WHERE installs.last_seen <= ?6`,

  // total, online and active in ONE statement: they come from the same moment, so online <= active <= total
  // always holds (the box refuses an answer where it does not).  ?1 now - 180 d  ?2 now - 12 h  ?3 now - 7 d
  counts: `SELECT COUNT(*) AS total,
  COALESCE(SUM(last_seen >= ?2), 0) AS online,
  COALESCE(SUM(last_seen >= ?3), 0) AS active7d
FROM installs
WHERE last_seen >= ?1`,

  // The distributions are over the boxes seen in the last 7 days (they add up to active7d).  ?1 now - 7 d
  versions: `SELECT version, COUNT(*) AS n
FROM installs
WHERE last_seen >= ?1
GROUP BY version
ORDER BY n DESC, version ASC
LIMIT ${MAX_VERSIONS}`,

  hardware: `SELECT hw, COUNT(*) AS n
FROM installs
WHERE last_seen >= ?1
GROUP BY hw
ORDER BY hw ASC`,

  countries: `SELECT COUNT(DISTINCT country) AS n
FROM installs
WHERE last_seen >= ?1 AND country IS NOT NULL`,

  purge: 'DELETE FROM installs WHERE last_seen < ?1',

  // A box asked to be forgotten (the parent switched the counter off). Idempotent: an unknown id deletes nothing.
  forget: 'DELETE FROM installs WHERE id = ?1',

  // The remembered counts (see loadSnapshot) go too, but only when a box was really deleted, so that forget requests
  // for ids nobody knows cannot make every visitor of the website cost a full recount.
  dropSnapshot: 'DELETE FROM meta WHERE key = ?1',

  metaGet: 'SELECT value, updated FROM meta WHERE key = ?1',

  metaSet: `INSERT INTO meta (key, value, updated) VALUES (?1, ?2, ?3)
ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated = excluded.updated`,
});

export async function recordPing(db, { id, version, hw, country }, now) {
  await db.prepare(SQL.upsertPing).bind(id, now, version, hw, country, now - DEDUPE_SECONDS).run();
}

// The counts, computed from the table. Reads every row of the last 7 to 180 days, so callers use loadSnapshot.
export async function computeSnapshot(db, now) {
  const active = now - ACTIVE_SECONDS;
  const [counts, versions, hardware, countries] = await db.batch([
    db.prepare(SQL.counts).bind(now - RETENTION_SECONDS, now - ONLINE_SECONDS, active),
    db.prepare(SQL.versions).bind(active),
    db.prepare(SQL.hardware).bind(active),
    db.prepare(SQL.countries).bind(active),
  ]);
  const row = counts.results[0] || {};
  const total = toCount(row.total);
  const online = toCount(row.online);
  const active7d = toCount(row.active7d);

  const versionCounts = {};
  let listed = 0;
  for (const r of versions.results) {
    versionCounts[r.version] = toCount(r.n);
    listed += toCount(r.n);
  }
  // A flood of made-up versions must not make the public answer grow without limit.
  if (active7d > listed) versionCounts.other = active7d - listed;

  const hardwareCounts = {};
  for (const r of hardware.results) hardwareCounts[r.hw] = toCount(r.n);

  return {
    at: now,
    online,
    active7d,
    total,
    countries: toCount((countries.results[0] || {}).n),
    versions: versionCounts,
    hw: hardwareCounts,
  };
}

// The counts as of at most `ttl` seconds ago. They are kept in the database (one row read) instead of being
// recomputed for every ping and every visit to the site: D1 bills for the rows it reads.
export async function loadSnapshot(db, now, ttl) {
  const row = await db.prepare(SQL.metaGet).bind('snapshot').first();
  if (row) {
    const kept = parseSnapshot(row.value);
    if (kept && kept.at <= now && now - kept.at < ttl) return kept;
  }
  const fresh = await computeSnapshot(db, now);
  try {
    await db.prepare(SQL.metaSet).bind('snapshot', JSON.stringify(fresh), now).run();
  } catch {
    // The stored copy is only an optimisation. The counts were read, so a write that fails (the daily write limit, a
    // full database, a hiccup) must not hide them from the website, the badges or the box that is waiting for its answer.
  }
  return fresh;
}

// Returns the number of boxes forgotten.
export async function purgeStale(db, now) {
  const result = await db.prepare(SQL.purge).bind(now - RETENTION_SECONDS).run();
  return toCount(result.meta && result.meta.changes);
}

// Deletes the box's whole record. Returns true when there was one. The remembered counts are dropped too, so that the
// public numbers stop including the box at once and not up to 5 minutes later. Safe to repeat: a second call finds
// nothing and changes nothing.
export async function forgetBox(db, id) {
  const result = await db.prepare(SQL.forget).bind(id).run();
  const deleted = toCount(result.meta && result.meta.changes) > 0;
  if (deleted) await db.prepare(SQL.dropSnapshot).bind('snapshot').run();
  return deleted;
}

export async function readMeta(db, key) {
  const row = await db.prepare(SQL.metaGet).bind(key).first();
  return row ? { value: row.value, updated: row.updated } : null;
}

export async function writeMeta(db, key, value, now) {
  await db.prepare(SQL.metaSet).bind(key, value, now).run();
}

function toCount(value) {
  const n = Number(value);
  return Number.isSafeInteger(n) && n >= 0 ? n : 0;
}

function parseSnapshot(text) {
  let s;
  try {
    s = JSON.parse(text);
  } catch {
    return null;
  }
  if (!s || typeof s !== 'object' || !Number.isSafeInteger(s.at)) return null;
  for (const key of ['online', 'active7d', 'total', 'countries']) {
    if (!Number.isSafeInteger(s[key]) || s[key] < 0) return null;
  }
  if (!s.versions || typeof s.versions !== 'object' || !s.hw || typeof s.hw !== 'object') return null;
  return s;
}
