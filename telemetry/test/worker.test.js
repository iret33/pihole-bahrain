// The Worker end to end, on every database backend (see test/support/harness.js).

import test, { describe } from 'node:test';
import assert from 'node:assert/strict';
import { createWorker } from '../src/worker.js';
import { SQL } from '../src/db.js';
import {
  DAY, HOUR, ID_A, ID_B, ID_C, MINUTE, T0,
  forgetRequest, getRequest, loadBackends, makeLog, makeWorld, pingBody, pingRequest,
} from './support/harness.js';

const backends = await loadBackends();

const hex = (i) => i.toString(16).padStart(32, '0');
const row = (id, lastSeen, extra = {}) => ({ id, first_seen: lastSeen - DAY, last_seen: lastSeen, version: '3.0.0', hw: 'orangepi-zero3', country: null, ...extra });

for (const backend of backends) {
  describe(backend.name, { skip: backend.skip }, () => {
    describe('POST /v1/ping', () => {
      test('counts a box and answers with the numbers the box expects', async () => {
        const w = makeWorld(backend);
        const res = await w.ping(pingBody(), { headers: { 'CF-IPCountry': 'BH' } });
        assert.equal(res.status, 200);
        assert.match(res.headers.get('content-type'), /^application\/json/);
        assert.equal(res.headers.get('cache-control'), 'no-store');
        assert.equal(res.headers.get('access-control-allow-origin'), null);
        assert.deepEqual(await res.json(), { online: 1, total: 1 });
        assert.deepEqual(await w.db.dump(), [{ id: ID_A, first_seen: T0, last_seen: T0, version: '3.0.0', hw: 'orangepi-zero3', country: 'BH' }]);
      });

      test('the answer is always sane for the box: integers, 0 <= online <= total', async () => {
        const w = makeWorld(backend);
        for (const id of [ID_A, ID_B, ID_C]) {
          const body = await (await w.ping(pingBody({ id }))).json();
          assert.ok(Number.isInteger(body.online) && Number.isInteger(body.total));
          assert.ok(body.online >= 0 && body.online <= body.total);
        }
        assert.deepEqual(await (await w.ping(pingBody({ id: ID_C }))).json(), { online: 3, total: 3 });
      });

      test('stores and logs nothing about who sent it', async () => {
        const w = makeWorld(backend);
        const secrets = ['203.0.113.77', '2001:db8::77', 'sinko/3.0.0-secret-agent'];
        await w.ping(pingBody(), {
          headers: {
            'CF-Connecting-IP': secrets[0], 'X-Forwarded-For': secrets[1], 'User-Agent': secrets[2], 'CF-IPCountry': 'SA', Accept: 'text/html',
          },
        });
        assert.deepEqual(Object.keys((await w.db.dump())[0]).sort(), ['country', 'first_seen', 'hw', 'id', 'last_seen', 'version']);
        const everything = JSON.stringify([await w.db.dump(), await w.db.dumpMeta(), w.db.calls, w.log.lines]);
        for (const secret of secrets) assert.equal(everything.includes(secret), false, secret);
        assert.deepEqual(w.log.lines, []);
      });

      test('an unknown country is stored as null and never erases a known one', async () => {
        const w = makeWorld(backend);
        const countryOf = async (id) => (await w.db.dump()).find((r) => r.id === id).country;
        await w.ping(pingBody({ id: ID_A }), { headers: { 'CF-IPCountry': 'XX' } });
        await w.ping(pingBody({ id: ID_B }), { headers: { 'CF-IPCountry': 'T1' } });
        await w.ping(pingBody({ id: ID_C }), { headers: { 'CF-IPCountry': 'bh' } });
        assert.deepEqual([await countryOf(ID_A), await countryOf(ID_B), await countryOf(ID_C)], [null, null, 'BH']);
        w.clock.advance(10 * MINUTE);
        await w.ping(pingBody({ id: ID_C }));                       // no header at all this time
        assert.equal(await countryOf(ID_C), 'BH');
        w.clock.advance(10 * MINUTE);
        await w.ping(pingBody({ id: ID_C }), { headers: { 'CF-IPCountry': 'AE' } });
        assert.equal(await countryOf(ID_C), 'AE');                   // a box that moved is counted where it is
      });

      test('dedupe: a second ping within 10 minutes changes nothing, at exactly 10 minutes it counts', async () => {
        const w = makeWorld(backend);
        await w.ping(pingBody({ v: '3.0.0', hw: 'orangepi-zero3' }), { headers: { 'CF-IPCountry': 'BH' } });
        w.clock.advance(599);
        const early = await w.ping(pingBody({ v: '3.0.1', hw: 'x86' }), { headers: { 'CF-IPCountry': 'SA' } });
        assert.equal(early.status, 200);                              // the box must not see an error and retry
        assert.deepEqual(await early.json(), { online: 1, total: 1 });
        assert.deepEqual(await w.db.dump(), [{ id: ID_A, first_seen: T0, last_seen: T0, version: '3.0.0', hw: 'orangepi-zero3', country: 'BH' }]);
        w.clock.advance(1);
        await w.ping(pingBody({ v: '3.0.1', hw: 'x86' }), { headers: { 'CF-IPCountry': 'SA' } });
        assert.deepEqual(await w.db.dump(), [{ id: ID_A, first_seen: T0, last_seen: T0 + 600, version: '3.0.1', hw: 'x86', country: 'SA' }]);
      });

      test('a ping that was ignored does not postpone the next one', async () => {
        const w = makeWorld(backend);
        await w.ping(pingBody());
        w.clock.advance(300);
        await w.ping(pingBody());                                     // ignored
        w.clock.advance(300);                                         // 600 s after the first, 300 s after the ignored one
        await w.ping(pingBody({ v: '3.1.0' }));
        assert.equal((await w.db.dump())[0].last_seen, T0 + 600);
      });

      test('the same box pinging many times at once is one box', async () => {
        const w = makeWorld(backend);
        const answers = await Promise.all(Array.from({ length: 8 }, () => w.ping(pingBody())));
        assert.deepEqual(answers.map((r) => r.status), Array(8).fill(200));
        assert.equal((await w.db.dump()).length, 1);
        assert.deepEqual(await w.stats().then((s) => [s.online, s.total]), [1, 1]);
      });

      test('the snapshot may be a few minutes old, but the box asking is always counted', async () => {
        const w = makeWorld(backend, { snapshotTtl: 300 });
        assert.equal((await w.stats()).online, 0);                    // an empty database, remembered for 5 minutes
        w.clock.advance(1);
        assert.deepEqual(await (await w.ping(pingBody({ id: ID_A }))).json(), { online: 1, total: 1 });
        w.clock.advance(1);
        assert.deepEqual(await (await w.ping(pingBody({ id: ID_B }))).json(), { online: 1, total: 1 });   // cached counts
        w.clock.advance(300);
        assert.deepEqual(await (await w.ping(pingBody({ id: ID_C }))).json(), { online: 3, total: 3 });   // refreshed
      });

      describe('is refused with a clear status, and nothing is stored', () => {
        const big = (n) => JSON.stringify(pingBody()) + ' '.repeat(n - JSON.stringify(pingBody()).length);
        const cases = [
          ['wrong content type', () => pingRequest(pingBody(), { type: 'text/plain' }), 415, 'unsupported_media_type'],
          ['no content type', () => pingRequest(pingBody(), { type: null }), 415, 'unsupported_media_type'],
          ['form content type', () => pingRequest(pingBody(), { type: 'application/x-www-form-urlencoded' }), 415, 'unsupported_media_type'],
          ['malformed JSON', () => pingRequest(null, { raw: '{"id":' }), 400, 'bad_json'],
          ['an empty body', () => pingRequest(null, { raw: '' }), 400, 'bad_json'],
          ['an array', () => pingRequest(null, { raw: '[]' }), 400, 'bad_body'],
          ['bad id', () => pingRequest(pingBody({ id: ID_A.toUpperCase() })), 400, 'invalid_id'],
          ['short id', () => pingRequest(pingBody({ id: 'abc' })), 400, 'invalid_id'],
          ['bad version', () => pingRequest(pingBody({ v: 'latest' })), 400, 'invalid_version'],
          ['bad hardware', () => pingRequest(pingBody({ hw: 'toaster' })), 400, 'invalid_hw'],
          ['an extra field', () => pingRequest(pingBody({ lang: 'ar' })), 400, 'unexpected_field'],
          ['513 bytes', () => pingRequest(null, { raw: big(513) }), 413, 'too_large'],
          ['a Content-Length over the limit', () => pingRequest(pingBody(), { headers: { 'Content-Length': '9999' } }), 413, 'too_large'],
          ['a Content-Length that lies about a big body', () => pingRequest(null, { raw: big(600), headers: { 'Content-Length': '40' } }), 413, 'too_large'],
          ['a Content-Length that is not a number', () => pingRequest(pingBody(), { headers: { 'Content-Length': 'many' } }), 400, 'bad_length'],
          ['bytes that are not UTF-8', () => pingRequest(null, { raw: new Uint8Array([0x7b, 0xff, 0xfe, 0x7d]) }), 400, 'bad_encoding'],
        ];
        for (const [name, make, status, code] of cases) {
          test(name, async () => {
            const w = makeWorld(backend);
            const res = await w.call(make());
            assert.equal(res.status, status);
            assert.deepEqual(await res.json(), { error: code });
            assert.equal(res.headers.get('cache-control'), 'no-store');
            assert.equal(res.headers.get('access-control-allow-origin'), null);
            assert.deepEqual(await w.db.dump(), []);
            assert.deepEqual(w.db.calls, []);
          });
        }

        test('exactly 512 bytes is fine', async () => {
          const w = makeWorld(backend);
          const raw = big(512);
          assert.equal(new TextEncoder().encode(raw).length, 512);
          assert.equal((await w.ping(null, { raw })).status, 200);
        });

        test('a chunked upload without a length is cut off after 512 bytes and the stream is cancelled', async () => {
          const w = makeWorld(backend);
          let cancelled = false;
          const chunk = new TextEncoder().encode(' '.repeat(300));
          const body = new ReadableStream({
            pull(controller) { controller.enqueue(chunk); },        // never ends by itself
            cancel() { cancelled = true; },
          });
          const request = new Request('https://counter.test/v1/ping', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body, duplex: 'half' });
          const res = await w.call(request);
          assert.equal(res.status, 413);
          assert.equal(cancelled, true);
        });
      });

      test('other methods and paths', async () => {
        const w = makeWorld(backend);
        for (const method of ['GET', 'HEAD', 'PUT', 'PATCH', 'DELETE']) {
          const res = await w.call(pingRequest(pingBody(), { method }));
          assert.equal(res.status, 405, method);
          assert.equal(res.headers.get('allow'), 'POST, OPTIONS');
        }
        for (const path of ['/v1/ping/', '/V1/PING', '/v1/pings', '/ping', '/v2/ping']) {
          const res = await w.call(pingRequest(pingBody(), { path }));
          assert.equal(res.status, 404, path);
          assert.deepEqual(await res.json(), { error: 'not_found' });
        }
        assert.deepEqual(await w.db.dump(), []);
      });
    });

    describe('POST /v1/forget', () => {
      const said = async (res) => ({ status: res.status, body: await res.json() });

      test("deletes the box's whole record and says so", async () => {
        const w = makeWorld(backend);
        await w.ping(pingBody({ id: ID_A }), { headers: { 'CF-IPCountry': 'BH' } });
        await w.ping(pingBody({ id: ID_B }));
        const res = await w.forget({ id: ID_A });
        assert.equal(res.status, 200);
        assert.match(res.headers.get('content-type'), /^application\/json/);
        assert.equal(res.headers.get('cache-control'), 'no-store');
        assert.equal(res.headers.get('access-control-allow-origin'), null);
        assert.deepEqual(await res.json(), { forgotten: true });
        assert.deepEqual((await w.db.dump()).map((r) => r.id), [ID_B]);
      });

      test('an id nobody knows gets the same answer and changes nothing', async () => {
        const w = makeWorld(backend);
        await w.ping(pingBody({ id: ID_B }));
        const before = await w.db.dump();
        assert.deepEqual(await said(await w.forget({ id: ID_A })), { status: 200, body: { forgotten: true } });
        assert.deepEqual(await w.db.dump(), before);
        assert.deepEqual(await said(await w.forget({ id: ID_A })), { status: 200, body: { forgotten: true } });
      });

      test('on an empty database, and twice in a row', async () => {
        const w = makeWorld(backend);
        assert.equal((await w.forget({ id: ID_A })).status, 200);
        await w.ping(pingBody({ id: ID_A }));
        assert.equal((await w.forget({ id: ID_A })).status, 200);
        assert.equal((await w.forget({ id: ID_A })).status, 200);
        assert.deepEqual(await w.db.dump(), []);
      });

      test('the public numbers stop counting the box at once, not when the remembered counts expire', async () => {
        const w = makeWorld(backend, { snapshotTtl: 300 });
        await w.ping(pingBody({ id: ID_A }));
        await w.ping(pingBody({ id: ID_B }));
        w.clock.advance(301);                                         // the first ping's remembered counts (1 box) are stale now
        const before = await w.stats();                               // counts both boxes and remembers that for 5 minutes
        assert.deepEqual([before.online, before.active7d, before.total], [2, 2, 2]);
        w.clock.advance(1);
        await w.forget({ id: ID_A });
        const after = await w.stats();                                // well inside the 5 minutes
        assert.deepEqual([after.online, after.active7d, after.total], [1, 1, 1]);
        assert.deepEqual(after.hw, { 'orangepi-zero3': 1 });
        assert.deepEqual(after.versions, { '3.0.0': 1 });
      });

      test('forgetting an unknown id does not throw the remembered counts away', async () => {
        const w = makeWorld(backend, { snapshotTtl: 300 });
        await w.ping(pingBody({ id: ID_B }));
        await w.stats();
        const kept = await w.db.dumpMeta();
        assert.equal(kept.some((m) => m.key === 'snapshot'), true);
        for (let i = 0; i < 5; i += 1) await w.forget({ id: hex(100 + i) });
        assert.deepEqual(await w.db.dumpMeta(), kept);                // a flood of forgets cannot force recounts
      });

      test('a box that forgets inside the dedupe window is gone, and its next ping is a new box, not a repeat', async () => {
        const w = makeWorld(backend);
        await w.ping(pingBody({ id: ID_A, v: '3.0.0' }), { headers: { 'CF-IPCountry': 'BH' } });
        w.clock.advance(60);                                          // well inside the 10 minutes
        await w.forget({ id: ID_A });
        assert.deepEqual(await w.db.dump(), []);
        w.clock.advance(60);
        const again = await w.ping(pingBody({ id: ID_A, v: '3.1.0' }));
        assert.deepEqual(await again.json(), { online: 1, total: 1 });
        assert.deepEqual(await w.db.dump(), [{ id: ID_A, first_seen: T0 + 120, last_seen: T0 + 120, version: '3.1.0', hw: 'orangepi-zero3', country: null }]);
      });

      test('a ping that arrives after the forget brings nothing back of the old record', async () => {
        const w = makeWorld(backend);
        await w.ping(pingBody({ id: ID_A }), { headers: { 'CF-IPCountry': 'SA' } });
        await w.forget({ id: ID_A });
        w.clock.advance(5 * HOUR);
        await w.ping(pingBody({ id: ID_A }));
        const [kept] = await w.db.dump();
        assert.equal(kept.first_seen, T0 + 5 * HOUR);                  // not the old first_seen
        assert.equal(kept.country, null);                              // not the old country
      });

      test("the daily purge and a forget do not get in each other's way", async () => {
        const w = makeWorld(backend);
        await w.db.insertRaw(row(hex(1), T0 - 200 * DAY));
        await w.db.insertRaw(row(hex(2), T0 - 1 * DAY));
        await w.forget({ id: hex(1) });                                // already due for the purge
        await w.purge();
        assert.deepEqual((await w.db.dump()).map((r) => r.id), [hex(2)]);
        assert.deepEqual(w.log.lines, ['log: sinko-counter: forgot 0 box(es) unseen for 180 days']);
      });

      test('stores and logs nothing about who sent it, and not the id either', async () => {
        const w = makeWorld(backend);
        await w.ping(pingBody({ id: ID_A }));
        const secrets = ['203.0.113.77', '2001:db8::77', 'sinko/3.0.0-secret-agent'];
        await w.forget({ id: ID_A }, {
          headers: { 'CF-Connecting-IP': secrets[0], 'X-Forwarded-For': secrets[1], 'User-Agent': secrets[2], 'CF-IPCountry': 'SA' },
        });
        const everything = JSON.stringify([await w.db.dump(), await w.db.dumpMeta(), w.log.lines]);
        for (const secret of secrets) assert.equal(everything.includes(secret), false, secret);
        assert.deepEqual(w.log.lines, []);
        const deleting = w.db.calls.filter(([sql]) => sql === SQL.forget || sql === SQL.dropSnapshot);
        assert.equal(deleting.length, 2);                              // the row, and the remembered counts
        assert.equal(JSON.stringify(deleting).includes('SA'), false);
      });

      describe('is refused with a clear status, and nothing is deleted', () => {
        const ok = { id: ID_A };
        const big = (n) => JSON.stringify(ok) + ' '.repeat(n - JSON.stringify(ok).length);
        const cases = [
          ['wrong content type', () => forgetRequest(ok, { type: 'text/plain' }), 415, 'unsupported_media_type'],
          ['no content type', () => forgetRequest(ok, { type: null }), 415, 'unsupported_media_type'],
          ['malformed JSON', () => forgetRequest(null, { raw: '{"id":' }), 400, 'bad_json'],
          ['an empty body', () => forgetRequest(null, { raw: '' }), 400, 'bad_json'],
          ['an array', () => forgetRequest(null, { raw: '[]' }), 400, 'bad_body'],
          ['no id', () => forgetRequest({}), 400, 'invalid_id'],
          ['upper case id', () => forgetRequest({ id: ID_A.toUpperCase() }), 400, 'invalid_id'],
          ['short id', () => forgetRequest({ id: 'abc' }), 400, 'invalid_id'],
          ['a number as id', () => forgetRequest({ id: 5 }), 400, 'invalid_id'],
          ['a ping body (the other fields are not part of a forget)', () => forgetRequest(pingBody()), 400, 'unexpected_field'],
          ['an extra field', () => forgetRequest({ id: ID_A, why: 'because' }), 400, 'unexpected_field'],
          ['513 bytes', () => forgetRequest(null, { raw: big(513) }), 413, 'too_large'],
          ['a Content-Length that is not a number', () => forgetRequest(ok, { headers: { 'Content-Length': 'many' } }), 400, 'bad_length'],
          ['bytes that are not UTF-8', () => forgetRequest(null, { raw: new Uint8Array([0x7b, 0xff, 0xfe, 0x7d]) }), 400, 'bad_encoding'],
        ];
        for (const [name, make, status, code] of cases) {
          test(name, async () => {
            const w = makeWorld(backend);
            await w.db.insertRaw(row(ID_A, T0));
            const before = await w.db.dump();
            const res = await w.call(make());
            assert.equal(res.status, status);
            assert.deepEqual(await res.json(), { error: code });
            assert.equal(res.headers.get('cache-control'), 'no-store');
            assert.equal(res.headers.get('access-control-allow-origin'), null);
            assert.deepEqual(await w.db.dump(), before);
            assert.deepEqual(w.db.calls, []);
          });
        }

        test('exactly 512 bytes is fine', async () => {
          const w = makeWorld(backend);
          const raw = big(512);
          assert.equal(new TextEncoder().encode(raw).length, 512);
          assert.equal((await w.forget(null, { raw })).status, 200);
        });
      });

      test('other methods and paths', async () => {
        const w = makeWorld(backend);
        await w.db.insertRaw(row(ID_A, T0));
        for (const method of ['GET', 'HEAD', 'PUT', 'PATCH', 'DELETE']) {
          const res = await w.call(forgetRequest({ id: ID_A }, { method }));
          assert.equal(res.status, 405, method);
          assert.equal(res.headers.get('allow'), 'POST, OPTIONS');
        }
        for (const path of ['/v1/forget/', '/V1/FORGET', '/v1/forgets', '/forget']) {
          const res = await w.call(pingRequest({ id: ID_A }, { path }));
          assert.equal(res.status, 404, path);
        }
        assert.equal((await w.db.dump()).length, 1);
      });

      test("gets no CORS: a web page cannot make anyone's box forgotten", async () => {
        const w = makeWorld(backend);
        const preflight = await w.get('/v1/forget', { method: 'OPTIONS', headers: { Origin: 'https://evil.example', 'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'content-type' } });
        assert.equal(preflight.status, 204);
        assert.equal(preflight.headers.get('access-control-allow-origin'), null);
        assert.equal(preflight.headers.get('access-control-allow-methods'), null);
        assert.equal(preflight.headers.get('allow'), 'POST, OPTIONS');
        const res = await w.forget({ id: ID_A }, { headers: { Origin: 'https://evil.example' } });
        assert.equal(res.headers.get('access-control-allow-origin'), null);
      });

      test('a database that is down: 503 and a short log line, so the box asks again later', async () => {
        const w = makeWorld(backend);
        await w.ping(pingBody({ id: ID_A }));
        w.db.broken = new Error('D1_ERROR: down (secret detail)');
        const res = await w.forget({ id: ID_A });
        assert.equal(res.status, 503);
        const text = await res.text();
        assert.deepEqual(JSON.parse(text), { error: 'unavailable' });
        assert.equal(text.includes('secret detail'), false);
        assert.ok(w.log.lines.every((l) => l.startsWith('error: sinko-counter: database error')));
        assert.equal(w.log.lines.join('\n').includes(ID_A), false);
        w.db.broken = null;
        assert.equal((await w.forget({ id: ID_A })).status, 200);
        assert.deepEqual(await w.db.dump(), []);
      });

      test('no database binding: 500 not_configured', async () => {
        const w = makeWorld(backend);
        const res = await w.worker.fetch(forgetRequest({ id: ID_A }), {});
        assert.equal(res.status, 500);
        assert.deepEqual(await res.json(), { error: 'not_configured' });
      });
    });

    describe('GET /v1/stats', () => {
      test('an empty database', async () => {
        const w = makeWorld(backend);
        const res = await w.get('/v1/stats');
        assert.equal(res.status, 200);
        assert.match(res.headers.get('content-type'), /^application\/json/);
        assert.equal(res.headers.get('cache-control'), 'public, max-age=60');
        assert.equal(res.headers.get('access-control-allow-origin'), '*');
        assert.deepEqual(await res.json(), {
          online: 0, active7d: 0, total: 0, countries: 0, versions: {}, hw: {}, downloads: 0, generatedAt: new Date(T0 * 1000).toISOString(),
        });
      });

      test('has exactly the fields of the contract', async () => {
        const w = makeWorld(backend);
        await w.ping(pingBody());
        assert.deepEqual(Object.keys(await w.stats()), ['online', 'active7d', 'total', 'countries', 'versions', 'hw', 'downloads', 'generatedAt']);
      });

      test('window edges: online is 12 hours, active 7 days, total 180 days, all inclusive', async () => {
        const w = makeWorld(backend);
        const edges = [
          ['online, at the edge', T0 - 12 * HOUR],
          ['active, just outside online', T0 - 12 * HOUR - 1],
          ['active, at the edge', T0 - 7 * DAY],
          ['counted, just outside active', T0 - 7 * DAY - 1],
          ['counted, at the edge', T0 - 180 * DAY],
          ['forgotten, just outside (the cron has not run yet)', T0 - 180 * DAY - 1],
        ];
        for (const [i, [, seen]] of edges.entries()) await w.db.insertRaw(row(hex(i + 1), seen));
        const s = await w.stats();
        assert.equal(s.online, 1);
        assert.equal(s.active7d, 3);
        assert.equal(s.total, 5);
        assert.equal(Object.values(s.versions).reduce((a, b) => a + b, 0), s.active7d);
        assert.equal(Object.values(s.hw).reduce((a, b) => a + b, 0), s.active7d);
      });

      test('online <= active7d <= total', async () => {
        const w = makeWorld(backend);
        for (let i = 0; i < 40; i += 1) await w.db.insertRaw(row(hex(i + 1), T0 - ((i * 7 * HOUR) % (200 * DAY))));
        const s = await w.stats();
        assert.ok(s.online <= s.active7d && s.active7d <= s.total, JSON.stringify(s));
      });

      test('versions, hardware and countries are counted over the boxes seen this week', async () => {
        const w = makeWorld(backend);
        const recent = T0 - HOUR;
        await w.db.insertRaw(row(hex(1), recent, { version: '3.0.0', hw: 'orangepi-zero3', country: 'BH' }));
        await w.db.insertRaw(row(hex(2), recent, { version: '3.0.0', hw: 'orangepi-zero3', country: 'BH' }));
        await w.db.insertRaw(row(hex(3), recent, { version: '3.0.0', hw: 'raspberrypi', country: 'SA' }));
        await w.db.insertRaw(row(hex(4), recent, { version: '3.1.0', hw: 'x86', country: null }));
        await w.db.insertRaw(row(hex(5), recent, { version: '2.2.0', hw: 'other', country: 'AE' }));
        await w.db.insertRaw(row(hex(6), T0 - 30 * DAY, { version: '1.0.0', hw: 'other', country: 'KW' }));   // not active
        const s = await w.stats();
        assert.deepEqual(s.versions, { '3.0.0': 3, '2.2.0': 1, '3.1.0': 1 });
        assert.deepEqual(Object.keys(s.versions), ['3.0.0', '2.2.0', '3.1.0']);       // most common first, ties by name
        assert.deepEqual(s.hw, { other: 1, 'orangepi-zero3': 2, raspberrypi: 1, x86: 1 });
        assert.equal(s.countries, 3);                                                // BH, SA, AE: the old KW box and the null do not count
        assert.equal(s.active7d, 5);
        assert.equal(s.total, 6);
      });

      test('made-up versions cannot make the answer grow: the top 20 and "other"', async () => {
        const w = makeWorld(backend);
        for (let i = 0; i < 25; i += 1) await w.db.insertRaw(row(hex(i + 1), T0 - HOUR, { version: `9.${i}.0` }));
        await w.db.insertRaw(row(hex(100), T0 - HOUR, { version: '1.0.0' }));
        await w.db.insertRaw(row(hex(101), T0 - HOUR, { version: '1.0.0' }));
        const s = await w.stats();
        assert.equal(Object.keys(s.versions).length, 21);
        assert.equal(Object.keys(s.versions)[0], '1.0.0');
        assert.equal(s.versions['1.0.0'], 2);
        assert.equal(s.versions.other, 27 - 2 - 19);
        assert.equal(Object.values(s.versions).reduce((a, b) => a + b, 0), s.active7d);
      });

      test('the counts are kept for 5 minutes so that visitors do not cost a database scan each', async () => {
        const w = makeWorld(backend, { snapshotTtl: 300 });
        assert.equal((await w.stats()).total, 0);
        await w.db.insertRaw(row(hex(1), T0));
        w.clock.advance(299);
        const stale = await w.stats();
        assert.equal(stale.total, 0);
        assert.equal(stale.generatedAt, new Date(T0 * 1000).toISOString());
        const readsBefore = w.db.calls.filter(([sql]) => sql === SQL.counts).length;
        w.clock.advance(1);
        const fresh = await w.stats();
        assert.equal(fresh.total, 1);
        assert.equal(fresh.generatedAt, new Date((T0 + 300) * 1000).toISOString());
        assert.equal(readsBefore, 1);
        assert.equal(w.db.calls.filter(([sql]) => sql === SQL.counts).length, 2);
      });

      test('the counts are still answered when the remembered copy cannot be written (write limit, full database)', async () => {
        const w = makeWorld(backend, { snapshotTtl: 300 });
        await w.ping(pingBody({ id: ID_A }));
        w.clock.advance(301);                                         // the remembered counts are stale: a recount, then a write
        w.db.failWrites = new Error('D1_ERROR: daily write limit exceeded');
        const stats = await w.get('/v1/stats');
        assert.equal(stats.status, 200);
        const body = await stats.json();
        assert.deepEqual([body.online, body.active7d, body.total], [1, 1, 1]);
        assert.equal((await w.get('/badge/online.json')).status, 200);
        assert.equal((await w.ping(pingBody({ id: ID_B }))).status, 503);   // a ping needs its own write: the box asks again later
        w.db.failWrites = null;
        assert.equal((await w.ping(pingBody({ id: ID_B }))).status, 200);
        assert.equal((await w.stats()).total, 2);
      });

      test('a damaged or future snapshot is ignored and rebuilt', async () => {
        const w = makeWorld(backend, { snapshotTtl: 300 });
        await w.db.insertRaw(row(hex(1), T0));
        for (const bad of ['{nonsense', '[]', 'null', JSON.stringify({ at: T0, online: 'many' }),
          JSON.stringify({ at: T0 + 5000, online: 99, active7d: 99, total: 99, countries: 0, versions: {}, hw: {} })]) {
          await w.db.prepare(SQL.metaSet).bind('snapshot', bad, T0).run();
          assert.equal((await w.stats()).total, 1, bad);
        }
      });

      test('HEAD works like GET without a body', async () => {
        const w = makeWorld(backend);
        const res = await w.get('/v1/stats', { method: 'HEAD' });
        assert.equal(res.status, 200);
        assert.equal(res.headers.get('cache-control'), 'public, max-age=60');
        assert.equal(await res.text(), '');
      });

      test('POST and friends are not allowed', async () => {
        const w = makeWorld(backend);
        const res = await w.get('/v1/stats', { method: 'POST' });
        assert.equal(res.status, 405);
        assert.equal(res.headers.get('allow'), 'GET, HEAD, OPTIONS');
        assert.equal(res.headers.get('access-control-allow-origin'), '*');
      });
    });

    describe('badges', () => {
      test('online: shields.io endpoint JSON in the brand colour', async () => {
        const w = makeWorld(backend);
        for (const id of [ID_A, ID_B, ID_C]) await w.ping(pingBody({ id }));
        const res = await w.get('/badge/online.json');
        assert.equal(res.status, 200);
        assert.match(res.headers.get('content-type'), /^application\/json/);
        assert.equal(res.headers.get('cache-control'), 'public, max-age=300');
        assert.equal(res.headers.get('access-control-allow-origin'), '*');
        assert.deepEqual(await res.json(), { schemaVersion: 1, label: 'boxes online', message: '3', color: '0F766E', cacheSeconds: 300 });
      });

      test('total counts every box still kept, with compact numbers', async () => {
        const w = makeWorld(backend);
        for (let i = 0; i < 1250; i += 1) await w.db.insertRaw(row(hex(i + 1), T0 - 100 * DAY));
        const total = await (await w.get('/badge/total.json')).json();
        assert.deepEqual(total, { schemaVersion: 1, label: 'boxes counted', message: '1.3k', color: '0F766E', cacheSeconds: 300 });
        const online = await (await w.get('/badge/online.json')).json();
        assert.equal(online.message, '0');
      });

      test('downloads: the GitHub total, compact; the other badges never ask GitHub', async () => {
        const w = makeWorld(backend);
        w.github.releases = [{ assets: [600, 5, 5] }, { assets: [640] }];
        await w.get('/badge/online.json');
        await w.get('/badge/total.json');
        assert.equal(w.github.requests.length, 0);
        const badge = await (await w.get('/badge/downloads.json')).json();
        assert.deepEqual(badge, { schemaVersion: 1, label: 'downloads', message: '1.3k', color: '0F766E', cacheSeconds: 300 });
        assert.equal(w.github.requests.length, 1);
      });

      test('downloads that are not known say so, in grey, as valid badge JSON', async () => {
        const w = makeWorld(backend);
        w.github.failWith = { status: 403 };
        const res = await w.get('/badge/downloads.json');
        assert.equal(res.status, 200);
        assert.deepEqual(await res.json(), { schemaVersion: 1, label: 'downloads', message: 'unavailable', color: 'lightgrey', cacheSeconds: 300 });
      });

      test('other names are not badges', async () => {
        const w = makeWorld(backend);
        for (const path of ['/badge/boxes.json', '/badge/online', '/badge/online.json/', '/badge/', '/badge/Online.json']) {
          const res = await w.get(path);
          assert.equal(res.status, 404, path);
        }
      });
    });

    describe('CORS and OPTIONS', () => {
      test('the public read-only endpoints may be read from any website', async () => {
        const w = makeWorld(backend);
        for (const path of ['/v1/stats', '/badge/online.json', '/badge/total.json', '/badge/downloads.json']) {
          const res = await w.get(path, { method: 'OPTIONS', headers: { Origin: 'https://iret33.github.io', 'Access-Control-Request-Method': 'GET' } });
          assert.equal(res.status, 204, path);
          assert.equal(res.headers.get('access-control-allow-origin'), '*');
          assert.equal(res.headers.get('access-control-allow-methods'), 'GET, HEAD, OPTIONS');
          assert.equal(res.headers.get('access-control-allow-headers'), 'Content-Type');
          assert.equal(res.headers.get('access-control-max-age'), '86400');
          assert.equal(await res.text(), '');
          const get = await w.get(path, { headers: { Origin: 'https://iret33.github.io' } });
          assert.equal(get.headers.get('access-control-allow-origin'), '*', path);
        }
      });

      test('the ping gets no CORS: a web page cannot make its visitors add themselves to the count', async () => {
        const w = makeWorld(backend);
        const preflight = await w.get('/v1/ping', { method: 'OPTIONS', headers: { Origin: 'https://evil.example', 'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'content-type' } });
        assert.equal(preflight.status, 204);
        assert.equal(preflight.headers.get('access-control-allow-origin'), null);
        assert.equal(preflight.headers.get('access-control-allow-methods'), null);
        assert.equal(preflight.headers.get('allow'), 'POST, OPTIONS');
        const ok = await w.ping(pingBody(), { headers: { Origin: 'https://evil.example' } });
        assert.equal(ok.headers.get('access-control-allow-origin'), null);
      });

      test('errors on the public endpoints carry the header too, so a page can read them', async () => {
        const w = makeWorld(backend);
        w.db.broken = new Error('D1_ERROR: down');
        const res = await w.get('/v1/stats');
        assert.equal(res.status, 503);
        assert.equal(res.headers.get('access-control-allow-origin'), '*');
        const unknown = await w.get('/nothing-here', { method: 'OPTIONS' });
        assert.equal(unknown.status, 404);
      });
    });

    describe('the daily purge', () => {
      test('forgets boxes unseen for more than 180 days and nothing else', async () => {
        const w = makeWorld(backend);
        await w.db.insertRaw(row(hex(1), T0 - 180 * DAY - 1));
        await w.db.insertRaw(row(hex(2), T0 - 180 * DAY));
        await w.db.insertRaw(row(hex(3), T0 - 400 * DAY));
        await w.db.insertRaw(row(hex(4), T0));
        await w.stats();                                                // leaves a snapshot row in meta
        const meta = await w.db.dumpMeta();
        await w.purge();
        assert.deepEqual((await w.db.dump()).map((r) => r.id), [hex(2), hex(4)]);
        assert.deepEqual(await w.db.dumpMeta(), meta);
        assert.deepEqual(w.log.lines, ['log: sinko-counter: forgot 2 box(es) unseen for 180 days']);
      });

      test('can run twice, and on an empty database', async () => {
        const w = makeWorld(backend);
        await w.purge();
        await w.db.insertRaw(row(hex(1), T0 - 181 * DAY));
        await w.purge();
        await w.purge();
        assert.deepEqual(await w.db.dump(), []);
      });

      test('a failure is not hidden: the cron run fails', async () => {
        const w = makeWorld(backend);
        w.db.broken = new Error('D1_ERROR: down');
        await assert.rejects(w.purge(), /down/);
      });

      test('the cron handler is wired to the worker', async () => {
        const w = makeWorld(backend);
        assert.equal(typeof w.worker.scheduled, 'function');
        await w.db.insertRaw(row(hex(1), T0 - 200 * DAY));
        await w.worker.scheduled({ cron: '17 3 * * *' }, w.env, { waitUntil() {} });
        assert.deepEqual(await w.db.dump(), []);
      });
    });

    describe('when things go wrong', () => {
      test('a database that is down: 503, a short log line, no detail for the caller', async () => {
        const w = makeWorld(backend);
        w.db.broken = new Error('D1_ERROR: no such table: installs (secret detail)');
        for (const request of [pingRequest(pingBody()), getRequest('/v1/stats'), getRequest('/badge/online.json')]) {
          const res = await w.call(request);
          assert.equal(res.status, 503);
          assert.equal(res.headers.get('cache-control'), 'no-store');
          const text = await res.text();
          assert.deepEqual(JSON.parse(text), { error: 'unavailable' });
          assert.equal(text.includes('secret detail'), false);
        }
        assert.equal(w.log.lines.length, 3);
        assert.ok(w.log.lines.every((l) => l.startsWith('error: sinko-counter: database error')));
        assert.equal(w.log.lines.join('\n').includes(ID_A), false);
        w.db.broken = null;
        assert.equal((await w.ping(pingBody())).status, 200);
      });

      test('no database binding: 500 not_configured', async () => {
        const w = makeWorld(backend);
        for (const env of [{}, { DB: null }, { DB: {} }]) {
          const res = await w.worker.fetch(pingRequest(pingBody()), env);
          assert.equal(res.status, 500);
          assert.deepEqual(await res.json(), { error: 'not_configured' });
          assert.equal((await w.worker.fetch(getRequest('/v1/stats'), env)).status, 500);
        }
      });

      test('an unexpected exception is a plain 500 and the log says only what kind', async () => {
        const log = makeLog();
        const worker = createWorker({ now: () => { throw new TypeError('clock exploded with 203.0.113.9 inside'); }, log });
        const res = await worker.fetch(pingRequest(pingBody()), { DB: makeWorld(backend).db });
        assert.equal(res.status, 500);
        assert.deepEqual(await res.json(), { error: 'internal' });
        assert.deepEqual(log.lines, ['error: sinko-counter: unexpected error (TypeError)']);
      });

      test('a request to the root says what this is', async () => {
        const w = makeWorld(backend);
        const res = await w.get('/');
        assert.equal(res.status, 200);
        assert.match(res.headers.get('content-type'), /^text\/plain/);
        const text = await res.text();
        assert.match(text, /anonymous counter/i);
        assert.match(text, /github\.com\/iret33\/sinko\/blob\/master\/docs\/privacy\.md/);
        // What the public answer says about itself must match docs/privacy.md: what a box sends, what the counter adds,
        // and that switching the counter off deletes the record.
        assert.match(text, /random code, its Sinko version and the kind of device/);
        assert.match(text, /country and the times it first and last heard/);
        assert.match(text, /off on the box deletes its record/);
        assert.equal((await w.get('/', { method: 'POST' })).status, 405);
      });

      test('the default export is a usable worker that reads the real clock', async () => {
        const entry = await import('../src/index.js');
        // workerd refuses to start a Worker whose entry module exports anything but handlers (found by running it).
        assert.deepEqual(Object.keys(entry), ['default']);
        const worker = entry.default;
        assert.equal(typeof worker.fetch, 'function');
        assert.equal(typeof worker.scheduled, 'function');
        const res = await worker.fetch(getRequest('/nothing'), {});
        assert.equal(res.status, 404);
      });
    });
  });
}
