'use strict';
// node --test tests/pb-core.test.js
const test = require('node:test');
const assert = require('node:assert/strict');
const C = require('../web/pb-core.js');

const row = (id, time, status, domain, ip, name) => ({ id, time, status, domain: domain || 'example.org', client: { ip: ip || '192.168.1.21', name: name || null } });

test('classify follows what Pi-hole does with the query', () => {
  for (const s of ['GRAVITY', 'REGEX', 'DENYLIST', 'EXTERNAL_BLOCKED_NULL', 'GRAVITY_CNAME', 'SPECIAL_DOMAIN', 'DBBUSY']) assert.equal(C.classify(s), 'blocked', s);
  for (const s of ['FORWARDED', 'RETRIED', 'RETRIED_DNSSEC']) assert.equal(C.classify(s), 'allowed', s);
  for (const s of ['CACHE', 'CACHE_STALE']) assert.equal(C.classify(s), 'memory', s);
  for (const s of ['IN_PROGRESS', 'UNKNOWN', 'SOMETHING_NEW', '', null, undefined]) assert.equal(C.classify(s), 'pending', String(s));
  assert.equal(C.classify('gravity'), 'blocked', 'case does not matter');
});

test('names: app by longest suffix, tidy unknown domains, hidden values', () => {
  const map = { 'youtube.com': 'youtube', 'googlevideo.com': 'youtube', 'ig.me': 'instagram' };
  assert.equal(C.appFor('r4---sn-abc.googlevideo.com', map), 'youtube');
  assert.equal(C.appFor('WWW.YouTube.com.', map), 'youtube');
  assert.equal(C.appFor('notyoutube.com', map), null);
  assert.equal(C.appFor('com', map), null);
  assert.equal(C.appFor('toString.example', map), null, 'no prototype lookups');
  assert.equal(C.baseDomain('r4---sn-abc.googlevideo.com'), 'googlevideo.com');
  assert.equal(C.baseDomain('www.bahrain.com.bh'), 'bahrain.com.bh');
  assert.equal(C.baseDomain('apple.com'), 'apple.com');
  assert.equal(C.baseDomain(''), '');
  assert.ok(C.isHiddenDomain('hidden') && C.isHiddenDomain('') && !C.isHiddenDomain('a.com'));
  assert.ok(C.isHiddenClient('0.0.0.0') && C.isHiddenClient('::') && !C.isHiddenClient('192.168.1.2'));
});

test('numbers', () => {
  assert.equal(C.formatCount(12345.6, 'en-GB'), '12,346');
  assert.equal(C.formatCount(undefined), '0');
  assert.equal(C.percent(0, 0), 0);
  assert.equal(C.percent(1, 3), 33);
  assert.equal(C.percent(1, 30), 3.3, 'one decimal below 10 %');
  assert.equal(C.percent(5, 5), 100);
});

test('feed: the first response only primes it, later ones give each new query once, oldest first', () => {
  const f = new C.Feed({ length: 5 });
  assert.match(f.url(), /length=20$/);
  assert.deepEqual(f.ingest({ queries: [row(10, 1000.5, 'FORWARDED'), row(9, 999, 'GRAVITY')], cursor: 10 }), [], 'priming gives no events');
  assert.equal(f.from, 998, 'from = newest server time - 2 s overlap');
  assert.equal(f.url(), '/api/queries?from=998&length=5');
  const events = f.ingest({ queries: [row(12, 1003, 'GRAVITY', 'www.youtube.com'), row(11, 1001, 'FORWARDED'), row(10, 1000.5, 'FORWARDED')], cursor: 12 });
  assert.deepEqual(events.map(e => e.id), [11, 12], 'newest-first input, oldest-first output, primed row not repeated');
  assert.equal(events[1].kind, 'blocked');
  assert.equal(events[1].domain, 'www.youtube.com');
  assert.deepEqual(f.ingest({ queries: [row(12, 1003, 'GRAVITY'), row(11, 1001, 'FORWARDED')], cursor: 12 }), [], 'nothing twice');
  assert.equal(f.from, 1001);
});

test('feed uses the server clock: the phone clock is never consulted', () => {
  const realNow = Date.now;
  Date.now = () => { throw new Error('the phone clock must not be used'); };
  try {
    const f = new C.Feed();
    f.ingest({ queries: [row(1, 5000, 'CACHE')], cursor: 1 });
    assert.equal(f.from, 4998);
    f.ingest({ queries: [row(2, 5010, 'CACHE')], cursor: 2 });
    assert.equal(f.from, 5008);
  } finally { Date.now = realNow; }
});

test('feed: queries still in progress are skipped now and delivered later with their final status', () => {
  const f = new C.Feed();
  f.ingest({ queries: [row(1, 100, 'FORWARDED')], cursor: 1 });
  assert.deepEqual(f.ingest({ queries: [row(2, 101, 'IN_PROGRESS')], cursor: 2 }), []);
  const later = f.ingest({ queries: [row(2, 101, 'GRAVITY')], cursor: 2 });
  assert.equal(later.length, 1);
  assert.equal(later[0].kind, 'blocked');
  assert.deepEqual(f.ingest({ queries: [row(2, 101, 'GRAVITY')], cursor: 2 }), [], 'and then only once');
});

test('feed: privacy level 3 (no queries, null cursor), hidden domains/clients, and a full page', () => {
  const f = new C.Feed({ length: 2 });
  f.ingest({ queries: [], cursor: null });
  assert.equal(f.privacy, true);
  assert.equal(f.url(), '/api/queries?from=0&length=2', 'it keeps asking, politely');
  f.ingest({ queries: [], cursor: 7 });
  assert.equal(f.privacy, false, 'an empty log is not the same as hidden queries');
  const ev = f.ingest({ queries: [row(8, 10, 'GRAVITY', 'hidden', '0.0.0.0'), row(9, 11, 'FORWARDED', 'hidden', '0.0.0.0')], cursor: 9 });
  assert.equal(f.truncated, true, 'a full page means some queries were probably not shown');
  assert.ok(ev[0].hiddenDomain && ev[0].hiddenClient);
  assert.deepEqual(f.ingest({ queries: 'not an array' }), []);
  assert.deepEqual(f.ingest(null), []);
});

test('feed: remembers only the last `keep` ids', () => {
  const f = new C.Feed({ keep: 3, length: 100 });
  f.ingest({ queries: [row(1, 1, 'CACHE')], cursor: 1 });
  f.ingest({ queries: [row(5, 5, 'CACHE'), row(4, 4, 'CACHE'), row(3, 3, 'CACHE'), row(2, 2, 'CACHE')], cursor: 5 });
  assert.ok(Object.keys(f.seen).length <= 3);
});

test('pacer: gentle rate, interesting queries first, bounded queue', () => {
  const p = new C.Pacer({ gap: 400, max: 3 });
  const ev = (id, kind, app, child) => ({ id, kind, app, child });
  p.push([ev(1, 'memory'), ev(2, 'allowed'), ev(3, 'blocked', 'youtube', true)]);
  assert.equal(p.next(0).id, 3, 'blocked app query of a child first');
  assert.equal(p.next(100), null, 'too soon');
  assert.equal(p.next(400).id, 2);
  assert.equal(p.next(800).id, 1);
  assert.equal(p.next(1200), null, 'empty');
  p.push([ev(4, 'memory'), ev(5, 'memory'), ev(6, 'blocked', 'tiktok'), ev(7, 'allowed', 'roblox')]);
  assert.equal(p.queue.length, 3);
  assert.equal(p.dropped, 1);
  assert.ok(!p.queue.some(e => e.id === 4 || e.id === 5) || p.queue.length === 3);
  assert.ok(p.queue.some(e => e.id === 6) && p.queue.some(e => e.id === 7), 'the dull ones are dropped, not the apps');
  assert.ok(C.priority({ kind: 'blocked', app: 'x', child: true }) > C.priority({ kind: 'memory' }));
});

test('pacer never exceeds the rate no matter how much traffic arrives', () => {
  const p = new C.Pacer({ gap: 380, max: 10 });
  let shown = 0;
  for (let t = 0; t < 10000; t += 16) {                          // ten seconds at 60 frames per second
    p.push([{ id: t, kind: 'allowed' }, { id: t + 1, kind: 'blocked' }]);        // 125 queries per second
    if (p.next(t)) shown++;
  }
  assert.ok(shown <= 27, 'at most about 2.6 packets a second, got ' + shown);
  assert.ok(shown >= 20);
  assert.ok(p.queue.length <= 10);
});

test('wires: S-curves with exact ends, and points along them', () => {
  const w = C.wire({ x: 0, y: 0 }, { x: 100, y: 40 });
  assert.equal(w.d, 'M0 0C50 0 50 40 100 40');
  assert.deepEqual(C.pointAt(w, 0), { x: 0, y: 0 });
  assert.deepEqual(C.pointAt(w, 1), { x: 100, y: 40 });
  const mid = C.pointAt(w, 0.5);
  assert.ok(Math.abs(mid.x - 50) < 1e-9 && Math.abs(mid.y - 20) < 1e-9);
  const v = C.wire({ x: 10, y: 0 }, { x: 30, y: 200 });
  assert.equal(v.d, 'M10 0C10 100 30 100 30 200', 'a mostly vertical wire leaves vertically');
  assert.deepEqual(C.pointAt(v, -3), { x: 10, y: 0 }, 'clamped');
  let last = -1;
  for (let i = 0; i <= 20; i++) { const e = C.easeInOut(i / 20); assert.ok(e >= last); last = e; }
  assert.equal(C.easeInOut(0), 0);
  assert.equal(C.easeInOut(1), 1);
});
