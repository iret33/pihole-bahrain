// What a ping may contain. The box sends exactly {"id","v","hw"} (see docs/maintainers/architecture.md, "Optional
// anonymous counter") and anything else is refused rather than silently dropped: the promise to families is that
// nothing else is sent, so a body that carries more is a bug in the sender that should be loud.

import { HttpError } from './http.js';

export const MAX_BODY_BYTES = 512;
export const HARDWARE = Object.freeze(['orangepi-zero3', 'raspberrypi', 'x86', 'other']);

const ID_RE = /^[0-9a-f]{32}$/;
// Semantic version: MAJOR.MINOR.PATCH, optionally -prerelease (numeric identifiers without leading zeros).
// No build metadata ("+...") and at most 32 characters: the value is stored and shown in /v1/stats.
const PRE_ID = '(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)';
const VERSION_RE = new RegExp(`^(?:0|[1-9][0-9]{0,3})\\.(?:0|[1-9][0-9]{0,3})\\.(?:0|[1-9][0-9]{0,3})(?:-${PRE_ID}(?:\\.${PRE_ID})*)?$`);
const MAX_VERSION_LENGTH = 32;
const JSON_TYPE_RE = /^application\/json\s*(?:;.*)?$/i;
const FIELDS = ['id', 'v', 'hw'];

export function isJsonContentType(value) {
  return typeof value === 'string' && JSON_TYPE_RE.test(value.trim());
}

export function isVersion(value) {
  return typeof value === 'string' && value.length <= MAX_VERSION_LENGTH && VERSION_RE.test(value);
}

// Throws HttpError(400, ...) with a code that names the first thing that is wrong. Returns {id, version, hw}.
export function parsePing(text) {
  let body;
  try {
    body = JSON.parse(text);
  } catch {
    throw new HttpError(400, 'bad_json');
  }
  if (body === null || typeof body !== 'object' || Array.isArray(body)) throw new HttpError(400, 'bad_body');
  for (const key of Object.keys(body)) {
    if (!FIELDS.includes(key)) throw new HttpError(400, 'unexpected_field');
  }
  if (typeof body.id !== 'string' || !ID_RE.test(body.id)) throw new HttpError(400, 'invalid_id');
  if (!isVersion(body.v)) throw new HttpError(400, 'invalid_version');
  if (typeof body.hw !== 'string' || !HARDWARE.includes(body.hw)) throw new HttpError(400, 'invalid_hw');
  return { id: body.id, version: body.v, hw: body.hw };
}

// CF-IPCountry is an ISO 3166-1 alpha-2 code. Cloudflare also sends "XX" (unknown) and "T1" (Tor): neither is a
// country, so both become null, and so does anything that is not exactly two letters.
export function parseCountry(value) {
  if (typeof value !== 'string') return null;
  const code = value.trim().toUpperCase();
  if (!/^[A-Z]{2}$/.test(code) || code === 'XX') return null;
  return code;
}
