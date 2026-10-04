import test from 'node:test';
import assert from 'node:assert/strict';
import { compactNumber } from '../src/format.js';

test('small numbers are written as they are', () => {
  for (const [n, text] of [[0, '0'], [7, '7'], [999, '999']]) assert.equal(compactNumber(n), text);
});

test('thousands: one decimal below 10k, none above, a trailing .0 is dropped', () => {
  const cases = [
    [1000, '1k'], [1049, '1k'], [1050, '1.1k'], [1200, '1.2k'], [1249, '1.2k'], [1250, '1.3k'],
    [9949, '9.9k'], [9950, '10k'], [9999, '10k'], [10000, '10k'], [12345, '12k'], [99499, '99k'], [99500, '100k'],
    [999_499, '999k'],
  ];
  for (const [n, text] of cases) assert.equal(compactNumber(n), text, String(n));
});

test('rolling over into the next unit never prints 1000k', () => {
  assert.equal(compactNumber(999_500), '1M');
  assert.equal(compactNumber(999_999), '1M');
  assert.equal(compactNumber(1_000_000), '1M');
  assert.equal(compactNumber(1_250_000), '1.3M');
  assert.equal(compactNumber(12_400_000), '12M');
  assert.equal(compactNumber(999_999_999), '1B');
  assert.equal(compactNumber(2_500_000_000), '2.5B');
});

test('fractions are cut and rubbish is refused', () => {
  assert.equal(compactNumber(1234.9), '1.2k');
  for (const bad of [-1, NaN, Infinity]) assert.throws(() => compactNumber(bad), RangeError);
});
