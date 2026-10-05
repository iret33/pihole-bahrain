// The GitHub download total behind /v1/stats and the downloads badge: sums, failures, caching, the token.

import test, { describe } from 'node:test';
import assert from 'node:assert/strict';
import { SQL } from '../src/db.js';
import { isRepoSlug } from '../src/downloads.js';
import { ID_A, loadBackends, makeWorld, pingBody } from './support/harness.js';

const backends = await loadBackends();
const releases = (counts) => counts.map((assets) => ({ assets }));

test('which repository names are acceptable', () => {
  for (const ok of ['iret33/sinko', 'a/b', 'Some-Org/some.repo_1']) assert.equal(isRepoSlug(ok), true, ok);
  for (const bad of ['', 'sinko', 'a/b/c', '../x', 'a/..', './x', 'a b/c', 'a/b?x=1', 'a/b#', '/b', 'a/', undefined, null, 5, 'a'.repeat(101) + '/b']) {
    assert.equal(isRepoSlug(bad), false, String(bad));
  }
});

for (const backend of backends) {
  describe(backend.name, { skip: backend.skip }, () => {
    describe('the sum', () => {
      test('adds every asset of every published release, drafts excluded, prereleases included', async () => {
        const w = makeWorld(backend);
        w.github.releases = [
          { assets: [100, 20, 3] },
          { assets: [40] },
          { assets: [9999], draft: true },
          { assets: [] },
          { assets: [7] },
        ];
        assert.equal((await w.stats()).downloads, 170);
      });

      test('no releases yet is zero downloads, not unknown', async () => {
        const w = makeWorld(backend);
        assert.equal((await w.stats()).downloads, 0);
      });

      test('asks GitHub the documented way, anonymously when there is no token', async () => {
        const w = makeWorld(backend, { env: { GITHUB_REPO: 'someone/else' } });
        await w.stats();
        assert.equal(w.github.requests.length, 1);
        const [{ url, headers }] = w.github.requests;
        assert.equal(url, 'https://api.github.com/repos/someone/else/releases?per_page=100&page=1');
        assert.equal(headers.get('user-agent'), 'sinko-counter');
        assert.match(headers.get('accept'), /github/);
        assert.equal(headers.has('authorization'), false);
      });

      test('follows the pages, and asks for one more after a full page', async () => {
        const w = makeWorld(backend);
        w.github.releases = releases(Array.from({ length: 130 }, () => [1]));
        assert.equal((await w.stats()).downloads, 130);
        assert.deepEqual(w.github.requests.map((r) => new URL(r.url).searchParams.get('page')), ['1', '2']);

        const exact = makeWorld(backend);
        exact.github.releases = releases(Array.from({ length: 100 }, () => [2]));
        assert.equal((await exact.stats()).downloads, 200);
        assert.deepEqual(exact.github.requests.map((r) => new URL(r.url).searchParams.get('page')), ['1', '2']);
      });

      test('more releases than are read is unknown, not a partial number', async () => {
        const w = makeWorld(backend);
        w.github.releases = releases(Array.from({ length: 1001 }, () => [1]));      // the first 10 pages are full
        assert.equal((await w.stats()).downloads, null);
        assert.equal(w.github.requests.length, 10);
      });

      test('a failure on a later page is unknown, not the sum of the pages that worked', async () => {
        const w = makeWorld(backend);
        w.github.releases = releases(Array.from({ length: 150 }, () => [1]));
        w.github.failPage = 2;
        assert.equal((await w.stats()).downloads, null);
        assert.deepEqual(w.log.lines, ['warn: sinko-counter: downloads unknown (GitHub answered HTTP 502)']);
      });
    });

    describe('when GitHub does not cooperate, downloads is null and the rest still works', () => {
      const failures = [
        ['a network error', (gh) => { gh.failWith = new TypeError('fetch failed'); }],
        ['the rate limit (403)', (gh) => { gh.failWith = { status: 403, body: '{"message":"API rate limit exceeded"}' }; }],
        ['the rate limit (429)', (gh) => { gh.failWith = { status: 429 }; }],
        ['a missing repository (404)', (gh) => { gh.failWith = { status: 404 }; }],
        ['a server error (500)', (gh) => { gh.failWith = { status: 500 }; }],
        ['an answer that is not JSON', (gh) => { gh.failWith = { status: 200, body: '<html>maintenance</html>' }; }],
        ['an answer that is not a list', (gh) => { gh.failWith = { status: 200, body: '{"message":"hello"}' }; }],
        ['an asset without a count', (gh) => { gh.failWith = { status: 200, body: '[{"assets":[{"name":"x"}]}]' }; }],
        ['a negative count', (gh) => { gh.failWith = { status: 200, body: '[{"assets":[{"download_count":-5}]}]' }; }],
        ['a count that is a string', (gh) => { gh.failWith = { status: 200, body: '[{"assets":[{"download_count":"12"}]}]' }; }],
        ['a fractional count', (gh) => { gh.failWith = { status: 200, body: '[{"assets":[{"download_count":1.5}]}]' }; }],
        ['assets that are not a list', (gh) => { gh.failWith = { status: 200, body: '[{"assets":"none"}]' }; }],
      ];
      for (const [name, arrange] of failures) {
        test(name, async () => {
          const w = makeWorld(backend);
          await w.ping(pingBody());
          arrange(w.github);
          const res = await w.get('/v1/stats');
          assert.equal(res.status, 200);
          const stats = await res.json();
          assert.equal(stats.downloads, null);
          assert.equal(stats.online, 1);
          const warnings = w.log.lines.filter((l) => l.startsWith('warn: sinko-counter: downloads unknown'));
          assert.equal(warnings.length, 1);
        });
      }

      test('a repository name that is not one is not even asked about', async () => {
        for (const GITHUB_REPO of ['', '../../etc', 'no-slash', undefined]) {
          const w = makeWorld(backend, { env: { GITHUB_REPO } });
          assert.equal((await w.stats()).downloads, null);
          assert.equal(w.github.requests.length, 0);
        }
      });
    });

    describe('caching', () => {
      test('15 minutes in the Cache API: one trip to GitHub for many visitors', async () => {
        const w = makeWorld(backend);
        w.github.releases = releases([[5]]);
        assert.equal((await w.stats()).downloads, 5);
        w.github.releases = releases([[6]]);
        w.clock.advance(899);
        assert.equal((await w.stats()).downloads, 5);
        assert.equal(w.github.requests.length, 1);
        w.clock.advance(1);
        assert.equal((await w.stats()).downloads, 6);
        assert.equal(w.github.requests.length, 2);
        assert.equal(w.cache.puts, 2);
      });

      test('visitors arriving together share one trip to GitHub', async () => {
        const w = makeWorld(backend);
        w.github.releases = releases([[1]]);
        const answers = await Promise.all([w.stats(), w.stats(), w.stats(), w.stats()]);
        assert.deepEqual(answers.map((a) => a.downloads), [1, 1, 1, 1]);
        assert.equal(w.github.requests.length, 1);
      });

      test('a failure is remembered for one minute, so an outage is not hammered', async () => {
        const w = makeWorld(backend);
        w.github.failWith = { status: 500 };
        assert.equal((await w.stats()).downloads, null);
        w.clock.advance(59);
        assert.equal((await w.stats()).downloads, null);
        assert.equal(w.github.requests.length, 1);
        w.github.failWith = null;
        w.github.releases = releases([[8]]);
        w.clock.advance(1);
        assert.equal((await w.stats()).downloads, 8);
        assert.equal(w.github.requests.length, 2);
      });

      test('where the Cache API does nothing (workers.dev) the database remembers instead', async () => {
        const w = makeWorld(backend, { cache: false });
        w.github.releases = releases([[3]]);
        assert.equal((await w.stats()).downloads, 3);
        w.clock.advance(899);
        assert.equal((await w.stats()).downloads, 3);
        assert.equal(w.github.requests.length, 1);
        w.clock.advance(1);
        await w.stats();
        assert.equal(w.github.requests.length, 2);
      });

      test('a Cache API that throws is survived, and the database answers', async () => {
        const w = makeWorld(backend);
        w.cache.broken = true;
        w.github.releases = releases([[4]]);
        assert.equal((await w.stats()).downloads, 4);
        assert.equal((await w.stats()).downloads, 4);
        assert.equal(w.github.requests.length, 1);
      });

      test('a failure is remembered by the database too', async () => {
        const w = makeWorld(backend, { cache: false });
        w.github.failWith = { status: 403 };
        await w.stats();
        w.clock.advance(30);
        await w.stats();
        assert.equal(w.github.requests.length, 1);
        w.clock.advance(30);
        await w.stats();
        assert.equal(w.github.requests.length, 2);
      });

      test('a damaged remembered answer is ignored', async () => {
        const w = makeWorld(backend, { cache: false });
        w.github.releases = releases([[2]]);
        for (const bad of ['{oops', 'null', '{"downloads":"many"}', '{"downloads":-1}']) {
          await w.db.prepare(SQL.metaSet).bind('downloads', bad, w.clock()).run();
          assert.equal((await w.stats()).downloads, 2, bad);
        }
      });

      test('the downloads badge shares the same cache as /v1/stats', async () => {
        const w = makeWorld(backend);
        w.github.releases = releases([[11]]);
        await w.stats();
        const badge = await (await w.get('/badge/downloads.json')).json();
        assert.equal(badge.message, '11');
        assert.equal(w.github.requests.length, 1);
      });
    });

    describe('the token', () => {
      test('is sent to GitHub as a bearer token, and nowhere else', async () => {
        const w = makeWorld(backend, { env: { GITHUB_TOKEN: 'ghp_secret_token_value' } });
        const res = await w.get('/v1/stats');
        assert.equal(w.github.requests[0].headers.get('authorization'), 'Bearer ghp_secret_token_value');
        assert.equal((await res.text()).includes('ghp_secret'), false);
        assert.equal(JSON.stringify(await w.db.dumpMeta()).includes('ghp_secret'), false);
        assert.equal(w.log.lines.join('\n').includes('ghp_secret'), false);
      });

      test('a revoked token must not hide the numbers: GitHub is asked again without it', async () => {
        const w = makeWorld(backend, { env: { GITHUB_TOKEN: 'ghp_revoked_token' } });
        w.github.rejectToken = true;
        w.github.releases = releases([[21]]);
        assert.equal((await w.stats()).downloads, 21);
        assert.equal(w.github.requests.length, 2);
        assert.equal(w.github.requests[1].headers.has('authorization'), false);
        assert.deepEqual(w.log.lines, ['warn: sinko-counter: GITHUB_TOKEN was refused (revoked or expired): asking GitHub without it']);
      });

      test('never appears in the log, even when the error itself talks about it', async () => {
        const w = makeWorld(backend, { env: { GITHUB_TOKEN: 'ghp_secret_token_value' } });
        w.github.failWith = new Error('request to https://api.github.com failed, headers: Bearer ghp_secret_token_value');
        const res = await w.get('/v1/stats');
        assert.equal((await res.json()).downloads, null);
        assert.deepEqual(w.log.lines, ['warn: sinko-counter: downloads unknown (GitHub could not be reached)']);
        w.clock.advance(120);
        w.github.failWith = Object.assign(new Error('The operation was aborted due to timeout'), { name: 'TimeoutError' });
        await w.stats();
        assert.equal(w.log.lines[1], 'warn: sinko-counter: downloads unknown (GitHub did not answer in time)');
        assert.equal(w.log.lines.join('\n').includes('ghp_secret'), false);
      });
    });

    test('downloads do not depend on pings and pings do not ask GitHub', async () => {
      const w = makeWorld(backend);
      await w.ping(pingBody({ id: ID_A }));
      assert.equal(w.github.requests.length, 0);
    });
  });
}
