// Compact numbers for the badges: 999, 1k, 1.2k, 12k, 1.3M. One decimal below 10 of a unit, none above, rounding
// half up, and a carry into the next unit so that 999,999 reads "1M" and never "1000k".
// Integer arithmetic only: 9,950 must not become "9.9k" because 9.95 has no exact binary form.

const UNITS = ['', 'k', 'M', 'B'];

export function compactNumber(value) {
  if (!Number.isFinite(value) || value < 0) throw new RangeError('compactNumber needs a finite number >= 0');
  const n = Math.floor(value);
  if (n < 1000) return String(n);
  let div = 1000;
  // The last unit always returns, so this loop ends there.
  for (let unit = 1; ; unit += 1, div *= 1000) {
    const tenths = Math.floor((n * 10 + div / 2) / div);
    if (tenths < 100) {
      return (tenths % 10 === 0 ? String(tenths / 10) : (tenths / 10).toFixed(1)) + UNITS[unit];
    }
    const whole = Math.floor((n + div / 2) / div);
    // A whole of 1000 is one of the next unit: go round again.
    if (whole < 1000 || unit === UNITS.length - 1) return String(whole) + UNITS[unit];
  }
}
