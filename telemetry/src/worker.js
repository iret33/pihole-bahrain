// Sinko's optional anonymous counter, a Cloudflare Worker with a D1 database. (index.js only exports the Worker:
// the runtime refuses a Worker module that exports anything but handlers, so constants and helpers live here.)
// The protocol is in docs/maintainers/architecture.md ("Optional anonymous counter"), the privacy statement in
// docs/privacy.md. This file never sees or stores an address, and never logs a request.
//
//   POST /v1/ping               {"id","v","hw"}  ->  {"online","total"}      (from the boxes)
//   POST /v1/forget             {"id"}           ->  {"forgotten":true}      (from a box whose parent switched the counter off)
//   GET  /v1/stats                                ->  the public numbers      (the project website)
//   GET  /badge/{online,total,downloads}.json     ->  shields.io endpoint JSON (README badges)
//   cron, daily                                   ->  forget boxes unseen for 180 days

import { CORS_HEADERS, HttpError, errorResponse, jsonResponse, readLimitedText } from './http.js';
import { MAX_BODY_BYTES, isJsonContentType, parseCountry, parseForget, parsePing } from './validate.js';
import { forgetBox, loadSnapshot, purgeStale, recordPing } from './db.js';
import { createDownloads } from './downloads.js';
import { compactNumber } from './format.js';

const SNAPSHOT_TTL_SECONDS = 5 * 60;
const BADGE_COLOR = '0F766E';             // the brand colour, without the #, as shields.io wants it
const BADGE_CACHE_SECONDS = 300;          // shields.io never caches for less
const STATS_CACHE = 'public, max-age=60';
const BADGE_CACHE = `public, max-age=${BADGE_CACHE_SECONDS}`;

const BADGES = {
  online: { label: 'boxes online', value: (s) => s.online },
  total: { label: 'boxes counted', value: (s) => s.total },
  downloads: { label: 'downloads', value: (s) => s.downloads },
};

// deps are for the tests: the clock (epoch seconds), fetch, the Cache API, the snapshot lifetime and the log.
export function createWorker(deps = {}) {
  const now = deps.now || (() => Math.floor(Date.now() / 1000));
  const fetchFn = deps.fetch || ((...args) => fetch(...args));
  const getCache = deps.cache === undefined ? () => (typeof caches === 'undefined' ? undefined : caches.default) : () => deps.cache;
  const snapshotTtl = deps.snapshotTtl === undefined ? SNAPSHOT_TTL_SECONDS : deps.snapshotTtl;
  const log = deps.log || console;
  let downloadsInFlight = null;          // visitors arriving together on a cold cache share one trip to GitHub

  // The log is for the operator, so it says which part failed and never what the request held (no address, no
  // user agent, no id, no token).
  const warn = (message) => log.warn(`sinko-counter: ${message}`);

  function database(env) {
    if (!env || !env.DB || typeof env.DB.prepare !== 'function') throw new HttpError(500, 'not_configured');
    return env.DB;
  }

  // D1 being unavailable is not the caller's fault and not worth a stack trace: say "try later".
  async function withDatabase(work) {
    try {
      return await work();
    } catch (err) {
      if (err instanceof HttpError) throw err;
      log.error(`sinko-counter: database error (${String(err && err.message).slice(0, 160)})`);
      throw new HttpError(503, 'unavailable');
    }
  }

  function downloads(env) {
    if (!downloadsInFlight) {
      const helper = createDownloads({ fetchFn, cache: getCache(), db: env.DB, now, repo: env.GITHUB_REPO, token: env.GITHUB_TOKEN, warn });
      downloadsInFlight = helper.get().finally(() => { downloadsInFlight = null; });
    }
    return downloadsInFlight;
  }

  // ---------------------------------------------------------------- handlers
  async function ping(request, env) {
    if (!isJsonContentType(request.headers.get('content-type'))) throw new HttpError(415, 'unsupported_media_type');
    const fields = parsePing(await readLimitedText(request, MAX_BODY_BYTES));
    const country = parseCountry(request.headers.get('CF-IPCountry'));
    const db = database(env);
    const t = now();
    const snapshot = await withDatabase(async () => {
      await recordPing(db, { ...fields, country }, t);
      return loadSnapshot(db, t, snapshotTtl);
    });
    // The box that is asking has just been counted. The snapshot may be a few minutes older than this ping, so
    // never answer less than the truth that is certain: this box exists and is online.
    const online = Math.max(1, snapshot.online);
    const total = Math.max(online, snapshot.total);
    return jsonResponse({ online, total }, { cache: 'no-store' });
  }

  // The parent switched the counter off: delete everything held for this box. The answer is the same whether or not the
  // id was known, so that a retry (the box asks again when an answer is lost) is harmless and the answer does not
  // tell anyone whether an id exists. Nothing is logged about the request, as with a ping.
  async function forget(request, env) {
    if (!isJsonContentType(request.headers.get('content-type'))) throw new HttpError(415, 'unsupported_media_type');
    const { id } = parseForget(await readLimitedText(request, MAX_BODY_BYTES));
    const db = database(env);
    await withDatabase(() => forgetBox(db, id));
    return jsonResponse({ forgotten: true }, { cache: 'no-store' });
  }

  // The counts and the download total for the two public endpoints. The total is optional: null when unknown.
  async function readNumbers(env, wantDownloads) {
    const db = database(env);
    const t = now();
    const [snapshot, count] = await Promise.all([
      withDatabase(() => loadSnapshot(db, t, snapshotTtl)),
      wantDownloads ? downloads(env) : null,
    ]);
    return { snapshot, downloads: count };
  }

  async function statsResponse(env) {
    const { snapshot, downloads: count } = await readNumbers(env, true);
    return jsonResponse({
      online: snapshot.online,
      active7d: snapshot.active7d,
      total: snapshot.total,
      countries: snapshot.countries,
      versions: snapshot.versions,
      hw: snapshot.hw,
      downloads: count,
      generatedAt: new Date(snapshot.at * 1000).toISOString(),
    }, { cache: STATS_CACHE, cors: true });
  }

  async function badgeResponse(kind, env) {
    const badge = BADGES[kind];
    const { snapshot, downloads: count } = await readNumbers(env, kind === 'downloads');
    const value = badge.value({ ...snapshot, downloads: count });
    // An unknown number is said to be unknown, in grey: never a made-up one, never an error colour.
    const known = Number.isSafeInteger(value) && value >= 0;
    return jsonResponse({
      schemaVersion: 1,
      label: badge.label,
      message: known ? compactNumber(value) : 'unavailable',
      color: known ? BADGE_COLOR : 'lightgrey',
      cacheSeconds: BADGE_CACHE_SECONDS,
    }, { cache: BADGE_CACHE, cors: true });
  }

  function rootResponse(env) {
    const repo = typeof env.GITHUB_REPO === 'string' ? env.GITHUB_REPO : '';
    const where = repo ? `\nWhat it stores and why: https://github.com/${repo}/blob/master/docs/privacy.md\n` : '\n';
    const text = 'Sinko anonymous counter.\n'
      + 'A box sends a random code, its Sinko version and the kind of device, and only when its parent said yes. '
      + 'The counter adds the country and the times it first and last heard from the box. Nothing else is stored.\n'
      + 'Switching the counter off on the box deletes its record here.';
    return new Response(`${text}${where}`, {
      headers: { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'public, max-age=3600', 'X-Content-Type-Options': 'nosniff' },
    });
  }

  // ---------------------------------------------------------------- routing
  const ROUTES = new Map([
    ['/', { methods: ['GET'], cors: true, handle: (request, env) => rootResponse(env) }],
    ['/v1/ping', { methods: ['POST'], cors: false, handle: ping }],
    ['/v1/forget', { methods: ['POST'], cors: false, handle: forget }],
    ['/v1/stats', { methods: ['GET'], cors: true, handle: (request, env) => statsResponse(env) }],
    ...Object.keys(BADGES).map((kind) => [`/badge/${kind}.json`, { methods: ['GET'], cors: true, handle: (request, env) => badgeResponse(kind, env) }]),
  ]);

  async function route(request, env) {
    const path = new URL(request.url).pathname;
    const entry = ROUTES.get(path);
    if (!entry) throw new HttpError(404, 'not_found');
    const method = request.method;
    const allowed = entry.methods.concat(entry.methods.includes('GET') ? ['HEAD'] : [], ['OPTIONS']);
    if (method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: entry.cors ? { ...CORS_HEADERS, 'Cache-Control': 'public, max-age=86400' } : { Allow: allowed.join(', ') } });
    }
    if (!allowed.includes(method)) throw new HttpError(405, 'method_not_allowed', allowed.join(', '));
    const response = await entry.handle(request, env);
    return method === 'HEAD' ? new Response(null, { status: response.status, headers: response.headers }) : response;
  }

  return {
    async fetch(request, env) {
      let cors = false;
      try {
        const entry = ROUTES.get(new URL(request.url).pathname);
        cors = entry ? entry.cors : true;
        return await route(request, env);
      } catch (err) {
        if (err instanceof HttpError) return errorResponse(err, { cors });
        log.error(`sinko-counter: unexpected error (${String(err && err.name)})`);
        return errorResponse(new HttpError(500, 'internal'), { cors });
      }
    },

    // Daily cron (wrangler.toml): forget boxes unseen for 180 days. A failure rejects, so Cloudflare shows the run as failed.
    async scheduled(event, env) {
      const forgotten = await purgeStale(database(env), now());
      log.log(`sinko-counter: forgot ${forgotten} box(es) unseen for 180 days`);
    },
  };
}
