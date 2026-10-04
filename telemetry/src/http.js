// Small HTTP helpers shared by the handlers: one error type, JSON responses, CORS.

export class HttpError extends Error {
  // `allow` is only for 405: the methods the path does accept.
  constructor(status, code, allow) {
    super(code);
    this.name = 'HttpError';
    this.status = status;
    this.code = code;
    this.allow = allow;
  }
}

// Only the public, read-only endpoints are readable from other websites (the project site fetches /v1/stats).
// /v1/ping deliberately never gets these headers: a box is not a browser, and refusing the preflight stops a
// web page from making its visitors' browsers add themselves to the count.
export const CORS_HEADERS = Object.freeze({
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
  'Access-Control-Max-Age': '86400',
});

export function jsonResponse(data, { status = 200, cache = 'no-store', cors = false, headers = {} } = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': cache,
      'X-Content-Type-Options': 'nosniff',
      ...(cors ? { 'Access-Control-Allow-Origin': '*' } : {}),
      ...headers,
    },
  });
}

export function errorResponse(err, { cors = false } = {}) {
  const extra = err.allow ? { Allow: err.allow } : {};
  return jsonResponse({ error: err.code }, { status: err.status, cors, headers: extra });
}

// Reads at most `limit` bytes of the request body and decodes them as UTF-8. The Content-Length header is only a
// hint (it can lie, and a chunked upload has none), so the bytes actually received are counted as well.
export async function readLimitedText(request, limit) {
  const declared = request.headers.get('content-length');
  if (declared !== null) {
    if (!/^\d{1,12}$/.test(declared)) throw new HttpError(400, 'bad_length');
    if (Number(declared) > limit) throw new HttpError(413, 'too_large');
  }
  if (request.body === null) return '';
  const reader = request.body.getReader();
  const chunks = [];
  let received = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      received += value.byteLength;
      if (received > limit) throw new HttpError(413, 'too_large');
      chunks.push(value);
    }
  } catch (err) {
    // Stop the upload. Without this a client could keep a connection open sending data nobody reads.
    await reader.cancel().catch(() => {});
    throw err instanceof HttpError ? err : new HttpError(400, 'unreadable_body');
  }
  const bytes = new Uint8Array(received);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  } catch {
    throw new HttpError(400, 'bad_encoding');
  }
}
