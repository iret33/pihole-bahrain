'use strict';
// node --test tests/state-contract.test.js
//
// The shared state (JSON in the description of the pb-state group) is written by the scheduler (bin/sinko) and by the
// parent page. tests/fixtures/state-cases.json is the single list of cases: tests/test_state_contract.py runs it against
// the scheduler's parse_state, this file runs the same cases against the page's parseState.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const C = require('../web/pb-core.js');

const CASES = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'state-cases.json'), 'utf8')).cases;

test('every case in the shared fixture normalises exactly as the scheduler does', () => {
  assert.ok(CASES.length > 20, 'the fixture file is not empty');
  for (const c of CASES) assert.deepEqual(C.parseState(c.input), c.expected, c.name);
});

test('normalising twice changes nothing', () => {
  for (const c of CASES) {
    const once = C.parseState(c.input);
    assert.deepEqual(C.parseState(JSON.stringify(once)), once, c.name);
  }
});

test('an empty or missing description gives the default state, and every call gets its own copy', () => {
  assert.deepEqual(C.parseState(''), C.defaultState());
  assert.deepEqual(C.parseState(null), C.defaultState());
  assert.deepEqual(C.parseState(undefined), C.defaultState());
  const a = C.defaultState();
  a.schedule.days.push(9);
  a.update.auto = true;
  assert.deepEqual(C.defaultState().schedule.days, [0, 1, 2, 3, 4, 5, 6]);
  assert.equal(C.defaultState().update.auto, false);
});

test('a read-modify-write of one field keeps every other field the scheduler wrote', () => {
  // What the scheduler might have stored while an update is running, a bedtime is set and the counter has answered.
  const stored = JSON.stringify({
    v: 1, timer: { mode: 'block', until: 1800000500, snapshot: { services: { youtube: true, tiktok: false }, offline: true } },
    schedule: { enabled: true, start: '22:00', end: '06:30', days: [0, 1, 2, 3, 4] }, scheduleActive: true,
    update: { auto: true, request: null, checkRequest: 1800000001234, latest: '3.1.0',
      notes: 'https://github.com/iret33/sinko/releases/tag/v3.1.0', checked: 1800000000, status: 'running', from: '3.0.0', to: '3.1.0',
      at: 1800000100, error: null, rolledBack: null },
    power: { request: null, action: null }, telemetry: { on: true }, community: { online: 1234, at: 1800000000 }, setup: { done: true }
  });
  const st = C.parseState(stored);
  st.setup.done = false;                                   // the one thing this write edits
  const expected = JSON.parse(stored);
  expected.setup.done = false;
  assert.deepEqual(C.parseState(JSON.stringify(st)), expected);
});

test('values the scheduler would drop are dropped here too (nothing from the state is ever trusted)', () => {
  const s = C.parseState(JSON.stringify({
    update: { notes: 'https://evil.example/x', latest: '3.1.0; rm -rf /', status: 'exploded', error: 'x'.repeat(500) },
    power: { action: 'rm', request: 5 }, hello: 1
  }));
  assert.equal(s.update.notes, null);
  assert.equal(s.update.latest, null);
  assert.equal(s.update.status, 'idle');
  assert.equal(s.update.error.length, 200);
  assert.deepEqual(s.power, { request: null, action: null });
  assert.equal('hello' in s, false);
});

test('characters, not UTF-16 units, are counted for markers and the error text', () => {
  const emoji = '\u{1F600}';
  assert.equal(C.parseState(JSON.stringify({ update: { request: emoji.repeat(40) } })).update.request, emoji.repeat(40), '40 characters are fine');
  assert.equal(C.parseState(JSON.stringify({ update: { request: emoji.repeat(41) } })).update.request, null, '41 are not');
  const err = C.parseState(JSON.stringify({ update: { status: 'failed', error: emoji.repeat(300) } })).update.error;
  assert.equal(Array.from(err).length, 200, 'cut at 200 characters');
  assert.ok(!/[\uD800-\uDBFF]$/.test(err), 'never in the middle of a character');
});

test('Python truthiness for the few flags the scheduler coerces with bool()', () => {
  const s = C.parseState(JSON.stringify({ scheduleActive: [], schedule: { enabled: {} } }));
  assert.equal(s.scheduleActive, false);
  assert.equal(s.schedule.enabled, false);
  assert.equal(C.parseState(JSON.stringify({ scheduleActive: 'x' })).scheduleActive, true);
  assert.equal(C.parseState(JSON.stringify({ scheduleActive: 1 })).scheduleActive, true);
});

test('bedtime values are validated: times in range, days unique and sorted', () => {
  const s = C.parseState(JSON.stringify({ schedule: { enabled: true, start: '25:00', end: '06:61', days: [3, 1, 1, 9, -1, 2.5, 'x', 6] } }));
  assert.equal(s.schedule.start, '21:00', 'an impossible hour keeps the default');
  assert.equal(s.schedule.end, '06:00', 'an impossible minute keeps the default');
  assert.deepEqual(s.schedule.days, [1, 3, 6]);
  assert.deepEqual(C.parseState(JSON.stringify({ schedule: { days: 'all' } })).schedule.days, [0, 1, 2, 3, 4, 5, 6], 'days that are not a list keep the default');
});

test('editState: re-reads what is stored now, changes only what the mutator touches, and never stores what the schema forbids', () => {
  const stored = JSON.stringify({ update: { auto: true, latest: '3.1.0', status: 'running', from: '3.0.0', to: '3.1.0', at: 1800000100 },
    telemetry: { on: false }, community: { online: 9, at: 1800000000 }, setup: { done: true }, hello: 'dropped' });
  const out = C.editState(stored, (st) => { st.schedule.enabled = true; st.update.request = 1800000999000; });
  const back = JSON.parse(out.json);
  assert.deepEqual(back, out.state, 'json and state agree');
  assert.equal(back.schedule.enabled, true);
  assert.equal(back.update.request, 1800000999000);
  assert.equal(back.update.auto, true);
  assert.equal(back.update.latest, '3.1.0');
  assert.equal(back.update.status, 'running');
  assert.deepEqual(back.telemetry, { on: false });
  assert.deepEqual(back.community, { online: 9, at: 1800000000 });
  assert.deepEqual(back.setup, { done: true });
  assert.equal('hello' in back, false);
  // A bug in a caller cannot put a link, a command or a bad action into the state.
  const bad = C.editState(stored, (st) => { st.update.notes = 'javascript:alert(1)'; st.power = { request: 5, action: 'format' }; st.update.status = 'hacked'; });
  assert.equal(bad.state.update.notes, null);
  assert.deepEqual(bad.state.power, { request: null, action: null });
  assert.equal(bad.state.update.status, 'idle');
});
