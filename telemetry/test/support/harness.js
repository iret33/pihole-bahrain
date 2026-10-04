// Shared pieces for the Worker tests: a clock, a fake Cache API, a fake GitHub, request builders and the list of
// database backends every scenario runs on.

import { FakeD1 } from './fake-d1.js';
import { loadSqliteBackend } from './sqlite-d1.js';
import { createWorker } from '../../src/worker.js';

export const T0 = 1_800_000_000;           // an arbitrary "now" (epoch seconds), well after 2026
export const MINUTE = 60;
export const HOUR = 3600;
export const DAY = 86400;

export const ID_A = 'a'.repeat(32);
export const ID_B = 'b'.repeat(32);
export const ID_C = '0123456789abcdef0123456789abcdef';

export function makeClock(start = T0) {
  let t = start;
  const clock = () => t;
  clock.set = (value) => { t = value; };
  clock.advance = (seconds) => { t += seconds; };
  return clock;
}

// Cache API with max-age, driven by the test clock. `broken` makes every call throw, like a runtime where the cache
// does nothing useful (workers.dev) or is down.
export class FakeCache {
  constructor(clock) {
    this.clock = clock;
    this.entries = new Map();
    this.broken = false;
    this.puts = 0;
  }

  async match(request) {
    if (this.broken) throw new Error('cache is down');
    const hit = this.entries.get(new URL(request.url).href);
    if (!hit || this.clock() >= hit.expires) return undefined;
    return hit.response.clone();
  }

  async put(request, response) {
    if (this.broken) throw new Error('cache is down');
    if (request.method !== 'GET') throw new TypeError('the Cache API only stores GET requests');
    const match = /max-age=(\d+)/.exec(response.headers.get('Cache-Control') || '');
    if (!match) throw new Error('413: no max-age, Cloudflare would refuse to cache it');
    this.puts += 1;
    this.entries.set(new URL(request.url).href, { response: response.clone(), expires: this.clock() + Number(match[1]) });
  }
}

// A GitHub whose releases list is a function of the test. `releases` is an array of
// {draft?, assets: [download_count...]}; set `failWith` to make it misbehave.
export class FakeGitHub {
  constructor() {
    this.releases = [];
    this.requests = [];
    this.failWith = null;               // {status, body?} or an Error
    this.failPage = null;               // a page number that answers 502 while the others work
    this.rejectToken = false;           // answer 401 when a token is sent
  }

  fetch = async (url, init = {}) => {
    const headers = new Headers(init.headers || {});
    this.requests.push({ url: String(url), headers });
    if (this.failWith instanceof Error) throw this.failWith;
    if (this.rejectToken && headers.has('authorization')) return new Response('{"message":"Bad credentials"}', { status: 401 });
    if (this.failWith) return new Response(this.failWith.body ?? '{"message":"nope"}', { status: this.failWith.status });
    const u = new URL(url);
    const page = Number(u.searchParams.get('page') || 1);
    if (this.failPage === page) return new Response('{}', { status: 502 });
    const perPage = Number(u.searchParams.get('per_page') || 30);
    const slice = this.releases.slice((page - 1) * perPage, page * perPage).map((r, i) => ({
      tag_name: `v${i}`,
      draft: Boolean(r.draft),
      assets: r.assets.map((n, k) => ({ name: `asset-${k}`, download_count: n })),
    }));
    return new Response(JSON.stringify(slice), { status: 200, headers: { 'Content-Type': 'application/json' } });
  };
}

// Records everything the Worker logs, so tests can prove what is not in it.
export function makeLog() {
  const lines = [];
  const add = (level) => (...args) => lines.push(`${level}: ${args.join(' ')}`);
  return { lines, log: add('log'), warn: add('warn'), error: add('error'), info: add('info'), debug: add('debug') };
}

export function pingBody(overrides = {}) {
  return { id: ID_A, v: '3.0.0', hw: 'orangepi-zero3', ...overrides };
}

export function pingRequest(body = pingBody(), { headers = {}, raw, method = 'POST', path = '/v1/ping', type = 'application/json' } = {}) {
  const init = { method, headers: { ...(type ? { 'Content-Type': type } : {}), ...headers } };
  if (method !== 'GET' && method !== 'HEAD') init.body = raw !== undefined ? raw : JSON.stringify(body);
  return new Request(`https://counter.test${path}`, init);
}

export function getRequest(path, { method = 'GET', headers = {} } = {}) {
  return new Request(`https://counter.test${path}`, { method, headers });
}

// A Worker wired to a fresh database, clock, cache, GitHub and log.
export function makeWorld(backend, { snapshotTtl = 0, env = {}, cache = true } = {}) {
  const db = backend.create();
  const clock = makeClock();
  const github = new FakeGitHub();
  const fakeCache = new FakeCache(clock);
  const log = makeLog();
  const worker = createWorker({
    now: clock,
    fetch: github.fetch,
    cache: cache ? fakeCache : null,
    snapshotTtl,
    log,
  });
  const fullEnv = { DB: db, GITHUB_REPO: 'iret33/sinko', ...env };
  return {
    db, clock, github, cache: fakeCache, log, worker, env: fullEnv,
    call: (request) => worker.fetch(request, fullEnv),
    ping: (body, options) => worker.fetch(pingRequest(body, options), fullEnv),
    get: (path, options) => worker.fetch(getRequest(path, options), fullEnv),
    stats: async () => (await worker.fetch(getRequest('/v1/stats'), fullEnv)).json(),
    purge: () => worker.scheduled({}, fullEnv),
  };
}

// Every scenario runs on each of these. The SQLite one is absent on Node versions without node:sqlite.
export async function loadBackends() {
  const backends = [{ name: 'fake D1', create: () => new FakeD1(), skip: false }];
  const sqlite = await loadSqliteBackend();
  backends.push(sqlite
    ? { name: 'real SQLite', create: sqlite, skip: false }
    : { name: 'real SQLite', create: null, skip: 'node:sqlite is not available in this Node (needs 22.5+)' });
  return backends;
}
