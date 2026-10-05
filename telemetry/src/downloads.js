// The number of times the project's release files were downloaded: the sum of GitHub's per-asset download_count
// over every published release. Optional extra for the website and the badges, so every failure becomes `null`
// ("unknown"). A number that might be wrong is never shown, and neither is a partial sum.
//
// Caching, in this order:
//   1. Cloudflare's Cache API, 15 minutes (the "right" cache, but it only works on a custom domain and it is
//      per data centre),
//   2. the `meta` row in D1 (works everywhere, shared by every data centre),
//   3. GitHub itself.
// A failure is remembered for one minute, so a GitHub outage or a rate limit is not hammered by every visitor.

import { readMeta, writeMeta } from './db.js';

export const FRESH_SECONDS = 15 * 60;
export const FAILURE_SECONDS = 60;
const PER_PAGE = 100;
const MAX_PAGES = 10;                    // 1000 releases; beyond that the sum would be partial, so it is "unknown"
const TIMEOUT_MS = 8000;
const REPO_RE = /^[A-Za-z0-9_.-]{1,100}\/[A-Za-z0-9_.-]{1,100}$/;
const META_KEY = 'downloads';

// Errors whose text is ours and safe to log. Anything else that fails (the network, a timeout) is logged by kind
// only: the message of a low-level error can describe the request, and the request carries the token.
class Unusable extends Error {}

const isCount = (n) => Number.isSafeInteger(n) && n >= 0;

export function isRepoSlug(value) {
  return typeof value === 'string' && REPO_RE.test(value) && !value.split('/').some((part) => part === '.' || part === '..');
}

// One GET of the releases list. A token is sent only to api.github.com. GitHub answers 401 to a token that was
// revoked or expired even for public data, so in that case the request is repeated without it.
async function getPage(fetchFn, repo, page, token, signal, warn) {
  const url = `https://api.github.com/repos/${repo}/releases?per_page=${PER_PAGE}&page=${page}`;
  const headers = {
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': 'sinko-counter',       // GitHub rejects requests without one
  };
  let response = await fetchFn(url, { headers: token ? { ...headers, Authorization: `Bearer ${token}` } : headers, signal });
  if (response.status === 401 && token) {
    warn('GITHUB_TOKEN was refused (revoked or expired): asking GitHub without it');
    response = await fetchFn(url, { headers, signal });
  }
  if (!response.ok) throw new Unusable(`GitHub answered HTTP ${Number(response.status)}`);
  let releases;
  try {
    releases = await response.json();
  } catch {
    throw new Unusable('GitHub did not send JSON');
  }
  if (!Array.isArray(releases)) throw new Unusable('GitHub sent something other than a list');
  return releases;
}

export async function sumReleaseDownloads({ fetchFn, repo, token, warn = () => {} }) {
  const signal = AbortSignal.timeout(TIMEOUT_MS);
  let total = 0;
  for (let page = 1; page <= MAX_PAGES; page += 1) {
    const releases = await getPage(fetchFn, repo, page, token, signal, warn);
    for (const release of releases) {
      if (!release || release.draft === true) continue;
      const assets = release.assets === undefined ? [] : release.assets;
      if (!Array.isArray(assets)) throw new Unusable('a release has no list of assets');
      for (const asset of assets) {
        if (!asset || !isCount(asset.download_count)) throw new Unusable('an asset has no usable download_count');
        total += asset.download_count;
      }
    }
    if (releases.length < PER_PAGE) {
      if (!Number.isSafeInteger(total)) throw new Unusable('the sum is too large');
      return total;
    }
  }
  throw new Unusable('more releases than are counted');
}

// get() resolves to the number of downloads, or null when it is not known. It never rejects.
export function createDownloads({ fetchFn, cache, db, now, repo, token, warn = () => {} }) {
  const cacheKey = () => new Request(`https://sinko-counter.invalid/cache/downloads?repo=${encodeURIComponent(repo)}`);

  async function fromCache() {
    try {
      const hit = cache && (await cache.match(cacheKey()));
      if (!hit) return undefined;
      const body = await hit.json();
      return body && (body.downloads === null || isCount(body.downloads)) ? body.downloads : undefined;
    } catch {
      return undefined;
    }
  }

  async function toCache(downloads, ttl) {
    try {
      if (!cache) return;
      await cache.put(
        cacheKey(),
        new Response(JSON.stringify({ downloads }), {
          headers: { 'Content-Type': 'application/json', 'Cache-Control': `public, max-age=${ttl}` },
        }),
      );
    } catch {
      // The cache is only an optimisation.
    }
  }

  async function fromDatabase() {
    try {
      const row = await readMeta(db, META_KEY);
      if (!row) return undefined;
      const kept = JSON.parse(row.value);
      const ttl = kept.downloads === null ? FAILURE_SECONDS : FRESH_SECONDS;
      const age = now() - row.updated;
      if (age < 0 || age >= ttl) return undefined;
      if (kept.downloads !== null && !isCount(kept.downloads)) return undefined;
      return { downloads: kept.downloads, remaining: ttl - age };
    } catch {
      return undefined;
    }
  }

  async function ask() {
    let downloads = null;
    try {
      downloads = await sumReleaseDownloads({ fetchFn, repo, token, warn });
    } catch (err) {
      const timedOut = err && (err.name === 'TimeoutError' || err.name === 'AbortError');
      warn(`downloads unknown (${err instanceof Unusable ? err.message : timedOut ? 'GitHub did not answer in time' : 'GitHub could not be reached'})`);
    }
    const ttl = downloads === null ? FAILURE_SECONDS : FRESH_SECONDS;
    await toCache(downloads, ttl);
    try {
      await writeMeta(db, META_KEY, JSON.stringify({ downloads }), now());
    } catch {
      // Same: optional.
    }
    return downloads;
  }

  return {
    async get() {
      if (!isRepoSlug(repo)) return null;
      const cached = await fromCache();
      if (cached !== undefined) return cached;
      const stored = await fromDatabase();
      if (stored) {
        await toCache(stored.downloads, stored.remaining);
        return stored.downloads;
      }
      return ask();
    },
  };
}
