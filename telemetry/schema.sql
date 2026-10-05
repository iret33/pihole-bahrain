-- Sinko anonymous counter: the whole database.
--
-- Safe to apply again and again (every statement says IF NOT EXISTS): the deploy workflow runs it on every
-- deploy. If a later version of the Worker needs another column or table, add an idempotent migration BELOW this
-- line instead of editing a statement above, so databases that already exist keep working.
--
--   npx wrangler d1 execute sinko-counter --remote --file=schema.sql

-- One row per box that said yes. `id` is the random 128-bit code the box made for itself (32 lowercase hex
-- characters); it is derived from nothing on the box. There is deliberately no column for an address, a
-- user agent, a name or anything else: what is not stored cannot leak.
CREATE TABLE IF NOT EXISTS installs (
  id         TEXT    NOT NULL PRIMARY KEY CHECK (length(id) = 32),
  first_seen INTEGER NOT NULL,            -- epoch seconds of the first ping
  last_seen  INTEGER NOT NULL,            -- epoch seconds of the last accepted ping (see the dedupe window)
  version    TEXT    NOT NULL,            -- "3.0.0"
  hw         TEXT    NOT NULL,            -- orangepi-zero3 | raspberrypi | x86 | other
  country    TEXT                         -- two letters from Cloudflare's CF-IPCountry, or NULL
) WITHOUT ROWID;

-- Every question the Worker asks is about a time window on last_seen (online, active, total, purge) and then
-- reads version, hw or country. Putting those columns in the index makes it a covering index: the counts never
-- touch the table, which keeps the number of rows D1 reads (and bills) low.
CREATE INDEX IF NOT EXISTS installs_by_last_seen ON installs (last_seen, version, hw, country);

-- Small key/value store for two cached answers: the counts ("snapshot") and the GitHub download total
-- ("downloads"). Cloudflare's Cache API is only reliable on a custom domain and it lives in one data centre at a
-- time, while this table works everywhere. So the database and GitHub are asked at most every few minutes.
-- (No semicolons inside comments in this file: some tools split a script on every semicolon.)
CREATE TABLE IF NOT EXISTS meta (
  key     TEXT    NOT NULL PRIMARY KEY,
  value   TEXT    NOT NULL,               -- JSON
  updated INTEGER NOT NULL                -- epoch seconds
) WITHOUT ROWID;
