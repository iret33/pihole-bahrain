// The site's logic without a browser: configuration, the numbers strip, what is shown and what is left out.

import test from 'node:test';
import assert from 'node:assert/strict';
import { loadScripts } from './helpers.mjs';

const Lib = loadScripts(['lib.js']).SinkoLib;

const json = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
const asset = (n) => ({ download_count: n });

// A fake fetch that answers by address and records every call.
function fakeFetch(routes) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url, init });
    for (const [prefix, answer] of routes) {
      if (url.startsWith(prefix)) {
        if (answer instanceof Error) throw answer;
        return typeof answer === 'function' ? answer(url) : answer;
      }
    }
    throw new Error(`unexpected request ${url}`);
  };
  fn.calls = calls;
  return fn;
}

const config = (extra = {}) => Lib.sanitizeConfig({ repo: 'iret33/sinko', statsUrl: '', buyUrl: '', supportUrl: '', ...extra });

test('a configuration is checked, and a bad value counts as empty at run time', () => {
  assert.deepEqual(Lib.configProblems({ repo: 'iret33/sinko', statsUrl: '', buyUrl: '', supportUrl: '' }), []);
  assert.deepEqual(Lib.configProblems({ repo: 'a/b', statsUrl: 'https://c.example', buyUrl: 'https://shop.example/x', supportUrl: 'mailto:help@example.org' }), []);
  const bad = [
    [{ repo: 'nope', statsUrl: '', buyUrl: '', supportUrl: '' }, /repo/],
    [{ repo: 'a/b', statsUrl: 'http://c.example', buyUrl: '', supportUrl: '' }, /statsUrl/],
    [{ repo: 'a/b', statsUrl: 'https://c.example/', buyUrl: '', supportUrl: '' }, /slash/],
    [{ repo: 'a/b', statsUrl: 'https://c.example/?x=1', buyUrl: '', supportUrl: '' }, /\?/],
    [{ repo: 'a/b', statsUrl: '', buyUrl: 'javascript:alert(1)', supportUrl: '' }, /buyUrl/],
    [{ repo: 'a/b', statsUrl: '', buyUrl: '', supportUrl: 'ftp://x.example' }, /supportUrl/],
    [{ repo: 'a/b', statsUrl: '', buyUrl: '', supportUrl: '', extra: 1 }, /unknown setting/],
    [{ repo: 'a/b', statsUrl: 5, buyUrl: '', supportUrl: '' }, /string/],
    [{ repo: 'a/b' }, /string/],
    [null, /object/],
  ];
  for (const [raw, pattern] of bad) assert.match(Lib.configProblems(raw).join('; '), pattern, JSON.stringify(raw));

  assert.deepEqual(config(), { repo: 'iret33/sinko', statsUrl: '', buyUrl: '', supportUrl: '' });
  assert.equal(config({ statsUrl: 'https://c.example/' }).statsUrl, 'https://c.example');
  assert.equal(config({ statsUrl: 'http://c.example' }).statsUrl, '');
  assert.equal(config({ statsUrl: 'https://c.example/?a=1' }).statsUrl, '');
  assert.equal(config({ buyUrl: 'javascript:alert(1)' }).buyUrl, '');
  assert.equal(config({ buyUrl: 'https://u:p@shop.example/' }).buyUrl, '');
  assert.equal(config({ supportUrl: 'mailto:help@example.org' }).supportUrl, 'mailto:help@example.org');
  assert.equal(config({ supportUrl: 'mailto:' }).supportUrl, '');
  assert.equal(config({ repo: '../x/y' }).repo, Lib.DEFAULT_REPO);
  assert.equal(Lib.sanitizeConfig(undefined).repo, Lib.DEFAULT_REPO);
});

test('the install command follows the repository', () => {
  const hardened = "curl --proto '=https' --proto-redir '=https' -fsSL https://github.com/";
  assert.equal(Lib.installCommand('iret33/sinko'), hardened + 'iret33/sinko/releases/latest/download/install.sh | sudo bash');
  assert.equal(Lib.installCommand('a/b'), hardened + 'a/b/releases/latest/download/install.sh | sudo bash');
});

test('the install command is the hardened form: https only, and no redirect may leave https', () => {
  for (const repo of ['iret33/sinko', 'a/b']) {
    const command = Lib.installCommand(repo);
    assert.ok(command.includes("--proto '=https'"), command);
    assert.ok(command.includes("--proto-redir '=https'"), command);
    assert.ok(command.endsWith('| sudo bash'), command);
  }
});

test('counter answers: only real counts are believed', () => {
  assert.deepEqual(Lib.parseStats({ online: 5, total: 9, downloads: 120 }), { online: 5, downloads: 120 });
  assert.deepEqual(Lib.parseStats({ online: 0, downloads: null }), { online: 0, downloads: null });
  for (const bad of [-1, 1.5, '5', null, NaN, Infinity, 1e11, true, [], {}]) {
    assert.deepEqual(Lib.parseStats({ online: bad, downloads: bad }), { online: null, downloads: null }, String(bad));
  }
  assert.deepEqual(Lib.parseStats({ online: 10, total: 9 }), { online: null, downloads: null });   // more online than counted: nonsense
  for (const junk of [null, undefined, 'x', 5, []]) assert.deepEqual(Lib.parseStats(junk), { online: null, downloads: null });
});

test('the download sum: drafts left out, anything odd means unknown', () => {
  assert.equal(Lib.sumDownloads([{ assets: [asset(3), asset(4)] }, { assets: [asset(10)] }]), 17);
  assert.equal(Lib.sumDownloads([{ assets: [asset(3)] }, { draft: true, assets: [asset(1000)] }]), 3);
  assert.equal(Lib.sumDownloads([{}, { assets: [] }]), 0);
  assert.equal(Lib.sumDownloads([]), 0);
  for (const bad of [null, {}, 'x', [null], [{ assets: 'x' }], [{ assets: [null] }], [{ assets: [{}] }], [{ assets: [asset(-1)] }],
    [{ assets: [asset(1.5)] }], [{ assets: [{ download_count: '9' }] }], [{ assets: [asset(1e11)] }]]) {
    assert.equal(Lib.sumDownloads(bad), null, JSON.stringify(bad));
  }
});

test('GitHub downloads: pages are followed, any failure is unknown, and the request is private', async () => {
  const page1 = Array.from({ length: 100 }, () => ({ assets: [asset(1)] }));
  const page2 = [{ assets: [asset(5)] }];
  const f = fakeFetch([['https://api.github.com/repos/a/b/releases?per_page=100&page=1', json(page1)], ['https://api.github.com/repos/a/b/releases?per_page=100&page=2', json(page2)]]);
  assert.equal(await Lib.githubDownloads(f, 'a/b'), 105);
  assert.equal(f.calls.length, 2);
  for (const { init } of f.calls) {
    assert.equal(init.credentials, 'omit');                   // no cookies
    assert.equal(init.referrerPolicy, 'no-referrer');         // GitHub is not told which page asked
    assert.equal(init.headers.Accept, 'application/json');    // a header that needs no CORS preflight
  }

  for (const answer of [json({}, 403), json({}, 500), new Error('offline'), json({ message: 'x' }), json([{ assets: [{}] }])]) {
    assert.equal(await Lib.githubDownloads(fakeFetch([['https://api.github.com/', answer]]), 'a/b'), null);
  }
  const failing = fakeFetch([['https://api.github.com/repos/a/b/releases?per_page=100&page=1', json(page1)], ['https://api.github.com/', json({}, 502)]]);
  assert.equal(await Lib.githubDownloads(failing, 'a/b'), null);      // page 2 failed: not the sum of page 1
  const endless = fakeFetch([['https://api.github.com/', json(page1)]]);
  assert.equal(await Lib.githubDownloads(endless, 'a/b'), null);      // more releases than are read: partial, so unknown
  assert.equal(endless.calls.length, 5);
  assert.equal(await Lib.githubDownloads(fakeFetch([]), '../x'), null);
});

test('the strip: the counter first, GitHub for the download count when the counter has none', async () => {
  const stats = { online: 40, total: 90, downloads: 321 };
  const f = fakeFetch([['https://c.example/v1/stats', json(stats)]]);
  assert.deepEqual(await Lib.loadCounters({ config: config({ statsUrl: 'https://c.example' }), fetch: f }), { online: 40, downloads: 321 });
  assert.equal(f.calls.length, 1);

  const noDownloads = fakeFetch([['https://c.example/v1/stats', json({ online: 40, total: 90, downloads: null })], ['https://api.github.com/', json([{ assets: [asset(7)] }])]]);
  assert.deepEqual(await Lib.loadCounters({ config: config({ statsUrl: 'https://c.example' }), fetch: noDownloads }), { online: 40, downloads: 7 });
});

test('the strip: no counter configured means no boxes figure, only GitHub downloads', async () => {
  const f = fakeFetch([['https://api.github.com/', json([{ assets: [asset(2), asset(3)] }])]]);
  assert.deepEqual(await Lib.loadCounters({ config: config(), fetch: f }), { online: null, downloads: 5 });
  assert.ok(f.calls.every((c) => c.url.startsWith('https://api.github.com/repos/iret33/sinko/')));
});

test('the strip: a counter that is down is not a reason to show a made-up number', async () => {
  const down = fakeFetch([['https://c.example/', json({}, 503)], ['https://api.github.com/', json({}, 403)]]);
  assert.deepEqual(await Lib.loadCounters({ config: config({ statsUrl: 'https://c.example' }), fetch: down }), { online: null, downloads: null });
  const junk = fakeFetch([['https://c.example/', json({ online: -3, downloads: 'lots' })], ['https://api.github.com/', new Error('offline')]]);
  assert.deepEqual(await Lib.loadCounters({ config: config({ statsUrl: 'https://c.example' }), fetch: junk }), { online: null, downloads: null });
  const counterDownGitHubUp = fakeFetch([['https://c.example/', new Error('offline')], ['https://api.github.com/', json([{ assets: [asset(9)] }])]]);
  assert.deepEqual(await Lib.loadCounters({ config: config({ statsUrl: 'https://c.example' }), fetch: counterDownGitHubUp }), { online: null, downloads: 9 });
});

test('the strip: a remembered answer saves GitHub a request, a damaged one is ignored, nothing useless is remembered', async () => {
  let saved = { online: 12, downloads: 99 };
  const f = fakeFetch([]);
  const cache = { get: () => saved, set: (v) => { saved = v; } };
  assert.deepEqual(await Lib.loadCounters({ config: config(), fetch: f, cache }), { online: 12, downloads: 99 });
  assert.equal(f.calls.length, 0);

  const store = [];
  const fresh = fakeFetch([['https://api.github.com/', json([{ assets: [asset(4)] }])]]);
  assert.deepEqual(await Lib.loadCounters({ config: config(), fetch: fresh, cache: { get: () => ({ online: -1, downloads: 'x' }), set: (v) => store.push(v) } }), { online: null, downloads: 4 });
  assert.deepEqual(store, [{ online: null, downloads: 4 }]);

  const none = [];
  await Lib.loadCounters({ config: config(), fetch: fakeFetch([['https://api.github.com/', json({}, 500)]]), cache: { get: () => null, set: (v) => none.push(v) } });
  assert.deepEqual(none, []);
  const throwing = { get: () => null, set: () => { throw new Error('storage is full'); } };
  assert.deepEqual(await Lib.loadCounters({ config: config(), fetch: fresh, cache: throwing }), { online: null, downloads: 4 });
});

test('numbers keep Latin digits in Arabic, as on the box', () => {
  assert.equal(Lib.formatCount(1242, 'en'), '1,242');
  assert.match(Lib.formatCount(1242, 'ar'), /^1.242$/);
  assert.match(Lib.formatCount(5, 'ar'), /^5$/);
  assert.equal(Lib.isCount(0), true);
  assert.equal(Lib.isCount(-1), false);
});
