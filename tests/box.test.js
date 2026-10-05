'use strict';
// node --test tests/box.test.js
// The pure logic behind the My box sheet (web/pb-box.js): what the update card shows, the plain-words readings of the box, the addresses,
// the password rules, and what a restore keeps. The sheet's behaviour in a browser is tests/box_smoke.py.
const test = require('node:test');
const assert = require('node:assert/strict');
const C = require('../web/pb-core.js');
const B = require('../web/pb-box.js').pure;

const NOW = 1800000000;
const state = (update) => C.parseState(JSON.stringify({ update }));
const view = (update, current = '3.0.0', now = NOW) => B.updateView(state(update), { now, current });

test('versions: only x.y.z compares, and only a strictly newer one counts', () => {
  assert.ok(B.isNewer('3.1.0', '3.0.0'));
  assert.ok(B.isNewer('3.0.10', '3.0.9'), 'numbers, not text');
  assert.ok(B.isNewer('10.0.0', '9.9.9'));
  assert.ok(!B.isNewer('3.0.0', '3.0.0'));
  assert.ok(!B.isNewer('2.9.9', '3.0.0'));
  for (const bad of ['', null, undefined, '3.1', 'v3.1.0', '3.1.0-beta', '3.1.0.1', 'latest']) {
    assert.ok(!B.isNewer(bad, '3.0.0'), String(bad));
    assert.ok(!B.isNewer('3.1.0', bad), String(bad));
  }
});

test('update card: up to date, a newer version, and a stale "latest" that is not newer', () => {
  assert.deepEqual(view({ checked: NOW - 3 * 3600 }), { phase: 'current', checked: NOW - 3 * 3600 });
  const v = view({ latest: '3.1.0', notes: 'https://github.com/iret33/sinko/releases/tag/v3.1.0', checked: NOW - 60 });
  assert.equal(v.phase, 'available');
  assert.equal(v.latest, '3.1.0');
  assert.equal(v.notes, 'https://github.com/iret33/sinko/releases/tag/v3.1.0');
  assert.equal(view({ latest: '3.0.0' }).phase, 'current', 'what is installed is not "available"');
  assert.equal(view({ latest: '3.1.0' }, '').phase, 'available', 'an unknown installed version trusts the scheduler');
});

test('update card: a request that has not been picked up is "starting", a run is "running", and a dead run is "stalled"', () => {
  assert.equal(view({ request: 1800000000000, latest: '3.1.0' }).phase, 'starting');
  assert.equal(view({ status: 'running', from: '3.0.0', to: '3.1.0', at: NOW - 30, latest: '3.1.0' }).phase, 'running');
  assert.equal(view({ status: 'running', to: '3.1.0', at: NOW - 30, request: 5 }).phase, 'running', 'running wins over a leftover marker');
  const r = view({ status: 'running', to: '3.1.0', at: NOW - 30 });
  assert.equal(r.to, '3.1.0');
  assert.equal(B.STALL_SEC, 15 * 60, 'the scheduler calls a run with no runner dead after 3 minutes; the page trails it by a wide margin');
  assert.equal(view({ status: 'running', to: '3.1.0', at: NOW - B.STALL_SEC - 1 }).phase, 'stalled', 'running for longer than that means nobody is judging the run');
  assert.equal(view({ status: 'running', to: '3.1.0', at: NOW - B.STALL_SEC + 60 }).phase, 'running', 'a slow but live run is not called stuck');
  assert.equal(view({ status: 'running', to: '3.1.0', at: 0 }).phase, 'stalled', 'running with no time at all too');
});

test('update card: the result of an attempt is shown for a while, a failure for a week', () => {
  assert.equal(view({ status: 'ok', to: '3.1.0', at: NOW - 60 }, '3.1.0').phase, 'ok');
  assert.equal(view({ status: 'ok', to: '3.1.0', at: NOW - 3600 }, '3.1.0').phase, 'current', 'an old success is not news');
  const f = view({ status: 'failed', from: '3.0.0', to: '3.1.0', latest: '3.1.0', at: NOW - 86400, error: 'The download did not match its checksum.' });
  assert.equal(f.phase, 'failed');
  assert.equal(f.error, 'The download did not match its checksum.');
  assert.equal(f.latest, '3.1.0', 'and it can be tried again');
  assert.equal(view({ status: 'failed', to: '3.1.0', latest: '3.1.0', at: NOW - 8 * 86400 }).phase, 'available', 'after a week it is just an update on offer');
  assert.equal(view({ status: 'failed', to: '3.1.0', latest: '3.2.0', at: NOW - 3600 }).phase, 'available', 'a failure of an older version is not the story now');
  assert.equal(view({ status: 'failed', to: '3.1.0', at: NOW - 3600 }, '3.0.0').phase, 'failed', 'failed and nothing else on offer');
  assert.equal(view({ status: 'failed', latest: '3.1.0', at: NOW - 3600 }).phase, 'failed', 'a failure that never learned which version it was still counts for the one on offer');
});

test('update card: a failure carries whether the previous version is back (true, false, or not known), and only the box decides it', () => {
  const failed = (rolledBack) => view({ status: 'failed', from: '3.0.0', to: '3.1.0', latest: '3.1.0', at: NOW - 60, error: 'x', rolledBack });
  assert.equal(failed(true).rolledBack, true);
  assert.equal(failed(false).rolledBack, false);
  assert.equal(failed(null).rolledBack, null);
  assert.equal(failed(undefined).rolledBack, null, 'a state with no such key is "not known"');
  assert.equal(failed('yes').rolledBack, null, 'anything that is not a real boolean is "not known" too (parseState)');
  assert.equal(failed(1).rolledBack, null);
  assert.equal(view({ status: 'ok', to: '3.1.0', at: NOW - 60, rolledBack: true }, '3.1.0').rolledBack, undefined, 'only a failure has one');
});

test('an update is "busy" while it runs, while a request waits, and while this page is asking for one', () => {
  const st = (update) => C.parseState(JSON.stringify({ update }));
  assert.equal(B.updateBusy(st({ status: 'running', at: NOW }), null), true);
  assert.equal(B.updateBusy(st({ request: 1800000000000 }), null), true);
  assert.equal(B.updateBusy(st({ status: 'idle' }), { kind: 'update' }), true, 'this page has just pressed Update now');
  assert.equal(B.updateBusy(st({ status: 'ok' }), null), false);
  assert.equal(B.updateBusy(st({ status: 'failed' }), null), false);
  assert.equal(B.updateBusy(null, null), false, 'no state read yet');
  assert.equal(B.updateBusy({}, null), false);
});

test('temperature in plain words, whatever unit Pi-hole uses', () => {
  assert.deepEqual(B.temperature({ cpu_temp: 47.3, hot_limit: 60, unit: 'C' }), { c: 47, word: 'cool' });
  assert.equal(B.temperature({ cpu_temp: 58, hot_limit: 60, unit: 'C' }).word, 'warm', 'Pi-hole calls 60 hot; for a small board it is only warm');
  assert.equal(B.temperature({ cpu_temp: 72, hot_limit: 60, unit: 'C' }).word, 'hot');
  assert.equal(B.temperature({ cpu_temp: 80, hot_limit: 90, unit: 'C' }).word, 'warm', 'a higher limit from Pi-hole is respected');
  assert.deepEqual(B.temperature({ cpu_temp: 116.6, hot_limit: 140, unit: 'F' }), { c: 47, word: 'cool' });
  assert.deepEqual(B.temperature({ cpu_temp: 320.45, hot_limit: 333.15, unit: 'K' }), { c: 47, word: 'cool' });
  for (const none of [null, undefined, {}, { cpu_temp: null, unit: 'C' }, { cpu_temp: 'hot' }, { cpu_temp: NaN }]) assert.equal(B.temperature(none), null, JSON.stringify(none));
});

test('memory in plain words', () => {
  const sys = (p) => ({ memory: { ram: { total: 1000000, used: p * 10000, available: 1000000 - p * 10000, '%used': p } } });
  assert.deepEqual(B.memory(sys(38.4)), { percent: 38, word: 'ok' });
  assert.equal(B.memory(sys(76)).word, 'busy');
  assert.equal(B.memory(sys(95)).word, 'full');
  assert.equal(B.memory({ memory: { ram: { total: 1000, available: 250 } } }).percent, 75, 'worked out when Pi-hole gives no percentage');
  for (const none of [null, {}, { memory: {} }, { memory: { ram: { total: 0 } } }]) assert.equal(B.memory(none), null, JSON.stringify(none));
});

test('uptime keeps the two biggest units', () => {
  assert.deepEqual(B.durationParts(3 * 86400 + 4 * 3600 + 600), [[3, 'day'], [4, 'hour']]);
  assert.deepEqual(B.durationParts(2 * 86400), [[2, 'day']]);
  assert.deepEqual(B.durationParts(5 * 3600 + 90), [[5, 'hour'], [1, 'minute']]);
  assert.deepEqual(B.durationParts(5 * 3600), [[5, 'hour']]);
  assert.deepEqual(B.durationParts(90), [[1, 'minute']]);
  assert.deepEqual(B.durationParts(5), [[1, 'minute']]);
  assert.deepEqual(B.durationParts(undefined), [[1, 'minute']]);
});

test('a gap between clocks reads as "about", not to the second', () => {
  assert.equal(B.roundGap(10800 - 1), 10800, 'a header that is a second short of three hours is three hours');
  assert.equal(B.roundGap(-10800 + 1), 10800, 'whichever clock is ahead');
  assert.equal(B.roundGap(5 * 3600 + 8 * 60), 5 * 3600 + 15 * 60, "to the nearest quarter of an hour from one hour up");
  assert.equal(B.roundGap(130), 120);
  assert.equal(B.roundGap(121), 120);
  assert.equal(B.roundGap(200), 180);
  assert.equal(B.roundGap(1), 60, 'never "0 minutes"');
});

test('last device seen: the newest, by its name, else its maker, else its address', () => {
  const dev = (lastQuery, name, vendor, ip) => ({ lastQuery, macVendor: vendor || '', ips: [{ ip: ip || '192.168.1.9', name: name || '' }] });
  assert.deepEqual(B.lastDevice([dev(100, 'old'), dev(300, 'Sara-iPad'), dev(200, 'mid')]), { name: 'Sara-iPad', lastQuery: 300 });
  assert.equal(B.lastDevice([dev(300, '', 'Samsung')]).name, 'Samsung');
  assert.equal(B.lastDevice([dev(300, '', '', '192.168.1.77')]).name, '192.168.1.77');
  assert.equal(B.lastDevice([dev(0, 'never')]), null, 'a device that never asked is not "seen"');
  assert.equal(B.lastDevice([]), null);
  assert.equal(B.lastDevice(undefined), null);
});

test('addresses: the number the page was opened with, the names Pi-hole gives to that number, and the .local name', () => {
  const hosts = ['192.168.1.50 family.lan', '192.168.1.60 printer.lan', 'fd00::1 v6.lan'];
  assert.deepEqual(B.addressRows({ hostname: '192.168.1.50', port: '', hosts, nodename: 'sinko' }), [
    { kind: 'ip', value: '192.168.1.50' }, { kind: 'name', value: 'family.lan' }, { kind: 'local', value: 'sinko.local' }]);
  assert.deepEqual(B.addressRows({ hostname: 'family.lan', port: '80', hosts, nodename: 'sinko' }).map((r) => r.value),
    ['192.168.1.50', 'family.lan', 'sinko.local'], 'opened by name: the number comes from the record that holds the name');
  assert.deepEqual(B.addressRows({ hostname: 'sinko.local', port: '', hosts: [], nodename: 'sinko' }), [{ kind: 'local', value: 'sinko.local' }],
    'opened by the .local name with no record and no box.json: only what is known');
  assert.deepEqual(B.addressRows({ hostname: 'sinko.local', port: '', boxIp: '192.168.1.50', hosts, nodename: 'sinko' }), [
    { kind: 'ip', value: '192.168.1.50' }, { kind: 'name', value: 'family.lan' }, { kind: 'local', value: 'sinko.local' }],
    'opened by the .local name: the box says its own number, and Pi-hole\'s record for that number gives the other name');
  assert.deepEqual(B.addressRows({ hostname: '10.0.0.7', boxIp: '192.168.1.50', hosts, nodename: 'sinko' }).map((r) => r.value).slice(0, 1), ['192.168.1.50'],
    'the number the box reports (its way to the router) wins over the one the page was opened by');
  assert.deepEqual(B.addressRows({ hostname: 'sinko.local', boxIp: '127.0.0.1', hosts: [], nodename: 'sinko' }), [{ kind: 'local', value: 'sinko.local' }],
    'a box.json address that no router could use is not shown');
  assert.ok(!B.addressRows({ hostname: '192.168.1.50', hosts, nodename: 'sinko' }).some((r) => /printer|v6/.test(r.value)), 'another device\'s record is never the box');
  assert.deepEqual(B.addressRows({ hostname: '192.168.1.50', port: '8080', hosts, nodename: 'sinko' }).map((r) => r.value),
    ['192.168.1.50', 'family.lan:8080', 'sinko.local:8080'], 'a port belongs to names, never to the number for the router');
  assert.deepEqual(B.addressRows({ hostname: '192.168.1.50', port: '443', hosts: [], nodename: '' }), [{ kind: 'ip', value: '192.168.1.50' }]);
  assert.deepEqual(B.addressRows({ hostname: '', hosts: undefined, nodename: undefined }), []);
});

test('addresses: nothing odd from Pi-hole or the host name becomes a row', () => {
  const rows = B.addressRows({ hostname: '192.168.1.50', hosts: ['192.168.1.50 good.lan <script> -bad- localhost a..b', 42, null, '  '], nodename: 'sinko' }).map((r) => r.value);
  assert.deepEqual(rows, ['192.168.1.50', 'good.lan', 'sinko.local']);
  assert.ok(!B.addressRows({ hostname: '192.168.1.50', hosts: [], nodename: 'my box; rm -rf /' }).some((r) => r.kind === 'local'), 'a host name with spaces is not an mDNS name');
  assert.ok(!B.addressRows({ hostname: '192.168.1.50', hosts: [], nodename: 'a.b' }).some((r) => r.kind === 'local'), 'nor one with dots');
  assert.deepEqual(B.addressRows({ hostname: '[fe80::1]', hosts: [], nodename: '' }), [], 'IPv6 is not offered as the number for a router');
  assert.deepEqual(B.addressRows({ hostname: 'localhost', hosts: [], nodename: '' }), [], 'localhost is not an address of the box');
});

test('a number for a router: four numbers, and not one that cannot be the box on a home network', () => {
  for (const ok of ['192.168.1.50', '10.0.0.2', '172.16.5.9', '100.64.1.1', '8.8.8.8']) assert.equal(B.usableIpv4(ok), ok, ok);
  for (const bad of ['', null, undefined, 42, 'sinko.local', '192.168.1', '192.168.1.256', '0.0.0.0', '127.0.0.1', '169.254.3.4', '224.0.0.1', '255.255.255.255',
    '192.168.1.50 ', ' 192.168.1.50', '192.168.1.50\n', '[fe80::1]', '::1', '1.2.3.4.5', '01.2.3.4x']) assert.equal(B.usableIpv4(bad), '', JSON.stringify(bad));
});

test('box.json: only the documented shape is believed, field by field', () => {
  const good = { v: 1, version: '3.0.0', ip: '192.168.1.50', tz: 'Asia/Bahrain', utcOffset: '+03:00', counter: true, mdns: true, at: 1790000000 };
  assert.deepEqual(B.parseBoxInfo(good), { version: '3.0.0', ip: '192.168.1.50', tz: 'Asia/Bahrain', utcOffset: '+03:00', counter: true, mdns: true, at: 1790000000 });
  for (const none of [null, undefined, 'x', 7, [], {}, { v: 2, ip: '192.168.1.50', at: 1790000000 }, { v: '1' }]) assert.equal(B.parseBoxInfo(none), null, JSON.stringify(none));
  // null fields are legal (the box can have no route, no zone name): they cost only what they were for
  assert.deepEqual(B.parseBoxInfo({ v: 1, version: '3.0.0', ip: null, tz: null, utcOffset: '+00:00', counter: false, mdns: false, at: 5 }),
    { version: '3.0.0', ip: '', tz: '', utcOffset: '+00:00', counter: false, mdns: false, at: 5 });
  assert.equal(B.parseBoxInfo(Object.assign({}, good, { ip: 'sinko.local' })).ip, '', 'a name is never the number for the router');
  assert.equal(B.parseBoxInfo(Object.assign({}, good, { ip: '0.0.0.0' })).ip, '');
  assert.equal(B.parseBoxInfo(Object.assign({}, good, { tz: '../../etc/passwd' })).tz, '');
  assert.equal(B.parseBoxInfo(Object.assign({}, good, { tz: 'Asia/Bahrain<script>' })).tz, '');
  assert.equal(B.parseBoxInfo(Object.assign({}, good, { utcOffset: '+3' })).utcOffset, '');
  assert.equal(B.parseBoxInfo(Object.assign({}, good, { utcOffset: '+25:00' })).utcOffset, '');
  assert.equal(B.parseBoxInfo(Object.assign({}, good, { counter: 'true' })).counter, false, 'true means the boolean true');
  assert.equal(B.parseBoxInfo(Object.assign({}, good, { counter: 1 })).counter, false);
  assert.equal(B.parseBoxInfo(Object.assign({}, good, { mdns: 'yes' })).mdns, false);
  for (const at of [-1, 0, 1.5, NaN, Infinity, '1790000000', null]) assert.equal(B.parseBoxInfo(Object.assign({}, good, { at })).at, 0, String(at));
  assert.equal(B.parseBoxInfo(Object.assign({}, good, { version: 'v3' })).version, '');
});

test('the box\'s pulse: late when box.json is more than 20 minutes behind the box\'s own clock, never for a missing or future one', () => {
  const info = (at) => B.parseBoxInfo({ v: 1, at });
  assert.equal(B.HEARTBEAT_LATE_SEC, 1200);
  assert.equal(B.heartbeatLate(info(NOW - 1201), NOW), true);
  assert.equal(B.heartbeatLate(info(NOW - 1200), NOW), false, 'exactly twenty minutes is not yet late');
  assert.equal(B.heartbeatLate(info(NOW - 600), NOW), false, 'ten minutes is the box\'s normal rhythm at worst');
  assert.equal(B.heartbeatLate(info(NOW + 3600), NOW), false, 'a file from the future (the box\'s clock was set back) is not a stopped scheduler');
  assert.equal(B.heartbeatLate(null, NOW), false, 'an older box has no pulse to miss');
  assert.equal(B.heartbeatLate(B.parseBoxInfo({ v: 1, at: 'soon' }), NOW), false, 'an unreadable time is no pulse either');
  assert.equal(B.heartbeatLate(info(NOW - 5000), undefined), false);
});

test('the time zone note: the box\'s own zone, a warning only for universal time, and the phone\'s difference', () => {
  const zi = (extra, phone) => B.zoneInfo(B.parseBoxInfo(Object.assign({ v: 1, at: 5 }, extra)), phone);
  assert.deepEqual(B.zoneInfo(null, 180), { kind: 'unknown' }, 'an older box');
  assert.deepEqual(zi({}, 180), { kind: 'unknown' }, 'a box that does not know its zone');
  assert.deepEqual(zi({ tz: 'Asia/Bahrain', utcOffset: '+03:00' }, 180), { kind: 'zone', label: 'Asia/Bahrain, UTC+03:00', differs: false });
  assert.equal(zi({ tz: 'Asia/Bahrain', utcOffset: '+03:00' }, 60).differs, true, 'a phone one zone away');
  assert.equal(zi({ tz: 'Asia/Bahrain', utcOffset: '+03:00' }, null).differs, null, 'a phone that does not say');
  assert.deepEqual(zi({ utcOffset: '-05:30' }, -330), { kind: 'zone', label: 'UTC-05:30', differs: false }, 'an offset alone is enough');
  for (const name of ['UTC', 'Etc/UTC', 'utc', 'Etc/UCT', 'Universal', 'Zulu']) assert.equal(zi({ tz: name, utcOffset: '+00:00' }, 180).kind, 'utc', name);
  assert.equal(zi({ tz: 'Europe/London', utcOffset: '+00:00' }, 180).kind, 'zone', 'London in winter is on +00:00 and is not universal time');
  assert.equal(zi({ utcOffset: '+00:00' }, 180).kind, 'zone', 'an offset of zero alone is not called UTC either');
});

test('the box\'s own wall clock from box.json\'s offset, and nothing when it is not known', () => {
  const info = B.parseBoxInfo({ v: 1, utcOffset: '+03:00', at: 5 });
  const epoch = Date.UTC(2026, 9, 4, 18, 30, 0) / 1000;             // 18:30 UTC is 21:30 at +03:00
  assert.equal(B.boxWallTime(info, epoch, 'en-GB'), '21:30');
  assert.equal(B.boxWallTime(B.parseBoxInfo({ v: 1, utcOffset: '-05:30', at: 5 }), epoch, 'en-GB'), '13:00');
  assert.equal(B.boxWallTime(B.parseBoxInfo({ v: 1, utcOffset: '+00:00', at: 5 }), epoch, 'en-GB'), '18:30');
  assert.equal(B.boxWallTime(B.parseBoxInfo({ v: 1, at: 5 }), epoch, 'en-GB'), '', 'no offset: the page falls back to this phone\'s own conversion');
  assert.equal(B.boxWallTime(null, epoch, 'en-GB'), '');
  assert.equal(B.boxWallTime(info, NaN, 'en-GB'), '');
});

test('the reason of a failed update is shown only as plain English details, and never when it reads like a command', () => {
  assert.equal(B.reasonDetails('The installer stopped with an error (exit status 1). The previous version (3.0.0) was put back.'),
    'The installer stopped with an error (exit status 1). The previous version (3.0.0) was put back.');
  assert.equal(B.reasonDetails('  The download did not match its checksum.\n'), 'The download did not match its checksum.');
  assert.equal(B.reasonDetails('The update did not run to the end.'), 'The update did not run to the end.', 'the word run alone is not a command');
  assert.equal(B.reasonDetails('The update could not be started.'), 'The update could not be started.');
  for (const bad of ['Going back did not work either: run sudo sinko doctor.', 'try `systemctl restart sinko`', 'sudo reboot', 'Run: sinko update', 'use sinko doctor to see',
    'then $(do-something)', 'apt-get install x', 'journalctl -u sinko']) assert.equal(B.reasonDetails(bad), '', bad);
  for (const none of [null, undefined, '', '   ', 42, {}]) assert.equal(B.reasonDetails(none), '', String(none));
  assert.ok(!/[\u0000-\u001f]/.test(B.reasonDetails('a\u0000b\u001bc')), 'no control characters');
});

test('a restore that took nothing is noticed from Pi-hole\'s own list of what it processed', () => {
  assert.equal(B.restoreVerdict({ files: ['etc/pihole/gravity.db->group', 'etc/pihole/gravity.db->client'], took: 0.01 }), 'ok', 'FTL\'s real answer');
  assert.equal(B.restoreVerdict({ processed: ['etc/pihole/gravity.db->group'] }), 'ok', 'what its documentation says');
  assert.equal(B.restoreVerdict({ files: [] }), 'nothing', 'a config-only or unrelated zip: 200 and an empty list');
  assert.equal(B.restoreVerdict({ files: ['etc/pihole/pihole.toml'] }), 'nothing');
  assert.equal(B.restoreVerdict({}), 'ok', 'an answer with no list is not judged: a future FTL cannot make every restore look like a failure');
  assert.equal(B.restoreVerdict(null), 'ok');
  assert.equal(B.restoreVerdict({ files: 'etc/pihole/gravity.db->group' }), 'ok', 'a list that is not a list is not judged');
});

test('a rebuild of the block lists is "ok" only when its own text shows success and no failure line', () => {
  const okText = '[i] Neutrino emissions detected...\n\n[\u2713] Pulling blocklist source list into range\n[\u2713] Swapping databases\n[\u2713] Pi-hole blocking is enabled\n';
  assert.equal(B.gravityVerdict(okText), 'ok');
  assert.equal(B.gravityVerdict(okText + '\n[\u2717] Status: Retrieval failed\n'), 'partial', 'one list that could not be fetched');
  assert.equal(B.gravityVerdict('[\u2717] DNS resolution is currently unavailable'), 'partial');
  assert.equal(B.gravityVerdict('   [\u2717] indented failure'), 'partial');
  assert.equal(B.gravityVerdict('[i] Gravity failed'), 'partial');
  assert.equal(B.gravityVerdict(''), 'partial', 'nothing that confirms it is not a confirmation');
  assert.equal(B.gravityVerdict(null), 'partial');
  assert.equal(B.gravityVerdict('[i] only information\n'), 'partial');
  assert.equal(B.gravityVerdict('\u001b[32m[\u2713]\u001b[0m Swapping databases'), 'ok', 'colour codes are ignored');
  assert.equal(B.gravityVerdict('[\u2713] a list says [\u2717] in the middle of a line'), 'ok', 'only a line that starts with the mark is a failure');
  // The raw stream of a real `pihole -g`: status lines are redrawn with carriage return + erase-line, so the mark is not at a line start.
  const raw = '  [i] Target: https://example.invalid/a.txt\r\u001b[K  [\u2713] Status: Retrieval successful\n  [i] Target: https://example.invalid/b.txt\r\u001b[K  [\u2717] Status: Connection Refused\n[\u2713] Swapping databases\n';
  assert.equal(B.gravityVerdict(raw), 'partial', 'a failed list after \\r and an erase-line code is still a failure');
  assert.equal(B.gravityVerdict(raw.replace('[\u2717] Status: Connection Refused', '[\u2713] Status: Retrieval successful')), 'ok', 'and the same stream without the failure is ok');
  assert.equal(B.gravityVerdict('\u001b[1;31m\r\u001b[2K[\u2717] Unable to\n[\u2713] rest'), 'partial', 'other erase and colour sequences too');
});

test('new password: 8 characters, typed twice, different from the old one', () => {
  assert.equal(B.passwordProblem('old-password', 'short', 'short'), 'boxPwShort');
  assert.equal(B.passwordProblem('old-password', 'longenough', 'longenouGH'), 'boxPwMismatch');
  assert.equal(B.passwordProblem('old-password', 'old-password', 'old-password'), 'boxPwSame');
  assert.equal(B.passwordProblem('old-password', 'new-password-1', 'new-password-1'), null);
  assert.equal(B.passwordProblem('', 'exactly8', 'exactly8'), null, 'the claim screen has no old password');
  assert.equal(B.passwordProblem('x', 'عربي١٢٣٤', 'عربي١٢٣٤'), null, 'eight characters of Arabic count as eight');
  assert.equal(B.passwordProblem('x', '\u{1F600}'.repeat(7), '\u{1F600}'.repeat(7)), 'boxPwShort', 'an emoji is one character, not two');
  assert.equal(B.passwordProblem('x', '\u{1F600}'.repeat(8), '\u{1F600}'.repeat(8)), null);
});

test('a restore imports only the gravity tables, and says so explicitly (a missing "import" means everything)', () => {
  const r = B.RESTORE_IMPORT;
  assert.equal(r.config, false);
  assert.equal(r.dhcp_leases, false);
  assert.deepEqual(Object.keys(r.gravity).sort(), ['adlist', 'adlist_by_group', 'client', 'client_by_group', 'domainlist', 'domainlist_by_group', 'group']);
  assert.ok(Object.values(r.gravity).every((v) => v === true));
  assert.deepEqual(JSON.parse(JSON.stringify(r)), r, 'it survives JSON');
});

test('after a restore the box keeps its own update, power, counter and setup fields, and takes bedtime and the timer from the backup', () => {
  const before = C.parseState(JSON.stringify({
    update: { auto: true, latest: '3.1.0', notes: 'https://github.com/iret33/sinko/releases/tag/v3.1.0', checked: 1800000000, status: 'ok', from: '3.0.0', to: '3.1.0', at: 1800000100 },
    telemetry: { on: false }, community: { online: 7, at: 1800000000 }, setup: { done: true },
    schedule: { enabled: false, start: '21:00', end: '06:00', days: [0, 1, 2, 3, 4, 5, 6] } }));
  // The backup was taken long ago on a box that was mid-update, about to restart, with the counter on and a bedtime set.
  const imported = JSON.stringify({
    update: { request: 1700000000000, checkRequest: 1700000000001, status: 'running', from: '2.2.0', to: '3.0.0', at: 1700000000 },
    power: { request: 1700000000002, action: 'poweroff' }, telemetry: { on: true }, community: { online: 99, at: 1700000000 }, setup: { done: false },
    schedule: { enabled: true, start: '22:00', end: '06:30', days: [0, 1, 2, 3, 4] }, scheduleActive: true,
    timer: { mode: 'block', until: 1800009999, snapshot: { services: { youtube: true }, offline: false } } });
  const out = C.editState(imported, B.keepBoxFields(before)).state;
  assert.deepEqual(out.update, before.update, 'no old request, no "running" that would turn into "did not finish"');
  assert.deepEqual(out.power, { request: null, action: null }, 'a restart that was pending in the backup does not happen');
  assert.deepEqual(out.telemetry, { on: false }, 'the counter answer is today\'s');
  assert.deepEqual(out.community, before.community);
  assert.deepEqual(out.setup, { done: true });
  assert.deepEqual(out.schedule, { enabled: true, start: '22:00', end: '06:30', days: [0, 1, 2, 3, 4] }, 'bedtime is part of the rules that were restored');
  assert.equal(out.scheduleActive, true);
  assert.equal(out.timer.mode, 'block');
});

test('Sinko\'s groups: a restore of something else is noticed', () => {
  const g = (...names) => names.map((name, i) => ({ id: i, name }));
  assert.ok(B.hasSinkoGroups(g('Default', 'pb-kids', 'pb-offline', 'pb-paused', 'pb-state', 'pb-svc-youtube')));
  assert.ok(!B.hasSinkoGroups(g('Default', 'pb-kids', 'pb-offline', 'pb-paused')));
  assert.ok(!B.hasSinkoGroups(g('Default')));
  assert.ok(!B.hasSinkoGroups(undefined));
  assert.ok(!B.hasSinkoGroups([null, {}]));
});

test('links: only https links are shown', () => {
  const l = B.safeLinks({ home: 'https://github.com/iret33/sinko', issues: 'http://example.org', releases: 'javascript:alert(1)', privacy: 'https://x.y/p q',
    license: ' https://github.com/iret33/sinko/blob/master/LICENSE ', support: '', buy: null, extra: 'https://evil.example' });
  assert.deepEqual(l, { home: 'https://github.com/iret33/sinko', license: 'https://github.com/iret33/sinko/blob/master/LICENSE' });
  assert.deepEqual(B.safeLinks(null), {});
  assert.deepEqual(B.safeLinks('nonsense'), {});
});

test('the shipped links.json has all seven keys, GitHub links, and nothing for support and buy yet', () => {
  const l = JSON.parse(require('node:fs').readFileSync(require('node:path').join(__dirname, '..', 'web', 'links.json'), 'utf8'));
  assert.deepEqual(Object.keys(l).sort(), ['buy', 'home', 'issues', 'license', 'privacy', 'releases', 'support']);
  for (const k of ['home', 'issues', 'releases', 'privacy', 'license']) assert.match(l[k], /^https:\/\/github\.com\/iret33\/sinko(\/|#|$)/, k);
  assert.equal(l.privacy, 'https://github.com/iret33/sinko/blob/master/docs/privacy.md', 'the privacy link opens the privacy statement, not a README anchor that does not exist');
  assert.equal(l.support, '');
  assert.equal(l.buy, '');
});

test('the backup file is named for the day, and nothing from the network goes into the name', () => {
  assert.equal(B.backupName(new Date(2026, 9, 4, 12)), 'sinko-backup-2026-10-04.zip');
  assert.equal(B.backupName(new Date(2026, 0, 9, 12)), 'sinko-backup-2026-01-09.zip');
});

test('both languages have exactly the same strings, with the same placeholders', () => {
  const S = require('../web/pb-box.js').strings;
  assert.deepEqual(Object.keys(S.en).sort(), Object.keys(S.ar).sort());
  for (const k of Object.keys(S.en)) {
    const ph = (s) => (s.match(/\{\w+\}/g) || []).sort().join();
    assert.equal(ph(S.en[k]), ph(S.ar[k]), k);
  }
});
