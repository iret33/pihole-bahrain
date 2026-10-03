/* pb-core.js: the pure logic behind the live picture on the parent page. No DOM in here.
 *
 *   - classify Pi-hole's query statuses (blocked / allowed / answered from the box's memory / not final yet)
 *   - name the app behind a domain (longest suffix, from pb/domains.json) and tidy unknown domains
 *   - follow /api/queries without repeating or missing anything, using the SERVER's clock
 *   - decide which queries are worth animating (a home makes many per second; the picture shows a few)
 *   - curve maths for the wires
 *
 * Loaded by the page before pb-live.js, and by node for tests/pb-core.test.js. ES5 on purpose.
 */
(function (root) {
  'use strict';

  // ------------------------------------------------------------------ statuses
  // FTL's query status strings (get_query_status_str in FTL's datastructure.c).
  var BLOCKED = { GRAVITY: 1, REGEX: 1, DENYLIST: 1, EXTERNAL_BLOCKED_IP: 1, EXTERNAL_BLOCKED_NULL: 1, EXTERNAL_BLOCKED_NXRA: 1,
    EXTERNAL_BLOCKED_EDE15: 1, GRAVITY_CNAME: 1, REGEX_CNAME: 1, DENYLIST_CNAME: 1, DBBUSY: 1, SPECIAL_DOMAIN: 1 };
  var ALLOWED = { FORWARDED: 1, RETRIED: 1, RETRIED_DNSSEC: 1 };
  var MEMORY = { CACHE: 1, CACHE_STALE: 1 };

  /** 'blocked' | 'allowed' (sent on to the internet) | 'memory' (answered by the box itself) | 'pending' (not final). */
  function classify(status) {
    var s = String(status || '').toUpperCase();
    if (BLOCKED[s]) return 'blocked';
    if (MEMORY[s]) return 'memory';
    if (ALLOWED[s]) return 'allowed';
    return 'pending';                                   // IN_PROGRESS, UNKNOWN, or a status a newer FTL adds
  }

  // ------------------------------------------------------------------ names
  function isHiddenDomain(d) { d = String(d || '').toLowerCase(); return d === '' || d === 'hidden' || d === '<hidden>'; }
  function isHiddenClient(ip) { return !ip || ip === '0.0.0.0' || ip === '::'; }

  var TWO_LEVEL = /^(com|net|org|gov|edu|co|ac)\.[a-z]{2}$/;
  /** 'r4---sn-x.googlevideo.com' -> 'googlevideo.com'; 'www.bahrain.com.bh' -> 'bahrain.com.bh'. */
  function baseDomain(domain) {
    var parts = String(domain || '').toLowerCase().replace(/\.$/, '').split('.');
    if (parts.length <= 2) return parts.join('.');
    var last2 = parts.slice(-2).join('.');
    return TWO_LEVEL.test(last2) ? parts.slice(-3).join('.') : last2;
  }

  /** The app id for a domain by its longest matching suffix, or null. `map` is pb/domains.json's "domains" object. */
  function appFor(domain, map) {
    var d = String(domain || '').toLowerCase().replace(/\.$/, '');
    while (d) {
      if (map && Object.prototype.hasOwnProperty.call(map, d)) return map[d];
      var dot = d.indexOf('.');
      if (dot < 0) return null;
      d = d.slice(dot + 1);
    }
    return null;
  }

  // ------------------------------------------------------------------ numbers
  function formatCount(n, locale) {
    n = Math.round(Number(n) || 0);
    try { return new Intl.NumberFormat(locale || 'en-GB').format(n); } catch (e) { return String(n); }
  }
  /** Percent of `part` in `whole`: 0 when nothing happened, one decimal below 10. */
  function percent(part, whole) {
    if (!whole) return 0;
    var p = 100 * part / whole;
    return p < 10 ? Math.round(p * 10) / 10 : Math.round(p);
  }

  // ------------------------------------------------------------------ following the query log
  /**
   * Turns successive /api/queries responses into new events, once each.
   *
   *   var feed = new Feed(); var url = feed.url();       // first call reads a few recent rows, only to remember them
   *   feed.ingest(response)                                // the first response only primes it: no events
   *   ... url = feed.url(); events = feed.ingest(response) // later: only queries not seen before, oldest first
   *
   * `from` comes from the newest `time` in the server's own responses, never from the phone's clock, which may differ.
   * Rows that are not final yet (IN_PROGRESS) are skipped WITHOUT being remembered, because FTL returns them again
   * later with their final status.
   */
  function Feed(options) {
    options = options || {};
    this.length = options.length || 60;
    this.keep = options.keep || 800;
    this.overlap = options.overlap === undefined ? 2 : options.overlap;     // seconds re-read on every poll
    this.seen = {};
    this.order = [];
    this.from = null;
    this.primed = false;
    this.privacy = false;        // true: the box hides queries (privacy level 3) or has none to show
    this.truncated = false;      // true: a poll came back full, so some queries were probably not shown
  }
  Feed.prototype.url = function () {
    // The first call reads a few recent rows just to remember them (so they are never replayed as "new").
    return this.from === null ? '/api/queries?length=20' : '/api/queries?from=' + this.from + '&length=' + this.length;
  };
  Feed.prototype._remember = function (id) {
    this.seen[id] = 1;
    this.order.push(id);
    while (this.order.length > this.keep) delete this.seen[this.order.shift()];
  };
  Feed.prototype.ingest = function (resp) {
    var rows = resp && resp.queries instanceof Array ? resp.queries : [];
    this.privacy = rows.length === 0 && resp && resp.cursor === null;
    var newest = null, fresh = [], i, r;
    for (i = 0; i < rows.length; i++) {
      r = rows[i];
      if (typeof r.time === 'number' && (newest === null || r.time > newest)) newest = r.time;
    }
    if (newest !== null) this.from = Math.max(0, Math.floor(newest - this.overlap));
    else if (this.from === null && resp) this.from = Math.floor(Number(resp.time) || 0) || null;
    if (!this.primed) {                                   // the first answer only tells us where "now" is
      this.primed = true;
      for (i = 0; i < rows.length; i++) if (classify(rows[i].status) !== 'pending') this._remember(rows[i].id);
      if (this.from === null) this.from = 0;
      return [];
    }
    this.truncated = rows.length >= this.length;
    for (i = 0; i < rows.length; i++) {
      r = rows[i];
      if (this.seen[r.id] || classify(r.status) === 'pending') continue;
      this._remember(r.id);
      fresh.push({
        id: r.id, time: r.time, kind: classify(r.status), status: r.status,
        domain: r.domain, hiddenDomain: isHiddenDomain(r.domain),
        ip: r.client && r.client.ip, name: r.client && r.client.name, hiddenClient: isHiddenClient(r.client && r.client.ip),
      });
    }
    fresh.sort(function (a, b) { return a.time - b.time || a.id - b.id; });
    return fresh;
  };

  // ------------------------------------------------------------------ deciding what to animate
  /** Higher = more worth showing: blocked, known apps and children's devices come first; memory answers last. */
  function priority(ev) {
    return (ev.kind === 'blocked' ? 4 : 0) + (ev.app ? 3 : 0) + (ev.child ? 2 : 0) + (ev.kind === 'memory' ? -1 : 0);
  }

  /**
   * Hands out queued events at a gentle pace and never lets the queue grow: when it is full, the least interesting
   * event is dropped (it still counts in the numbers, it just does not get its own packet).
   */
  function Pacer(options) {
    options = options || {};
    this.gap = options.gap === undefined ? 380 : options.gap;    // ms between packets: about 2.6 per second at most
    this.max = options.max || 10;
    this.queue = [];
    this.last = -1e12;
    this.dropped = 0;
  }
  Pacer.prototype.push = function (events) {
    var i, j, worst;
    for (i = 0; i < events.length; i++) {
      this.queue.push(events[i]);
      if (this.queue.length > this.max) {
        worst = 0;
        for (j = 1; j < this.queue.length; j++) if (priority(this.queue[j]) < priority(this.queue[worst])) worst = j;
        this.queue.splice(worst, 1);
        this.dropped++;
      }
    }
  };
  /** The next event to animate at time `now` (ms), or null if it is too soon or nothing waits. */
  Pacer.prototype.next = function (now) {
    if (!this.queue.length || now - this.last < this.gap) return null;
    var best = 0, i;
    for (i = 1; i < this.queue.length; i++) if (priority(this.queue[i]) > priority(this.queue[best])) best = i;
    this.last = now;
    return this.queue.splice(best, 1)[0];
  };

  // ------------------------------------------------------------------ curves for the wires
  function lerp(a, b, t) { return a + (b - a) * t; }
  function easeInOut(t) { return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2; }
  function clamp01(t) { return t < 0 ? 0 : t > 1 ? 1 : t; }

  /** A smooth S-curve from a to b ({x, y}); it leaves along the longer axis, so wires look right in any layout. */
  function wire(a, b) {
    var horizontal = Math.abs(b.x - a.x) >= Math.abs(b.y - a.y);
    var c1 = horizontal ? { x: lerp(a.x, b.x, 0.5), y: a.y } : { x: a.x, y: lerp(a.y, b.y, 0.5) };
    var c2 = horizontal ? { x: lerp(a.x, b.x, 0.5), y: b.y } : { x: b.x, y: lerp(a.y, b.y, 0.5) };
    var r = function (n) { return Math.round(n * 10) / 10; };
    return { p0: a, p1: c1, p2: c2, p3: b,
      d: 'M' + r(a.x) + ' ' + r(a.y) + 'C' + r(c1.x) + ' ' + r(c1.y) + ' ' + r(c2.x) + ' ' + r(c2.y) + ' ' + r(b.x) + ' ' + r(b.y) };
  }
  /** The point at fraction t (0..1) along a wire() curve. */
  function pointAt(w, t) {
    t = clamp01(t);
    var u = 1 - t, a = u * u * u, b = 3 * u * u * t, c = 3 * u * t * t, d = t * t * t;
    return { x: a * w.p0.x + b * w.p1.x + c * w.p2.x + d * w.p3.x, y: a * w.p0.y + b * w.p1.y + c * w.p2.y + d * w.p3.y };
  }

  var api = {
    classify: classify, isHiddenDomain: isHiddenDomain, isHiddenClient: isHiddenClient, baseDomain: baseDomain, appFor: appFor,
    formatCount: formatCount, percent: percent, Feed: Feed, priority: priority, Pacer: Pacer,
    lerp: lerp, easeInOut: easeInOut, clamp01: clamp01, wire: wire, pointAt: pointAt,
  };
  root.PBCore = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : this);
