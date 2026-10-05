import test from 'node:test';
import assert from 'node:assert/strict';
import { HttpError } from '../src/http.js';
import { HARDWARE, isJsonContentType, isVersion, parseCountry, parseForget, parsePing } from '../src/validate.js';

const ID = '0123456789abcdef0123456789abcdef';
const refused = (text, code) => assert.throws(() => parsePing(text), (err) => err instanceof HttpError && err.status === 400 && err.code === code, `${text} -> ${code}`);

test('a valid ping is understood, in either key order and with spaces', () => {
  const expected = { id: ID, version: '3.0.0', hw: 'x86' };
  assert.deepEqual(parsePing(JSON.stringify({ id: ID, v: '3.0.0', hw: 'x86' })), expected);
  assert.deepEqual(parsePing(` { "hw": "x86", "v": "3.0.0", "id": "${ID}" } \n`), expected);
});

test('every hardware kind the box can send is accepted, nothing else', () => {
  assert.deepEqual([...HARDWARE], ['orangepi-zero3', 'raspberrypi', 'x86', 'other']);
  for (const hw of HARDWARE) assert.equal(parsePing(JSON.stringify({ id: ID, v: '3.0.0', hw })).hw, hw);
  for (const hw of ['arm', 'ORANGEPI-ZERO3', 'orangepi-zero3 ', 'orangepi-zero2', '', null, 3, ['x86'], { a: 1 }]) {
    refused(JSON.stringify({ id: ID, v: '3.0.0', hw }), 'invalid_hw');
  }
  refused(JSON.stringify({ id: ID, v: '3.0.0' }), 'invalid_hw');
});

test('the id is exactly 32 lowercase hex characters', () => {
  const body = (id) => JSON.stringify({ id, v: '3.0.0', hw: 'x86' });
  for (const id of [ID.toUpperCase(), ID.slice(1), `${ID}0`, `${ID.slice(1)}g`, `${ID.slice(1)} `, ` ${ID.slice(1)}`, '', null, 5, [ID], `${ID}\n`, 'é'.repeat(32)]) {
    refused(body(id), 'invalid_id');
  }
  refused(JSON.stringify({ v: '3.0.0', hw: 'x86' }), 'invalid_id');
});

test('the version is a semantic version, no more than 32 characters', () => {
  const good = ['0.0.0', '3.0.0', '10.20.30', '3.0.0-rc.1', '3.0.0-alpha', '1.0.0-0', '1.0.0-x-y.7.z-9', '9999.9999.9999'];
  const bad = ['', '3', '3.0', '3.0.0.0', 'v3.0.0', '03.0.0', '3.00.0', '3.0.0-', '3.0.0-01', '3.0.0+build', '3.0.0-rc.', '3.0.0-.rc',
    ' 3.0.0', '3.0.0 ', '10000.0.0', '3.0.0-' + 'a'.repeat(30), '１.0.0', 'latest', '3.0.0\n'];
  for (const v of good) assert.equal(isVersion(v), true, v);
  for (const v of bad) assert.equal(isVersion(v), false, JSON.stringify(v));
  for (const v of [3, null, undefined, ['3.0.0']]) assert.equal(isVersion(v), false);
  for (const v of bad) refused(JSON.stringify({ id: ID, v, hw: 'x86' }), 'invalid_version');
  refused(JSON.stringify({ id: ID, hw: 'x86' }), 'invalid_version');
});

test('unknown fields are refused, because nothing else is supposed to be sent', () => {
  refused(JSON.stringify({ id: ID, v: '3.0.0', hw: 'x86', lang: 'ar' }), 'unexpected_field');
  refused(JSON.stringify({ id: ID, v: '3.0.0', hw: 'x86', tz: null }), 'unexpected_field');
  refused('{"id":"' + ID + '","v":"3.0.0","hw":"x86","__proto__":{"a":1}}', 'unexpected_field');
});

test('anything that is not a JSON object is refused', () => {
  refused('', 'bad_json');
  refused('{', 'bad_json');
  refused('not json', 'bad_json');
  refused("{'id': 1}", 'bad_json');
  for (const text of ['null', '[]', '[1]', '"x"', '12', 'true']) refused(text, 'bad_body');
});

test('content types: JSON with optional parameters only', () => {
  for (const ok of ['application/json', 'Application/JSON', 'application/json; charset=utf-8', 'application/json;charset=UTF-8', ' application/json ']) {
    assert.equal(isJsonContentType(ok), true, ok);
  }
  for (const no of ['', 'text/plain', 'application/jsonx', 'application/json-patch+json', 'application/x-www-form-urlencoded', 'multipart/form-data; boundary=x', 'json', null]) {
    assert.equal(isJsonContentType(no), false, String(no));
  }
});

test('the country comes from CF-IPCountry: two letters, never XX or T1', () => {
  assert.equal(parseCountry('BH'), 'BH');
  assert.equal(parseCountry('bh'), 'BH');
  assert.equal(parseCountry(' sa '), 'SA');
  for (const v of ['XX', 'xx', 'T1', '', 'B', 'BHR', 'B1', '1B', 'ب ح', null, undefined]) assert.equal(parseCountry(v), null, String(v));
});

const refusedForget = (text, code) => assert.throws(() => parseForget(text), (err) => err instanceof HttpError && err.status === 400 && err.code === code, `${text} -> ${code}`);

test('a forget request is exactly {"id"} and the id is as strict as in a ping', () => {
  assert.deepEqual(parseForget(JSON.stringify({ id: ID })), { id: ID });
  assert.deepEqual(parseForget(` { "id" : "${ID}" } \n`), { id: ID });
  for (const id of [ID.toUpperCase(), ID.slice(1), `${ID}0`, `${ID.slice(1)}g`, '', null, 5, [ID], `${ID}\n`]) {
    refusedForget(JSON.stringify({ id }), 'invalid_id');
  }
  refusedForget('{}', 'invalid_id');
});

test('a forget request that carries anything else is refused, and so is anything that is not an object', () => {
  refusedForget(JSON.stringify({ id: ID, v: '3.0.0' }), 'unexpected_field');
  refusedForget(JSON.stringify({ id: ID, v: '3.0.0', hw: 'x86' }), 'unexpected_field');
  refusedForget('{"id":"' + ID + '","__proto__":{"a":1}}', 'unexpected_field');
  refusedForget('', 'bad_json');
  refusedForget('{', 'bad_json');
  for (const text of ['null', '[]', '"x"', '12', 'true']) refusedForget(text, 'bad_body');
});
